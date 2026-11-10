"""Derive the new-round POPULATION cohort — `data/cohort_stats/per_study_cohort_new.csv`.

The 66 materialized 3-modality (femur / center-RF / sweep) studies from the new protocol round,
turned into a percentile-placement reference so a live patient can be placed against new-round peers
rather than only the old-round 282. It is a PERCENTILE reference ONLY — the new round has no doctors'
grades, so it is NOT a model-refit source; the frozen condition model's mu/sd stay on the old-round
cohort (v1.0 / v1.1). Kept SEPARATE from the old-round cohort (population axis, not version axis).

Faithfulness — the whole point of building it through production code:
  * Per cine, segment EVERY frame on the canonical NATIVE bundle, rank by RF area, take the top-5,
    and median each feature over them: `landmark.measure_frame_geometry` -> `compute_frame_features`,
    the SAME primitive the live assessment + the old-round precompute use, so a cohort number and a
    live number on the same mask + spacing are bit-identical (labeling landmark contract).
  * Merge the three cines with `feature_routing.route_aggregate` — the SAME routing the live study
    runs: ALL measurements (geometry + fat + QMT/STAR) from the femur, ONLY texture from the center-RF
    (Akadir 2026-07-14). So a study placed here and the same study scored live are measured by one ruler.
  * int_cv (texture) is admitted to the cohort ONLY when the 3-way agrees (resolve_rf_source's
    int_cv_trusted) — an untrusted center-RF echo is withheld (left blank), never faked into the
    reference distribution.

De-identification: study_code = "NR" + sha8(folder name) — unique + PHI-safe (the raw folder name
carries patient identifiers and must never reach a committed file). Demographics come from the one
canonical name grammar.

KNOWN, DOCUMENTED simplifications (shared with the way the old-round cohort was cut, so the two stay
internally comparable; recorded in the provenance file):
  * landmark AUTO-selection (top-5 by RF area) stands in for an operator's frame picks.
  * the femur landmark is ranked by RF area, not `femur_cine_scorer`'s femur ranking.

Usage:
  .venv/bin/python scripts/derive_new_round_cohort.py            # segment + assemble (GPU; ~background)
  .venv/bin/python scripts/derive_new_round_cohort.py --from-json scratchpad/new_round_landmark_full.json
  .venv/bin/python scripts/derive_new_round_cohort.py --snapshot 5   # uid-keyed, name-free copy of the
        # working landmark JSON as data/cohort_stats/new_round_landmark_r5.json (no cohort rewrite)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import median

sys.path.insert(0, "scripts")
import preflight_helpers.env_bootstrap  # noqa: F401  (wires the PYTHONPATH bridge)

from preflight_helpers import env
from preflight_helpers import flag_kill_ledger as fkl
from preflight_helpers.feature_routing import route_aggregate
from preflight_helpers.rf_reliability import resolve_rf_source
from preflight_helpers.study_discovery import list_frame_bases, study_modalities
from preflight_helpers.study_name_grammar import parse as parse_study_name

STUDIES_ROOT = Path("data/studies_root")
NATIVE_STEM = "native_512x256_v5___300_samples____fold4"
LANDMARK_JSON = Path("scratchpad/new_round_landmark_full.json")
# Per-freeze, uid-keyed, name-free copy of the landmark measurements, written beside
# per_study_cohort_new_r<N>.csv by cohort_freeze_log --record (2026-09-03). LANDMARK_JSON itself is
# gitignored, overwritten by every apply, and carries the raw folder name in `study` (PHI).
LANDMARK_SNAPSHOT = "new_round_landmark_r{n}.json"
OUT_CSV = env.COHORT_STATS_DIR / "per_study_cohort_new.csv"
OUT_PROV = env.COHORT_STATS_DIR / "new_round_cohort_provenance.json"

# Everything the 17-col schema, the routing, and the RF decision need, medianed per cine over the
# top-5 landmark frames. (star_ratio is NOT here — it is derived per study from qmt_mm / BMI.)
KEEP = ["full_max_thickness_mm", "full_thickness_q50_mm", "rf_thickness_mm", "visible_rf_area_mm2",
        "int_cv_within", "qmt_mm", "fat_to_depth_q50", "fat_thickness_q50_mm", "fat_thickness_max_mm",
        "muscle_fat_ratio"]
K = 5

# v1.0 per_study_cohort.csv column order — matched exactly so every consumer (percentile / box /
# dot-strip) reads the new cohort identically.
COLUMNS = ["study_code", "rf_thickness_mm", "muscle_fat_ratio", "age", "sex", "bmi", "quality_label",
           "triple_rated", "qmt_mm", "star_ratio", "qmt_provenance", "int_cv_within",
           "full_thickness_q50_mm", "fat_thickness_q50_mm", "fat_thickness_max_mm", "fat_to_depth_q50",
           "visible_rf_area_mm2"]

_SEX = {"e": "M", "m": "M", "k": "F", "f": "F"}   # erkek/male -> M, kadın/female -> F; else unknown

# Reviewed texture withholds live in the flag→kill ledger (feature grain, `int_cv_within`), read
# at freeze time via fkl.feature_kills() — migrated 2026-08-13 from the hand-maintained
# TEXTURE_WITHHOLD constant so a verdict recorded with measurement_verdict.py --scope feature
# enforces without a code edit. The founding case (AKA017 / NR1C66A990, Akadir in-app 2026-08-03):
# a centre-RF shot at the FEMUR plane passes the 3-way agreement gate precisely BECAUSE it images
# the same anatomy as the femur cine, so its echo enters as trusted despite the wrong depth — the
# gate is defeated by the error it should catch. Withhold the echo, keep every length measurement.


def _study_code(dirname: str) -> str:
    return "NR" + hashlib.sha256(dirname.encode("utf-8")).hexdigest()[:8].upper()


RUNS_DIR = Path("data/runs")
# What each role's contributed frames may refine (spec §4b): femur frames -> the geometry / fat /
# QMT medians that role serves; RF frames -> the texture median alone. The gate anchor
# (full_max_thickness_mm) is deliberately NOT poolable — trust decisions read automatic values.
_POOL_FEMUR = [k for k in KEEP if k not in ("full_max_thickness_mm", "int_cv_within")]
_POOL_RF = ["int_cv_within"]


def _pool_contributions(femur: dict, rf: dict, entries: list, *, skip_int_cv: bool):
    """The "5+xth frame": per feature, the served value becomes the median over the automatic
    evidence PLUS every contributed run's vetted frames. The automatic side is the real top-K
    frames when the landmark JSON carries them (`top_frames`, schema of 2026-08-14+) — else the
    auto median replicated K times, keeping its true weight at coarser grain. A record that
    predates per-frame persistence contributes its aggregate value once, flagged. When a
    run-bound texture release exists, int_cv is NOT pooled — an explicit verdict outranks
    evidence addition. Returns (femur', rf', provenance | None)."""
    ops = {"femur": [], "rf": []}
    runs, notes = [], []
    for e in entries:
        run_id = e.get("source_run_id") or (e.get("fields") or ["?"])[0]
        rec_p = RUNS_DIR / run_id / "record.json"
        if not rec_p.exists():
            notes.append(f"{run_id}: record missing — skipped")
            continue
        rec = json.load(open(rec_p))
        rp = rec.get("routed_picks") or {}
        got = False
        for role in ("femur", "rf"):
            ffs = (rp.get(role) or {}).get("frame_features") or []
            if ffs:
                ops[role].extend(ffs)
                got = True
        if not got:
            agg = rec.get("aggregate") or {}
            ops["femur"].append({k: agg.get(k) for k in _POOL_FEMUR})
            ops["rf"].append({"int_cv_within": agg.get("int_cv_within")})
            notes.append(f"{run_id}: aggregate granularity (record predates per-frame persistence)")
        runs.append(run_id)
    if not runs:
        return femur, rf, None

    def _pooled(role_dict: dict, role: str, feats: list) -> dict:
        out = dict(role_dict)
        top = role_dict.get("top_frames") or []
        for k in feats:
            op_vals = [f.get(k) for f in ops[role] if isinstance(f.get(k), (int, float))]
            if not op_vals:
                continue
            auto_vals = [tf.get(k) for tf in top if isinstance(tf.get(k), (int, float))]
            if not auto_vals and role_dict.get(k) is not None:
                auto_vals = [role_dict[k]] * K
            out[k] = float(median(auto_vals + op_vals))
        return out

    femur2 = _pooled(femur, "femur", _POOL_FEMUR)
    rf2 = rf if skip_int_cv else _pooled(rf, "rf", _POOL_RF)
    prov = {"runs": runs, "frames": {r: len(ops[r]) for r in ("femur", "rf")},
            "int_cv_pooled": (not skip_int_cv) and any(
                isinstance(f.get("int_cv_within"), (int, float)) for f in ops["rf"]),
            "notes": notes}
    return femur2, rf2, prov


def _demographics(dirname: str) -> dict:
    """PHI-safe demographics from the canonical grammar (never the raw name)."""
    p = parse_study_name(dirname)
    bmi = None
    if p.height and p.weight and p.height > 0:
        bmi = round(p.weight / (p.height / 100.0) ** 2, 1)
    return {"age": p.age, "sex": _SEX.get((p.sex or "").lower(), "unknown"), "bmi": bmi,
            "matched": p.matched}


# --------------------------------------------------------------------------- #
# Phase 1 — segment + landmark-measure every study (the expensive, GPU pass).
# --------------------------------------------------------------------------- #
class _Segmenter:
    """The frozen NATIVE bundle + the primitives `measure_study` needs, loaded ONCE (GPU). Kept as an
    object so a caller that measures a few studies (the grading-pack builder, a receipt script) loads
    the model once, and so a test can hand `measure_study` a fake with the same attributes."""

    def __init__(self):
        import torch  # noqa: F401
        from preflight_helpers.model_registry import list_bundles
        from preflight_helpers.segmenter import (BundleMeta, load_learner_safe, predict_mask,
                                                 build_voi_hist_clahe_stack, synthesize_final_u8,
                                                 LABEL_RF)
        from preflight_helpers.landmark import measure_frame_geometry
        from preflight_helpers.run_pipeline import _spacing_row_from_dicom

        bundle = next(x for x in list_bundles(compute_hash=False)
                      if NATIVE_STEM in Path(x.pkl_path).stem)
        self.pkl_path = Path(bundle.pkl_path)
        self.learn = load_learner_safe(bundle.pkl_path)
        self.meta = BundleMeta.from_pkl(bundle.pkl_path)
        self.predict_mask = predict_mask
        self.stack = build_voi_hist_clahe_stack
        self.synth = synthesize_final_u8
        self.label_rf = LABEL_RF
        self.geometry = measure_frame_geometry
        self.spacing_row = _spacing_row_from_dicom


def measure_study(sp: Path, seg, roles: tuple = ("femur", "rf", "sweep")) -> dict | None:
    """Segment EVERY frame of every role of ONE study on the frozen bundle, rank by RF area, keep the
    top-K. Per role: the medians (the served numbers), the per-frame values (`top_frames`) and — since
    2026-09-03 — WHICH frames they are (`top_frame_bases`, best-first, index-aligned with
    `top_frames`). Before that the freeze kept values only, so nothing on disk could say which frames
    a served number came from; the doctors' grading pack would have had to re-derive ~5,000
    segmentations to prove "shown = served" (grading-round audit 2026-09-02, risk #1). Frame bases
    are capture timestamps (`20260702_150730000`), never names. Returns None only when the study's
    modalities cannot be read; a study with no measurable role still returns a row (empty `vals`).
    `roles` (2026-09-04) lets a consumer that only serves the cine frames — the grading-pack builder —
    skip the sweep's ~100 segmentations per study; the freeze itself always passes all three."""
    try:
        mods = study_modalities(sp)
    except Exception as e:
        print(f"[SKIP] {sp.name[:40]}: study_modalities failed: {e}", flush=True)
        return None
    vals: dict = {}
    for role in roles:
        m = mods.get(role)
        if not m or not m.raw_image_dir:
            continue
        try:
            spacing = seg.spacing_row(m.raw_image_dir.parent, None)
        except Exception:
            spacing = {}
        cands = []
        for fb in list_frame_bases(m.raw_image_dir):
            try:
                final = seg.synth(m.raw_image_dir, fb)
                mask = seg.predict_mask(seg.learn, seg.stack(final, modalities=seg.meta.modalities),
                                        seg.meta)
                sp2 = dict(spacing)
                sp2["mask_height"], sp2["mask_width"] = mask.shape[:2]
                ff = seg.geometry(mask, final, sp2)
                area = int((mask == seg.label_rf).sum())
                if area > 0:
                    prov = ff.get("qmt_provenance")
                    cands.append((area, {k: ff.get(k) for k in KEEP}, prov, fb))
            except Exception:
                continue
        if not cands:
            continue
        cands.sort(key=lambda t: t[0], reverse=True)   # stable: ties keep frame-base order
        top = cands[:K]
        lm = {}
        for k in KEEP:
            xs = [c[1][k] for c in top if c[1][k] is not None]
            lm[k] = float(median(xs)) if xs else None
        provs = [c[2] for c in top if c[2]]
        lm["qmt_provenance"] = Counter(provs).most_common(1)[0][0] if provs else None
        lm["n_frames"] = len(cands)
        # the top-K per-frame values themselves (2026-08-14): the contribute-frames pool
        # medians over REAL frames on both sides instead of replicating the auto median.
        lm["top_frames"] = [c[1] for c in top]
        # ...and which frames those are (2026-09-03) — the shown-set identity the grading pack serves.
        lm["top_frame_bases"] = [c[3] for c in top]
        vals[role] = lm
    g = lambda r: (vals.get(r) or {}).get("full_max_thickness_mm")  # noqa: E731
    dec = resolve_rf_source(g("femur"), g("rf"), g("sweep"))
    return {"study": sp.name, "study_uid": _study_code(sp.name), "vals": vals, "decision": dec}


def measure_all() -> list:
    seg = _Segmenter()
    print(f"[model] {seg.pkl_path.stem[:60]}", flush=True)

    studies = sorted({c.parent for c in STUDIES_ROOT.rglob("captures")}, key=lambda p: p.name)
    # Ledger-killed studies (uid-keyed: duplicate ingests, dead recordings) never enter the
    # cohort — membership is no longer "has a captures/ dir" alone (the 08-03 audit's gap).
    dead = fkl.killed_study_uids()
    n_all = len(studies)
    studies = [sp for sp in studies if _study_code(sp.name) not in dead]
    if len(studies) < n_all:
        print(f"[studies] {n_all - len(studies)} ledger-killed studies excluded", flush=True)
    print(f"[studies] {len(studies)} materialized", flush=True)
    rows = []
    for i, sp in enumerate(studies):
        row = measure_study(sp, seg)
        if row is None:
            continue
        rows.append(row)
        if (i + 1) % 8 == 0:
            dec = row["decision"]
            print(f"[{i+1}/{len(studies)}] {sp.name[:26]:26s} -> {dec['source']:9s} "
                  f"trusted={dec['int_cv_trusted']}", flush=True)
    LANDMARK_JSON.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(LANDMARK_JSON, "w"), indent=1)
    print(f"[wrote] {LANDMARK_JSON}  ({len(rows)} studies)", flush=True)
    return rows


def _name_tokens(names: list) -> set:
    """Every alphabetic name token (>=3 chars) the study-name grammar reads off the folder names —
    the surname/first-name pieces that must not survive into a committed file."""
    toks: set = set()
    for n in names:
        try:
            toks.update(t for t in parse_study_name(n).name_tokens if len(t) >= 3)
        except Exception:
            continue
    return toks


def _refuse_if_named(text: str, names: list, refreeze: int) -> None:
    """A folder name, or any of its name tokens as a whole word (case-insensitive), in the text =
    refuse. Whole-word against the snapshot's templated vocabulary has no false positive today
    (r5: 0 hits over 176 folder names) and a false positive is a loud stop, never a silent leak."""
    import re
    leaked = [n for n in names if n and n in text]
    hits = sorted(t for t in _name_tokens(names)
                  if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, flags=re.IGNORECASE))
    if leaked or hits:
        raise ValueError(f"landmark snapshot r{refreeze} would carry {len(leaked)} study folder "
                         f"name(s) and {len(hits)} name token(s) — refusing to write")


def build_landmark_snapshot(rows: list, *, refreeze: int, date: str, expect_uids=None) -> dict:
    """The validated, name-free, uid-keyed document for ONE freeze (see snapshot_landmark). With
    `expect_uids` (the r<N> CSV's study codes) the document is bound to the cohort it sits beside:
    a cohort uid the rows cannot describe → refuse (the working JSON is not this freeze's source);
    a row outside the cohort (a ledger kill applied at assembly, the AKA158-in-r4 case) → dropped
    and counted, never committed. Raises ValueError instead of writing anything."""
    studies, names = [], []
    for r in rows:
        name = r.get("study")
        uid = r.get("study_uid") or (_study_code(name) if name else None)
        if not uid:
            continue
        if name:
            names.append(name)
        studies.append({"study_uid": uid, "vals": r.get("vals") or {},
                        "decision": r.get("decision") or {}})
    dropped = 0
    if expect_uids is not None:
        expect = set(expect_uids)
        missing = sorted(expect - {s["study_uid"] for s in studies})
        if missing:
            raise ValueError(f"landmark snapshot r{refreeze}: the landmark rows do not describe the "
                             f"cohort — {len(missing)} cohort uid(s) absent (e.g. {missing[:3]})")
        dropped = sum(1 for s in studies if s["study_uid"] not in expect)
        studies = [s for s in studies if s["study_uid"] in expect]
    studies.sort(key=lambda s: s["study_uid"])
    measured = [s for s in studies if s["vals"]]
    with_bases = sum(1 for s in measured
                     if all("top_frame_bases" in (v or {}) for v in s["vals"].values()))
    identity = ("top_frame_bases" if measured and with_bases == len(measured)
                else "values_only" if with_bases == 0 else "partial")
    doc = {
        "refreeze": refreeze, "date": date, "bundle": NATIVE_STEM,
        "ranker": "RF-area top-5 (repr_scorer landmark)", "k": K,
        "frame_identity": identity, "studies_with_frame_bases": with_bases,
        "n_studies": len(studies), "dropped_not_in_cohort": dropped, "studies": studies,
    }
    _refuse_if_named(json.dumps(doc, indent=1, ensure_ascii=False), names, refreeze)
    return doc


def landmark_snapshot_path(refreeze: int, out_dir: Path = env.COHORT_STATS_DIR) -> Path:
    return Path(out_dir) / LANDMARK_SNAPSHOT.format(n=refreeze)


def write_landmark_snapshot(doc: dict, *, out_dir: Path = env.COHORT_STATS_DIR,
                            force: bool = False) -> Path:
    """A freeze's evidence is written once; replacing it needs an explicit `force` (the CLI's
    `--force`) so a later `--snapshot 5` can never quietly restamp r6 measurements as r5."""
    out = landmark_snapshot_path(doc["refreeze"], out_dir)
    if out.exists() and not force:
        raise FileExistsError(f"{out} exists — a freeze's evidence is not rewritten "
                              f"(pass force / --force to replace it deliberately)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def snapshot_landmark(rows: list, *, refreeze: int, date: str,
                      out_dir: Path = env.COHORT_STATS_DIR, expect_uids=None,
                      force: bool = False) -> Path:
    """Persist ONE freeze's landmark measurements durably: `new_round_landmark_r<N>.json` beside
    `per_study_cohort_new_r<N>.csv`, keyed by study_uid, with every folder name stripped. The working
    LANDMARK_JSON is gitignored, overwritten by each apply and carries the raw folder name in `study`
    (PHI), so until 2026-09-03 no freeze kept a record of the per-frame evidence its numbers came
    from. `frame_identity` says whether the rows carry `top_frame_bases` (measured 2026-09-03+) or
    values only (r1–r5: a consumer must re-derive the frames on the frozen bundle and receipt them
    against `top_frames`). Validation (names, cohort membership) happens BEFORE anything is written."""
    doc = build_landmark_snapshot(rows, refreeze=refreeze, date=date, expect_uids=expect_uids)
    return write_landmark_snapshot(doc, out_dir=out_dir, force=force)


# --------------------------------------------------------------------------- #
# Phase 2 — assemble the cohort CSV from the landmark measurements (cheap, pure).
# --------------------------------------------------------------------------- #
def assemble_cohort(rows: list) -> tuple[list, dict]:
    out_rows, prov_studies = [], []
    n_texture_withheld = n_no_demo = 0
    # Kills are filtered HERE too, not only in measure_all: the --from-json path replays a
    # landmark JSON that may predate a kill (the AKA158 duplicate sits in the r4 JSON).
    dead = fkl.killed_study_uids()
    killed_excluded = sorted(_study_code(r["study"]) for r in rows if _study_code(r["study"]) in dead)
    rows = [r for r in rows if _study_code(r["study"]) not in dead]
    clears_all = fkl.feature_clears()
    clears_applied, clears_stale = [], []
    manual_kills = {uid for uid, feats in fkl.feature_kills().items() if "int_cv_within" in feats}
    contribs_all = fkl.contributions()
    contributions_applied: dict = {}

    def _match(a, b):
        """A clear-snapshot value vs the currently measured one — equal within 0.05 mm, with
        None only matching None (a view appearing/disappearing invalidates the clear too)."""
        if a is None or b is None:
            return a is None and b is None
        try:
            return abs(float(a) - float(b)) <= 0.05
        except (TypeError, ValueError):
            return False

    for r in rows:
        vals = r["vals"]
        femur = dict(vals.get("femur") or {})
        rf = dict(vals.get("rf") or {})
        sweep = dict(vals.get("sweep") or {})
        code = _study_code(r["study"])
        # the femur-vs-center comparison ONLY decides whether the center-RF texture is
        # trustworthy — computed from the AUTOMATIC values, BEFORE any pooling, so a
        # contribution can refine a served number but never flip a trust decision or stale a
        # recorded clear (the contribute-frames precedence rule, spec §4b).
        dec = resolve_rf_source(femur.get("full_max_thickness_mm"), rf.get("full_max_thickness_mm"),
                                sweep.get("full_max_thickness_mm"))
        # ---- contributions: operator-vetted frames join the canonical pool (the "5+xth frame").
        contribs = contribs_all.get(code) or []
        contrib_prov = None
        if contribs:
            _clear0 = (clears_all.get(code) or {}).get("int_cv_within")
            _release_bound = bool(_clear0 and _clear0.get("released_value") is not None)
            femur, rf, contrib_prov = _pool_contributions(
                femur, rf, contribs, skip_int_cv=_release_bound)
            if contrib_prov:
                contributions_applied[code] = contrib_prov
        # route through the SAME merge the live study uses: ALL measurements <- femur, texture <- center-RF.
        routed = route_aggregate(sweep, femur, rf)

        demo = _demographics(r["study"])
        if not demo["matched"]:
            n_no_demo += 1
        bmi = demo["bmi"]
        qmt = routed.get("qmt_mm")
        star = round(qmt / bmi, 4) if (qmt is not None and bmi) else None

        # int_cv admitted only when the 3-way validates the center-RF echo AND no reviewed withhold
        # applies; else withheld (blank). One recorded exception (2026-08-13): a feature-grain
        # CLEAR in the ledger — Akadir reviewed the disagreement and consented to the texture.
        # The clear binds to the per-view thicknesses it was decided on: honored only while the
        # currently measured values still match its snapshot (0.05 mm), else it is STALE and the
        # gate's distrust stands.
        manual_withhold = code in manual_kills
        clear = (clears_all.get(code) or {}).get("int_cv_within")
        texture_cleared = False
        released_value = released_run = None
        if clear and not manual_withhold and not dec["int_cv_trusted"]:
            snap = clear.get("values") or {}
            cur = {"max_femur_mm": femur.get("full_max_thickness_mm"),
                   "max_center_mm": rf.get("full_max_thickness_mm"),
                   "max_sweep_mm": sweep.get("full_max_thickness_mm")}
            if all(_match(snap.get(k), cur[k]) for k in cur):
                texture_cleared = True
                clears_applied.append(code)
                # A RUN-BOUND clear (the refine-surface contract, spec §3): the operator vetted
                # a bounded RF region in an ACCEPTED run, so the released number is that run's
                # refined value — serving the auto value here would re-admit the very
                # contamination the refinement excluded. Plain clears carry no released_value
                # and keep serving the auto (routed) texture, as before.
                if clear.get("released_value") is not None:
                    released_value = float(clear["released_value"])
                    released_run = clear.get("source_run_id")
            else:
                clears_stale.append(code)
        int_cv = (routed.get("int_cv_within")
                  if ((dec["int_cv_trusted"] or texture_cleared) and not manual_withhold) else None)
        if texture_cleared and released_value is not None:
            int_cv = released_value
        if int_cv is None:
            n_texture_withheld += 1

        # qmt_provenance follows the femur cine (that is where QMT is measured).
        qmt_prov = (femur or {}).get("qmt_provenance") or ("no_femur" if not femur else None)

        row = {
            "study_code": code,
            "rf_thickness_mm": routed.get("rf_thickness_mm"),
            "muscle_fat_ratio": routed.get("muscle_fat_ratio"),
            "age": demo["age"],
            "sex": demo["sex"],
            "bmi": bmi,
            "quality_label": "",          # no doctors' grades on the new round
            "triple_rated": False,
            "qmt_mm": qmt,
            "star_ratio": star,
            "qmt_provenance": qmt_prov,
            "int_cv_within": int_cv,
            "full_thickness_q50_mm": routed.get("full_thickness_q50_mm"),
            "fat_thickness_q50_mm": routed.get("fat_thickness_q50_mm"),
            "fat_thickness_max_mm": routed.get("fat_thickness_max_mm"),
            "fat_to_depth_q50": routed.get("fat_to_depth_q50"),
            "visible_rf_area_mm2": routed.get("visible_rf_area_mm2"),
        }
        out_rows.append(row)
        prov_studies.append({"study_code": row["study_code"], "rf_source": dec["source"],
                             "reliability": dec["reliability"], "int_cv_trusted": dec["int_cv_trusted"],
                             "manual_texture_withhold": manual_withhold,
                             "texture_cleared": texture_cleared,
                             "texture_released_from_run": released_run})

    n = len(out_rows)
    agree = sum(1 for p in prov_studies if p["rf_source"] == "center_rf")
    prov = {
        "cohort": "new_round_population",
        "n_studies": n,
        "purpose": "percentile-placement reference only (no doctors' grades; NOT a model-refit source)",
        "bundle": NATIVE_STEM,
        "landmark": {"ranker": "RF-area top-5 (repr_scorer landmark)", "k": K},
        "routing": "feature_routing.route_aggregate — ALL measurements from femur, texture from center-RF",
        "geometry_source": "femur (all studies)",
        "texture_trust_counts": dict(Counter(p["rf_source"] for p in prov_studies)),
        "center_rf_validated_pct": round(100 * agree / n, 1) if n else 0.0,
        "texture_withheld": n_texture_withheld,
        "manual_texture_withholds": sorted(p["study_code"] for p in prov_studies
                                           if p["manual_texture_withhold"]),
        "killed_studies_excluded": killed_excluded,
        "texture_clears_applied": sorted(clears_applied),
        "texture_clears_stale": sorted(clears_stale),
        "contributions_applied": contributions_applied,
        "studies_without_demographics": n_no_demo,
        "simplifications": [
            "landmark auto-selection (top-5 by RF area) stands in for operator frame picks",
            "femur landmark ranked by RF area, not femur_cine_scorer",
        ],
        "per_study": prov_studies,
    }
    return out_rows, prov


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return str(v)


def write_csv(rows: list) -> None:
    import csv
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")   # match the v1.0 cohort CSV's \n (not csv's default \r\n)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([_fmt(r[c]) for c in COLUMNS])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", type=Path, default=None,
                    help="skip segmentation; assemble from a prior landmark JSON")
    ap.add_argument("--snapshot", type=int, metavar="N", default=None,
                    help="write data/cohort_stats/new_round_landmark_r<N>.json from the working "
                         "landmark JSON (or --from-json) — uid-keyed, name-free; no cohort rewrite")
    ap.add_argument("--date", default=None, help="date stamped into a --snapshot (default: JSON mtime)")
    ap.add_argument("--force", action="store_true",
                    help="with --snapshot: replace an existing r<N> snapshot (a freeze's evidence is "
                         "otherwise written once)")
    args = ap.parse_args()

    if args.snapshot is not None:
        import csv as _csv
        import time
        src = args.from_json or LANDMARK_JSON
        rows = json.load(open(src))
        date = args.date or time.strftime("%Y-%m-%d", time.localtime(src.stat().st_mtime))
        # bound to the archived cohort it sits beside, when that archive exists
        archive = env.COHORT_STATS_DIR / f"per_study_cohort_new_r{args.snapshot}.csv"
        expect = ({r["study_code"] for r in _csv.DictReader(open(archive, encoding="utf-8"))}
                  if archive.exists() else None)
        out = snapshot_landmark(rows, refreeze=args.snapshot, date=date, expect_uids=expect,
                                force=args.force)
        doc = json.load(open(out))
        print(f"[snapshot] r{args.snapshot} <- {src} ({len(rows)} rows)  -> {out}")
        print(f"  n_studies {doc['n_studies']}  bound to {archive.name if expect is not None else 'nothing (no r-archive)'}"
              f"  dropped_not_in_cohort {doc['dropped_not_in_cohort']}  frame_identity {doc['frame_identity']} "
              f"({doc['studies_with_frame_bases']} with bases)  date {doc['date']}")
        return

    if args.from_json:
        rows = json.load(open(args.from_json))
        print(f"[from-json] {args.from_json} ({len(rows)} studies)", flush=True)
    else:
        rows = measure_all()

    out_rows, prov = assemble_cohort(rows)
    write_csv(out_rows)
    OUT_PROV.parent.mkdir(parents=True, exist_ok=True)
    json.dump(prov, open(OUT_PROV, "w"), indent=1)

    print(f"\n[wrote] {OUT_CSV}  ({len(out_rows)} rows)")
    print(f"[wrote] {OUT_PROV}")
    print(f"  geometry: ALL from femur | texture-trust: {prov['texture_trust_counts']}  "
          f"(center-RF validated {prov['center_rf_validated_pct']}%)")
    print(f"  texture withheld: {prov['texture_withheld']}  | no-demographics: {prov['studies_without_demographics']}")
    codes = [r["study_code"] for r in out_rows]
    print(f"  unique study_codes: {len(set(codes))}/{len(codes)}")
    print(f"  sex: {dict(Counter(r['sex'] for r in out_rows))}")


if __name__ == "__main__":
    main()
