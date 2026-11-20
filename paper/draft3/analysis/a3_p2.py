import numpy as np, pandas as pd, sys, time
from common import *
from censar import CensAR, simulate
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
d = load(); res = {}
FUNDS = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']
# ---------- model-free: first free close
rows = []
for sym in FUNDS:
    x = d[(d.symbol == sym) & (d.s >= 0)].set_index('s')
    free = x[(x.index >= 1) & (~x.hit_up)]
    s1 = int(free.index[0])
    # first close not constrained at the end of the day: last trade not at the limit and close below limit
    lim_end = x.band_high_max.where(x.hit_up_max, x.band_high)  # crude
    rows.append(dict(symbol=sym, prem_T0=100 * x.loc[0, 'prem_known'], ceil_T0=100 * (np.exp(x.loc[0, 'ceil']) - 1), capped_T0=bool(x.loc[0, 'capped']),
                     s_free=s1, prem_free=100 * x.loc[s1, 'prem_known'], prem_s1=100 * x.loc[1, 'prem_known'], hit1=bool(x.loc[1, 'hit_up']),
                     prem_max=100 * x.loc[1:, 'prem_known'].max(), s_max=int(x.loc[1:, 'prem_known'].idxmax()),
                     prem_16_40=100 * (np.exp(x.loc[16:40, 'lp'].mean()) - 1), lp_16_40=100 * x.loc[16:40, 'lp'].mean(), prem_last=100 * x.prem_known.iloc[-1],
                     prem_41_47=100 * x.loc[41:47, 'prem_known'].mean()))
t = pd.DataFrame(rows); print(t.round(1).to_string()); res['free'] = t.round(3).to_dict('records')

# ---------- censored AR on the official-band era
out = {}
START = '2024-02-06'
for sym in FUNDS:
    x = d[(d.symbol == sym) & (d.date >= START) & (d.date <= T0)].dropna(subset=['lp', 'ceil']).copy()
    post = d[(d.symbol == sym) & (d.s >= 1) & (d.s <= 47)].set_index('s')
    y = x.lp.values; c = x.ceil.values; k = x.capped.values.copy()
    y = np.where(k, c, y)   # on capped days the observation is the ceiling
    m = CensAR(y, c, k, step=0.004); t0 = time.time(); r = m.fit(); a, rho, s, _ = m.unpack(m.th)
    ll, st, hid = m.filter(m.th, store=True)
    rng = np.random.default_rng(7); p0, paths = m.forecast(m.th, st, H=40, npaths=20000, rng=rng)
    avg = paths[:, 15:40].mean(axis=1)                      # sessions 16-40
    f_mean = avg.mean(); lo, hi = np.quantile(avg, [.05, .95])
    lev = (np.exp(paths[:, 15:40]) - 1).mean()             # forecast mean premium level
    real_lp = post.loc[16:40, 'lp'].mean(); real_lev = (np.exp(post.loc[16:40, 'lp']) - 1).mean()
    # naive: OLS AR on observed premium
    A = np.column_stack([np.ones(len(y) - 1), y[:-1]]); b = np.linalg.lstsq(A, y[1:], rcond=None)[0]; sn = np.std(y[1:] - A @ b)
    hT = (st[1] * m.g).sum() if st[0] == 'den' else st[1]
    hq = None
    if st[0] == 'den':
        cdf = np.cumsum(st[1]); hq = [float(m.g[np.searchsorted(cdf, q)]) for q in (.05, .5, .95)]
    out[sym] = dict(n=len(y), capped_share=float(k.mean()), a=a, rho=rho, sigma=s, mu=a / (1 - rho), ll=m.ll, nfev=r.nfev,
                    rho_ols=b[1], sigma_ols=sn, ceil_T0=float(c[-1]), obs_T0=float(y[-1]), capped_T0=bool(k[-1]),
                    hidden_T0=float(hT), hidden_T0_q=hq, hidden_T0_prem=100 * (np.exp(hT) - 1),
                    f_mean=float(f_mean), f_lo=float(lo), f_hi=float(hi), f_lev=float(100 * lev), real_lp=float(real_lp), real_lev=float(100 * real_lev),
                    induced_share=float(1 - lev / real_lev), verdict=('above' if real_lp > hi else ('below' if real_lp < lo else 'inside')),
                    th=[float(v) for v in m.th], secs=time.time() - t0)
    o = out[sym]
    print(sym, 'n', o['n'], 'capped', round(o['capped_share'], 3), 'rho', round(rho, 4), 'sigma', round(s, 4), 'mu', round(o['mu'], 3), '| rho_ols', round(b[1], 4),
          '| ceil', round(100 * (np.exp(c[-1]) - 1), 1), 'hidden', round(o['hidden_T0_prem'], 1), 'q', None if hq is None else [round(100 * (np.exp(v) - 1), 1) for v in hq],
          '| forecast 16-40 lev', round(o['f_lev'], 1), 'band', round(100 * (np.exp(lo) - 1), 1), round(100 * (np.exp(hi) - 1), 1), '| realised', round(o['real_lev'], 1),
          '| induced', round(o['induced_share'], 2), o['verdict'], round(o['secs'], 1), 's')
res['censar'] = out
save('a3', res)
