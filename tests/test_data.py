"""Tests for data.py: demographics parser and manifest builder."""
import pytest
import numpy as np
import pandas as pd
from pathlib import Path

from labeling_app.data import (
    build_study_manifest,
    generate_study_code_map,
    load_study_code_map,
    parse_demographics,
    select_center_frame,
)


# ---------------------------------------------------------------------------
# parse_demographics
# ---------------------------------------------------------------------------

class TestParseDemographics:

    def test_standard_female_ht(self):
        d = parse_demographics("0000074_anonabj_anonabz_56k_164_81_ht")
        assert d["id"] == "0000074"
        assert d["last_name"] == "anonabj"
        assert d["first_name"] == "anonabz"
        assert d["age"] == 56
        assert d["gender"] == "F"
        assert d["height_cm"] == 164
        assert d["weight_kg"] == 81
        assert d["comorbidities"] == "ht"
        assert d["other_comorbidities"] is None
        assert d["parse_ok"] is True

    def test_standard_male_no_comorbidity(self):
        d = parse_demographics("0000119_anonabi_anonabl_47e_176_76")
        assert d["id"] == "0000119"
        assert d["age"] == 47
        assert d["gender"] == "M"
        assert d["height_cm"] == 176
        assert d["weight_kg"] == 76
        assert d["comorbidities"] is None
        assert d["other_comorbidities"] is None
        assert d["parse_ok"] is True

    def test_trailing_underscore_no_crash(self):
        d = parse_demographics("0000075_anonabe_anonabq_28k_157_64_")
        assert d["age"] == 28
        assert d["gender"] == "F"
        assert d["height_cm"] == 157
        assert d["weight_kg"] == 64
        # No crash — that's the main assertion
        assert isinstance(d, dict)

    def test_unknown_gender_y(self):
        d = parse_demographics("0000102_anonacg_anonabs_45y_146_72")
        assert d["age"] == 45
        assert d["gender"] == "unknown"
        assert d["height_cm"] == 146
        assert d["weight_kg"] == 72

    def test_dm_comorbidity(self):
        d = parse_demographics("0000050_test_user_45k_165_70_dm")
        assert d["comorbidities"] == "dm"
        assert d["other_comorbidities"] is None

    def test_ht_dm_combined(self):
        d = parse_demographics("0000051_test_name_50e_170_80_ht+dm")
        assert d["comorbidities"] == "ht+dm"
        assert d["other_comorbidities"] is None

    def test_dual_comorbidity_dm_ht_is_canonicalized(self):
        d = parse_demographics("0000197_anonaaz_anonadz_58k_160_119_dm_ht")
        assert d["age"] == 58
        assert d["gender"] == "F"
        assert d["height_cm"] == 160
        assert d["weight_kg"] == 119
        assert d["comorbidities"] == "ht+dm"
        assert d["other_comorbidities"] is None
        assert d["parse_ok"] is True

    def test_dual_comorbidity_ht_dm_is_canonicalized(self):
        d = parse_demographics("0000082_anonado_anonadb_84e_157_73_ht_dm")
        assert d["age"] == 84
        assert d["gender"] == "M"
        assert d["height_cm"] == 157
        assert d["weight_kg"] == 73
        assert d["comorbidities"] == "ht+dm"
        assert d["other_comorbidities"] is None
        assert d["parse_ok"] is True

    def test_unknown_suffix_recorded_separately(self):
        d = parse_demographics("0000223_anonaaj_anonady_54k_155_58_behçet")
        assert d["age"] == 54
        assert d["gender"] == "F"
        assert d["height_cm"] == 155
        assert d["weight_kg"] == 58
        assert d["comorbidities"] is None
        assert d["other_comorbidities"] == "behçet"
        assert d["parse_ok"] is True

    def test_known_and_other_comorbidities_are_separated(self):
        d = parse_demographics("0000130_anonabd_anonabk__66k_158_82_ht_dm_kah_")
        assert d["last_name"] == "anonabd"
        assert d["first_name"] == "anonabk"
        assert d["age"] == 66
        assert d["gender"] == "F"
        assert d["height_cm"] == 158
        assert d["weight_kg"] == 82
        assert d["comorbidities"] == "ht+dm"
        assert d["other_comorbidities"] == "kah"
        assert d["parse_ok"] is True

    def test_uppercase_known_and_multiple_unknown_suffixes(self):
        d = parse_demographics("0000033_yağlı_anonacv_43y_169_89_DM_hl_kah")
        assert d["age"] == 43
        assert d["gender"] == "unknown"
        assert d["height_cm"] == 169
        assert d["weight_kg"] == 89
        assert d["comorbidities"] == "dm"
        assert d["other_comorbidities"] == "hl+kah"
        assert d["parse_ok"] is True

    def test_bare_numeric_age_defaults_to_unknown_gender(self):
        d = parse_demographics("0000116_anonact_Anonabv_anonacw_56_169_86_")
        assert d["last_name"] == "anonact"
        assert d["first_name"] == "Anonabv anonacw"
        assert d["age"] == 56
        assert d["gender"] == "unknown"
        assert d["height_cm"] == 169
        assert d["weight_kg"] == 86
        assert d["parse_ok"] is True

    def test_fused_age_gender_height_token(self):
        d = parse_demographics("0000188_anonabf_anonaea_24k156_49")
        assert d["age"] == 24
        assert d["gender"] == "F"
        assert d["height_cm"] == 156
        assert d["weight_kg"] == 49
        assert d["parse_ok"] is True

    def test_split_age_and_gender_tokens(self):
        d = parse_demographics("0000019_anonacd_h_26y_e_169_91")
        assert d["last_name"] == "anonacd"
        assert d["first_name"] == "h"
        assert d["age"] == 26
        assert d["gender"] == "M"
        assert d["height_cm"] == 169
        assert d["weight_kg"] == 91
        assert d["parse_ok"] is True

    def test_weight_with_kg_suffix(self):
        d = parse_demographics("0000027_anonadt_anonaba_34y_153_64kg")
        assert d["last_name"] == "anonadt"
        assert d["first_name"] == "anonaba"
        assert d["age"] == 34
        assert d["gender"] == "unknown"
        assert d["height_cm"] == 153
        assert d["weight_kg"] == 64
        assert d["parse_ok"] is True

    def test_missing_height_weight_no_crash(self):
        # Only id + name-like tokens — no height/weight parseable
        d = parse_demographics("0000099_anonaag_anonaci")
        assert d["parse_ok"] is False
        assert d["bmi"] is None
        assert isinstance(d, dict)  # no crash

    def test_utf8_turkish_chars(self):
        """Turkish characters must not raise any encoding errors."""
        d = parse_demographics("0000074_anonabj_anonabz_56k_164_81_ht")
        assert d["last_name"] == "anonabj"
        assert d["first_name"] == "anonabz"

    def test_bmi_calculation(self):
        """164cm, 81kg → BMI = 81 / (1.64)^2 = 30.1"""
        d = parse_demographics("0000074_anonabj_anonabz_56k_164_81_ht")
        assert d["bmi"] == pytest.approx(30.1, abs=0.1)

    def test_bmi_none_when_height_missing(self):
        d = parse_demographics("0000099_user_name_45k")
        assert d["bmi"] is None


# ---------------------------------------------------------------------------
# build_study_manifest — unit-level (no file I/O needed)
# We test the structure returned by a minimal synthetic CSV.
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_csv(tmp_path):
    """Create a minimal transport_metadata.csv with 2 studies, 3 frames each."""
    rows = []
    for s_idx, study in enumerate(["0000074_anonabj_anonabz_56k_164_81_ht",
                                    "0000033_test_user_30e_170_75"]):
        for f_idx, fb in enumerate(["20260102_100000", "20260102_100010", "20260102_100020"]):
            rows.append({
                "study_id": f"anonaaf_sess_{s_idx}_{study}",
                "annotator": "anonaaf",
                "session": f"sess_{s_idx}",
                "study_dirname": study,
                "frame_base": fb,
                "relative_frame_dir": f"anonaaf/sess_{s_idx}/{study}",
                "raw_path": f"anonaaf/sess_{s_idx}/{study}/{fb}_raw.png",
                "before_dsc_path": "",
                "final_path": f"anonaaf/sess_{s_idx}/{study}/{fb}_final.png",
                "original_jpg_path": "",
                "source_dcm_path": "",
                "source_mp4_path": f"anonaaf/sess_{s_idx}/{study}/source.mp4",
                "source_mp4_source": "h264",
                "mask_path": f"anonaaf/sess_{s_idx}/{study}/{fb}_mask.png",
                "mask_height": "512",
                "mask_width": "256",
                "mask_unique_values": "[0,1,2]",
                "mask_positive_pixels": "10000",
                "jpg_height": "512",
                "jpg_width": "249",
                "dcm_rows": "585",
                "dcm_cols": "900",
                "pixel_spacing_x": "0.13486072",
                "pixel_spacing_y": "0.13486072",
                "voi_path": "", "log_path": "", "clahe_path": "",
                "contrast_path": "", "edge_path": "", "hist_path": "",
                "speckle_path": "", "depth_path": "", "frangi_path": "", "diff_path": "",
            })
    df = pd.DataFrame(rows)
    csv_path = tmp_path / "transport_metadata.csv"
    df.to_csv(csv_path, index=False)
    return csv_path, str(tmp_path)


class TestBuildStudyManifest:

    def test_row_count(self, minimal_csv):
        csv_path, transport_root = minimal_csv
        manifest = build_study_manifest(str(csv_path), transport_root)
        assert len(manifest) == 2  # 2 unique studies

    def test_frame_count(self, minimal_csv):
        csv_path, transport_root = minimal_csv
        manifest = build_study_manifest(str(csv_path), transport_root)
        assert all(manifest["frame_count"] == 3)

    def test_demographics_parsed(self, minimal_csv):
        csv_path, transport_root = minimal_csv
        manifest = build_study_manifest(str(csv_path), transport_root)
        row = manifest[manifest["study_dirname"] == "0000074_anonabj_anonabz_56k_164_81_ht"].iloc[0]
        assert row["age"] == 56
        assert row["gender"] == "F"
        assert row["height_cm"] == 164
        assert row["other_comorbidities"] is None or pd.isna(row["other_comorbidities"])

    def test_pixel_spacing_present(self, minimal_csv):
        csv_path, transport_root = minimal_csv
        manifest = build_study_manifest(str(csv_path), transport_root)
        assert all(manifest["pixel_spacing_x"].notna())
        assert all(manifest["has_pixel_spacing"])

    def test_frame_paths_are_lists(self, minimal_csv):
        csv_path, transport_root = minimal_csv
        manifest = build_study_manifest(str(csv_path), transport_root)
        for _, row in manifest.iterrows():
            assert isinstance(row["final_paths"], list)
            assert isinstance(row["mask_paths"], list)
            assert len(row["final_paths"]) == 3

    def test_study_code_map_generation_and_manifest_join(self, minimal_csv, tmp_path):
        csv_path, transport_root = minimal_csv
        code_map_path = tmp_path / "study_codes.csv"
        code_map = generate_study_code_map(str(csv_path), str(code_map_path))
        manifest = build_study_manifest(str(csv_path), transport_root, str(code_map_path))

        assert len(code_map) == 2
        assert manifest["study_code"].notna().all()
        assert manifest["study_code"].astype(str).str.fullmatch(r"[A-Z]{3}\d{3}").all()

    def test_load_study_code_map_rejects_duplicate_codes(self, tmp_path):
        code_map_path = tmp_path / "study_codes.csv"
        pd.DataFrame(
            [
                {
                    "study_code": "ABC123",
                    "study_dirname": "study_A",
                    "numeric_study_id": "0001",
                    "annotator": "ann",
                    "session": "sess",
                    "relative_frame_dir": "ann/sess/study_A",
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                },
                {
                    "study_code": "ABC123",
                    "study_dirname": "study_B",
                    "numeric_study_id": "0002",
                    "annotator": "ann",
                    "session": "sess",
                    "relative_frame_dir": "ann/sess/study_B",
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                },
            ]
        ).to_csv(code_map_path, index=False)

        with pytest.raises(ValueError, match="duplicate study_code"):
            load_study_code_map(str(code_map_path))

    def test_generate_study_code_map_is_deterministic(self, minimal_csv, tmp_path):
        csv_path, _ = minimal_csv
        path_a = tmp_path / "study_codes_a.csv"
        path_b = tmp_path / "study_codes_b.csv"

        map_a = generate_study_code_map(str(csv_path), str(path_a))
        map_b = generate_study_code_map(str(csv_path), str(path_b))

        pd.testing.assert_frame_equal(
            map_a.drop(columns=["created_at_utc"]).reset_index(drop=True),
            map_b.drop(columns=["created_at_utc"]).reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# Integration test: real CSV (marked, skipped when transport missing)
# ---------------------------------------------------------------------------

REAL_CSV = Path(__file__).parent.parent / "transport" / "transport_metadata.csv"

@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
def test_build_manifest_real_row_count():
    manifest = build_study_manifest(
        str(REAL_CSV), str(REAL_CSV.parent)
    )
    assert len(manifest) == 319

@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
def test_build_manifest_real_frame_counts():
    manifest = build_study_manifest(
        str(REAL_CSV), str(REAL_CSV.parent)
    )
    assert (manifest["frame_count"] >= 2).all()
    assert (manifest["frame_count"] <= 4).all()


@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
def test_build_manifest_real_dual_comorbidity_row():
    manifest = build_study_manifest(str(REAL_CSV), str(REAL_CSV.parent))
    row = manifest[manifest["study_dirname"] == "0000197_anonaaz_anonadz_58k_160_119_dm_ht"].iloc[0]
    assert row["age"] == 58
    assert row["gender"] == "F"
    assert row["height_cm"] == 160
    assert row["weight_kg"] == 119
    assert row["bmi"] == pytest.approx(46.5, abs=0.1)
    assert row["comorbidities"] == "ht+dm"
    assert row["other_comorbidities"] is None or pd.isna(row["other_comorbidities"])


@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
def test_build_manifest_real_mixed_suffix_row():
    manifest = build_study_manifest(str(REAL_CSV), str(REAL_CSV.parent))
    row = manifest[manifest["study_dirname"] == "0000130_anonabd_anonabk__66k_158_82_ht_dm_kah_"].iloc[0]
    assert row["age"] == 66
    assert row["gender"] == "F"
    assert row["height_cm"] == 158
    assert row["weight_kg"] == 82
    assert row["comorbidities"] == "ht+dm"
    assert row["other_comorbidities"] == "kah"


@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
def test_real_corpus_parse_recovery_level():
    manifest = build_study_manifest(str(REAL_CSV), str(REAL_CSV.parent))
    assert manifest["parse_ok"].tolist().count(True) == 315


@pytest.mark.integration
@pytest.mark.skipif(not REAL_CSV.exists(), reason="transport CSV not available")
@pytest.mark.parametrize(
    "study_dirname",
    [
        "0000019_Quick_ID__",
        "0000048_anonaas_m_E_176_85",
        "0000004_anonaaa_h_27e",
        "0000006_anonacd_m_50e",
    ],
)
def test_real_corpus_incomplete_cases_remain_partial(study_dirname):
    manifest = build_study_manifest(str(REAL_CSV), str(REAL_CSV.parent))
    row = manifest[manifest["study_dirname"] == study_dirname].iloc[0]
    assert bool(row["parse_ok"]) is False


# ---------------------------------------------------------------------------
# select_center_frame
# ---------------------------------------------------------------------------

class TestSelectCenterFrame:

    def _make_study_row(self, mask_arrays, tmp_path):
        """Write masks to tmp_path; return a minimal study_row Series."""
        from PIL import Image
        mask_paths = []
        for i, arr in enumerate(mask_arrays):
            p = tmp_path / f"mask_{i}.png"
            Image.fromarray(arr).save(p)
            mask_paths.append(str(p))

        return pd.Series({
            "mask_paths": mask_paths,
            "frame_bases": [f"frame_{i}" for i in range(len(mask_arrays))],
            "frame_count": len(mask_arrays),
        })

    def _make_standard_qmt_mask(self) -> np.ndarray:
        """Mask that yields standard_qmt: fascia, RF, VI, femur in clean column."""
        from labeling_app.measurements import LABEL_FASCIA, LABEL_RF, LABEL_VI, LABEL_FEMUR
        m = np.zeros((50, 50), dtype=np.uint8)
        m[5, 20:30] = LABEL_FASCIA
        m[6:18, 20:30] = LABEL_RF
        m[18:30, 20:30] = LABEL_VI
        m[33:37, 20:30] = LABEL_FEMUR
        return m

    def _make_relaxed_only_qmt_mask(self) -> np.ndarray:
        """Mask that has only a VM-tolerant femur path, not a strict one."""
        from labeling_app.measurements import LABEL_FASCIA, LABEL_RF, LABEL_VI, LABEL_FEMUR
        m = np.zeros((50, 50), dtype=np.uint8)
        m[5, 20:30] = LABEL_FASCIA
        m[6:12, 20:30] = LABEL_RF
        m[12:24, 20:30] = 3
        m[24:28, 20:30] = LABEL_VI
        m[33:37, 20:30] = LABEL_FEMUR
        return m

    def test_prefers_temporal_center(self, tmp_path):
        """Temporal center (index 1) is returned when it has standard_qmt."""
        qmt_mask = self._make_standard_qmt_mask()
        empty = np.zeros((50, 50), dtype=np.uint8)
        # Only temporal center (idx 1) has standard_qmt
        masks = [empty, qmt_mask, empty]
        row = self._make_study_row(masks, tmp_path)
        assert select_center_frame(row) == 1

    def test_falls_back_to_other_frame_with_standard_qmt(self, tmp_path):
        """When temporal center has no QMT, picks nearest frame that does."""
        qmt_mask = self._make_standard_qmt_mask()
        empty = np.zeros((50, 50), dtype=np.uint8)
        # Frame 0 has standard_qmt; frames 1 (center) and 2 are empty
        masks = [qmt_mask, empty, empty]
        row = self._make_study_row(masks, tmp_path)
        assert select_center_frame(row) == 0

    def test_relaxed_only_temporal_center_does_not_beat_strict_frame(self, tmp_path):
        """Frame selection must follow the strict-only production QMT policy."""
        strict_mask = self._make_standard_qmt_mask()
        relaxed_only = self._make_relaxed_only_qmt_mask()
        empty = np.zeros((50, 50), dtype=np.uint8)
        masks = [strict_mask, relaxed_only, empty]
        row = self._make_study_row(masks, tmp_path)
        assert select_center_frame(row) == 0

    def test_fallback_to_middle_when_no_standard_qmt(self, tmp_path):
        """When no frame yields standard_qmt, returns temporal middle."""
        masks = [np.zeros((50, 50), dtype=np.uint8) for _ in range(3)]
        row = self._make_study_row(masks, tmp_path)
        idx = select_center_frame(row)
        assert idx == 1  # temporal middle of [0, 1, 2]
