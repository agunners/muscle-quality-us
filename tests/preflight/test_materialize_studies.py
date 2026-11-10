"""Tests for the intake-zip -> studies_root bridge (scripts/materialize_studies.py).

Synthetic zips carry REAL minimal DICOMs (Rows x PixelSpacing encodes an absolute depth) AND real
minimal JPEGs (a controlled frame WIDTH so the deconfliction classifies the modality), so both the
RELATIVE modality classification and the RELATIVE depth-match are genuinely exercised: the sweep is
the discoverable primary and gets a depth-matched .dcm AT ITS OWN PLANE (not a fixed 4.9); the femur
takes the deepest .dcm; the materialized tree passes discover_studies + has frames; the annex never
surfaces as a second study; a deeper-probe sweep MATERIALIZES; and the gate HOLDS a two-sweep
(both-legs) zip, a no-sweep zip, and a sweep whose own-plane .dcm is missing.
"""
from __future__ import annotations

import io as _io
import json
import zipfile
from pathlib import Path

import materialize_studies as ms
from preflight_helpers.study_discovery import discover_studies, list_frame_bases


def _dcm_bytes(depth_cm: float) -> bytes:
    """A minimal but real DICOM whose Rows x PixelSpacing reads back as depth_cm (PixelSpacing 0.1
    -> Rows = depth_cm * 100), so cs._dcm_depth_cm recovers the depth the way it does on real data."""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    ds = Dataset()
    ds.Rows = int(round(depth_cm * 100))
    ds.Columns = 256
    ds.PixelSpacing = [0.1, 0.1]
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.ImplementationClassUID = generate_uid()
    buf = _io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def _real_jpg(width: int, height: int = 512) -> bytes:
    """A minimal real JPEG of a given width, so the deconfliction reads this capture's frame width —
    modality is classified by RELATIVE width (the narrowest cine is the femur)."""
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("L", (width, height)).save(buf, "JPEG")
    return buf.getvalue()


def _add_capture(zf, study, raw_ts, n_frames, mp4_ts, dcm_ts, dcm_depth, width=399):
    """One capture: a RawImagesForResearch_<raw_ts> folder with n_frames bare jpgs (the FIRST a real
    `width`-px JPEG so the deconfliction reads this capture's frame width; the rest are stubs) + the
    aux pngs, a paired mp4, and (if dcm_ts) an attached real DICOM at dcm_depth cm. mp4_ts=None writes
    NO mp4 — an orphan RawImages (a deleted/re-recorded take, or a borrowed still with no video)."""
    date, base = raw_ts.split("_")[0], int(raw_ts.split("_")[1])
    for i in range(n_frames):
        stem = f"{date}_{base + i:09d}"
        zf.writestr(f"RawImagesForResearch_{raw_ts}/{stem}.jpg", _real_jpg(width) if i == 0 else b"x")
        zf.writestr(f"RawImagesForResearch_{raw_ts}/{stem}_raw.png", b"x")
    if mp4_ts:
        zf.writestr(f"{study}_{mp4_ts}.mp4", b"x")
    if dcm_ts:
        zf.writestr(f"{study}_{dcm_ts}_1.dcm", _dcm_bytes(dcm_depth))


def _make_zip(path, study, captures):
    with zipfile.ZipFile(path, "w") as zf:
        for c in captures:
            _add_capture(zf, study, c["raw_ts"], c["n"], c["mp4_ts"], c.get("dcm_ts"),
                         c.get("depth", 4.9), c.get("width", 399))
    return str(path)


# one sweep (>=200 frames, shallow 399px/4.9cm) + a femur cine (deep 285px/6.9cm) + an rf cine
# (shallow 399px/4.9cm) -> materializable MAIN. Widths drive the RELATIVE modality classification.
MAIN = [
    {"raw_ts": "20260610_105102000", "n": 200, "width": 399, "mp4_ts": "20260610_105105", "dcm_ts": "20260610_105110", "depth": 4.9},
    {"raw_ts": "20260610_105202000", "n": 12,  "width": 285, "mp4_ts": "20260610_105205", "dcm_ts": "20260610_105210", "depth": 6.9},
    {"raw_ts": "20260610_105302000", "n": 12,  "width": 399, "mp4_ts": "20260610_105305", "dcm_ts": "20260610_105310", "depth": 4.9},
]


def test_normalize_raw_dirname():
    assert ms._normalize_raw_dirname("RawImagesForResearch_20260610_105102000") == \
        "RawImagesForResearch_20260610_105102000"                     # exact literal: no-op
    assert ms._normalize_raw_dirname("rawimages_20260610_1").startswith("RawImagesForResearch_")
    assert ms._normalize_raw_dirname("RawImagesForResearch_x")[-1] == "x"


def test_session_buckets_legacy_spelled_out_name():
    # the real 10jun26 name uses spelled-out '25_erkek' (grammar rejects it) — must still bucket
    # by date_count, not degrade to the full (doubled, PHI-bearing) study name.
    assert ms._session_for("10jun26_001_anonaam_anonacx_anonacu_25_erkek_174_70") == "10jun26_001"
    assert ms._session_for("07jun06_093_anonabr_anonaab_49e_179_82") == "07jun06_093"


def test_plan_selects_sweep_as_primary(tmp_path):
    study = "10jun26_001_anonaam_anonacx_25e_174_70"
    zp = _make_zip(tmp_path / f"{study}.zip", study, MAIN)
    p = ms.plan_zip(zp, operator="mat_test")
    assert p.materialize is True
    assert p.primary.is_sweep and p.primary_nframes == 200
    assert abs(p.dcm_depths[p.primary_dcm] - 4.9) < 0.05      # primary got its shallow-plane 4.9cm .dcm
    assert len(p.annex) == 2
    assert p.session == "10jun26_001"
    # relative depth-match: the femur capture takes the DEEPEST .dcm; nothing is left to pool
    femur = next(c for c in p.annex if c.identity == "femur")
    assert abs(p.dcm_depths[p.dcm_for[femur.raw_folder]] - 6.9) < 0.05
    assert p.pool == []


def test_materialize_is_discoverable_with_depth_matched_modalities(tmp_path):
    study = "10jun26_001_anonaam_anonacx_25e_174_70"
    zp = _make_zip(tmp_path / f"{study}.zip", study, MAIN)
    root = tmp_path / "studies_root"
    p = ms.plan_zip(zp, operator="mat_test")
    ok, msg = ms.write_plan(p, root, force=False)
    assert ok, msg

    study_dir = root / "mat_test" / "10jun26_001" / study
    # the discoverable triple at the study root: exactly ONE .dcm (the depth-matched sweep one)
    root_dcms = [c for c in study_dir.iterdir() if c.suffix == ".dcm"]
    assert len(root_dcms) == 1
    raws = [c for c in study_dir.iterdir() if c.name.startswith("RawImagesForResearch_")]
    assert len(raws) == 1 and len(list_frame_bases(raws[0])) == 200
    # each modality now carries its OWN depth-matched .dcm (relative rank), so nothing is pooled
    assert list((study_dir / "captures" / "_dcm_pool").glob("*.dcm")) == []
    femur_dir = next(d for d in (study_dir / "captures").iterdir() if "femur" in d.name)
    rf_dir = next(d for d in (study_dir / "captures").iterdir() if "_rf_" in d.name)
    assert len(list(femur_dir.glob("*.dcm"))) == 1 and len(list(rf_dir.glob("*.dcm"))) == 1
    assert (study_dir / "materialize_manifest.json").exists()

    # discovery: exactly ONE valid entry; the annex captures never surface as their own studies
    entries = [e for e in discover_studies(root) if e.study_path == study_dir]
    assert len(entries) == 1 and entries[0].valid and entries[0].drop_reason is None
    assert len([e for e in discover_studies(root) if str(root) in str(e.study_path)]) == 1

    vok, vmsg = ms.verify_in_place(study_dir, root)
    assert vok, vmsg


def test_atomic_write_leaves_no_partial(tmp_path):
    study = "10jun26_001_anonaam_anonacx_25e_174_70"
    zp = _make_zip(tmp_path / f"{study}.zip", study, MAIN)
    root = tmp_path / "studies_root"
    ms.write_plan(ms.plan_zip(zp, operator="mat_test"), root, force=False)
    assert not list(root.rglob("*.partial"))     # the staging dir was renamed away, none left


def test_materialize_with_length_ambiguous_caveat(tmp_path):
    # a sweep in the content-frame 'ambiguous' band (30-60, between a 15-frame still and a 77+ sweep)
    # is MATERIALIZED with a caveat, not held — is_sweep already cleared the bar; a human spot-checks.
    study = "10jun26_003_anonaak_sweep_25e_174_70"
    caps = [
        {"raw_ts": "20260610_130002000", "n": 50, "width": 399, "mp4_ts": "20260610_130005", "dcm_ts": "20260610_130010", "depth": 4.9},
        {"raw_ts": "20260610_130102000", "n": 10, "width": 285, "mp4_ts": "20260610_130105", "dcm_ts": "20260610_130110", "depth": 6.9},
    ]
    zp = _make_zip(tmp_path / f"{study}.zip", study, caps)
    p = ms.plan_zip(zp, operator="mat_test")
    assert p.materialize is True
    assert any("length-ambiguous" in c for c in p.primary_caveats)


def test_deeper_sweep_materializes(tmp_path):
    # a deeper-probe sweep (6.9cm, NO 4.9 .dcm) now MATERIALIZES with its OWN geometry — the old
    # fixed-4.9 target wrongly held these (the probe-831 case). Relative rank: the only shallow
    # plane present is 6.9, so the sweep takes the 6.9cm .dcm instead of being rejected.
    study = "20jun26_076_deep_sweep_69k_158_89"
    caps = [{"raw_ts": "20260618_140002000", "n": 200, "width": 285, "mp4_ts": "20260618_140005", "dcm_ts": "20260618_140010", "depth": 6.9}]
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, caps), operator="mat_test")
    assert p.materialize is True
    assert abs(p.dcm_depths[p.primary_dcm] - 6.9) < 0.05


def test_hold_when_sweep_plane_dcm_missing(tmp_path):
    # sweep + femur, but the sweep's OWN (shallow) .dcm is missing — only the femur's deep .dcm
    # exists, so the femur claims it and the discoverable primary has no geometry -> HOLD.
    study = "10jun26_004_no_shallow_25e_174_70"
    caps = [
        {"raw_ts": "20260610_140002000", "n": 200, "width": 399, "mp4_ts": "20260610_140005", "dcm_ts": None, "depth": 4.9},
        {"raw_ts": "20260610_140102000", "n": 12,  "width": 285, "mp4_ts": "20260610_140105", "dcm_ts": "20260610_140110", "depth": 6.9},
    ]
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, caps), operator="mat_test")
    assert p.materialize is False and "geometry unresolvable" in p.reason


def test_hold_when_sweep_dcm_skipped_amid_femur_retake(tmp_path):
    # femur + a femur RETAKE (two DEEP .dcm: 9.9 & 8.9) but the sweep's own (shallow) .dcm was
    # skipped. Relative rank alone would hand the sweep the leftover 8.9cm femur-plane .dcm
    # (wrong-scale geometry — the corruption the bridge exists to prevent); the GUARD drops it, so
    # the sweep has no plausible .dcm -> HOLD. (Old fixed-4.9 code also held this, but by luck.)
    study = "20jun26_020_retake_69k_158_89"
    caps = [
        {"raw_ts": "20260618_150002000", "n": 200, "width": 285, "mp4_ts": "20260618_150005", "dcm_ts": None, "depth": 6.9},               # sweep, NO .dcm
        {"raw_ts": "20260618_150102000", "n": 12,  "width": 199, "mp4_ts": "20260618_150105", "dcm_ts": "20260618_150110", "depth": 9.9},  # femur
        {"raw_ts": "20260618_150202000", "n": 12,  "width": 210, "mp4_ts": "20260618_150205", "dcm_ts": "20260618_150210", "depth": 8.9},  # femur retake (deep)
    ]
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, caps), operator="mat_test")
    assert p.materialize is False and "geometry unresolvable" in p.reason


def test_hold_when_two_sweeps(tmp_path):
    study = "07jun06_093_sub_cohort_49e_179_82"
    both_legs = MAIN[:1] + [
        {"raw_ts": "20260608_113102000", "n": 220, "width": 399, "mp4_ts": "20260608_113105", "dcm_ts": "20260608_113110", "depth": 4.9},
    ]
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, both_legs), operator="mat_test")
    assert p.materialize is False and "leg" in p.reason.lower()


def test_hold_when_no_sweep(tmp_path):
    study = "10jun26_002_no_sweep_30e_180_80"
    stills = [
        {"raw_ts": "20260610_120002000", "n": 10, "width": 285, "mp4_ts": "20260610_120005", "dcm_ts": "20260610_120010", "depth": 6.9},
        {"raw_ts": "20260610_120102000", "n": 10, "width": 399, "mp4_ts": "20260610_120105", "dcm_ts": "20260610_120110", "depth": 4.9},
    ]
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, stills), operator="mat_test")
    assert p.materialize is False and "sweep" in p.reason.lower()


# the anonaal 21jun26_111 case, distilled: a 2nd femur take whose mp4 was deleted is an ORPHAN beside a
# cleanly PAIRED femur -> auto-excluded as a redundant duplicate (frames copied NOWHERE, logged in the
# manifest), while the sweep + its mp4 + the paired femur materialize normally. The source zip is untouched.
DUP_FEMUR = [
    {"raw_ts": "20260626_143212000", "n": 12, "width": 285, "mp4_ts": None,                "dcm_ts": "20260626_143242", "depth": 6.9},  # dup femur: mp4 deleted -> orphan
    {"raw_ts": "20260626_143250000", "n": 12, "width": 285, "mp4_ts": "20260626_143254", "dcm_ts": "20260626_143317", "depth": 4.9},  # paired femur
    {"raw_ts": "20260626_143350000", "n": 90, "width": 399, "mp4_ts": "20260626_143413", "dcm_ts": "20260626_143410", "depth": 4.9},  # sweep
]


def test_duplicate_orphan_excluded(tmp_path):
    study = "21jun26_111_anonaal_51e_174_96_43r"
    zp = _make_zip(tmp_path / f"{study}.zip", study, DUP_FEMUR)
    p = ms.plan_zip(zp, operator="mat_test")
    assert p.materialize is True
    # exactly the duplicate orphan femur is excluded, with the {folder,nframes,identity,reason} schema
    assert len(p.excluded_captures) == 1
    e = p.excluded_captures[0]
    assert e["folder"] == "RawImagesForResearch_20260626_143212000"
    assert e["identity"] == "femur" and e["nframes"] == 12
    assert set(e) == {"folder", "nframes", "identity", "reason"} and "duplicate" in e["reason"]
    # the excluded take never becomes a capture; the sweep + its mp4 + the PAIRED femur are intact
    annex_folders = {Path(c.raw_folder).name for c in p.annex}
    assert "RawImagesForResearch_20260626_143212000" not in annex_folders
    assert "RawImagesForResearch_20260626_143250000" in annex_folders          # paired femur kept
    assert p.primary.raw_folder.endswith("143350000") and p.primary.mp4.endswith("143413.mp4")

    # materialize: the excluded take's frames are copied NOWHERE, and the manifest records the exclusion
    root = tmp_path / "studies_root"
    ok, msg = ms.write_plan(p, root, force=False)
    assert ok, msg
    study_dir = root / "mat_test" / p.session / study
    assert not any("143212000" in str(q) for q in study_dir.rglob("*")), "excluded frames must not be copied"
    manifest = json.loads((study_dir / "materialize_manifest.json").read_text(encoding="utf-8"))
    assert [m["folder"] for m in manifest["excluded_captures"]] == ["RawImagesForResearch_20260626_143212000"]


# a borrowed femur (no in-zip video -> its RawImages is an ORPHAN) that is the ONLY femur must be KEPT
# (frames preserved as an annex capture) and escalated to an Ask-tier caveat — never auto-dropped,
# because there is no clean sibling to fall back on (the safety rule: ambiguity never auto-acts).
LONE_FEMUR = [
    {"raw_ts": "20260626_120000000", "n": 90, "width": 399, "mp4_ts": "20260626_120005", "dcm_ts": "20260626_120010", "depth": 4.9},  # sweep
    {"raw_ts": "20260626_130000000", "n": 12, "width": 285, "mp4_ts": None,                "dcm_ts": "20260626_130010", "depth": 6.9},  # borrowed femur: dcm, NO mp4
]


def test_lone_unpaired_orphan_kept(tmp_path):
    study = "26jun26_021_borrowed_femur_50e_170_80_45r"
    p = ms.plan_zip(_make_zip(tmp_path / f"{study}.zip", study, LONE_FEMUR), operator="mat_test")
    assert p.materialize is True
    assert p.excluded_captures == []                               # a lone orphan is NEVER auto-excluded
    femur = [c for c in p.annex if c.identity == "femur"]
    assert len(femur) == 1 and femur[0].pairing == "orphan"        # KEPT as an annex capture
    assert any("only femur take" in c and "KEPT" in c for c in p.primary_caveats)  # Ask-tier flag
    # the STRUCTURED contract the audit reads for PAIRING-AMBIGUOUS (parallel to excluded_captures),
    # so the check no longer depends on the human caveat wording.
    assert len(p.ambiguous_captures) == 1
    assert p.ambiguous_captures[0]["identity"] == "femur" and "KEPT" in p.ambiguous_captures[0]["reason"]
