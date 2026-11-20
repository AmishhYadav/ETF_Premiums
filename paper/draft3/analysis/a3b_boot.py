"""Parametric bootstrap of the whole P2 procedure under the estimated null of pure revelation."""
import numpy as np, pandas as pd, json, sys, time, os
from multiprocessing import Pool
from common import *
from censar import CensAR, simulate
B = int(sys.argv[1]) if len(sys.argv) > 1 else 299
FUNDS = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']
a3 = json.load(open(os.path.join(OUT, 'a3.json')))['censar']
d = load()
def fmean(a, rho, h):
    mu = a / (1 - rho); j = np.arange(16, 41); return float((mu + rho ** j * (h - mu)).mean())
def job(args):
    sym, b = args
    x = d[(d.symbol == sym) & (d.date >= '2024-02-06') & (d.date <= T0)].dropna(subset=['lp', 'ceil'])
    c = x.ceil.values; o = a3[sym]; th = (o['a'], o['rho'], o['sigma']); want = o['capped_T0']
    rng = np.random.default_rng(100000 * (FUNDS.index(sym) + 1) + b)
    for attempt in range(400):
        y, k, lat, fut = simulate(th, c, x.lp.values[0], 40, rng)
        if bool(k[-1]) == want and 0.03 < k.mean() < 0.97: break
    else:
        return None
    m = CensAR(y, c, k, step=0.008, hi=max(c.max(), y.max()) + 0.9)
    try:
        m.fit(start=o['th'], maxiter=400, xatol=5e-4, fatol=5e-3)
    except Exception as e:
        return None
    a, rho, s, _ = m.unpack(m.th); ll, st = m.filter(m.th, return_state=True)
    h = float((st[1] * m.g).sum()) if st[0] == 'den' else float(st[1])
    return dict(sym=sym, b=b, rho=rho, sigma=s, a=a, hid=h, hid_true=float(lat[-1]), fm=fmean(a, rho, h), real=float(fut[15:40].mean()), cap=float(k.mean()), tries=attempt + 1)
if __name__ == '__main__':
    t0 = time.time(); tasks = [(s, b) for s in FUNDS for b in range(B)]
    with Pool(2) as p: r = [v for v in p.imap_unordered(job, tasks, chunksize=4) if v is not None]
    json.dump(r, open(os.path.join(OUT, 'a3b_boot.json'), 'w'))
    print('done', len(r), round(time.time() - t0), 's')
