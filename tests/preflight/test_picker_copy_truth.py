"""Guard against the v8 'stale-copy' regression.

Once feature routing landed (feature_routing.route_study_aggregate — the score reads the femur/RF
picks, geometry from the femur, echo-intensity from the center-RF), the picker/review copy that
still called those picks "validation-only / next step" and called the sweep "what scores today"
became actively FALSE — it told the operator their picks don't score when they do. This test bans
those exact substrings from the picker + review pages so the contradiction cannot creep back in.

If you are intentionally changing this copy, update the banned list with care: the rule is that the
copy must never claim the femur/center-RF picks are unscored, or that the sweep is the sole scored
path, in a routed (multi-modality) study.
"""
from pathlib import Path

_PAGES = Path(__file__).resolve().parents[2] / "labeling_app" / "assessment_pages"

# Substrings that were literally false after routing shipped — must not reappear (source-contiguous).
_BANNED = (
    "per-modality router",
    "feed the feature router",
    "what scores today",
    "today's scored path",
    "Validation-only for now",
    "not the score yet",
)

_FILES = ("p03_frame_picker.py", "p04_frame_review.py")


def test_no_stale_pre_routing_copy():
    hits = []
    for name in _FILES:
        text = (_PAGES / name).read_text()
        for bad in _BANNED:
            if bad in text:
                hits.append(f"{name}: {bad!r}")
    assert not hits, (
        "Stale pre-routing copy resurfaced — the femur/center-RF picks DO feed the score "
        "(feature_routing.route_study_aggregate):\n  " + "\n  ".join(hits)
    )
