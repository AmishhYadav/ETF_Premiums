import numpy as np, pandas as pd, json
from common import *
from censar import CensAR
d = load(); FUNDS = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']
def run(sym, start='2024-02-06', capdef='capped', X=None, lagobs=False):
    x = d[(d.symbol == sym) & (d.date >= start) & (d.date <= T0)].dropna(subset=['lp', 'ceil']).copy()
    k = x[capdef].values.copy() if capdef != 'none' else np.zeros(len(x), bool)
    y = np.where(k, x.ceil.values, x.lp.values); c = x.ceil.values
    Xm = None
    if X == 'cov':
        nav5 = np.log(x.nav_known / x.nav_known.shift(5)).fillna(0).values
        nb = d[d.symbol == 'NIFTYBEES'].set_index('date').close; n5 = np.log(nb / nb.shift(5)).reindex(x.date).fillna(0).values
        du = np.log(x.units / x.units.shift(1)).fillna(0).values
        Xm = np.column_stack([nav5, n5, du])
    m = CensAR(y, c, k, X=Xm, step=0.004); m.fit(maxiter=1500); a, rho, s, gam = m.unpack(m.th)
    ll, st, hid = m.filter(m.th, store=True)
    h = (st[1] * m.g).sum() if st[0] == 'den' else st[1]
    rng = np.random.default_rng(3); p0, paths = m.forecast(m.th, st, H=40, npaths=20000, rng=rng, Xf=None if Xm is None else np.zeros((40, Xm.shape[1])))
    avg = paths[:, 15:40].mean(axis=1); lo, hi = np.quantile(avg, [.05, .95]); lev = (np.exp(paths[:, 15:40]) - 1).mean()
    post = d[(d.symbol == sym) & (d.s >= 16) & (d.s <= 40)]; rl = post.lp.mean(); rlev = (np.exp(post.lp) - 1).mean()
    return dict(n=len(y), cap=float(k.mean()), rho=float(rho), sigma=float(s), gam=[float(g) for g in gam], hidden=float(100 * (np.exp(h) - 1)), f_lev=float(100 * lev), lo=float(100 * (np.exp(lo) - 1)), hi=float(100 * (np.exp(hi) - 1)),
                real=float(100 * rlev), verdict='above' if rl > hi else ('below' if rl < lo else 'inside'), induced=float(1 - lev / rlev))
d['hitcap'] = d.hit_up_official & (d.date <= T0)
out = {}
for sym in FUNDS:
    out[sym] = {'main': run(sym), 'cap=high at limit': run(sym, capdef='hitcap'), 'ignore censoring': run(sym, capdef='none'), 'from 2025': run(sym, start='2025-01-01'), 'covariates': run(sym, X='cov')}
    for k, v in out[sym].items(): print(sym, k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in v.items()})
json.dump(out, open(OUT + '/a3c.json', 'w'), indent=1)
