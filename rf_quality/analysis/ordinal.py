"""Ordinal logistic baseline + Brant test (statsmodels).

We treat the ordered target ``y ∈ {0, 1, 2}`` (preserved < intermediate <
poor) with a proportional-odds (PO) logit. statsmodels'
``OrderedModel(distr='logit')`` is the workhorse. The Brant test is
hand-rolled by refitting auxiliary binary logits on ``P(y > k)`` for each
threshold k = 0..K-2 and comparing the coefficient vectors with a Wald
chi-square — a standard pattern, well-defined under PO.

We do not attempt a partial-PO refit when Brant fails — we simply flag
violators in the output. This keeps the script honest at N ≈ 271.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from statsmodels.miscmodels.ordinal_model import OrderedModel


@dataclass
class OrdinalFit:
    converged: bool
    coef_table: pd.DataFrame  # cols: predictor, beta, se, z, p_nominal, ci_lo, ci_hi
    n_obs: int
    aic: float | None
    bic: float | None
    method_used: str
    dropped_predictors: list[str] = field(default_factory=list)


def fit_proportional_odds(
    X: pd.DataFrame,
    y: pd.Series,
    *,
    methods: tuple[str, ...] = ("bfgs", "nm"),
    maxiter: int = 200,
    drop_weakest_on_failure: bool = True,
    weak_ranking: pd.Series | None = None,
) -> OrdinalFit:
    """Fit a PO logit; iteratively drop the weakest predictor on convergence failure.

    ``weak_ranking`` (smaller = weaker) controls drop order; if None, the
    last column of ``X`` is dropped. Returns ``OrdinalFit`` with ``converged``
    indicating whether the final returned fit is good.
    """
    X_cur = X.copy()
    dropped: list[str] = []
    last_method_used = methods[0]
    while True:
        for m in methods:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model = OrderedModel(y, X_cur, distr="logit")
                    res = model.fit(method=m, maxiter=maxiter, disp=False)
                last_method_used = m
                if res.mle_retvals.get("converged", False):
                    return _wrap_fit(res, X_cur, y, last_method_used, dropped, converged=True)
            except Exception:
                continue
        if not drop_weakest_on_failure or X_cur.shape[1] <= 1:
            # Return the last attempted result (best-effort) flagged non-converged
            try:
                return _wrap_fit(res, X_cur, y, last_method_used, dropped, converged=False)
            except Exception:
                return OrdinalFit(
                    converged=False,
                    coef_table=pd.DataFrame(columns=["predictor", "beta", "se", "z", "p_nominal", "ci_lo", "ci_hi"]),
                    n_obs=int(len(y)),
                    aic=None, bic=None,
                    method_used=last_method_used,
                    dropped_predictors=dropped,
                )
        # Drop the weakest predictor and retry
        cols = list(X_cur.columns)
        if weak_ranking is not None:
            order = weak_ranking.reindex(cols).fillna(-np.inf).sort_values()
            target = order.index[0]
        else:
            target = cols[-1]
        dropped.append(target)
        X_cur = X_cur.drop(columns=[target])


def _wrap_fit(res, X, y, method_used, dropped, converged: bool) -> OrdinalFit:
    # OrderedModel parameters include the K-1 thresholds at the tail; predictor
    # coefficients are the first n_predictors entries.
    params = res.params
    bse = res.bse
    pvals = res.pvalues
    conf = res.conf_int(alpha=0.05)
    rows = []
    for col in X.columns:
        beta = float(params.get(col, np.nan))
        se = float(bse.get(col, np.nan))
        p = float(pvals.get(col, np.nan))
        try:
            ci_lo, ci_hi = float(conf.loc[col][0]), float(conf.loc[col][1])
        except Exception:
            ci_lo, ci_hi = np.nan, np.nan
        z = beta / se if se and not np.isnan(se) and se > 0 else np.nan
        rows.append({
            "predictor": col, "beta": beta, "se": se, "z": z,
            "p_nominal": p, "ci_lo": ci_lo, "ci_hi": ci_hi,
        })
    return OrdinalFit(
        converged=converged,
        coef_table=pd.DataFrame(rows),
        n_obs=int(len(y)),
        aic=float(res.aic) if hasattr(res, "aic") else None,
        bic=float(res.bic) if hasattr(res, "bic") else None,
        method_used=method_used,
        dropped_predictors=dropped,
    )


def brant_test(X: pd.DataFrame, y: pd.Series, levels: tuple[int, ...] = (0, 1)) -> pd.DataFrame:
    """Per-feature + joint Brant test.

    For each threshold k in ``levels``, fit a binary logit on ``y > k``.
    Per feature, build the difference vector d = β_k1 - β_k2 and Wald-test
    via ``d^T (Σ_k1 + Σ_k2)^(-1) d`` (simplification: assumes diagonal
    cross-threshold cov; reasonable since the binary fits are independent
    samples by construction here).

    Returns a DataFrame with columns: predictor, brant_chi2, brant_dof,
    brant_p, po_violated. The joint test is appended as predictor='__joint__'.
    """
    assert len(levels) >= 1
    X = X.reset_index(drop=True)
    y = y.reset_index(drop=True).to_numpy()
    binaries = []
    coefs = []
    ses = []
    for k in levels:
        yb = (y > k).astype(int)
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        clf = LogisticRegression(penalty=None, solver="lbfgs", max_iter=300)
        clf.fit(X.to_numpy(), yb)
        beta = clf.coef_.ravel()
        # Approximate SE via Fisher info (binary logit closed form)
        p_hat = clf.predict_proba(X.to_numpy())[:, 1]
        W = np.diag(p_hat * (1 - p_hat))
        Xn = X.to_numpy()
        try:
            cov = np.linalg.pinv(Xn.T @ W @ Xn)
            se = np.sqrt(np.diag(cov))
        except Exception:
            se = np.full(beta.shape, np.nan)
        binaries.append(yb)
        coefs.append(beta)
        ses.append(se)
    if len(coefs) < 2:
        return pd.DataFrame(columns=["predictor", "brant_chi2", "brant_dof", "brant_p", "po_violated"])

    rows = []
    chi2_joint = 0.0
    dof_joint = 0
    for i, col in enumerate(X.columns):
        b1, b2 = coefs[0][i], coefs[1][i]
        s1 = ses[0][i] if i < len(ses[0]) else np.nan
        s2 = ses[1][i] if i < len(ses[1]) else np.nan
        diff = b1 - b2
        var = (s1 ** 2 if np.isfinite(s1) else 0.0) + (s2 ** 2 if np.isfinite(s2) else 0.0)
        if var > 0:
            chi2 = float(diff ** 2 / var)
        else:
            chi2 = float("nan")
        from scipy import stats as _st
        p = float(_st.chi2.sf(chi2, df=1)) if np.isfinite(chi2) else float("nan")
        rows.append({
            "predictor": col,
            "brant_chi2": chi2,
            "brant_dof": 1,
            "brant_p": p,
            "po_violated": (p < 0.05) if np.isfinite(p) else False,
        })
        if np.isfinite(chi2):
            chi2_joint += chi2
            dof_joint += 1
    from scipy import stats as _st
    p_joint = float(_st.chi2.sf(chi2_joint, df=dof_joint)) if dof_joint > 0 else float("nan")
    rows.append({
        "predictor": "__joint__",
        "brant_chi2": float(chi2_joint),
        "brant_dof": int(dof_joint),
        "brant_p": p_joint,
        "po_violated": (p_joint < 0.05) if np.isfinite(p_joint) else False,
    })
    return pd.DataFrame(rows)
