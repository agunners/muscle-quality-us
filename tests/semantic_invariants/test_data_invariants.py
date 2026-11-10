"""Pure-data invariants on the QA layer's tables.

These are the load-bearing checks that catch "someone re-added a rule that
contradicts the design." No frames, no model — just assertions on the
static tables themselves.
"""
from __future__ import annotations

import pytest

from labeling_app.image_quality import (
    _GATING_TABLE, GATING_MEASUREMENTS,
)


# ---------------------------------------------------------------------------
# edge_clip_lateral — RESERVE STATE
# ---------------------------------------------------------------------------
# Akadir 2026-05-24 § 1 + § 4 + § 5: "remove all about RF completeness,
# including the detectors, I even said to put it in a reserve (detector)
# state, because well, this probe is not the probe we can extract meaningful
# stuff, this is just noise, from now on..."
#
# Operational definition of "reserve state": the detector still fires + goes
# on the record (Transparency Rule 1), but it taints NO measurement in the
# gating table. So a frame where the muscle visibly runs to the lateral edge
# is still a valid frame; the visible-portion measurements (visible_rf_area,
# full_area) are valid measurements OF THE VISIBLE PORTION; the rename in
# Layer 1 acknowledged that, and this invariant carries the operational
# follow-through. If anyone re-adds an invalidation rule, this test goes red.

def test_edge_clip_lateral_is_reserve_state() -> None:
    """edge_clip_lateral must taint NOTHING in the gating table.

    The 'visible RF area' rename (Layer 1) was the LETTER of the user's
    feedback. This is the SPIRIT: the visible measurement, by definition,
    cannot be invalid due to FOV — the FOV is what defines it. The
    detector stays in the registry for the record; it does not invalidate
    any measurement.
    """
    row = _GATING_TABLE.get("edge_clip_lateral", {})
    assert row == {}, (
        f"edge_clip_lateral should be reserve (empty gating row); got {row}. "
        f"The rename to visible_rf_area was cosmetic if this row still has "
        f"invalidations.")


# ---------------------------------------------------------------------------
# Every measurement that appears in any _GATING_TABLE row is canonical
# ---------------------------------------------------------------------------
# A stray measurement name in a row (typo, deprecated field) silently
# disables an invalidation. This catches it.

def test_every_gated_measurement_is_in_gating_measurements_or_alias() -> None:
    """A measurement name in any _GATING_TABLE row must resolve to a
    canonical GATING_MEASUREMENTS entry (directly or via alias)."""
    # Aliases — the canonical-measurement map. Import here to avoid a
    # name-collision and to keep the test honest about which symbols it
    # depends on.
    from labeling_app.image_quality import _MEASUREMENT_ALIASES
    canonical = set(GATING_MEASUREMENTS)
    aliased = set(_MEASUREMENT_ALIASES.keys())
    allowed = canonical | aliased
    bad: dict[str, list[str]] = {}
    for flag, row in _GATING_TABLE.items():
        unknown = [m for m in row if m not in allowed]
        if unknown:
            bad[flag] = unknown
    assert not bad, (
        f"Unknown measurement names in _GATING_TABLE — typo or stale "
        f"reference: {bad}")
