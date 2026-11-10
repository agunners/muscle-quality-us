"""Typed YAML config for the pipeline (fail-fast validation, CLI override)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PathsCfg:
    decision_log: Path
    transport_metadata: Path
    study_codes: Path
    transport_root: Path
    runs_root: Path
    seg_qc_cache: Path | None = None  # null-safe if absent

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PathsCfg":
        seg = d.get("seg_qc_cache")
        return PathsCfg(
            decision_log=Path(d["decision_log"]).expanduser().resolve(),
            transport_metadata=Path(d["transport_metadata"]).expanduser().resolve(),
            study_codes=Path(d["study_codes"]).expanduser().resolve(),
            transport_root=Path(d["transport_root"]).expanduser().resolve(),
            runs_root=Path(d["runs_root"]).expanduser().resolve(),
            seg_qc_cache=Path(seg).expanduser().resolve() if seg else None,
        )


@dataclass(frozen=True)
class MaskCfg:
    # Class IDs derived from the active label palette (see upstream
    # ``../muscle_quality`` configs — confirmed against a 3-frame sample
    # in the current dataset; class 5 is the dominant class touching
    # the RF top and bottom rows).
    muscle_class_id: int = 1
    fascia_class_id: int = 5
    skin_class_id: int = 6
    fat_class_id: int = 8
    femur_class_id: int = 4
    erosion_px: int = 3


@dataclass(frozen=True)
class QCCfg:
    # Tier 1 (image). Thresholds are MVP placeholders; plan §3 asks for
    # dataset-tuned p10 / p90 after Phase 1 QA on all 956 frames.
    min_focus_laplacian_var: float = 40.0
    max_saturation_ratio: float = 0.10         # computed on in-scan ROI. Plan §3 asks for dataset-tuned p90 after Phase 1 — 0.10 is the MVP placeholder.
    min_global_entropy: float = 4.5
    # Tier 2A (discrete mask)
    min_rf_full_area_px: int = 500
    min_rf_analysis_area_px: int = 100
    max_components: int = 3
    top1_component_frac: float = 0.95
    min_area_retained_fraction: float = 0.40
    # RF bbox aspect (min/max): flag only when suspiciously skinny. Wide,
    # shallow muscles are anatomically normal. Widened from plan draft
    # ``[0.04, 0.25]`` after spot-checking 3 frames where aspect was
    # 0.51–0.60 on clean masks.
    mask_elongation_lo: float = 0.10
    mask_elongation_hi: float = 0.95
    min_fascia_support_area_px: int = 50
    max_fascia_continuity_gap_frac: float = 0.25  # column-scan gap rate < 0.25 (i.e. continuity >= 0.75)
    min_boundary_gradient_coherence: float = 0.10  # dataset-tuned; placeholder
    # Tier 4 study-aggregation
    min_frames_per_study_for_agg: int = 2


@dataclass(frozen=True)
class YoungCorrectionCfg:
    """Young 2015 (Muscle Nerve 52(6) 963-971) additive fat-attenuation correction.

    EI_corrected = EI_raw + fat_thickness_cm × cf

    ``literature_cf_per_cm`` = 40.5278 (Young Eq. 5/6, derived for LOGIQ e at 58 dB,
    8 MHz, 4 cm depth; device-specific by Young's own caveat).

    ``cohort_ei_per_fat_mm_slope`` is the cohort between-subject regression slope
    (EI per mm of fat thickness) from
    ``analysis/22_fat_correction_alpha_fit.py`` regressing int_mean ~ fat_thickness_mm
    on the healthy/preserved subset of Track A. ``None`` until the fit script runs.

    Unit-honest framing (round-2): this slope is NOT a refit of Young's α. Young's
    α was estimated within-subject by varying probe pressure (n=5 subjects × 4
    pressures), isolating fat path-length while holding underlying muscle fixed.
    Our cohort fit is between-subject (n≈216, R²≈0.115) — the slope conflates
    fat-attenuation with any cohort variation that correlates with fat thickness
    (BMI, age, demographics). The two slopes estimate related but not identical
    quantities; side-by-side comparison is informational only. Reserve "α" for
    Young's literature value; call our slope the "cohort EI/fat-thickness slope".

    The downstream column suffix ``_fat_corrected_young_refit_*`` is preserved
    for backward compatibility; renaming would force unnecessary registry/audit
    churn. The column-suffix mapping is documented in ``feature_changelog.md``.
    """
    literature_cf_per_cm: float = 40.5278
    cohort_ei_per_fat_mm_slope: float | None = None


@dataclass(frozen=True)
class FeaturesCfg:
    glcm_levels: int = 32
    modality_primary: str = "final"
    modalities_all: tuple[str, ...] = ("final", "raw", "before_dsc")
    band_min_pairs: int = 16  # band-GLCM gate
    band_min_px: int = 64
    young_correction: YoungCorrectionCfg = field(default_factory=YoungCorrectionCfg)


@dataclass(frozen=True)
class AnalysisCfg:
    """Configuration for the Stage B–F analysis scripts (03–09).

    Kept as a raw dict for now so each analysis script reads its own sub-block
    without forcing a premature typed schema — stages evolve independently.
    """
    raw: dict[str, object] = field(default_factory=dict)

    def get(self, *path: str, default=None):
        node = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


@dataclass(frozen=True)
class AppCfg:
    paths: PathsCfg
    mask: MaskCfg = field(default_factory=MaskCfg)
    qc: QCCfg = field(default_factory=QCCfg)
    features: FeaturesCfg = field(default_factory=FeaturesCfg)
    seed: int = 7
    smoke_n_studies: int = 10
    analysis: AnalysisCfg = field(default_factory=AnalysisCfg)

    def to_yaml(self) -> str:
        d = {
            "paths": {k: (str(v) if v is not None else None) for k, v in asdict(self.paths).items()},
            "mask": asdict(self.mask),
            "qc": asdict(self.qc),
            "features": {
                **{k: v for k, v in asdict(self.features).items() if k not in ("young_correction",)},
                "modalities_all": list(self.features.modalities_all),
                "young_correction": asdict(self.features.young_correction),
            },
            "seed": self.seed,
            "smoke_n_studies": self.smoke_n_studies,
            "analysis": self.analysis.raw,
        }
        return yaml.safe_dump(d, sort_keys=False)


def load_config(path: str | Path) -> AppCfg:
    with open(path) as f:
        d = yaml.safe_load(f)
    if not isinstance(d, dict):
        raise ValueError(f"Config at {path} is not a dict")
    if "paths" not in d:
        raise ValueError("Config missing required 'paths' section")

    paths = PathsCfg.from_dict(d["paths"])
    mask = MaskCfg(**d.get("mask", {}))
    qc = QCCfg(**d.get("qc", {}))
    feats_raw = dict(d.get("features", {}))
    if "modalities_all" in feats_raw:
        feats_raw["modalities_all"] = tuple(feats_raw["modalities_all"])
    yc_raw = feats_raw.pop("young_correction", None)
    if yc_raw is not None:
        feats_raw["young_correction"] = YoungCorrectionCfg(**yc_raw)
    features = FeaturesCfg(**feats_raw)

    cfg = AppCfg(
        paths=paths,
        mask=mask,
        qc=qc,
        features=features,
        seed=int(d.get("seed", 7)),
        smoke_n_studies=int(d.get("smoke_n_studies", 10)),
        analysis=AnalysisCfg(raw=dict(d.get("analysis", {}))),
    )
    _validate_paths(cfg)
    return cfg


def _validate_paths(cfg: AppCfg) -> None:
    for name, p in (
        ("decision_log", cfg.paths.decision_log),
        ("transport_metadata", cfg.paths.transport_metadata),
        ("study_codes", cfg.paths.study_codes),
    ):
        if not p.exists():
            raise FileNotFoundError(f"{name} not found at {p}")
    if not cfg.paths.transport_root.exists():
        raise FileNotFoundError(f"transport_root not found at {cfg.paths.transport_root}")
    cfg.paths.runs_root.mkdir(parents=True, exist_ok=True)
