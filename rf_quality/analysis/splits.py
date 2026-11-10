"""Fold splits — StratifiedGroupKFold, materialised once into ``splits.json``.

Stage-downstream scripts read the materialised split, so every stage sees
the same train/test indices. Re-seeding for the Stage-D fold-perturbation
sanity baseline uses ``build_kfold(..., seed=seed+1)`` separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold


@dataclass(frozen=True)
class FoldAssignment:
    fold_id: int
    train_study_codes: list[str]
    test_study_codes: list[str]


def build_kfold(
    targets: pd.DataFrame,
    *,
    target_col: str,
    n_folds: int,
    seed: int,
) -> list[FoldAssignment]:
    """Return a list of FoldAssignment, one per fold."""
    mask = targets[target_col].notna()
    df = targets.loc[mask].reset_index(drop=True)
    y = df[target_col].to_numpy()
    groups = df["study_code"].to_numpy()
    skf = StratifiedGroupKFold(n_splits=n_folds, shuffle=True, random_state=seed)
    folds: list[FoldAssignment] = []
    for fi, (train_idx, test_idx) in enumerate(skf.split(np.zeros(len(df)), y, groups=groups)):
        folds.append(
            FoldAssignment(
                fold_id=fi,
                train_study_codes=df.loc[train_idx, "study_code"].tolist(),
                test_study_codes=df.loc[test_idx, "study_code"].tolist(),
            )
        )
    return folds


def save_splits(folds: list[FoldAssignment], path: Path) -> Path:
    payload = {
        str(f.fold_id): {"train": f.train_study_codes, "test": f.test_study_codes}
        for f in folds
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_splits(path: Path) -> list[FoldAssignment]:
    payload = json.loads(path.read_text())
    out: list[FoldAssignment] = []
    for fid_str, d in sorted(payload.items(), key=lambda kv: int(kv[0])):
        out.append(
            FoldAssignment(
                fold_id=int(fid_str),
                train_study_codes=list(d["train"]),
                test_study_codes=list(d["test"]),
            )
        )
    return out


def fold_masks(
    folds: list[FoldAssignment], study_codes: pd.Series
) -> list[tuple[np.ndarray, np.ndarray]]:
    sc = study_codes.to_numpy()
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for f in folds:
        train = np.isin(sc, np.asarray(f.train_study_codes))
        test = np.isin(sc, np.asarray(f.test_study_codes))
        out.append((train, test))
    return out
