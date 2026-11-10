"""placements_for threads the cohort source through to cohort_placement.percentile, so the
headline percentile honors the operator's legacy/new/mix selector (Akadir 2026-06-26 #17). Before
the fix it hard-coded legacy while the box plots honored the selector — the headline and its own
box plot could disagree. Torch-free."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))
import preflight_helpers.env_bootstrap  # noqa: F401,E402

from preflight_helpers.feature_routing import placements_for  # noqa: E402
from preflight_helpers.cohort_placement import percentile, LEGACY, NEW  # noqa: E402
from preflight_helpers.feature_catalog import ALL_DISPLAY_FEATURES  # noqa: E402


def _agg():
    # Distinct, plausible-ish values so percentile has something to place.
    return {f: float(i + 1) for i, f in enumerate(ALL_DISPLAY_FEATURES)}


def test_threads_legacy_source():
    agg = _agg()
    pl = placements_for(agg, age=40, sex="M", source=LEGACY)
    for f in ALL_DISPLAY_FEATURES:
        exp = percentile(f, agg[f], age=40, sex="M", source=LEGACY)
        assert pl[f]["overall"] == exp.overall, f
        assert pl[f]["value"] == exp.value, f


def test_default_source_is_legacy():
    agg = _agg()
    assert placements_for(agg, 40, "M") == placements_for(agg, 40, "M", source=LEGACY)


def test_threads_new_source():
    # Whatever the new cohort returns (empty -> overall None until new studies accrue),
    # placements_for must match percentile(source=NEW) feature-for-feature — proving the
    # source param reaches percentile end-to-end (legacy would give different placements).
    agg = _agg()
    pl = placements_for(agg, 40, "M", source=NEW)
    for f in ALL_DISPLAY_FEATURES:
        exp = percentile(f, agg[f], age=40, sex="M", source=NEW)
        assert pl[f]["overall"] == exp.overall, f
