"""Build the doctors' GRADING PACK for one freeze — the frozen, renamed, name-free corpus the grading
table's app serves (requirements v1 §10.4 PR-1..PR-5, `docs/grading_table_requirements_2026-09-03.md`).

What it produces (`data/grading_pack/<pack_id>/`, gitignored — ≈7 GB with videos):

  items.csv          one row per gradable study, name-free: study_uid, era, display codes, sex, age band,
                     BMI, the old consensus grade (old era), pair_id, full_thickness_q50_mm (new-repeat
                     strata), media flags. The item-list generator in the grading repo reads ONLY this.
  served_rows.csv    the 17 served columns per study + provenance + the texture WITHHOLD reason (DR-6):
                     new rows from per_study_cohort_new_r<N>.csv + new_round_cohort_provenance_r<N>.json,
                     old rows from per_study_cohort.csv. What Phase 2 shows, frozen with the pack.
  pairs.json         the 11 same-person pairs pinned BY UID (the code-level list in
                     scripts/analysis/bridge_l1_pairs.py picks the wrong twin for AKA106 / AKA124).
  items/<uid>/       new era: femur/01..05.jpg + femur/01_overlay.png, rf/01..05.jpg + rf/01_overlay.png,
                     femur.mp4, rf.mp4, sweep.mp4 | sweep_sheet.jpg (54/175 have no sweep video → a
                     6-frame contact sheet, PR-3). old era: stills/01..0k.png + 0k_overlay.png (annotator
                     mask), sweep.mp4. Nothing under items/ carries a folder name — file names are
                     generic, frame identities are capture timestamps.
  manifest.json      pack_id, freeze_id, bundle, ranker, k, per-item roles/frames/frame_bases/videos/
                     receipt, per-file sha256, and pack_sha256 (sha256 over the sorted file digests).
  receipt.json       PR-1 / DR-2: for every new study and role, the per-frame values measured for the
                     packed frames == the freeze's `top_frames` in new_round_landmark_r<N>.json. r5 is a
                     values_only freeze (no persisted frame identity), so the pack RE-DERIVES the top-5
                     with the SAME `measure_study` the freeze ran and proves equality — "shown = served".

Stages (each idempotent; `frames` resumes per study):
  --stage metadata   items.csv, served_rows.csv, pairs.json           (seconds; no GPU)
  --stage old        the old-era items from the chassis transport     (minutes; no GPU)
  --stage frames     the new-era items: segment femur+RF cines on the frozen bundle, receipt, copy,
                     overlays, videos / contact sheets                 (GPU; run in the background)
  --stage finalize   manifest + hashes + the PHI name gate; exits 1 on any receipt mismatch or name hit
  --stage all        metadata → old → frames → finalize

PHI gate (PR-4, NR-1): every registry folder name and every name token the study-name grammar reads
off those names is searched (whole word, case-insensitive) over every path and every text file the pack
writes. One hit = refuse to finalize. Names are read only to build the uid maps and the pair join; they
are never written.

Run from the repo root, on a GPU machine (the DGX), with the repo's own venv:
  .venv/bin/python scripts/grading/build_grading_pack.py --pack-id pack_r5_20260904 --stage all
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path.cwd()))  # the repo root: preflight_helpers.landmark imports labeling_app
import preflight_helpers.env_bootstrap  # noqa: F401,E402  (PYTHONPATH bridge to the sibling repos)

from preflight_helpers import env  # noqa: E402
from preflight_helpers import flag_kill_ledger as fkl  # noqa: E402
from preflight_helpers.study_discovery import list_frame_bases, study_modalities  # noqa: E402
from preflight_helpers.study_name_grammar import parse as parse_study_name  # noqa: E402

import derive_new_round_cohort as dnc  # noqa: E402  (measure_study, _Segmenter, KEEP, NATIVE_STEM)

REFREEZE = 5
FREEZE_ID = f"r{REFREEZE}"
R5_CSV = env.COHORT_STATS_DIR / f"per_study_cohort_new_r{REFREEZE}.csv"
OLD_CSV = env.COHORT_STATS_DIR / "per_study_cohort.csv"
LANDMARK = env.COHORT_STATS_DIR / f"new_round_landmark_r{REFREEZE}.json"
PROVENANCE = env.COHORT_STATS_DIR / f"new_round_cohort_provenance_r{REFREEZE}.json"
REGISTRY = Path("data/study_registry.csv")
QA_MASTER = Path("data/qa_master.csv")
STUDIES_ROOT = Path("data/studies_root")
CHASSIS = Path("~/dev/muscle_quality_labeling_exercise_claude_PROMPTED").expanduser()
TRANSPORT = CHASSIS / "transport"
TRANSPORT_META = TRANSPORT / "transport_metadata.csv"
PALETTE = CHASSIS / "label_palette.json"
OUT_ROOT = Path("data/grading_pack")

SERVED_COLUMNS = dnc.COLUMNS  # the 17-column served schema, shared by both eras
K = dnc.K
OVERLAY_ALPHA = 0.45
SHEET_FRAMES = 6

# The 11 same-person pairs at the CODE level (scripts/analysis/bridge_l1_pairs.py PAIRS, 2026-08-31).
# Codes are only the starting point: two of the new codes (AKA106, AKA124) are shared by two studies
# each, so the pack pins every pair by uid through the qa_master name join (see pin_pairs).
PAIR_CODES = [
    ("AKA285B20", "KAK428"), ("AKA080", "VQL999"), ("AKA78", "HKR015"), ("AKA004", "HZB881"),
    ("AKA106", "QHO558"), ("AKA116", "BHW541"), ("AKA017", "TDO396"), ("AKA018", "ERH017"),
    ("AKA124", "ZQZ188"), ("AKA030", "GIW402"), ("AKA033", "DQB975"),
]
_NOISE = {"sag", "sol", "left", "right", "pro", "probe", "study", "final", "kayit"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    print(msg, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _age_band(age) -> str:
    try:
        a = int(float(age))
    except (TypeError, ValueError):
        return "unknown"
    lo = 5 * (a // 5)
    return f"{lo}-{lo + 4}"


def _f(v):
    """CSV cell → float | None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_tokens(name: str) -> frozenset:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"[^a-z]+", s) if len(t) >= 3]
    return frozenset(t for t in toks if t not in _NOISE)


def load_registry() -> dict:
    rows = list(csv.DictReader(open(REGISTRY, encoding="utf-8")))
    new = {r["study_uid"]: r for r in rows if r["cohort"] == "new" and r["status"] == "present"}
    old = {r["study_uid"]: r for r in rows if r["cohort"] == "old"}
    legacy_to_uid = {r["legacy_study_code"]: u for u, r in old.items() if r.get("legacy_study_code")}
    all_dirnames = sorted({r["dirname"] for r in rows if r.get("dirname")})
    return {"rows": rows, "new": new, "old": old, "legacy_to_uid": legacy_to_uid,
            "dirnames": all_dirnames}


def disambiguated_codes(new_reg: dict) -> dict:
    """display_code, suffixed with uid[2:6] when the code is shared (the assessment app's rule)."""
    counts = Counter(r["display_code"] for r in new_reg.values())
    out = {}
    for uid, r in new_reg.items():
        code = r["display_code"]
        out[uid] = f"{code}-{uid[2:6]}" if counts[code] > 1 else code
    return out


# --------------------------------------------------------------------------- #
# stage: metadata
# --------------------------------------------------------------------------- #
def pin_pairs(reg: dict, r5_uids: set) -> list:
    """Pin the 11 pairs by uid. The old side is unique by legacy code; the new side is resolved by
    display code and, where the code is shared, by the same-person token join same_person_pairs.py
    uses (≥2 shared name tokens, smaller set contained). Exactly one new uid must survive per pair."""
    qa = {r["study_uid"]: r for r in csv.DictReader(open(QA_MASTER, encoding="utf-8"))}
    by_code: dict = {}
    for uid, r in reg["new"].items():
        by_code.setdefault(r["display_code"], []).append(uid)
    out = []
    for i, (new_code, old_code) in enumerate(PAIR_CODES, start=1):
        old_uid = reg["legacy_to_uid"].get(old_code)
        if not old_uid:
            raise SystemExit(f"pair {i}: old legacy code {old_code} not in the registry")
        cands = [u for u in by_code.get(new_code, []) if u in r5_uids]
        if len(cands) > 1:
            on = _norm_tokens(qa[old_uid]["study_name"])
            cands = [u for u in cands
                     if len(_norm_tokens(qa[u]["study_name"]) & on) >= 2
                     and min(len(_norm_tokens(qa[u]["study_name"])), len(on))
                     == len(_norm_tokens(qa[u]["study_name"]) & on)]
        if len(cands) != 1:
            raise SystemExit(f"pair {i} ({new_code} <- {old_code}): {len(cands)} new candidates after "
                             f"the name join — refusing to guess")
        out.append({"pair_id": f"P{i:02d}", "new_uid": cands[0], "old_uid": old_uid,
                    "new_display_code": new_code, "old_legacy_code": old_code})
    return out


def _withhold_reason(uid: str, prov_row: dict | None, kills: dict, ledger_entries: list) -> str | None:
    """Why the served texture is blank, for the Phase-2 panel (DR-6). Ledger verdict text first,
    then the freeze's own trust flags, never an empty cell."""
    if uid in kills and "int_cv_within" in kills[uid]:
        ev = None
        for e in ledger_entries:
            if e.get("study_uid") == uid and e.get("scope") == "feature" and \
                    (e.get("status") in ("killed", "withheld") or e.get("verdict") in ("kill", "withhold")) and \
                    "int_cv_within" in (e.get("fields") or e.get("features") or []):
                ev = e.get("evidence") or e.get("reason") or ev
        # the ledger's evidence reads "AKA017: centre-RF shot at the 7.9 cm femur plane; … Decided in-app …":
        # keep the clinical sentence, drop the code prefix and the bookkeeping tail
        text = re.sub(r"^[A-Z]{3}\d{2,3}[^:]*:\s*", "", (ev or "").strip())
        text = re.split(r"\s+Decided\b|\s+\(commit\b", text)[0].strip().rstrip(".;")
        return "withheld — " + (text[:160] if text else "reviewed verdict: texture withheld")
    if prov_row and prov_row.get("int_cv_trusted") is False:
        return "withheld — the three captures disagree on echo; centre-RF texture not trusted"
    if prov_row and prov_row.get("manual_texture_withhold"):
        return "withheld — manual texture withhold"
    return "withheld — reason not recorded in the freeze"


def stage_metadata(out: Path) -> None:
    reg = load_registry()
    r5 = list(csv.DictReader(open(R5_CSV, encoding="utf-8")))
    old = list(csv.DictReader(open(OLD_CSV, encoding="utf-8")))
    prov = json.load(open(PROVENANCE, encoding="utf-8"))
    prov_by = {p["study_code"]: p for p in prov.get("per_study", [])}
    kills = fkl.feature_kills()
    try:
        ledger_entries = fkl.entries()
    except Exception:
        ledger_entries = []
    r5_uids = {r["study_code"] for r in r5}
    missing = sorted(r5_uids - set(reg["new"]))
    if missing:
        raise SystemExit(f"{len(missing)} r5 uids are not present on this machine: {missing[:3]}")
    disamb = disambiguated_codes(reg["new"])
    pairs = pin_pairs(reg, r5_uids)
    pair_of = {}
    for p in pairs:
        pair_of[p["new_uid"]] = p["pair_id"]
        pair_of[p["old_uid"]] = p["pair_id"]

    items, served = [], []
    for r in sorted(r5, key=lambda x: x["study_code"]):
        uid = r["study_code"]
        rr = reg["new"][uid]
        pr = prov_by.get(uid)
        int_cv = _f(r.get("int_cv_within"))
        items.append({
            "study_uid": uid, "era": "new", "display_code": disamb[uid],
            "registry_display_code": rr["display_code"], "legacy_code": "",
            "sex": r.get("sex") or "unknown", "age": r.get("age"), "age_band": _age_band(r.get("age")),
            "bmi": r.get("bmi"), "grade_old": "", "triple_rated": "",
            "pair_id": pair_of.get(uid, ""), "full_thickness_q50_mm": r.get("full_thickness_q50_mm"),
            "texture_withheld": int_cv is None,
        })
        row = {c: r.get(c, "") for c in SERVED_COLUMNS}
        row.update({
            "study_uid": uid, "era": "new", "freeze_id": FREEZE_ID, "age_band": _age_band(r.get("age")),
            "rf_source": (pr or {}).get("rf_source", ""), "reliability": (pr or {}).get("reliability", ""),
            "int_cv_trusted": (pr or {}).get("int_cv_trusted", ""),
            "texture_released_from_run": (pr or {}).get("texture_released_from_run") or "",
            "withhold_reason": _withhold_reason(uid, pr, kills, ledger_entries) if int_cv is None else "",
        })
        served.append(row)

    frame = [r for r in old if str(r.get("triple_rated")).lower() == "true"]
    frame_codes = {r["study_code"] for r in frame}
    for p in pairs:
        if p["old_legacy_code"] not in frame_codes:
            raise SystemExit(f"pair-old {p['old_legacy_code']} is outside the 270 triple-rated frame")
    for r in sorted(frame, key=lambda x: x["study_code"]):
        uid = reg["legacy_to_uid"].get(r["study_code"])
        if not uid:
            raise SystemExit(f"old legacy code {r['study_code']} has no registry uid")
        items.append({
            "study_uid": uid, "era": "old", "display_code": r["study_code"],
            "registry_display_code": "", "legacy_code": r["study_code"],
            "sex": r.get("sex") or "unknown", "age": r.get("age"), "age_band": _age_band(r.get("age")),
            "bmi": r.get("bmi"), "grade_old": r.get("quality_label"), "triple_rated": "True",
            "pair_id": pair_of.get(uid, ""), "full_thickness_q50_mm": r.get("full_thickness_q50_mm"),
            "texture_withheld": _f(r.get("int_cv_within")) is None,
        })
        row = {c: r.get(c, "") for c in SERVED_COLUMNS}
        row.update({"study_uid": uid, "era": "old", "freeze_id": "v1.2", "age_band": _age_band(r.get("age")),
                    "rf_source": "", "reliability": "", "int_cv_trusted": "",
                    "texture_released_from_run": "",
                    "withhold_reason": ("not measured in the v1.2 cohort"
                                        if _f(r.get("int_cv_within")) is None else "")})
        served.append(row)

    out.mkdir(parents=True, exist_ok=True)
    item_cols = ["study_uid", "era", "display_code", "registry_display_code", "legacy_code", "sex", "age",
                 "age_band", "bmi", "grade_old", "triple_rated", "pair_id", "full_thickness_q50_mm",
                 "texture_withheld"]
    with open(out / "items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=item_cols)
        w.writeheader()
        w.writerows(items)
    served_cols = ["study_uid", "era", "freeze_id", "age_band"] + [c for c in SERVED_COLUMNS
                                                                    if c != "study_code"] + \
                  ["rf_source", "reliability", "int_cv_trusted", "texture_released_from_run",
                   "withhold_reason"]
    with open(out / "served_rows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=served_cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(served)
    json.dump({"freeze_id": FREEZE_ID, "n_pairs": len(pairs), "pairs": pairs,
               "source": "scripts/analysis/bridge_l1_pairs.py PAIRS, pinned by uid through the "
                         "qa_master same-person token join (same_person_pairs.py rule)"},
              open(out / "pairs.json", "w", encoding="utf-8"), indent=1)
    n_new = sum(1 for i in items if i["era"] == "new")
    n_old = len(items) - n_new
    meta = {"pack_id": out.name, "freeze_id": FREEZE_ID, "n_new": n_new, "n_old": n_old,
            "old_frame": "per_study_cohort.csv triple_rated == True",
            "grade_old_counts": dict(Counter(i["grade_old"] for i in items if i["era"] == "old")),
            "new_texture_withheld": sum(1 for i in items if i["era"] == "new" and i["texture_withheld"])}
    json.dump(meta, open(out / "metadata_stage.json", "w", encoding="utf-8"), indent=1)
    _run_name_gate(out, reg, texts_only=True)
    _log(f"[metadata] items {len(items)} (new {n_new}, old {n_old}) · pairs {len(pairs)} · "
         f"old grades {meta['grade_old_counts']} · new texture withheld {meta['new_texture_withheld']}")


# --------------------------------------------------------------------------- #
# overlays / media
# --------------------------------------------------------------------------- #
def _load_palette() -> dict:
    raw = json.load(open(PALETTE, encoding="utf-8"))
    pal = {}
    for lab in raw.get("labels", []):
        r, g, b, a = lab.get("rgba", [0, 0, 0, 0.75])
        pal[int(lab["id"])] = (int(r * 255), int(g * 255), int(b * 255))
    return pal


def overlay_png(img_path: Path, mask_arr, palette: dict, out_path: Path) -> None:
    """Alpha-blend a label mask (any size; NEAREST-resized to the image) over the image."""
    import numpy as np
    from PIL import Image

    img = Image.open(img_path).convert("RGB")
    m = Image.fromarray(np.asarray(mask_arr, dtype=np.uint8))
    if m.size != img.size:
        m = m.resize(img.size, Image.NEAREST)
    m_arr = np.asarray(m)
    base = np.asarray(img).astype(np.float32)
    color = np.zeros_like(base)
    hit = np.zeros(m_arr.shape, dtype=bool)
    for lid, rgb in palette.items():
        sel = m_arr == lid
        if sel.any():
            color[sel] = rgb
            hit |= sel
    blended = base.copy()
    blended[hit] = (1 - OVERLAY_ALPHA) * base[hit] + OVERLAY_ALPHA * color[hit]
    Image.fromarray(blended.clip(0, 255).astype(np.uint8)).save(out_path, optimize=True)


def contact_sheet(jpgs: list, out_path: Path, n: int = SHEET_FRAMES) -> None:
    from PIL import Image

    idx = [round(i * (len(jpgs) - 1) / (n - 1)) for i in range(n)] if len(jpgs) >= n else list(range(len(jpgs)))
    tiles = []
    for i in idx:
        im = Image.open(jpgs[i]).convert("RGB")
        w, h = im.size
        tiles.append(im.resize((int(w * 512 / h), 512)))
    cols, gutter = 3, 6
    rows = math.ceil(len(tiles) / cols)
    tw = max(t.size[0] for t in tiles)
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * gutter, rows * 512 + (rows + 1) * gutter), (18, 18, 18))
    for j, t in enumerate(tiles):
        r, c = divmod(j, cols)
        sheet.paste(t, (gutter + c * (tw + gutter), gutter + r * (512 + gutter)))
    sheet.save(out_path, quality=88)


# --------------------------------------------------------------------------- #
# stage: old
# --------------------------------------------------------------------------- #
def stage_old(out: Path) -> None:
    from PIL import Image
    import numpy as np

    reg = load_registry()
    items = list(csv.DictReader(open(out / "items.csv", encoding="utf-8")))
    meta = list(csv.DictReader(open(TRANSPORT_META, encoding="utf-8")))
    by_dir: dict = {}
    for m in meta:
        by_dir.setdefault(m["study_dirname"], []).append(m)
    palette = _load_palette()
    done = 0
    for it in items:
        if it["era"] != "old":
            continue
        uid = it["study_uid"]
        rec_path = out / "items" / uid / "media.json"
        if rec_path.exists():
            continue
        dirname = reg["old"][uid]["dirname"]
        frames = sorted(by_dir.get(dirname, []), key=lambda m: m["frame_base"])
        if not frames:
            raise SystemExit(f"old {uid}: no transport frames")
        d = out / "items" / uid / "stills"
        d.mkdir(parents=True, exist_ok=True)
        stills, overlays, bases = [], [], []
        for k, fr in enumerate(frames, start=1):
            src = TRANSPORT / fr["final_path"]
            dst = d / f"{k:02d}.png"
            shutil.copyfile(src, dst)
            stills.append(str(dst.relative_to(out)))
            bases.append(fr["frame_base"])
            mp = fr.get("mask_path")
            if mp and (TRANSPORT / mp).is_file():
                mask = np.asarray(Image.open(TRANSPORT / mp).convert("L"))
                ov = d / f"{k:02d}_overlay.png"
                overlay_png(dst, mask, palette, ov)
                overlays.append(str(ov.relative_to(out)))
            else:
                overlays.append(None)
        video = None
        mp4 = frames[0].get("source_mp4_path")
        if mp4 and (TRANSPORT / mp4).is_file():
            dst = out / "items" / uid / "sweep.mp4"
            shutil.copyfile(TRANSPORT / mp4, dst)
            video = str(dst.relative_to(out))
        rec = {"study_uid": uid, "era": "old", "roles": {"stills": {"frames": stills, "frame_bases": bases,
                                                                    "overlays": overlays}},
               "videos": {"sweep": video}, "sweep_source": "mp4" if video else "none",
               "receipt": {"stills": "transport_copy"}}
        json.dump(rec, open(rec_path, "w", encoding="utf-8"), indent=1)
        done += 1
    _log(f"[old] {done} old items written this run")


# --------------------------------------------------------------------------- #
# stage: frames (GPU)
# --------------------------------------------------------------------------- #
def _compare_top_frames(measured: list, reference: list) -> dict:
    """Per-frame value equality between what the pack measured on the packed frames and what the
    freeze recorded (`top_frames`). Same order (best-first), same None pattern, |Δ| ≤ 1e-9."""
    if len(measured) != len(reference):
        return {"match": False, "reason": f"k {len(measured)} vs {len(reference)}", "max_abs_delta": None}
    max_d, none_mismatch = 0.0, 0
    for a, b in zip(measured, reference):
        for k in dnc.KEEP:
            va, vb = a.get(k), b.get(k)
            if (va is None) != (vb is None):
                none_mismatch += 1
                continue
            if va is None:
                continue
            max_d = max(max_d, abs(float(va) - float(vb)))
    ok = none_mismatch == 0 and max_d <= 1e-9
    return {"match": ok, "max_abs_delta": max_d, "none_mismatch": none_mismatch}


def stage_frames(out: Path, limit: int | None = None) -> None:
    reg = load_registry()
    items = [i for i in csv.DictReader(open(out / "items.csv", encoding="utf-8")) if i["era"] == "new"]
    landmark = json.load(open(LANDMARK, encoding="utf-8"))
    lm_by = {s["study_uid"]: s for s in landmark["studies"]}
    palette = _load_palette()
    todo = [i for i in items if not (out / "items" / i["study_uid"] / "media.json").exists()]
    if limit:
        todo = todo[:limit]
    _log(f"[frames] {len(items)} new items, {len(todo)} to do (bundle {dnc.NATIVE_STEM})")
    if not todo:
        return
    seg = dnc._Segmenter()
    _log(f"[model] {seg.pkl_path.stem[:70]}")
    t_all = time.time()
    for n, it in enumerate(todo, start=1):
        uid = it["study_uid"]
        t0 = time.time()
        sp = STUDIES_ROOT / reg["new"][uid]["rel_path"]
        row = dnc.measure_study(sp, seg, roles=("femur", "rf"))
        if row is None or not row.get("vals"):
            raise SystemExit(f"{uid}: measure_study returned nothing")
        mods = study_modalities(sp)
        item_dir = out / "items" / uid
        item_dir.mkdir(parents=True, exist_ok=True)
        roles_rec, receipt = {}, {}
        for role in ("femur", "rf"):
            lm = row["vals"].get(role)
            ref = (lm_by.get(uid) or {}).get("vals", {}).get(role)
            if lm is None or ref is None:
                raise SystemExit(f"{uid}/{role}: missing measurement or freeze reference")
            cmp = _compare_top_frames(lm["top_frames"], ref["top_frames"])
            med_d = max((abs(float(lm[k]) - float(ref[k])) for k in dnc.KEEP
                         if lm.get(k) is not None and ref.get(k) is not None), default=0.0)
            cmp["median_max_abs_delta"] = med_d
            cmp["served_median_match"] = med_d <= 1e-9
            receipt[role] = cmp
            raw_dir = mods[role].raw_image_dir
            d = item_dir / role
            d.mkdir(exist_ok=True)
            frames = []
            for k, base in enumerate(lm["top_frame_bases"], start=1):
                dst = d / f"{k:02d}.jpg"
                shutil.copyfile(raw_dir / f"{base}.jpg", dst)
                frames.append(str(dst.relative_to(out)))
            # the segmentation the numbers came from, on the rank-1 frame (Phase 2 only)
            base1 = lm["top_frame_bases"][0]
            final = seg.synth(raw_dir, base1)
            mask = seg.predict_mask(seg.learn, seg.stack(final, modalities=seg.meta.modalities), seg.meta)
            ov = d / "01_overlay.png"
            overlay_png(d / "01.jpg", mask, palette, ov)
            roles_rec[role] = {"frames": frames, "frame_bases": lm["top_frame_bases"],
                               "overlays": [str(ov.relative_to(out))] + [None] * (len(frames) - 1),
                               "n_frames_in_cine": lm.get("n_frames")}
            # the role's cine video sits beside its RawImagesForResearch_ dir
            vids = sorted(raw_dir.parent.glob("*.mp4"))
            if vids:
                dst = item_dir / f"{role}.mp4"
                shutil.copyfile(vids[0], dst)
                roles_rec[role]["video"] = str(dst.relative_to(out))
        videos = {r: roles_rec[r].get("video") for r in ("femur", "rf")}
        root_vids = sorted(sp.glob("*.mp4"))
        sweep_source = "none"
        if root_vids:
            dst = item_dir / "sweep.mp4"
            shutil.copyfile(root_vids[0], dst)
            videos["sweep"] = str(dst.relative_to(out))
            sweep_source = "mp4"
        else:
            sw = mods.get("sweep")
            jpgs = [sw.raw_image_dir / f"{b}.jpg" for b in list_frame_bases(sw.raw_image_dir)] if sw else []
            if jpgs:
                dst = item_dir / "sweep_sheet.jpg"
                contact_sheet(jpgs, dst)
                videos["sweep"] = str(dst.relative_to(out))
                sweep_source = "contact_sheet"
            else:
                videos["sweep"] = None
        rec = {"study_uid": uid, "era": "new", "roles": roles_rec, "videos": videos,
               "sweep_source": sweep_source, "receipt": receipt,
               "measured_with": {"bundle": dnc.NATIVE_STEM, "ranker": landmark["ranker"], "k": K}}
        json.dump(rec, open(item_dir / "media.json", "w", encoding="utf-8"), indent=1)
        ok = all(receipt[r]["match"] for r in receipt)
        _log(f"[{n}/{len(todo)}] {uid} {time.time() - t0:5.1f}s receipt={'MATCH' if ok else 'MISMATCH'} "
             f"sweep={sweep_source} elapsed {(time.time() - t_all) / 60:.1f} min")


# --------------------------------------------------------------------------- #
# stage: finalize
# --------------------------------------------------------------------------- #
def _name_tokens(names: list) -> set:
    toks: set = set()
    for n in names:
        try:
            toks.update(t for t in parse_study_name(n).name_tokens if len(t) >= 3)
        except Exception:
            continue
    return toks


def _run_name_gate(out: Path, reg: dict, *, texts_only: bool = False) -> None:
    """Refuse if any registry folder name, or any name token as a whole word, appears in a pack path or
    in any text the pack writes. Loud stop; a false positive is inspected, never silenced."""
    names = reg["dirnames"]
    texts = []
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in (".csv", ".json", ".txt", ".md"):
            texts.append((str(p.relative_to(out)), p.read_text(encoding="utf-8", errors="ignore")))
    paths_blob = "\n".join(str(p.relative_to(out)) for p in out.rglob("*"))
    blobs = [("<paths>", paths_blob)] + texts
    leaked, hits = [], []
    toks = sorted(_name_tokens(names))
    for label, text in blobs:
        for n in names:
            if n and n in text:
                leaked.append((label, "<folder-name>"))
        low = text.lower()
        for t in toks:
            tl = t.lower()
            if tl in low and re.search(r"(?<!\w)" + re.escape(tl) + r"(?!\w)", low):
                hits.append((label, tl))
    if leaked or hits:
        detail = "; ".join(f"{l}:{h}" for l, h in (leaked + hits)[:12])
        raise SystemExit(f"PHI GATE: {len(leaked)} folder-name leak(s) and {len(hits)} name-token hit(s) "
                         f"in the pack — refusing ({detail})")
    _log(f"[phi-gate] 0 folder names, 0 name tokens over {len(blobs)} text blobs and "
         f"{paths_blob.count(chr(10)) + 1} paths ({len(names)} names, {len(toks)} tokens)")


def stage_finalize(out: Path, *, allow_mismatch: bool = False) -> None:
    reg = load_registry()
    items = list(csv.DictReader(open(out / "items.csv", encoding="utf-8")))
    landmark = json.load(open(LANDMARK, encoding="utf-8"))
    meta_stage = json.load(open(out / "metadata_stage.json", encoding="utf-8"))
    item_recs, missing, mismatches = {}, [], []
    for it in items:
        uid = it["study_uid"]
        p = out / "items" / uid / "media.json"
        if not p.exists():
            missing.append(uid)
            continue
        rec = json.load(open(p, encoding="utf-8"))
        rec.update({k: it[k] for k in ("era", "display_code", "legacy_code", "pair_id")})
        if it["era"] == "new" and not all(v.get("match") for v in rec["receipt"].values()):
            mismatches.append(uid)
        item_recs[uid] = rec
    if missing:
        raise SystemExit(f"finalize: {len(missing)} items have no media yet (e.g. {missing[:3]}) — run "
                         f"the old / frames stages first")
    if mismatches and not allow_mismatch:
        raise SystemExit(f"finalize: RECEIPT MISMATCH on {len(mismatches)} new items {mismatches[:5]} — "
                         f"the packed frames do not reproduce the freeze; refusing (--allow-mismatch to "
                         f"record and continue)")
    _log("[finalize] hashing every file under items/ ...")
    files = {}
    for p in sorted((out / "items").rglob("*")):
        if p.is_file():
            files[str(p.relative_to(out))] = _sha256(p)
    for name in ("items.csv", "served_rows.csv", "pairs.json"):
        files[name] = _sha256(out / name)
    digest_lines = "\n".join(f"{h}  {k}" for k, h in sorted(files.items())) + "\n"
    pack_sha = hashlib.sha256(digest_lines.encode("utf-8")).hexdigest()
    (out / "SHA256SUMS").write_text(digest_lines, encoding="utf-8")
    n_new = sum(1 for r in item_recs.values() if r["era"] == "new")
    manifest = {
        "pack_id": out.name, "freeze_id": FREEZE_ID, "refreeze": REFREEZE,
        "bundle": dnc.NATIVE_STEM, "ranker": landmark["ranker"], "k": K,
        "freeze_frame_identity": landmark["frame_identity"],
        "receipt": {"rule": "per-frame values of the packed top-5 == the freeze's top_frames (|Δ| ≤ 1e-9, "
                            "same None pattern) for femur and rf, per study",
                    "n_new": n_new, "n_match": n_new - len(mismatches), "mismatches": mismatches},
        "n_items": len(item_recs), "n_new": n_new, "n_old": len(item_recs) - n_new,
        "old_frame": meta_stage["old_frame"], "grade_old_counts": meta_stage["grade_old_counts"],
        "sweep_sources": dict(Counter(r["sweep_source"] for r in item_recs.values() if r["era"] == "new")),
        "items": item_recs, "files": files, "pack_sha256": pack_sha,
        "sources": {"served_new": R5_CSV.name, "served_old": OLD_CSV.name, "landmark": LANDMARK.name,
                    "provenance": PROVENANCE.name},
    }
    json.dump(manifest, open(out / "manifest.json", "w", encoding="utf-8"), indent=1, sort_keys=True)
    json.dump({"pack_id": out.name, "freeze_id": FREEZE_ID, "rule": manifest["receipt"]["rule"],
               "per_item": {u: r["receipt"] for u, r in item_recs.items() if r["era"] == "new"},
               "n_match": n_new - len(mismatches), "n_mismatch": len(mismatches)},
              open(out / "receipt.json", "w", encoding="utf-8"), indent=1, sort_keys=True)
    _run_name_gate(out, reg)
    _log(f"[finalize] pack {out.name}: {len(item_recs)} items ({n_new} new / {len(item_recs) - n_new} old), "
         f"{len(files)} files, receipt {n_new - len(mismatches)}/{n_new} MATCH, "
         f"sweep {manifest['sweep_sources']}, pack_sha256 {pack_sha[:16]}…")
    if mismatches:
        _log(f"[finalize] WARNING: {len(mismatches)} receipt mismatches recorded (allowed by flag)")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack-id", required=True)
    ap.add_argument("--stage", choices=["metadata", "old", "frames", "finalize", "all"], required=True)
    ap.add_argument("--limit", type=int, default=None, help="frames stage: only this many new items")
    ap.add_argument("--allow-mismatch", action="store_true")
    a = ap.parse_args()
    out = OUT_ROOT / a.pack_id
    stages = ["metadata", "old", "frames", "finalize"] if a.stage == "all" else [a.stage]
    for s in stages:
        if s == "metadata":
            stage_metadata(out)
        elif s == "old":
            stage_old(out)
        elif s == "frames":
            stage_frames(out, a.limit)
        elif s == "finalize":
            stage_finalize(out, allow_mismatch=a.allow_mismatch)


if __name__ == "__main__":
    main()
