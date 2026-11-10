"""Real-data regression tests for QMT policy, proxy STAR, and visualization."""
from pathlib import Path

import pytest

from labeling_app.data import build_study_manifest, select_center_frame
from labeling_app.measurements import compute_all_metrics, _fallback_qmt_bounds, _select_qmt_column
from labeling_app.visualization import load_mask_pil


REAL_CSV = Path(__file__).parent.parent / "transport" / "transport_metadata.csv"


@pytest.fixture(scope="module")
def real_manifest():
    if not REAL_CSV.exists():
        pytest.skip("transport CSV not available")
    return build_study_manifest(str(REAL_CSV), str(REAL_CSV.parent))


def _spacing(row):
    return {
        "pixel_spacing_x": row.get("pixel_spacing_x"),
        "pixel_spacing_y": row.get("pixel_spacing_y"),
        "dcm_rows": row.get("dcm_rows"),
        "jpg_height": row.get("jpg_height"),
        "jpg_width": row.get("jpg_width"),
        "mask_height": row.get("mask_height"),
        "mask_width": row.get("mask_width"),
        "bmi": row.get("bmi"),
    }


def _study_payload(manifest, prefix: str, annotator: str):
    matches = manifest[
        manifest["study_dirname"].str.startswith(prefix)
        & (manifest["annotator"] == annotator)
    ]
    assert len(matches) == 1, f"Expected one study for {prefix}/{annotator}, found {len(matches)}"
    row = matches.iloc[0]
    center_idx = select_center_frame(row)
    mask = load_mask_pil(row["mask_paths"][center_idx])
    assert mask is not None
    all_masks = [load_mask_pil(path) for path in row["mask_paths"] if path]
    all_masks = [mask_arr for mask_arr in all_masks if mask_arr is not None]
    metrics = compute_all_metrics(mask, None, all_masks, _spacing(row))
    return row, mask, metrics


@pytest.mark.integration
@pytest.mark.parametrize(
    ("prefix", "annotator"),
    [
        ("0000032", "anonaaf"),
        ("0000134", "anonaaf"),
    ],
)
def test_pdf_regressions_true_qmt_column_matches_annotation_columns(real_manifest, prefix, annotator):
    _, _, metrics = _study_payload(real_manifest, prefix, annotator)
    assert metrics["qmt_column"] is not None
    assert metrics["qmt_annotation_columns"][0] == metrics["qmt_column"]


@pytest.mark.integration
def test_pdf_regression_vi_and_qmt_columns_can_differ(real_manifest):
    _, _, metrics = _study_payload(real_manifest, "0000048", "anonaaq")
    assert metrics["qmt_provenance"] == "standard_qmt"
    assert metrics["vi_column"] != metrics["qmt_column"]


@pytest.mark.integration
def test_pdf_regression_proxy_star_present_for_fallback(real_manifest):
    _, _, metrics = _study_payload(real_manifest, "0000037", "anonaaf")
    assert metrics["qmt_provenance"] in {"femur_supported_proxy", "no_femur_proxy"}
    assert metrics["star_ratio"] is not None
    assert metrics["star_ratio"] > 0


@pytest.mark.integration
def test_pdf_regression_strict_policy_keeps_relaxed_only_case_as_fallback(real_manifest):
    _, mask, metrics = _study_payload(real_manifest, "0000046", "anonaaf")
    assert metrics["qmt_provenance"] == "femur_supported_proxy"
    assert metrics["qmt_column"] is not None
    assert metrics["qmt_annotation_columns"] == [metrics["qmt_column"]]
    assert _select_qmt_column(mask) is None
    assert _select_qmt_column(mask, strict=False) is not None


@pytest.mark.integration
def test_pdf_regression_standard_qmt_case_remains_stable(real_manifest):
    _, _, metrics = _study_payload(real_manifest, "0000149", "anonaaf")
    assert metrics["qmt_provenance"] == "standard_qmt"
    assert metrics["qmt_annotation_columns"][0] == metrics["qmt_column"]


@pytest.mark.integration
def test_dataset_qmt_display_column_matches_recorded_column(real_manifest):
    for _, row in real_manifest.iterrows():
        center_idx = select_center_frame(row)
        center_mask = load_mask_pil(row["mask_paths"][center_idx])
        assert center_mask is not None
        all_masks = [load_mask_pil(path) for path in row["mask_paths"] if path]
        all_masks = [mask_arr for mask_arr in all_masks if mask_arr is not None]
        metrics = compute_all_metrics(center_mask, None, all_masks, _spacing(row))

        if metrics["qmt_column"] is None:
            assert metrics["qmt_annotation_columns"] == []
        else:
            assert metrics["qmt_annotation_columns"]
            assert metrics["qmt_annotation_columns"][0] == metrics["qmt_column"]


@pytest.mark.integration
def test_dataset_measurable_proxy_cases_do_not_fall_to_no_measurement(real_manifest):
    for _, row in real_manifest.iterrows():
        center_idx = select_center_frame(row)
        center_mask = load_mask_pil(row["mask_paths"][center_idx])
        assert center_mask is not None
        all_masks = [load_mask_pil(path) for path in row["mask_paths"] if path]
        all_masks = [mask_arr for mask_arr in all_masks if mask_arr is not None]
        metrics = compute_all_metrics(center_mask, None, all_masks, _spacing(row))

        has_proxy_span = any(
            _fallback_qmt_bounds(center_mask, col) is not None
            for col in range(center_mask.shape[1])
        )
        if has_proxy_span:
            assert metrics["qmt_provenance"] != "no_measurement"
