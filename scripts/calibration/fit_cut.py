"""Subsystem F (minimal) — the 2-cut fitter.

The consumer half of the calibration loop, scoped to option A's TWO live open
cuts: `dark_clip_high` (roi_dark_fraction, fires HIGH) and `wrong_site`
(rf_column_coverage, fires LOW). Given operator labels (from the p04
selection-capture or p4x) joined against the CURRENT cohort score table (drift
policy P1 — never the label-time snapshot), it reports, per label_source:
the agree rate at the deployed cut, the Youden-J optimum, and the
sensitivity-targeted cut (Se >= se_target at Sp >= sp_floor). No promotion here
— this is the readout a human reads before flipping a registry cut.

`fit_detector_cut` is pure + unit-tested; the CLI just loads + dispatches. The
loop fires the moment labels land; until then the CLI reports "0 labels".

Run:  .venv/bin/python scripts/calibration/fit_cut.py [dark_clip_high|wrong_site]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))            # labeling_app.*
sys.path.insert(0, str(_REPO / "scripts"))  # calibration.*, preflight_helpers.*

# (detector, score column meaning, fire direction, deployed-cut threshold key)
_CUTS = {
    "dark_clip_high": ("high", "dark_clip_high__roi_fraction_max"),
    "wrong_site": ("low", "wrong_site__rf_column_coverage_min"),
}


def fit_detector_cut(
    scores: dict, labels: dict, *, direction: str, current_cut: float,
    se_target: float = 0.80, sp_floor: float = 0.85,
) -> dict:
    """Fit a 1-D operating point from operator labels.

    `scores`  — {key: raw_score} (the detector's headline scalar per frame).
    `labels`  — {key: "yes"|"no"|"unsure"} ("yes" = the problem is really there).
    `direction` — "high" (predict problem when score > cut) | "low" (score < cut).

    Returns counts, the agree/Se/Sp at `current_cut`, the Youden-J optimum, and
    the sensitivity-targeted cut (highest Sp among cuts with Se>=se_target AND
    Sp>=sp_floor) or None if that floor is unreachable (→ mechanism review).
    "unsure" labels and frames missing a score are dropped.
    """
    pairs = [(scores[k], labels[k]) for k in labels
             if k in scores and scores[k] is not None and labels[k] in ("yes", "no")]
    n = len(pairs)
    out = {"n": n, "n_pos": 0, "n_neg": 0, "current_cut": current_cut,
           "agree_at_current": None, "se_at_current": None, "sp_at_current": None,
           "youden": None, "sens_target": None,
           "se_target": se_target, "sp_floor": sp_floor, "direction": direction}
    if n == 0:
        return out
    out["n_pos"] = sum(1 for _, lab in pairs if lab == "yes")
    out["n_neg"] = n - out["n_pos"]

    def _pred(s: float, cut: float) -> bool:
        return (s > cut) if direction == "high" else (s < cut)

    def _se_sp(cut: float):
        tp = sum(1 for s, lab in pairs if lab == "yes" and _pred(s, cut))
        fn = sum(1 for s, lab in pairs if lab == "yes" and not _pred(s, cut))
        tn = sum(1 for s, lab in pairs if lab == "no" and not _pred(s, cut))
        fp = sum(1 for s, lab in pairs if lab == "no" and _pred(s, cut))
        se = tp / (tp + fn) if (tp + fn) else None
        sp = tn / (tn + fp) if (tn + fp) else None
        return se, sp, (tp + tn) / n

    vals = sorted({s for s, _ in pairs})
    cands = ([vals[0] - 1e-6]
             + [(vals[i] + vals[i + 1]) / 2 for i in range(len(vals) - 1)]
             + [vals[-1] + 1e-6])

    best = None
    feasible = []
    for c in cands:
        se, sp, _ = _se_sp(c)
        if se is None or sp is None:
            continue
        j = se + sp - 1
        if best is None or j > best["j"]:
            best = {"cut": round(c, 6), "se": round(se, 4), "sp": round(sp, 4),
                    "j": round(j, 4)}
        if se >= se_target and sp >= sp_floor:
            feasible.append((sp, c, se))
    out["youden"] = best
    if feasible:
        sp, c, se = max(feasible)
        out["sens_target"] = {"cut": round(c, 6), "se": round(se, 4),
                              "sp": round(sp, 4)}

    se0, sp0, agree = _se_sp(current_cut)
    out["se_at_current"] = round(se0, 4) if se0 is not None else None
    out["sp_at_current"] = round(sp0, 4) if sp0 is not None else None
    out["agree_at_current"] = round(agree, 4)
    return out


def _load(detector: str, source: Optional[str] = None) -> dict:
    """Load + dispatch: labels (latest per study/frame) joined to live scores."""
    import preflight_helpers.env_bootstrap  # noqa: F401
    from calibration.label_store import load_labels
    from calibration.candidate_selector import load_score_table
    from labeling_app.qa_thresholds import get

    direction, cut_key = _CUTS[detector]
    current_cut = float(get(cut_key).value)

    rows = [r for r in load_labels() if r.get("detector") == detector
            and (source is None or r.get("label_source") == source)]
    # dedup to the latest label per (study, frame)
    latest: dict = {}
    for r in sorted(rows, key=lambda r: r.get("timestamp_utc", "")):
        latest[(r["study_code"], r["frame_base"])] = r["label"]

    df = load_score_table()
    det = df[df["detector"] == detector]
    scores = {(r.study_code, r.frame_base): r.raw_score
              for r in det.itertuples(index=False)}
    return fit_detector_cut({k: scores.get(k) for k in latest}, latest,
                            direction=direction, current_cut=current_cut)


def main() -> None:
    dets = [sys.argv[1]] if len(sys.argv) > 1 else list(_CUTS)
    for det in dets:
        if det not in _CUTS:
            print(f"unknown detector {det!r}; choices: {list(_CUTS)}")
            continue
        rep = _load(det)
        print(f"\n=== {det} (deployed cut {rep['current_cut']}, fires "
              f"{rep['direction']}) ===")
        if rep["n"] == 0:
            print("  0 labels yet — the loop is wired and ready; fires once "
                  "the p04 selection-capture / p4x produce labels for this cut.")
            continue
        print(f"  n={rep['n']}  positives={rep['n_pos']}  negatives={rep['n_neg']}")
        print(f"  at deployed cut: agree={rep['agree_at_current']} "
              f"Se={rep['se_at_current']} Sp={rep['sp_at_current']}")
        print(f"  Youden-J: {rep['youden']}")
        print(f"  sens-target (Se>={rep['se_target']} @ Sp>={rep['sp_floor']}): "
              f"{rep['sens_target']}")


if __name__ == "__main__":
    main()
