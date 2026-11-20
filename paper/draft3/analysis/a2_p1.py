import numpy as np, pandas as pd
from common import *
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 300)
d = load(); res = {}
# (i) 7 Sep 2026 return ranked within own daily returns since Jan 2023
rows = []
for sym in INTL + DOM + BULL:
    x = d[(d.symbol == sym) & (d.date >= '2023-01-01')].dropna(subset=['ret'])
    r0 = x.loc[x.date == T1, 'ret'].iloc[0]
    pre = x[x.date < T1]
    rank_up = int((pre.ret >= r0).sum()) + 1
    p_two = (np.abs(pre.ret) >= abs(r0)).mean()
    dl = x.loc[x.date == T1, 'dlp'].iloc[0]
    navret = x.loc[x.date == T1, 'ret_nav_1'].iloc[0]
    rows.append(dict(symbol=sym, ret=100 * (np.exp(r0) - 1), n=len(pre), rank=rank_up, p_two=p_two, max_pre=100 * (np.exp(pre.ret.max()) - 1),
                     dlp=100 * dl, sd=100 * pre.ret.std(), z=r0 / pre.ret.std(), nav_same_chg=None))
t = pd.DataFrame(rows); print(t.round(3).to_string()); res['holiday'] = t.round(5).to_dict('records')
# NAV change on 7 Sep (same-date NAV vs 4 Sep)
for sym in INTL:
    x = d[(d.symbol == sym)].set_index('date')
    print(sym, 'NAV 4 Sep', x.loc[T0, 'nav_same_date'], 'NAV 7 Sep', x.loc[T1, 'nav_same_date'], 'known at 7 Sep close', x.loc[T1, 'nav_known'], x.loc[T1, 'nav_known_date'])

# (ii) single break date in the level of lp, least squares, per fund, window Jan 2025 - 13 Nov 2026, 10% trimming
def breakdate(y):
    n = len(y); best = (np.inf, None); tr = max(10, int(0.1 * n)); tot = ((y - y.mean()) ** 2).sum()
    cs = np.cumsum(y); cs2 = np.cumsum(y ** 2)
    out = []
    for k in range(tr, n - tr):
        s1 = cs2[k - 1] - cs[k - 1] ** 2 / k
        s2 = (cs2[-1] - cs2[k - 1]) - (cs[-1] - cs[k - 1]) ** 2 / (n - k)
        out.append((s1 + s2, k))
    ssr, k = min(out)
    F = (tot - ssr) / (ssr / (n - 2))
    return k, F, y[:k].mean(), y[k:].mean()
rows = []
for sym in INTL + DOM:
    x = d[(d.symbol == sym) & (d.date >= '2025-01-01')].dropna(subset=['lp'])
    k, F, m1, m2 = breakdate(x.lp.values)
    # also break in first difference free: use last 250 sessions before and all after
    rows.append(dict(symbol=sym, break_date=str(x.date.iloc[k].date()), s=int(x.s.iloc[k]), supF=F, before=100 * m1, after=100 * m2))
t = pd.DataFrame(rows); print(t.round(2).to_string()); res['break'] = t.round(4).to_dict('records')
# narrower window: 120 sessions either side would be unbalanced; use 1 Jan 2026 on
rows = []
for sym in INTL + DOM:
    x = d[(d.symbol == sym) & (d.date >= '2026-01-01')].dropna(subset=['lp'])
    k, F, m1, m2 = breakdate(x.lp.values)
    rows.append(dict(symbol=sym, break_date=str(x.date.iloc[k].date()), s=int(x.s.iloc[k]), supF=F, before=100 * m1, after=100 * m2))
t = pd.DataFrame(rows); print(t.round(2).to_string()); res['break2026'] = t.round(4).to_dict('records')

# (iii) placebo dates: statistic = mean lp over sessions [0..4] from date minus mean over [-5..-1], treated minus domestic
P = d.pivot(index='date', columns='symbol', values='lp')
P = P[P.index >= '2023-01-01']
def stat(P, cols, h=5):
    a = P[cols].rolling(h).mean()                 # mean over t-h+1..t
    post = a.shift(-(h - 1)); pre = a.shift(1)   # post: t..t+h-1 ; pre: t-h..t-1
    return (post - pre)
S = stat(P, INTL + DOM)
treat5 = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100']
us4 = US
for name, cols in [('five ceilinged funds', treat5), ('four US-linked', us4), ('six', INTL)]:
    z = (S[cols].mean(axis=1) - S[['NIFTYBEES', 'JUNIORBEES', 'BANKBEES']].mean(axis=1)).dropna()
    v = z.loc[T1]; rk = int((z >= v).sum()); n = len(z)
    zpre = z[z.index < T1 - pd.Timedelta(days=10)]
    print(name, 'stat', round(100 * v, 2), 'rank', rk, 'of', n, 'max pre', round(100 * zpre.max(), 2), str(zpre.idxmax().date()), 'p', rk / n)
    res.setdefault('placebo', {})[name] = dict(stat=100 * v, rank=rk, n=n, max_pre=100 * zpre.max(), max_pre_date=str(zpre.idxmax().date()), top5=[(str(i.date()), round(100 * x, 2)) for i, x in z.sort_values(ascending=False).head(8).items()])
    print(res['placebo'][name]['top5'])
for sym in INTL + ['NIFTYBEES', 'JUNIORBEES', 'BANKBEES']:
    z = S[sym].dropna(); v = z.loc[T1]; rk = int((z >= v).sum())
    print(sym, round(100 * v, 2), rk, len(z))
    res.setdefault('placebo_fund', {})[sym] = dict(stat=100 * v, rank=rk, n=len(z))
# event-time path: mean lp by session for windows
ev = d[(d.s >= -20) & (d.s <= 47) & d.symbol.isin(INTL + DOM)].pivot(index='s', columns='symbol', values='lp')
res['event_path'] = (100 * ev).round(3).reset_index().to_dict('records')
# DiD: post (sessions 16-40) minus pre (sessions -60..0), treated minus controls
pre = d[(d.s >= -60) & (d.s <= 0)].groupby('symbol').lp.mean(); post = d[(d.s >= 16) & (d.s <= 40)].groupby('symbol').lp.mean()
dd = (post - pre) * 100; print(dd.round(2)); res['did'] = dd.round(3).to_dict()
save('a2', res)
