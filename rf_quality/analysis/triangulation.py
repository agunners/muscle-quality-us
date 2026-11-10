"""Stage F triangulation — joins B/C/D/E/LGBM into per-feature decisions.

Decision rule (round-2 5-tier ladder; supersedes round-1's 3-tier
{confirmed_candidate, borderline, drop}). The five tiers — strict to lenient
— make the gradient between full-pass and borderline visible at the
audience-facing layer, so claims like "this family has 2 stems at
confirmed_strong, 4 at borderline_strong, family-aggregate |ρ|=0.55" can be
made directly from ``stage_f_family_summary.csv``:

    if mech == "mismatch":
        decision = "borderline"
    elif sign_consistent_methods and not mismatch and risk ≤ cap:
        if   passes == 5: decision = "confirmed_strong"
        elif passes == 4: decision = "confirmed"
        elif passes == 3: decision = "borderline_strong"
        elif passes >= 2: decision = "borderline"
        else:             decision = "drop"
    elif passes >= borderline_methods_passed or risk == "high":
        decision = "borderline"
    else:
        decision = "drop"

Round-1 mapping (for backward reading of historical artifacts):
    round-1 confirmed_candidate ⊇ round-2 {confirmed_strong, confirmed}
                                  (when passes ≥ 3 + sign-consistent +
                                   risk ≤ cap, which all confirmed_candidate
                                   rows satisfy by construction).
    round-1 borderline           ⊇ round-2 {borderline_strong, borderline}.
    round-1 drop                  = round-2 drop.

``recommended_role`` encodes product-intent direction (cross-sectional /
longitudinal / qc_support / exploratory_only / both). The longitudinal tag
is intent only — no longitudinal test has been run.

Sign check across methods uses SIGNED outputs (B ρ, E Track A β, E Track B β).
LGBM permutation importance is unsigned; it contributes to
``methods_passed_count`` but not to ``sign_consistent_across_methods``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..registry import REGISTRY, Family, registry_dict
from .mechanistic import expected_sign, mechanism_verdict


SCANNER_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "n/a": 1}


def _risk_le(r: str, cap: str) -> bool:
    return SCANNER_RISK_RANK.get(r, 99) <= SCANNER_RISK_RANK.get(cap, 99)


def _stem_meta(stem: str) -> dict[str, object]:
    specs = registry_dict()
    s = specs.get(stem)
    if s is None:
        return {"family": "?", "scanner_risk": "?", "preferred": False, "role": "?", "aggregation_policy": "?"}
    return {
        "family": s.family.value,
        "scanner_risk": s.scanner_risk,
        "preferred": s.preferred,
        "role": s.role,
        "aggregation_policy": s.aggregation_policy,
    }


def assemble(
    *,
    stage_b: pd.DataFrame,
    stage_c_survivors: pd.DataFrame,
    stage_d: pd.DataFrame,
    stage_e_a: pd.DataFrame,
    stage_e_b: pd.DataFrame,
    stage_lgbm: pd.DataFrame,
    confirmed_strong_methods_passed: int = 5,
    confirmed_methods_passed: int = 4,
    borderline_strong_methods_passed: int = 3,
    borderline_methods_passed: int = 2,
    scanner_risk_cap: str = "medium",
) -> pd.DataFrame:
    sb = stage_b.set_index("stem", drop=False) if not stage_b.empty else pd.DataFrame().set_index(pd.Index([]))
    sc = set(stage_c_survivors["stem"]) if (stage_c_survivors is not None and not stage_c_survivors.empty) else set()
    sd = stage_d.set_index("stem", drop=False) if not stage_d.empty else pd.DataFrame().set_index(pd.Index([]))
    sea = stage_e_a.set_index("stem", drop=False) if not stage_e_a.empty else pd.DataFrame().set_index(pd.Index([]))
    seb = stage_e_b.set_index("stem", drop=False) if not stage_e_b.empty else pd.DataFrame().set_index(pd.Index([]))
    sl = stage_lgbm.set_index("stem", drop=False) if not stage_lgbm.empty else pd.DataFrame().set_index(pd.Index([]))

    stems = sorted(set(sb.index) | sc | set(sd.index) | set(sea.index) | set(seb.index) | set(sl.index))

    rows = []
    for stem in stems:
        meta = _stem_meta(stem)
        bh = bool(sb.loc[stem, "bh_survives"]) if stem in sb.index else False
        rho_col = "spearman_r_majority" if (stem in sb.index and "spearman_r_majority" in sb.columns) else "spearman_r_stouffer"
        rho = float(sb.loc[stem, rho_col]) if stem in sb.index else float("nan")
        sign_consistent_3 = bool(sb.loc[stem, "sign_consistent_3_raters"]) if stem in sb.index else False
        in_c = stem in sc
        d_keep = bool(sd.loc[stem, "keep"]) if (stem in sd.index and "keep" in sd.columns) else False
        d_freq = float(sd.loc[stem, "selection_freq"]) if stem in sd.index else float("nan")
        d_sign = float(sd.loc[stem, "sign_consistency"]) if stem in sd.index else float("nan")

        a_beta = float(sea.loc[stem, "beta"]) if stem in sea.index else float("nan")
        a_p = float(sea.loc[stem, "p_nominal"]) if stem in sea.index else float("nan")
        a_p_bh = float(sea.loc[stem, "p_bh"]) if (stem in sea.index and "p_bh" in sea.columns) else float("nan")
        a_lo = float(sea.loc[stem, "ci_lo"]) if stem in sea.index else float("nan")
        a_hi = float(sea.loc[stem, "ci_hi"]) if stem in sea.index else float("nan")

        b_beta = float(seb.loc[stem, "beta"]) if stem in seb.index else float("nan")
        b_p = float(seb.loc[stem, "p_nominal"]) if stem in seb.index else float("nan")
        b_p_bh = float(seb.loc[stem, "p_bh"]) if (stem in seb.index and "p_bh" in seb.columns) else float("nan")
        b_lo = float(seb.loc[stem, "ci_lo"]) if stem in seb.index else float("nan")
        b_hi = float(seb.loc[stem, "ci_hi"]) if stem in seb.index else float("nan")

        # Significance uses BH-adjusted p where available, nominal otherwise.
        a_sig_eff = (np.isfinite(a_p_bh) and a_p_bh < 0.05) if np.isfinite(a_p_bh) else (np.isfinite(a_p) and a_p < 0.05)
        b_sig_eff = (np.isfinite(b_p_bh) and b_p_bh < 0.05) if np.isfinite(b_p_bh) else (np.isfinite(b_p) and b_p < 0.05)
        a_b_same_sign = (np.isfinite(a_beta) and np.isfinite(b_beta) and (a_beta * b_beta > 0))

        l_imp = float(sl.loc[stem, "perm_importance_mean"]) if stem in sl.index else float("nan")
        l_keep = bool(sl.loc[stem, "keep"]) if (stem in sl.index and "keep" in sl.columns) else False

        passes = int(sum([
            bh,
            in_c,
            d_keep,
            (a_sig_eff or b_sig_eff),
            l_keep,
        ]))

        # Sign consistency across SIGNED methods only (B ρ, E-A β, E-B β).
        # LGBM importance is unsigned → excluded from the sign-check.
        signed_views: list[float] = []
        if np.isfinite(rho):
            signed_views.append(float(np.sign(rho)))
        if np.isfinite(a_beta) and a_sig_eff:
            signed_views.append(float(np.sign(a_beta)))
        if np.isfinite(b_beta) and b_sig_eff:
            signed_views.append(float(np.sign(b_beta)))
        sign_consistent_methods = (len(signed_views) >= 2 and len(set(signed_views)) == 1)

        # Mechanistic plausibility: compare observed ρ sign to the pre-registered expectation.
        observed_sign = int(np.sign(rho)) if np.isfinite(rho) else 0
        mech = mechanism_verdict(stem, observed_sign)

        # recommended role
        if a_sig_eff and b_sig_eff and a_b_same_sign:
            role = "both"
        elif a_sig_eff and not b_sig_eff:
            role = "cross_sectional_candidate"
        elif (not a_sig_eff and not b_sig_eff) and bh and meta["role"] == "support":
            role = "qc_support"
        elif (not bh) and (not in_c) and (not d_keep) and (not l_keep):
            role = "exploratory_only"
        elif meta["family"] in ("geometry", "fat", "depth_ratio") and (sign_consistent_methods or sign_consistent_3):
            role = "longitudinal_candidate"
        else:
            role = "exploratory_only"

        # 5-tier decision ladder (round-2). The mechanistic-mismatch and
        # high-scanner-risk branches still demote to borderline (note:
        # round-2 γ-pass set every EXPECTED_SIGN to 0, so mech="mismatch"
        # is unreachable in round-2 unless a future round restores priors).
        #
        # Within the sign-consistent / risk≤cap branch, the tier follows
        # methods_passed_count strictly:
        #   5 → confirmed_strong, 4 → confirmed, 3 → borderline_strong,
        #   2 → borderline, <2 → drop.
        risk = str(meta.get("scanner_risk", "n/a"))
        if mech == "mismatch":
            decision = "borderline"
        elif sign_consistent_methods and _risk_le(risk, scanner_risk_cap):
            if passes >= confirmed_strong_methods_passed:
                decision = "confirmed_strong"
            elif passes >= confirmed_methods_passed:
                decision = "confirmed"
            elif passes >= borderline_strong_methods_passed:
                decision = "borderline_strong"
            elif passes >= borderline_methods_passed:
                decision = "borderline"
            else:
                decision = "drop"
        elif passes >= borderline_methods_passed or risk == "high":
            # ≥ borderline-threshold passes (or high scanner risk) but signed
            # methods disagree → demote to borderline rather than drop.
            # Captures features like ``ei_to_fat_ratio`` whose Stage-E β
            # collapses once demographics absorb the signal — a valid
            # hypothesis-generating finding, not a rejection.
            decision = "borderline"
        else:
            decision = "drop"

        rows.append({
            "stem": stem,
            **meta,
            "stage_b_spearman": rho,
            "stage_b_bh_survives": bh,
            "stage_b_sign_consistent_3_raters": sign_consistent_3,
            "stage_c_representative": in_c,
            "stage_d_selection_freq": d_freq,
            "stage_d_sign_consistency": d_sign,
            "stage_d_keep": d_keep,
            "stage_e_track_a_beta": a_beta,
            "stage_e_track_a_ci": f"[{a_lo:.3f}, {a_hi:.3f}]" if np.isfinite(a_lo) else "",
            "stage_e_track_a_p_bh": a_p_bh,
            "stage_e_track_b_beta": b_beta,
            "stage_e_track_b_ci": f"[{b_lo:.3f}, {b_hi:.3f}]" if np.isfinite(b_lo) else "",
            "stage_e_track_b_p_bh": b_p_bh,
            "stage_e_sign_consistent_A_vs_B": a_b_same_sign,
            "stage_lgbm_perm_importance": l_imp,
            "stage_lgbm_keep": l_keep,
            "methods_passed_count": int(passes),
            "sign_consistent_across_signed_methods": sign_consistent_methods,
            "expected_sign": expected_sign(stem),
            "observed_sign": observed_sign,
            "mechanism_verdict": mech,
            "recommended_role": role,
            "decision": decision,
        })

    df = pd.DataFrame(rows)
    decision_rank = {
        "confirmed_strong": 0,
        "confirmed": 1,
        "borderline_strong": 2,
        "borderline": 3,
        "drop": 4,
    }
    df = df.sort_values(
        ["decision", "methods_passed_count", "stage_b_spearman"],
        ascending=[True, False, False],
        key=lambda s: s.map(decision_rank) if s.name == "decision" else s,
    ).reset_index(drop=True)
    return df


def family_summary(per_feature: pd.DataFrame) -> pd.DataFrame:
    """Round-2: per-family rollup carrying the 5-tier counts plus
    family-aggregate signal scores under two weighting schemes.

    Two signal columns travel together for honest reading:

      * ``family_aggregate_rho`` — methods-weighted mean of |stage_b_spearman|
        across family members (weights = ``methods_passed_count``). Reads as
        "what level of cohort signal does the family carry, weighted by how
        many statistical voters supported each member?" **Caveat:** when a
        family has one strong rep (passes=5) and many drops (passes=0), the
        weighted mean is dominated by the rep. The metric will look like
        family-level signal density but is rep-dominance.

      * ``family_aggregate_rho_unweighted`` — unweighted mean of
        |stage_b_spearman| across the family members **that entered Stage F**
        (i.e. that survived Stage B BH-FDR; Stage F drops Stage-B-rejected
        stems before triangulating). Reads as "what is the family's average
        solo cohort signal among its BH-surviving members?" The gap between
        weighted and unweighted is the rep-dominance diagnostic: large gap
        = rep-dominated; small gap = signal-dense across the family.

    Note: a third metric — fraction of *registry-defined* family members
    that survived BH — would require passing the full Stage B df into this
    function. Not currently plumbed; the response-pack writer reads
    ``n_drop / n_features`` from this rollup and Stage B BH counts from
    ``stage_b_audit.json`` to reconstruct breadth qualitatively.

    **Reading rule for the response pack** (per `acts_567_authoring_playbook.md`
    §"Family-level signal"): **NEVER quote `family_aggregate_rho` alone.**
    Always co-quote with ``n_drop / n_features`` AND
    ``family_aggregate_rho_unweighted``. A family with
    ``family_aggregate_rho=0.6, family_aggregate_rho_unweighted=0.15,
    n_drop=10/12`` is rep-dominated; a family with
    ``family_aggregate_rho=0.55, family_aggregate_rho_unweighted=0.45,
    n_drop=2/12`` is genuinely signal-dense.

    Methods-weighted mean falls back to unweighted when every member has
    ``methods_passed_count == 0`` (sum-of-weights guard).
    """
    cols = [
        "family", "n_features",
        "n_confirmed_strong", "n_confirmed",
        "n_borderline_strong", "n_borderline", "n_drop",
        "n_confirmed_total", "n_borderline_total",
        "family_max_methods", "family_aggregate_rho",
        "family_aggregate_rho_unweighted",
        "any_member_in_c", "max_selection_freq", "dominant_scanner_risk",
        "n_mechanism_match", "n_mechanism_mismatch",
    ]
    if per_feature.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for fam, grp in per_feature.groupby("family", sort=False):
        n_strong = int((grp["decision"] == "confirmed_strong").sum())
        n_conf = int((grp["decision"] == "confirmed").sum())
        n_bord_strong = int((grp["decision"] == "borderline_strong").sum())
        n_bord = int((grp["decision"] == "borderline").sum())
        n_drop_ = int((grp["decision"] == "drop").sum())

        rho_abs = grp["stage_b_spearman"].abs()
        weights = grp["methods_passed_count"].astype(float)
        finite = rho_abs.notna() & weights.notna()
        if finite.any():
            w = weights[finite]
            r = rho_abs[finite]
            wsum = float(w.sum())
            agg_rho = float((w * r).sum() / wsum) if wsum > 0 else float(r.mean())
            agg_rho_unweighted = float(r.mean())
        else:
            agg_rho = float("nan")
            agg_rho_unweighted = float("nan")

        rows.append({
            "family": fam,
            "n_features": int(len(grp)),
            "n_confirmed_strong": n_strong,
            "n_confirmed": n_conf,
            "n_borderline_strong": n_bord_strong,
            "n_borderline": n_bord,
            "n_drop": n_drop_,
            "n_confirmed_total": n_strong + n_conf,
            "n_borderline_total": n_bord_strong + n_bord,
            "family_max_methods": (
                int(grp["methods_passed_count"].max())
                if grp["methods_passed_count"].notna().any() else 0
            ),
            "family_aggregate_rho": agg_rho,
            "family_aggregate_rho_unweighted": agg_rho_unweighted,
            "any_member_in_c": bool(grp["stage_c_representative"].any()),
            "max_selection_freq": (
                float(grp["stage_d_selection_freq"].max())
                if grp["stage_d_selection_freq"].notna().any() else float("nan")
            ),
            "dominant_scanner_risk": (
                grp["scanner_risk"].mode().iloc[0]
                if not grp["scanner_risk"].mode().empty else "n/a"
            ),
            "n_mechanism_match": int((grp["mechanism_verdict"] == "match").sum()),
            "n_mechanism_mismatch": int((grp["mechanism_verdict"] == "mismatch").sum()),
        })
    return (
        pd.DataFrame(rows)
        .sort_values(["n_confirmed_total", "family_aggregate_rho"], ascending=[False, False])
        .reset_index(drop=True)
    )
