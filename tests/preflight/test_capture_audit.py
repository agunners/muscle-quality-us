"""Red-team the capture-association audit tooling itself (not just the finding).

A throwaway validation script this session had a silent bug that would have falsely read "all clean";
these lock the real behaviour on synthetic trees: an orphan role flags, a clean one doesn't, a
depth-mismatch flags, a matched one doesn't, duplicate/no-dcm roles flag, the benign
"length-ambiguous" caveat does NOT flag (the over-flag caught + fixed 2026-07-01), and no capture row
is silently dropped.

Scoped 2026-07-07 (dedup vs check_studies): the audit is POST-MATERIALIZE score-integrity ONLY. A
stills-only SWEEP or an off-grid depth is an acquisition fact Kontrol owns and must NOT flag here; a
scored-role orphan flags ONLY when a cine was available (a deconfliction mis-pick), never on a pure
acquisition gap (no cine anywhere).
"""
import json
from pathlib import Path

from PIL import Image

from preflight_helpers.capture_audit import audit_study, audit_tree


def _jpg(dir_: Path, width: int, n: int = 15):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("L", (width, 512)).save(dir_ / f"20260626_{140000000 + i:09d}.jpg", "JPEG")


def _make_study(root: Path, name: str, captures, *, ambiguous=None, caveats=None):
    """captures: list of dicts {label, identity, width, mp4(bool), dcm_depth(float|None)}.
    The 'sweep' capture lands at the study root; the rest under captures/<label>/."""
    study = root / name
    study.mkdir(parents=True, exist_ok=True)
    primary, annex = {}, []
    for c in captures:
        if c["identity"] == "sweep":
            _jpg(study / "RawImagesForResearch_20260626_000000", c["width"])
            if c["mp4"]:
                (study / "sweep.mp4").write_bytes(b"x")
            primary = {"identity": "sweep", "dcm": "s.dcm" if c["dcm_depth"] else None,
                       "dcm_depth_cm": c["dcm_depth"], "caveats": caveats or []}
        else:
            cap = study / "captures" / c["label"]
            _jpg(cap / "RawImagesForResearch_20260626_000001", c["width"])
            if c["mp4"]:
                (cap / "v.mp4").write_bytes(b"x")
            annex.append({"dir": c["label"], "identity": c["identity"],
                          "dcm": "a.dcm" if c["dcm_depth"] else None, "dcm_depth_cm": c["dcm_depth"]})
    mf = {"primary": primary, "annex": annex,
          "ambiguous_captures": ambiguous or [], "dcm_pool": [], "orphan_dcms": []}
    (study / "materialize_manifest.json").write_text(json.dumps(mf))
    return study


# width->depth: 399->4.93, 285->6.90, 199->9.88 (1967/width)
CLEAN = [
    {"label": "sweep(root)", "identity": "sweep", "width": 399, "mp4": True, "dcm_depth": 4.9},
    {"label": "c1_rf_shallow", "identity": "rf", "width": 399, "mp4": True, "dcm_depth": 4.9},
    {"label": "c2_femur_deep", "identity": "femur", "width": 285, "mp4": True, "dcm_depth": 6.9},
]


def test_clean_study_is_clean(tmp_path):
    s = _make_study(tmp_path, "st_clean", CLEAN)
    a = audit_study(s)
    assert a.verdict == "CLEAN"
    assert a.reasons == []
    assert len(a.captures) == 3          # no dropped rows


def test_orphan_role_flags(tmp_path):
    caps = [dict(c) for c in CLEAN]
    caps[1]["mp4"] = False               # the RF (scored) cine loses its video -> the score's RF rides on a
    s = _make_study(tmp_path, "st_orphan", caps)   # stills orphan -> FLAGGED (AKA017's orphan-hijack shape)
    a = audit_study(s)
    assert a.verdict == "FLAGGED"
    assert any("ROLE_ON_ORPHAN" in r for r in a.reasons)


def test_sweep_on_orphan_does_not_flag(tmp_path):
    # The sweep is non-scored (EI cross-check only). A stills-only sweep is an acquisition fact Kontrol
    # owns ('eşleşmeyen mp4/RawImages'); the audit must NOT re-flag it — femur+RF are clean cines, score
    # intact. This is the real 2-Jul athlete batch shape: femur(mp4) rf(mp4) sweep(orphan).
    caps = [dict(c) for c in CLEAN]
    caps[0]["mp4"] = False               # the SWEEP loses its video -> stills-only orphan
    a = audit_study(_make_study(tmp_path, "st_sweep_orphan", caps))
    assert a.verdict == "CLEAN"
    assert not any("ROLE_ON_ORPHAN" in r for r in a.reasons)


def test_scored_role_orphan_flags_via_both_paths(tmp_path):
    # A SCORED role on a stills-only orphan ALWAYS flags — the score's input is a non-cine (an acquisition
    # gap and an assignment bug are indistinguishable post-materialize). It must fire via the per-row check
    # AND, when real materialize emits the {identity:'rf'} ambiguous entry for a lone stills rf, via the
    # manifest path too. The two were once inconsistent — a study_has_cine proxy guarded the per-row path
    # but NOT the manifest path (red-team 2026-07-07); the proxy was removed so both agree.
    caps = [dict(c) for c in CLEAN]
    caps[1]["mp4"] = False               # RF is stills-only
    s = _make_study(tmp_path, "st_scored_orphan", caps,
                    ambiguous=[{"folder": "RawImagesForResearch_rf", "identity": "rf",
                                "reason": "orphan rf capture (no mp4)"}])
    a = audit_study(s)
    assert a.verdict == "FLAGGED"
    assert any("ROLE_ON_ORPHAN" in r for r in a.reasons)   # per-row path fires
    assert a.manifest_ambiguous                             # AND manifest path fires — consistent


def test_sweep_depth_mismatch_does_not_flag(tmp_path):
    # DEPTH_MISMATCH is scoped to scored roles too (2026-07-07): a wrong ruler on the non-scored SWEEP does
    # not affect the score, so it must not drive FLAGGED — only a scored capture's ruler mismatch does.
    caps = [dict(c) for c in CLEAN]
    caps[0]["dcm_depth"] = 7.9           # sweep frame is 4.9cm (399px) but a 7.9cm ruler attached
    a = audit_study(_make_study(tmp_path, "st_sweep_mismatch", caps))
    assert not any("DEPTH_MISMATCH" in r for r in a.reasons)
    assert a.verdict == "CLEAN"


def test_off_grid_depth_no_longer_flags(tmp_path):
    # Off-protocol depth is acquisition ('protokol dışı derinlikte' in Kontrol). The audit used to
    # re-flag it as OFF_GRID_DEPTH — removed 2026-07-07. A capture at an off-grid depth whose own dcm
    # agrees (no ruler mismatch) is CLEAN to the audit.
    caps = [dict(c) for c in CLEAN]
    caps[1]["width"] = 504               # 1967/504 = 3.90cm — off the (4.9, 5.9, ...) grid
    caps[1]["dcm_depth"] = 3.9           # its ruler agrees -> no DEPTH_MISMATCH
    a = audit_study(_make_study(tmp_path, "st_offgrid", caps))
    assert not any("OFF_GRID" in r for r in a.reasons)
    assert a.verdict == "CLEAN"


def test_depth_mismatch_flags_and_match_does_not(tmp_path):
    caps = [dict(c) for c in CLEAN]
    caps[1]["dcm_depth"] = 7.9           # 399px frame is 4.9cm but a 7.9cm ruler attached (AKA017 shape)
    s = _make_study(tmp_path, "st_mismatch", caps)
    a = audit_study(s)
    assert any("DEPTH_MISMATCH" in r for r in a.reasons)
    # and the clean control does NOT raise it
    assert not any("DEPTH_MISMATCH" in r for r in audit_study(_make_study(tmp_path, "st_ok", CLEAN)).reasons)


def test_duplicate_role_flags(tmp_path):
    caps = CLEAN + [{"label": "c3_femur_deep", "identity": "femur", "width": 249, "mp4": True, "dcm_depth": None}]
    s = _make_study(tmp_path, "st_dup", caps)
    a = audit_study(s)
    assert any("DUPLICATE_ROLE:femur" in r for r in a.reasons)
    assert any("NO_DCM" in r for r in a.reasons)          # the second femur has no ruler


def test_benign_length_ambiguous_caveat_does_not_flag(tmp_path):
    """The over-flag caught 2026-07-01: a primary caveat must be surfaced but NOT drive FLAGGED."""
    s = _make_study(tmp_path, "st_caveat", CLEAN,
                    caveats=["length-ambiguous (300 frames near sweep/still boundary)"])
    a = audit_study(s)
    assert a.verdict == "CLEAN"
    assert a.manifest_caveats and "length-ambiguous" in a.manifest_caveats[0]


def test_ambiguous_capture_drives_flag(tmp_path):
    s = _make_study(tmp_path, "st_amb", CLEAN,
                    ambiguous=[{"folder": "RawImagesForResearch_x", "identity": "rf",
                                "reason": "orphan rf capture (no mp4) — borrowed/missing video?"}])
    a = audit_study(s)
    assert a.verdict == "FLAGGED"
    assert a.manifest_ambiguous


def test_ambiguous_sweep_surfaces_but_does_not_flag(tmp_path):
    # A manifest ambiguity on the non-scored SWEEP (the real 2-Jul athlete 'orphan sweep, kept for
    # review') is surfaced as a note but must NOT drive FLAGGED — same acquisition fact Kontrol owns.
    s = _make_study(tmp_path, "st_amb_sweep", CLEAN,
                    ambiguous=[{"folder": "RawImagesForResearch_x", "identity": "sweep",
                                "reason": "orphan sweep capture (no mp4) — kept for review"}])
    a = audit_study(s)
    assert a.verdict == "CLEAN"
    assert any("orphan sweep" in c for c in a.manifest_caveats)   # surfaced as a note
    assert not a.manifest_ambiguous                                # but not a flag


def test_audit_tree_finds_every_captures_study(tmp_path):
    _make_study(tmp_path, "st_a", CLEAN)
    _make_study(tmp_path, "st_b", CLEAN)
    audits = audit_tree(tmp_path)
    assert {a.study for a in audits} == {"st_a", "st_b"}
