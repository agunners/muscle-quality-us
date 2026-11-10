#!/usr/bin/env python
"""Cohort capture-association audit (torch-free) → data/audit/capture_consistency.{json,md}.

For every materialized SCORED role (femur/RF) it checks: role->capture integrity (is it a real MP4-backed
cine or an orphan stills group?), DICOM->frame depth agreement (the wrong ruler), and duplicate roles, and
surfaces the manifest's OWN buried `ambiguous_captures`/`caveats`. Acquisition QA (completeness, off-grid
depth, the non-scored sweep) is check_studies'/Kontrol's job — deduped 2026-07-07. This is the exact scan
that isolated AKA017 (its RF rode on a flagged 6.9cm stills orphan carrying a 7.9cm DICOM).

    source scripts/env.sh
    .venv/bin/python scripts/audit_capture_consistency.py            # whole tree
    .venv/bin/python scripts/audit_capture_consistency.py --root data/studies_root
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from preflight_helpers.capture_audit import audit_tree, DEPTH_TOL_CM  # noqa: E402


def _study_code(name: str) -> str:
    t = name.split("_")
    return f"AKA{t[1]}" if len(t) > 1 and t[1].isdigit() else name


def _phi_safe(name: str) -> str:
    """study_code + non-PHI demographics only (drop soyad/ad — the alpha-only name tokens) so a pasted
    report never carries a patient name. data/audit is gitignored, but reports get pasted into docs."""
    demo = [t for t in name.split("_") if any(c.isdigit() for c in t)]   # keeps date/count/probe/age/h/w/asis
    tail = " ".join(demo[3:]) if len(demo) > 3 else " ".join(demo)
    return f"{_study_code(name)} · {tail}".strip(" ·")


def render_derivation(deriv: dict, flagged_names: set) -> list:
    """Per-capture segmented pixel→mm→feature for flagged studies — the 'show me the numbers' layer.
    Reads data/audit/measurement_derivation.json (D2). attached = the DICOM the pipeline used;
    width = the capture's own frame-width-derived spacing (the correct ruler when they disagree)."""
    if not deriv:
        return []
    L = ["## Measurement derivation (segmented) — flagged studies", "",
         "Per capture: what anatomy it actually sees (class %) + key features under the **attached** DICOM "
         "vs the **width-derived** (true-depth) spacing. Where they differ, the attached DICOM is the wrong "
         "ruler.", ""]
    for name, blk in deriv.items():
        if name not in flagged_names:
            continue
        L.append(f"### {_phi_safe(name)}")
        L.append("| capture | frame-depth | fat% | femur% | full_thk (att→wid) | fat_thk (att→wid) | int_cv | qmt |")
        L.append("|---|---|---|---|---|---|---|---|")
        for cap, c in blk.get("captures", {}).items():
            a, w = c["features_attached_dicom"], c["features_width_derived"]
            cls = c.get("seg_classes_pct", {})

            def _r(x, n=1):
                return round(x, n) if isinstance(x, (int, float)) else x

            def pair(k):
                av, wv = a.get(k), w.get(k)
                if av is None and wv is None:
                    return "—"
                if av == wv or (isinstance(av, float) and isinstance(wv, float) and abs(av - wv) < 0.05):
                    return f"{_r(av)}"
                return f"{_r(av)}→**{_r(wv)}**"
            L.append(f"| {cap} | {c.get('frame_depth_cm')}cm | {cls.get('fat','—')} | {cls.get('femur','—')} "
                     f"| {pair('full_thickness_q50_mm')} | {pair('fat_thickness_q50_mm')} "
                     f"| {_r(w.get('int_cv_within'), 3)} | {_r(w.get('qmt_mm'))} |")
        L.append("")
    return L


def render_markdown(audits, deriv=None) -> str:
    flagged = [a for a in audits if a.verdict == "FLAGGED"]
    L = ["# Capture-association audit",
         "",
         f"Torch-free consistency check over the materialized tree — role↔capture + DICOM↔frame "
         f"(tol {DEPTH_TOL_CM} cm). Frame depth is the capture's own signal (`1967/jpg_width`, 128L).",
         "",
         f"**{len(audits)} studies · {len(flagged)} FLAGGED · {len(audits) - len(flagged)} clean.**",
         ""]
    if flagged:
        L += ["## ⚠ Flagged studies", ""]
        for a in flagged:
            L.append(f"### {_phi_safe(a.study)}")
            L.append(f"**Reasons:** {', '.join(a.reasons) or '(manifest ambiguity only)'}")
            for mfl in a.manifest_ambiguous:
                L.append(f"- ⚠ manifest: {mfl}")
            for cav in a.manifest_caveats:
                L.append(f"- ℹ note: {cav}")
            L.append("")
            L.append("| capture | role | frames | width | frame-depth | mp4 | dcm-depth | flags |")
            L.append("|---|---|---|---|---|---|---|---|")
            for c in a.captures:
                L.append(f"| {c['label']} | {c['identity']} | {c['nframes']} | {c['width_px']} | "
                         f"{c['frame_depth_cm']} | {'yes' if c['has_mp4'] else '**NO**'} | "
                         f"{c['attached_dcm_depth_cm']} | {', '.join(c['flags']) or '—'} |")
            L.append("")
            L.append("**Scorer rides on:** " + "; ".join(
                f"{role}→{v.get('capture')} (frame {v.get('frame_depth_cm')} / dcm "
                f"{v.get('attached_dcm_depth_cm')}cm{' ⚠ ' + ','.join(v['flags']) if v.get('flags') else ''})"
                for role, v in a.scorer_roles.items()))
            L.append("")
    L += render_derivation(deriv, {a.study for a in flagged})
    L += ["## All studies", "", "| study | verdict | reasons |", "|---|---|---|"]
    for a in audits:
        L.append(f"| {_phi_safe(a.study)} | {a.verdict} | {', '.join(a.reasons) or '—'} |")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO / "data/studies_root"))
    ap.add_argument("--out", default=str(REPO / "data/audit"))
    args = ap.parse_args()

    audits = audit_tree(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    deriv_path = out / "measurement_derivation.json"
    deriv = json.loads(deriv_path.read_text()) if deriv_path.exists() else None
    (out / "capture_consistency.json").write_text(
        json.dumps([asdict(a) for a in audits], indent=1, default=str))
    (out / "capture_consistency.md").write_text(render_markdown(audits, deriv))

    flagged = [a for a in audits if a.verdict == "FLAGGED"]
    print(f">>> {len(audits)} studies, {len(flagged)} FLAGGED -> {out}/capture_consistency.{{json,md}}")
    for a in flagged:
        print(f"    {_study_code(a.study):8s} {', '.join(a.reasons)}")


if __name__ == "__main__":
    main()
