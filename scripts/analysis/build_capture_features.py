"""Rebuild the per-capture feature substrate that `refute.py` consumes.

The 2026-07-29 run of this substrate lived in `scratchpad/featscreen/`, which is
gitignored, so it did not survive the Acer -> DGX move: the repeatability numbers
quoted in NORTHSTAR (MDC95 2.14 mm, ICC 0.967, n=66) could be read but not
re-derived. Output therefore lands in `scripts/analysis/results/`, which is
tracked and did survive.

One row per (study_uid, role) capture, values medianed over the capture's top-k
largest-RF frames by `compute_features.compute_capture_features`. Resumable: an
existing output file is read back and completed pairs are skipped, and
`compute_features` caches each capture keyed on the segmenter bundle sha.

    .venv/bin/python scripts/analysis/build_capture_features.py [--limit N] [--roles rf,sweep]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent.parent
RESULTS = HERE / "results"
OUT_CSV = RESULTS / "capture_features.csv"
PROV_JSON = RESULTS / "capture_features_provenance.json"

META_COLS = ["study_uid", "role", "n_frames_in_capture", "n_frames_segmented",
             "spacing_source", "seconds_total"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after N captures (smoke)")
    ap.add_argument("--roles", default="femur,rf,sweep")
    ap.add_argument("--out", default=str(OUT_CSV))
    args = ap.parse_args()

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    out_path = Path(args.out)
    RESULTS.mkdir(parents=True, exist_ok=True)

    frames = pd.read_csv(ROOT / "data" / "qa_frames.csv", usecols=["study_uid", "role"])
    pairs = (frames[frames.role.isin(roles)]
             .groupby(["study_uid", "role"]).size().reset_index(name="n_frames"))
    pairs = pairs.sort_values(["study_uid", "role"]).reset_index(drop=True)

    done: set[tuple[str, str]] = set()
    rows: list[dict] = []
    if out_path.exists():
        prev = pd.read_csv(out_path)
        rows = prev.to_dict("records")
        done = {(str(r["study_uid"]), str(r["role"])) for r in rows}
        print(f"resuming: {len(done)} captures already in {out_path.name}", flush=True)

    todo = [p for p in pairs.itertuples(index=False) if (p.study_uid, p.role) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(pairs)} captures total · {len(done)} done · {len(todo)} to compute", flush=True)

    from compute_features import compute_capture_features, LANDMARK_K

    t_start = time.time()
    bundle = None
    for i, p in enumerate(todo, 1):
        rec: dict = {}
        try:
            vals = compute_capture_features(p.study_uid, p.role, record_out=rec)
        except Exception as exc:                      # never lose the whole run to one capture
            print(f"  [{i}/{len(todo)}] {p.study_uid} {p.role}  ERROR {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        bundle = bundle or rec.get("bundle_sha8")
        row = {"study_uid": p.study_uid, "role": p.role,
               "n_frames_in_capture": rec.get("n_frames_in_capture"),
               "n_frames_segmented": rec.get("n_frames_segmented"),
               "spacing_source": rec.get("spacing_source"),
               "seconds_total": rec.get("seconds_total")}
        row.update(vals)
        rows.append(row)

        if i % 10 == 0 or i == len(todo):
            pd.DataFrame(rows).to_csv(out_path, index=False)
            rate = (time.time() - t_start) / i
            eta = rate * (len(todo) - i) / 60.0
            n_ok = sum(1 for v in vals.values() if v is not None)
            print(f"  [{i}/{len(todo)}] {p.study_uid} {p.role}  {n_ok} stems  "
                  f"{rate:.1f}s/capture  ETA {eta:.0f} min", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)

    stems = [c for c in df.columns if c not in META_COLS]
    prov = {
        "generated_by": "scripts/analysis/build_capture_features.py",
        "reason": "scratchpad/featscreen/capture_features.csv was lost in the Acer->DGX move "
                  "(scratchpad/ is gitignored); this rebuild lands in a tracked path",
        "source_frame_index": "data/qa_frames.csv",
        "segmenter_bundle_sha8": bundle,
        "landmark_k": LANDMARK_K,
        "n_captures": int(len(df)),
        "n_studies": int(df.study_uid.nunique()),
        "roles": df.role.value_counts().to_dict(),
        "n_stems": len(stems),
        "seconds_wall": round(time.time() - t_start, 1),
    }
    PROV_JSON.write_text(json.dumps(prov, indent=2))
    print(f"\nwrote {out_path}  ({len(df)} captures, {df.study_uid.nunique()} studies, "
          f"{len(stems)} stems)\nwrote {PROV_JSON}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
