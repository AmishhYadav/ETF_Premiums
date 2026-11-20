import numpy as np, pandas as pd
from common import *
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 300)
d = load()
res = {}
# periods
per = {'A: Jan 2021-Jan 2022 (creation open)': ('2021-01-01', '2022-01-31'),
       'B: Feb 2022-Dec 2023': ('2022-02-01', '2023-12-31'),
       'C: Jan 2024-4 Sep 2026 (rationed, NAV band)': ('2024-01-01', '2026-09-04'),
       'D: 7 Sep-13 Nov 2026 (previous-close band)': ('2026-09-07', '2026-11-13')}
rows = []
for sym in INTL + DOM + BULL:
    x = d[d.symbol == sym]
    for k, (a, b) in per.items():
        y = x[(x.date >= a) & (x.date <= b)]
        if len(y) == 0: continue
        rows.append(dict(symbol=sym, period=k[0], n=len(y), prem_mean=100 * y.prem_known.mean(), prem_med=100 * y.prem_known.median(),
                         prem_sd=100 * y.prem_known.std(), prem_max=100 * y.prem_known.max(), prem_min=100*y.prem_known.min(),
                         up=100 * y.hit_up_max.mean(), closeup=100 * (y.close_eq | y.last_at_up).mean(), dn=100 * y.hit_down_max.mean(),
                         to_med=100 * y.turnover.median(), to_mean=100 * y.turnover.mean(), deliv=100 * y.deliv_share.mean(),
                         trade=y.avg_trade.median(), trades=y.trades.median(), value_cr=y.value.mean()/1e7 if y.value.mean()>1e6 else y.value.mean()))
t = pd.DataFrame(rows)
print(t.round(2).to_string())
res['by_period'] = t.round(4).to_dict('records')
# check units of value
print(d[d.symbol=='MONQ50'][['date','volume','value','avg_price','trades','avg_trade']].tail(3))
# old-regime capped share over the official-band era
pre = d[(d.date >= '2024-02-06') & (d.date <= T0) & d.symbol.isin(INTL)]
cs = pre.groupby('symbol').agg(n=('lp', 'size'), hit=('hit_up_official', 'mean'), capped=('capped', 'mean'))
print(cs)
res['capped_share'] = cs.reset_index().to_dict('records')
# longest capped run and run ending T0
for sym in INTL:
    x = pre[pre.symbol == sym].sort_values('date'); c = x.capped.values
    best = cur = 0
    for v in c:
        cur = cur + 1 if v else 0; best = max(best, cur)
    end = 0
    for v in c[::-1]:
        if v: end += 1
        else: break
    h=x.hit_up_official.values; endh=0
    for v in h[::-1]:
        if v: endh+=1
        else: break
    print(sym, 'longest capped run', best, 'run ending 4 Sep', end, 'hit-run ending', endh, 'last 120 capped share', c[-120:].mean().round(3))
    res.setdefault('runs', {})[sym] = dict(longest=best, ending=end, hit_ending=endh, last120=float(c[-120:].mean()))
# ceiling distribution on the quoted and known premium (pure nav_t2 days only)
pp = pre[(pre.band_base_kind == 'nav_t2')].copy()
pp['cq'] = pp.band_high / pp.nav_same_date - 1; pp['ck'] = pp.band_high / pp.nav_known - 1
q = pp[pp.symbol.isin(US)][['cq', 'ck']].quantile([.01, .05, .25, .5, .75, .95, .99]) * 100
print(q.round(1)); res['ceiling_quantiles_US'] = q.round(2).reset_index().to_dict('records')
print(pp.band_base_kind.value_counts(), pre.band_base_kind.value_counts())
save('a1', res)
