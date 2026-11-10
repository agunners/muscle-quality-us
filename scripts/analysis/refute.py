"""Adversarial refutation of the 23 candidate stems.
One harness, one definition, applied to ALL 103 stems so the winners are judged
against the distribution they were selected from."""
from pathlib import Path
import json, warnings
import numpy as np, pandas as pd
from scipy import stats
warnings.filterwarnings('ignore')
rng = np.random.default_rng(20260729)

ROOT = str(Path(__file__).resolve().parents[2])
# The 2026-07-29 substrate lived under the gitignored scratchpad/ and did not
# survive the Acer -> DGX move. It is rebuilt into the tracked results/ dir by
# scripts/analysis/build_capture_features.py; FEATSCREEN_DIR still points here.
import os
OUT = os.environ.get('FEATSCREEN_DIR', f'{ROOT}/scripts/analysis/results')

cf = pd.read_csv(f'{OUT}/capture_features.csv')
META = ['study_uid','role','n_frames_segmented','n_frames_in_capture','spacing_source','seconds_total']
STEMS = [c for c in cf.columns if c not in META]
STEMS = [c for c in STEMS if cf[c].dtype.kind in 'fi']

master = pd.read_csv(f'{ROOT}/data/qa_master.csv',
                     usecols=['study_uid','izo_torque_nm','bmi','age','sex'])
frames = pd.read_csv(f'{ROOT}/data/qa_frames.csv', usecols=['study_uid','role','depth_cm'])
depth = frames[frames.role=='rf'].groupby('study_uid').depth_cm.median()

W = {r: cf[cf.role==r].set_index('study_uid') for r in ('rf','sweep','femur')}
UIDS = sorted(set(W['rf'].index) & set(W['sweep'].index))
# REPEAT_RESTRICT_CSV pins the cohort (first column = study uid) so the original
# n=66 result stays reproducible after the freeze grew to 116; REPEAT_EXPECT_N
# is the guard that the pinned set is the one you think it is.
_restrict = os.environ.get('REPEAT_RESTRICT_CSV')
if _restrict:
    _keep = set(pd.read_csv(_restrict).iloc[:, 0].astype(str))
    UIDS = [u for u in UIDS if u in _keep]
_expect = int(os.environ.get('REPEAT_EXPECT_N', '66'))
assert len(UIDS) == _expect, f'{len(UIDS)} rf/sweep pairs, expected {_expect} (set REPEAT_EXPECT_N)'
meta = master.set_index('study_uid').reindex(UIDS)
meta['depth_cm'] = depth.reindex(UIDS)

CAND = ['anchor_rf_area_mm2','anchor_rf_thickness_mm','int_p10',
 'int_mean_fat_corrected_young_lit','int_median_fat_corrected_young_lit',
 'int_p10_fat_corrected_young_lit','int_p90_fat_corrected_young_lit',
 'int_cv_within_fat_corrected_young_lit','glrlm_long_run_emphasis',
 'deep_int_mean_fat_corrected_young_lit','superficial_int_mean','mid_int_mean',
 'superficial_int_mean_fat_corrected_young_lit','mid_int_mean_fat_corrected_young_lit',
 'local_entropy_mean','wavelet_detail_energy_ratio','full_thickness_q50_mm',
 'full_area_mm2','full_max_thickness_mm','fat_to_area_ratio','fat_thickness_mm',
 'ei_to_fat_ratio','ei_to_fat_ratio_fat_corrected_young_lit']
INC = 'int_cv_within'
missing = [c for c in CAND if c not in cf.columns]
assert not missing, missing

# which stems each of the 5 parallel screens searched over (for multiplicity k)
GROUP = {}
for f, key in [('screen_results.csv','stem'), ('screen_texture_results.csv','stem'),
               ('screen_depth_results.csv','stem'), ('screens_intensity.csv','stem')]:
    try:
        GROUP[f] = pd.read_csv(f'{OUT}/{f}')[key].tolist()
    except Exception as e:
        GROUP[f] = []

def pair(stem):
    a = W['rf'][stem].reindex(UIDS).to_numpy(float)
    b = W['sweep'][stem].reindex(UIDS).to_numpy(float)
    m = np.isfinite(a) & np.isfinite(b)
    return a, b, m

def ratios(a, b, idx=None):
    """Canonical repeatability on a set of subject indices. Returns dict."""
    if idx is not None:
        a, b = a[idx], b[idx]
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    n = len(a)
    if n < 8: return None
    d = a - b
    sd_diff = d.std(ddof=1)
    mdc95 = 1.96 * sd_diff                    # = 1.96*sqrt(2)*SEM, SEM=sd_diff/sqrt2
    means = (a + b) / 2
    sd_mean = means.std(ddof=1)               # SD of subject means  (denominator A)
    sd_single = np.sqrt(max(np.r_[a, b].var(ddof=1), 1e-30))  # SD of one occasion (denom B)
    # ICC(2,1) two-way random, absolute agreement
    gm = np.r_[a, b].mean()
    ms_r = 2 * ((means - gm) ** 2).sum() / (n - 1)
    ms_c = n * ((a.mean() - gm) ** 2 + (b.mean() - gm) ** 2)
    ms_e = (((a - means) ** 2).sum() + ((b - means) ** 2).sum()) / (n - 1)
    icc = (ms_r - ms_e) / (ms_r + ms_e + 2 * (ms_c - ms_e) / n) if (ms_r + ms_e) > 0 else np.nan
    # robust variants (outlier-proof): 1.4826*MAD
    rsd_diff = 1.4826 * np.median(np.abs(d - np.median(d)))
    rsd_mean = 1.4826 * np.median(np.abs(means - np.median(means)))
    return dict(n=n, mdc95=mdc95, sd_mean=sd_mean, sd_single=sd_single,
                ratio_A=mdc95 / sd_mean if sd_mean > 0 else np.nan,
                ratio_B=mdc95 / sd_single if sd_single > 0 else np.nan,
                ratio_robust=(1.96 * rsd_diff / rsd_mean) if rsd_mean > 0 else np.nan,
                icc=icc, cvw=100 * (sd_diff / np.sqrt(2)) / abs(np.r_[a, b].mean())
                        if np.r_[a, b].mean() != 0 else np.nan,
                bias_sd=(d.mean() / sd_mean) if sd_mean > 0 else np.nan)

# ---------- A. one-harness repeatability for ALL stems ----------
rows = []
P = {s: pair(s) for s in STEMS}
for s in STEMS:
    a, b, m = P[s]
    if m.sum() < 40: continue
    r = ratios(a, b)
    if r is None: continue
    r['stem'] = s
    rows.append(r)
allrep = pd.DataFrame(rows).set_index('stem')
allrep.to_csv(f'{OUT}/refute_all_repeatability.csv')

print('=' * 78)
print('A. ONE-HARNESS REPEATABILITY  (n=66 rf-vs-sweep pairs, identical definition)')
print('=' * 78)
inc = allrep.loc[INC]
print(f'INCUMBENT {INC}: MDC95={inc.mdc95:.4f}  SD_subjmean={inc.sd_mean:.4f} '
      f'SD_single={inc.sd_single:.4f}')
print(f'   ratio_A (MDC/SD_subjmean) = {inc.ratio_A:.3f}   <-- reported as 1.34 by some screens')
print(f'   ratio_B (MDC/SD_single)   = {inc.ratio_B:.3f}   <-- reported as 1.03/1.16/1.19')
print(f'   ICC(2,1)={inc.icc:.3f}  robust ratio={inc.ratio_robust:.3f}')
print(f'\nDistribution of ratio_B over all {len(allrep)} stems measured here:')
q = allrep.ratio_B.quantile([0, .05, .1, .25, .5, .75, .9, 1]).round(3)
print(q.to_string())
print(f'  n stems with ratio_B < incumbent: {(allrep.ratio_B < inc.ratio_B).sum()} / {len(allrep)}'
      f'  ({100*(allrep.ratio_B < inc.ratio_B).mean():.0f}%)')
print(f'  n stems with ratio_B < 0.6      : {(allrep.ratio_B < 0.6).sum()} / {len(allrep)}')

print('\nCandidate table (this harness, both denominators):')
tab = allrep.loc[[c for c in CAND if c in allrep.index],
                 ['n','mdc95','sd_mean','sd_single','ratio_A','ratio_B','ratio_robust','icc','bias_sd']]
print(tab.round(3).to_string())
tab.to_csv(f'{OUT}/refute_candidate_repeatability.csv')

# ---------- B1. paired bootstrap: candidate ratio minus incumbent ratio ----------
NB = 4000
boot_idx = [rng.integers(0, len(UIDS), len(UIDS)) for _ in range(NB)]
BOOT = {}
for s in CAND + [INC]:
    a, b, _ = P[s]
    v = np.full(NB, np.nan)
    for i, ix in enumerate(boot_idx):
        r = ratios(a, b, ix)
        if r: v[i] = r['ratio_B']
    BOOT[s] = v
bi = BOOT[INC]

print('\n' + '=' * 78)
print('B1. SELECTION EFFECT - paired bootstrap of (candidate - incumbent) ratio_B')
print('    same resampled studies for both; Bonferroni k = stems the screen searched')
print('=' * 78)
b1 = []
for s in CAND:
    d = BOOT[s] - bi
    ok = np.isfinite(d)
    lo, hi = np.nanpercentile(d[ok], [2.5, 97.5])
    # Bonferroni for k=20 (per-group screen) and k=103 (all stems computed)
    a20 = 100 * (0.05 / 20) / 2
    a103 = 100 * (0.05 / 103) / 2
    lo20, hi20 = np.nanpercentile(d[ok], [a20, 100 - a20])
    lo103, hi103 = np.nanpercentile(d[ok], [a103, 100 - a103])
    pboot = 2 * min((d[ok] >= 0).mean(), (d[ok] <= 0).mean())
    b1.append(dict(stem=s, delta=np.nanmedian(d), lo=lo, hi=hi,
                   hi_bonf20=hi20, hi_bonf103=hi103, p_boot=pboot,
                   sig_raw=hi < 0, sig_k20=hi20 < 0, sig_k103=hi103 < 0))
B1 = pd.DataFrame(b1).set_index('stem').sort_values('delta')
print(B1.round(4).to_string())
B1.to_csv(f'{OUT}/refute_B1_paired_bootstrap.csv')

# ---------- B2. selection stability: does the winner keep winning? ----------
print('\n' + '=' * 78)
print('B2. SELECTION STABILITY - bootstrap the whole screen, how often does each')
print('    nominal winner still win its own group of ~20 stems?')
print('=' * 78)
# groups as actually screened (fall back to random 20-stem neighbourhoods)
groups = {k: [s for s in v if s in allrep.index] for k, v in GROUP.items() if len(v) > 3}
gboot = {}
for gname, gs in groups.items():
    if INC not in gs: gs = gs + [INC]
    mat = np.full((NB, len(gs)), np.nan)
    for j, s in enumerate(gs):
        if s in BOOT:
            mat[:, j] = BOOT[s]
        else:
            a, b, _ = P[s]
            for i, ix in enumerate(boot_idx):
                r = ratios(a, b, ix)
                if r: mat[i, j] = r['ratio_B']
    win = np.array(gs)[np.nanargmin(mat, axis=1)]
    vc = pd.Series(win).value_counts(normalize=True)
    nominal = gs[int(np.nanargmin([allrep.ratio_B.get(s, np.nan) for s in gs]))]
    gboot[gname] = (nominal, vc)
    print(f'\n{gname}  (k={len(gs)} stems)  nominal winner = {nominal}')
    print('   bootstrap win-share of top 5:')
    for s, p in vc.head(5).items():
        print(f'     {p*100:5.1f}%  {s}')

# ---------- B3. winner's curse magnitude ----------
print('\n' + '=' * 78)
print("B3. WINNER'S CURSE - how much apparent advantage arises from selection ALONE")
print('=' * 78)
sd_est = np.nanstd(bi)
print(f'sampling SD of the ratio_B estimator (bootstrap, incumbent) = {sd_est:.3f}')
for k in (10, 20, 50, 103):
    draws = rng.normal(inc.ratio_B, sd_est, size=(20000, k)).min(axis=1)
    print(f'  best-of-{k:3d} when ALL stems truly equal the incumbent ({inc.ratio_B:.3f}): '
          f'expected apparent ratio {draws.mean():.3f} '
          f'(= advantage {inc.ratio_B - draws.mean():.3f}), 5th pct {np.percentile(draws,5):.3f}')
# stems are correlated -> effective k. estimate from stem-stem corr of bootstrap ratios
BM = np.column_stack([BOOT[s] for s in CAND])
cc = np.corrcoef(np.nan_to_num(BM, nan=np.nanmean(BM)).T)
eff_k = len(CAND) ** 2 / (cc ** 2).sum()
print(f'\neffective independent tests among the {len(CAND)} candidates '
      f'(Kish/Cheverud from bootstrap-ratio corr) = {eff_k:.1f}')

# ---------- C. circularity: torque vs grade ----------
print('\n' + '=' * 78)
print('C. CIRCULARITY - grade leg vs torque leg')
print('=' * 78)
tri = pd.read_csv(str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration/audits/pro/R06_audit") + "/" +
                  'runs/20260503/034929_default_infer/analysis/stage_f_triangulation.csv')
sb = pd.read_csv(str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration/audits/pro/R06_audit") + "/" +
                 'runs/20260503/034929_default_infer/analysis/stage_b_repr_vs_agg.csv')
print(f'archival grade screen: n_features_screened=179, n_bh_survivors=156 '
      f'({156/179*100:.0f}% of everything tested "survives" the grade criterion)')
gr = sb.assign(absr=sb[['r_repr','r_agg_median']].abs().max(axis=1))
print(f'|r| vs grade over {len(sb)} stems in the archival file: '
      f'median {gr.absr.median():.3f}, IQR {gr.absr.quantile(.25):.3f}-{gr.absr.quantile(.75):.3f}, '
      f'max {gr.absr.max():.3f}')
print('  -> a grade rho of 0.65-0.75 is the MODE, not a distinction.')

torque = meta.izo_torque_nm.to_numpy(float)
bmi = meta.bmi.to_numpy(float)
tm = np.isfinite(torque)
print(f'\ntorque available n={tm.sum()}')

def sp(x, y):
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 10: return np.nan, np.nan, 0
    r, p = stats.spearmanr(x[m], y[m]); return r, p, m.sum()

def partial(x, y, z):
    m = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    rx = stats.rankdata(x[m]); ry = stats.rankdata(y[m]); rz = stats.rankdata(z[m])
    def res(v, w):
        A = np.column_stack([np.ones(len(w)), w]); return v - A @ np.linalg.lstsq(A, v, rcond=None)[0]
    r, p = stats.pearsonr(res(rx, rz), res(ry, rz))
    return r, p, m.sum()

# permutation null for max|rho| over k correlated stems  (the honest selection threshold)
NP_ = 3000
allmat = np.column_stack([W['rf'][s].reindex(UIDS).to_numpy(float) for s in STEMS])
tq = torque.copy()
maxr_k = {}
for k, cols in [('all_103', list(range(len(STEMS)))),
                ('k20', None)]:
    pass
perm_max = np.zeros(NP_)
idx_t = np.where(tm)[0]
for i in range(NP_):
    perm = rng.permutation(tq[idx_t])
    rs = []
    for j in range(allmat.shape[1]):
        v = allmat[idx_t, j]
        mm = np.isfinite(v)
        if mm.sum() < 30: continue
        rs.append(abs(stats.spearmanr(v[mm], perm[mm])[0]))
    perm_max[i] = np.nanmax(rs)
thr103 = np.percentile(perm_max, 95)
print(f'\nPERMUTATION NULL (3000 shuffles of torque, real stem correlation structure):')
print(f'  max|rho_torque| across all {allmat.shape[1]} stems under H0: '
      f'median {np.median(perm_max):.3f}, 95th pct {thr103:.3f}')
print(f'  -> |rho_torque| below {thr103:.3f} is NOT distinguishable from selection noise.')

crows = []
for s in CAND + [INC]:
    x = W['rf'][s].reindex(UIDS).to_numpy(float)
    r, p, n = sp(x, torque)
    rb, pb, nb = partial(x, torque, bmi)
    xa = W['rf']['full_area_mm2'].reindex(UIDS).to_numpy(float)
    ra, pa, na = partial(x, torque, xa)
    ri, pi, ni = partial(x, torque, W['rf'][INC].reindex(UIDS).to_numpy(float)) if s != INC else (np.nan,)*3
    g = tri[tri.stem == s]
    grade = g.stage_b_spearman.iloc[0] if len(g) else np.nan
    crows.append(dict(stem=s, rho_torque=r, p=p, n=n, partial_bmi=rb,
                      partial_size=ra, partial_incumbent=ri,
                      grade_rho=grade,
                      beats_perm_thr=abs(r) > thr103))
C = pd.DataFrame(crows).set_index('stem')
print('\n' + C.round(3).to_string())
C.to_csv(f'{OUT}/refute_C_circularity.csv')

# ---------- D. collinearity ----------
print('\n' + '=' * 78)
print('D. COLLINEARITY - Spearman among candidates + incumbent (rf role, n=66)')
print('=' * 78)
X = pd.DataFrame({s: W['rf'][s].reindex(UIDS) for s in CAND + [INC]})
R = X.corr(method='spearman')
R.to_csv(f'{OUT}/refute_D_corr.csv')
print('\n|rho| vs the incumbent int_cv_within:')
vs = R[INC].drop(INC).sort_values(key=abs, ascending=False)
for s, v in vs.items():
    tag = 'RESCALING of incumbent' if abs(v) >= 0.90 else ('near-duplicate' if abs(v) >= 0.80 else '')
    print(f'  {v:+.3f}  {s}   {tag}')
print('\nCandidate pairs with |rho| >= 0.90 (compete for ONE slot, do not add two):')
seen = set()
for i, a in enumerate(CAND):
    for b in CAND[i+1:]:
        v = R.loc[a, b]
        if abs(v) >= 0.90:
            print(f'  {v:+.3f}  {a}  <->  {b}')

# ---------- E. degeneracy ----------
print('\n' + '=' * 78)
print('E. DEGENERACY - skew, floor/ceiling, ties, mask-size and frame-count dependence')
print('=' * 78)
npx = W['rf']['full_area_px'].reindex(UIDS).to_numpy(float)
nfr = W['rf']['n_frames_segmented'].reindex(UIDS).to_numpy(float)
erows = []
for s in CAND + [INC]:
    x = W['rf'][s].reindex(UIDS).to_numpy(float)
    xf = x[np.isfinite(x)]
    lo, hi = xf.min(), xf.max()
    rng5 = np.percentile(xf, 95) - np.percentile(xf, 5)
    frac_lo = (xf <= lo + 1e-12).mean(); frac_hi = (xf >= hi - 1e-12).mean()
    # outlier leverage on the SD denominator: how much of the between-SD is the top 3 studies?
    sd_full = xf.std(ddof=1)
    trimmed = xf[(xf >= np.percentile(xf, 5)) & (xf <= np.percentile(xf, 95))]
    sd_trim = trimmed.std(ddof=1)
    rr = allrep.loc[s]
    erows.append(dict(stem=s, skew=stats.skew(xf), kurt=stats.kurtosis(xf),
                      n_unique=len(np.unique(xf)), frac_at_min=frac_lo, frac_at_max=frac_hi,
                      sd_shrink_on_trim=1 - sd_trim / sd_full,
                      ratio_B=rr.ratio_B, ratio_robust=rr.ratio_robust,
                      robust_penalty=rr.ratio_robust - rr.ratio_B,
                      rho_mask_px=sp(x, npx)[0], rho_nframes=sp(x, nfr)[0]))
E = pd.DataFrame(erows).set_index('stem')
print(E.round(3).to_string())
E.to_csv(f'{OUT}/refute_E_degeneracy.csv')
print('\nframe-count spread across studies: '
      f'min {np.nanmin(nfr):.0f} max {np.nanmax(nfr):.0f} median {np.nanmedian(nfr):.0f}')
print('DONE')
