"""The held-out Se/Sp validation harness — the check the data-gated detector MOEs
will run once labels exist. Pinned to hand-computed confusion matrices, both
firing directions, and a held-out split."""
from preflight_helpers.detector_validation import (
    confusion_at_cut, held_out_se_sp, meets_band, sensitivity_specificity,
    youden_cut,
)


def test_higher_fires_perfect_separation():
    labels = [1, 1, 1, 0, 0, 0]
    scores = [9, 8, 7, 3, 2, 1]          # present=high; cut 5 separates cleanly
    ss = sensitivity_specificity(labels, scores, 5, higher_fires=True)
    assert ss["sensitivity"] == 1.0 and ss["specificity"] == 1.0
    assert (ss["tp"], ss["fp"], ss["tn"], ss["fn"]) == (3, 0, 3, 0)


def test_lower_fires_continuity_like():
    # fascia continuity: low = dropout (present); fires BELOW the cut
    labels = [1, 1, 0, 0]
    scores = [0.42, 0.60, 0.90, 1.00]
    ss = sensitivity_specificity(labels, scores, 0.70, higher_fires=False)
    assert ss["sensitivity"] == 1.0 and ss["specificity"] == 1.0


def test_partial_confusion():
    labels = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    scores = [9, 8, 7, 4, 6, 2, 2, 2, 2, 2]   # one FN (4<5) + one FP (6>=5)
    ss = sensitivity_specificity(labels, scores, 5, higher_fires=True)
    assert ss["sensitivity"] == 0.75 and abs(ss["specificity"] - 5 / 6) < 1e-9


def test_youden_finds_the_gap():
    labels = [1, 1, 1, 0, 0, 0]
    scores = [9, 8, 7, 3, 2, 1]
    y = youden_cut(labels, scores, higher_fires=True)
    assert y["youden_j"] == 1.0 and 3 < y["cut"] <= 7


def test_held_out_no_leak():
    # cut fitted on the train fold only, reported on the disjoint test fold; the
    # split keeps every positive above every negative AND the train fold holds the
    # lowest positive, so the fitted cut generalizes to the held-out fold
    labels = [1, 1, 1, 1, 0, 0, 0, 0]
    scores = [9, 8, 7, 6, 3, 2, 1, 0]
    r = held_out_se_sp(labels, scores, train_idx=[0, 3, 4, 7],
                       test_idx=[1, 2, 5, 6], higher_fires=True)
    assert r["sensitivity"] == 1.0 and r["specificity"] == 1.0
    assert r["cut"] == 6.0   # the train fold's lowest positive


def test_meets_band():
    assert meets_band({"sensitivity": 0.85, "specificity": 0.90})
    assert not meets_band({"sensitivity": 0.75, "specificity": 0.99})   # Se short
    assert not meets_band({"sensitivity": 0.95, "specificity": 0.80})   # Sp short
