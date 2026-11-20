import numpy as np, pandas as pd
from common import *
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 300)
d = load(); res = {}
F5 = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100']
g = d[d.symbol.isin(INTL)].copy()
for c in ['hit_up_max', 'hit_up', 'hit_down_max', 'last_at_up']:
    for j in (1, 2, 3): g[f'{c}_l{j}'] = g.groupby('symbol')[c].shift(j).fillna(False).astype(float)
g['ret_l1'] = g.groupby('symbol')['ret'].shift(1)
g['ret_l5'] = g.groupby('symbol')['ret'].transform(lambda s: s.rolling(5).sum().shift(1))
g['ltrades'] = np.log(g.trades); g['ltrade_sz'] = np.log(g.avg_trade); g['lto'] = np.log(g.turnover.clip(lower=1e-6))
g['dltrades'] = g.groupby('symbol')['ltrades'].diff(); g['dlsz'] = g.groupby('symbol')['ltrade_sz'].diff()
g['retn'] = g.groupby('symbol')['ret'].shift(-1); g['dlpn'] = g.groupby('symbol')['dlp'].shift(-1)
g['trn'] = g.groupby('symbol')['ltrades'].shift(-1) - g.ltrades; g['szn'] = g.groupby('symbol')['ltrade_sz'].shift(-1) - g.ltrade_sz
post = g[(g.s >= 1) & g.symbol.isin(F5)].copy()
# (a) the day after an upper-limit day (post-change): close at max limit vs touch
for lab, cond in [('high at maximum upper limit', post.hit_up_max), ('close/last at upper limit', post.last_at_up | post.close_eq), ('high at initial upper limit only', post.hit_up & ~post.hit_up_max), ('high at max lower limit', post.hit_down_max), ('no limit', ~post.hit_up & ~post.hit_down)]:
    z = post[cond].dropna(subset=['retn'])
    o = dict(n=len(z), ret_next=100 * z.retn.mean(), share_pos=float((z.retn > 0).mean()) if len(z) else None, dlp_next=100 * z.dlpn.mean(), trades_chg=100 * z.trn.mean(), size_chg=100 * z.szn.mean(),
             ret_day=100 * z.ret.mean(), funds=z.symbol.value_counts().to_dict())
    print(lab, o); res.setdefault('after_limit', {})[lab] = o
# pre-change (NAV band, since Feb 2024): day after close at the ceiling
pre = g[(g.date >= '2024-02-06') & (g.s <= 0)]
z = pre[pre.capped].dropna(subset=['retn']); print('pre capped: next-day ret', 100 * z.retn.mean(), len(z))
# (b) regression of dlp on turnover, delivered share, lagged own returns, day-after-limit indicators; fund fixed effects; Driscoll-Kraay SEs
def dk_ols(df, ycol, xcols, lags=4):
    df = df.dropna(subset=[ycol] + xcols).copy()
    X = df[xcols].copy(); y = df[ycol].copy()
    # fund fixed effects by demeaning
    Xd = X - X.groupby(df.symbol).transform('mean'); yd = y - y.groupby(df.symbol).transform('mean')
    b = np.linalg.lstsq(Xd.values, yd.values, rcond=None)[0]; e = yd.values - Xd.values @ b
    XtX = Xd.values.T @ Xd.values; XtXi = np.linalg.inv(XtX)
    h = pd.DataFrame(Xd.values * e[:, None]).groupby(df.date.values).sum().sort_index().values  # T x k cross-sectional sums
    S = h.T @ h
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1); G = h[l:].T @ h[:-l]; S += w * (G + G.T)
    V = XtXi @ S @ XtXi; se = np.sqrt(np.diag(V))
    r2 = 1 - (e ** 2).sum() / (yd.values ** 2).sum()
    return pd.DataFrame(dict(b=b, se=se, t=b / se), index=xcols), len(df), r2, df.date.nunique()
post['to'] = post.turnover; post['dv'] = post.deliv_share
specs = {'(1)': ['ret_l1', 'ret_l5'], '(2)': ['to', 'dv', 'ret_l1', 'ret_l5'], '(3)': ['to', 'dv', 'ret_l1', 'ret_l5', 'hit_up_max_l1', 'hit_up_max_l2', 'hit_up_max_l3', 'hit_down_max_l1']}
for k, xs in specs.items():
    t, n, r2, T = dk_ols(post, 'dlp', xs); print(k, 'n', n, 'T', T, 'R2', round(r2, 3)); print(t.round(3))
    res.setdefault('reg', {})[k] = dict(n=n, T=T, r2=r2, coef=t.round(5).reset_index().to_dict('records'))
# excluding MONQ50
t, n, r2, T = dk_ols(post[post.symbol != 'MONQ50'], 'dlp', specs['(2)']); print('ex MONQ50', n, round(r2, 3)); print(t.round(3)); res['reg']['(2) ex MONQ50'] = dict(n=n, T=T, r2=r2, coef=t.round(5).reset_index().to_dict('records'))
t, n, r2, T = dk_ols(post[post.symbol == 'MONQ50'].assign(symbol='x'), 'dlp', specs['(2)']); print('MONQ50 only', n, round(r2, 3)); print(t.round(3)); res['reg']['(2) MONQ50'] = dict(n=n, T=T, r2=r2, coef=t.round(5).reset_index().to_dict('records'))
# pre-change comparison (uncapped days only is selection; report all days Feb 2024-4 Sep 2026)
prr = g[(g.date >= '2024-02-06') & (g.s <= 0) & g.symbol.isin(F5)].copy(); prr['to'] = prr.turnover; prr['dv'] = prr.deliv_share
t, n, r2, T = dk_ols(prr, 'dlp', specs['(2)']); print('pre', n, round(r2, 3)); print(t.round(3)); res['reg']['(2) pre-change'] = dict(n=n, T=T, r2=r2, coef=t.round(5).reset_index().to_dict('records'))
# (c) co-movement of daily premium changes
def comove(x):
    W = x.pivot(index='date', columns='symbol', values='dlp')[F5].dropna(); C = W.corr(); ev = np.linalg.eigvalsh(C.values)[::-1]
    off = C.values[np.triu_indices(5, 1)]
    return C, float(off.mean()), float(ev[0] / ev.sum()), len(W)
for lab, x in [('pre: 250 sessions to 4 Sep', g[(g.s <= 0) & (g.s > -250)]), ('pre uncapped era: 2024', g[(g.date >= '2024-02-06') & (g.date <= '2024-12-31')]), ('post', g[g.s >= 1]), ('post ex sessions 1-15', g[g.s >= 16])]:
    C, m, pc1, n = comove(x); print(lab, 'mean pairwise corr', round(m, 3), 'PC1 share', round(pc1, 3), n); print(C.round(2))
    res.setdefault('comove', {})[lab] = dict(mean_corr=m, pc1=pc1, n=n, corr=C.round(3).to_dict())
# does MONQ50's lagged return lead the others?
W = g[g.s >= 1].pivot(index='date', columns='symbol', values='dlp'); R = g[g.s >= 1].pivot(index='date', columns='symbol', values='ret')
for sym in ['MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100']:
    print(sym, 'corr with MONQ50 same day', round(W[sym].corr(W['MONQ50']), 3), 'with MONQ50 lagged', round(W[sym].corr(W['MONQ50'].shift(1)), 3), 'own lag', round(W[sym].corr(W[sym].shift(1)), 3))
    res.setdefault('lead', {})[sym] = dict(same=float(W[sym].corr(W['MONQ50'])), lag=float(W[sym].corr(W['MONQ50'].shift(1))), own=float(W[sym].corr(W[sym].shift(1))))
print('MONQ50 own lag', W['MONQ50'].corr(W['MONQ50'].shift(1)))
# (d) trading intensity before/after
for sym in F5 + ['HNGSNGBEES', 'NIFTYBEES']:
    a = d[(d.symbol == sym) & (d.s <= 0) & (d.s > -60)]; b = d[(d.symbol == sym) & (d.s >= 1)]
    o = dict(trades_pre=a.trades.median(), trades_post=b.trades.median(), size_pre=a.avg_trade.median(), size_post=b.avg_trade.median(), deliv_pre=100 * a.deliv_share.mean(), deliv_post=100 * b.deliv_share.mean(),
             to_pre=100 * a.turnover.mean(), to_post=100 * b.turnover.mean(), range_pre=100 * np.log(a.high / a.low).mean(), range_post=100 * np.log(b.high / b.low).mean())
    print(sym, {k: round(float(v), 2) for k, v in o.items()}); res.setdefault('intensity', {})[sym] = o
# (e) April exposure on the last day
last = d[(d.date == d.date.max()) & d.symbol.isin(INTL)].copy()
b_ = 0.2; last['Pi'] = last.prem_known; last['fall_widened'] = 1 - (1 + b_) / (1 + last.Pi); last['fall_first'] = 1 - 1.1 / (1 + last.Pi)
last['sessions'] = np.ceil(np.log((1 + last.Pi) / 1.2) / np.log(1 / 0.8)).clip(lower=0)
last['loss_cr'] = last.units * last.close * last.fall_widened.clip(lower=0) / 1e7
print(last[['symbol', 'date', 'Pi', 'x_april', 'fall_widened', 'fall_first', 'sessions', 'loss_cr']].round(3)); res['april'] = last[['symbol', 'Pi', 'x_april', 'fall_widened', 'fall_first', 'sessions', 'loss_cr']].round(5).to_dict('records')
# (f) 2022 episode
for sym in ['MON100', 'MONQ50', 'HNGSNGBEES', 'MAFANG', 'MASPTOP50', 'MAHKTECH']:
    x = d[(d.symbol == sym) & (d.date >= '2021-12-01') & (d.date <= '2022-12-31')].set_index('date')
    mq = x.prem_known.resample('ME').agg(['mean', 'max']) * 100; print(sym, mq.round(1).T.to_string())
    res.setdefault('ep2022', {})[sym] = [dict(m=str(i.date())[:7], mean=float(r['mean']), max=float(r['max'])) for i, r in mq.iterrows()]
save('a5', res)
