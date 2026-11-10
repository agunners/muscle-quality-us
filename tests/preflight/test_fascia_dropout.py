"""E4 — the Tier-A fascia-continuity / anisotropy-dropout detector. A continuous
fascia is silent; a >30% dropout span fires `anisotropy_dropout_superficial`;
too-small RF is not assessable; and the detector degrades gracefully without
skimage (Akadir 2026-06-04 note 9)."""
import numpy as np

from labeling_app import image_quality
from labeling_app.image_quality import _detect_fascia_dropout
from labeling_app.measurements import LABEL_FASCIA, LABEL_RF


def _synth(fascia_cols) -> tuple[np.ndarray, np.ndarray]:
    """RF muscle cols 10-69 (top row 40); LABEL_FASCIA just above the top on the
    given columns. Flat image -> Frangi finds no ridge -> seg-only continuity,
    so the test is deterministic regardless of skimage."""
    m = np.zeros((100, 80), dtype=np.int64)
    m[40:80, 10:70] = LABEL_RF
    for c in fascia_cols:
        m[37:40, c] = LABEL_FASCIA
    img = np.zeros((100, 80), dtype=np.uint8)
    return img, m


def test_continuous_fascia_is_silent():
    img, m = _synth(range(10, 70))
    r = _detect_fascia_dropout(img, m)
    assert r["flag"] is None
    assert r["metadata"]["continuity_fraction"] > 0.95


def test_fascia_dropout_fires():
    img, m = _synth(range(10, 40))   # fascia on only ~half the RF columns
    r = _detect_fascia_dropout(img, m)
    assert r["flag"] == "anisotropy_dropout_superficial"
    assert r["metadata"]["continuity_fraction"] < 0.70


def test_small_rf_not_assessable():
    m = np.zeros((100, 80), dtype=np.int64)
    m[40:80, 10:18] = LABEL_RF       # 8 columns < _FASCIA_MIN_COLS
    r = _detect_fascia_dropout(np.zeros((100, 80), np.uint8), m)
    assert r["flag"] is None
    assert r["metadata"]["continuity_fraction"] is None


def test_degrades_without_skimage(monkeypatch):
    monkeypatch.setattr(image_quality, "_frangi", None)
    img, m = _synth(range(10, 70))
    r = _detect_fascia_dropout(img, m)
    assert r["flag"] is None
    assert r["metadata"]["frangi_used"] is False
