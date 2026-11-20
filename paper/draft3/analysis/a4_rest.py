import numpy as np, pandas as pd
from common import *
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 300)
d = load(); res = {}
ue = pd.read_parquet(EXPORT + 'unit_events.parquet'); ue['date'] = pd.to_datetime(ue['date']); ue['prev_date'] = pd.to_datetime(ue['prev_date'])
ue = ue[ue.symbol.isin(INTL)]
# ---- P3a: event study around unit changes (batch-dated)
rows = []
for _, e in ue.iterrows():
    x = d[d.symbol == e.symbol].reset_index(drop=True); i = x.index[x.date == e.date]
    if len(i) == 0: i = x.index[x.date > e.date][:1]
    i = int(i[0]); 
    if i - 10 < 0 or i + 10 >= len(x): continue
    lp = x.lp.values; cap = x.capped.values
    rows.append(dict(symbol=e.symbol, date=e.date, cu=e.creation_units_change, dln=np.log(e.units_new / e.units_prev),
                     lp_m10=lp[i - 10], lp_m5=lp[i - 5], lp_m1=lp[i - 1], lp_0=lp[i], lp_p5=lp[i + 5], lp_p10=lp[i + 10],
                     capped_m5=cap[i - 10:i].mean(), capped_p=cap[i:i + 11].mean(), nav_value_cr=e.units_change * x.nav_known.values[i] / 1e7,
                     gap=e.days_since_prev))
ev = pd.DataFrame(rows); ev['d_m5_p10'] = ev.lp_p10 - ev.lp_m5; ev['d_m10_p10'] = ev.lp_p10 - ev.lp_m10; ev['pre_run'] = ev.lp_m1 - ev.lp_m10
print(ev.assign(date=ev.date.dt.date).round(3).to_string())
cr = ev[ev.cu > 0]; rd = ev[ev.cu < 0]
def summ(z, lab):
    o = dict(n=len(z), lp_m5=100 * z.lp_m5.mean(), d_m5_p10=100 * z.d_m5_p10.mean(), se=100 * z.d_m5_p10.std() / np.sqrt(len(z)), share_neg=(z.d_m5_p10 < 0).mean(),
             d_m10_p10=100 * z.d_m10_p10.mean(), med=100 * z.d_m5_p10.median(), mean_dln=100 * z.dln.mean(), nav_cr=z.nav_value_cr.sum())
    print(lab, {k: round(v, 3) for k, v in o.items()}); return o
res['creations'] = summ(cr, 'creations'); res['redemptions'] = summ(rd, 'redemptions')
res['creations_prem5'] = summ(cr[cr.lp_m5 > 0.05], 'creations at premium>5%')
for sym in ['MONQ50', 'MON100']:
    res['cr_' + sym] = summ(cr[cr.symbol == sym], 'creations ' + sym); res['rd_' + sym] = summ(rd[rd.symbol == sym], 'redemptions ' + sym)
# elasticity for MONQ50 (large % changes)
z = cr[cr.symbol == 'MONQ50']; b = np.polyfit(z.dln, z.d_m5_p10, 1); print('MONQ50 slope d lp on d ln units', b, 'ratio of means', z.d_m5_p10.mean() / z.dln.mean())
res['monq50_elasticity'] = dict(slope=b[0], intercept=b[1], ratio=z.d_m5_p10.mean() / z.dln.mean(), n=len(z))
res['events'] = ev.assign(date=ev.date.dt.strftime('%Y-%m-%d')).round(5).to_dict('records')
# premium at which creations and redemptions happened
print('creation premium level (t-5) quantiles', (100 * cr.lp_m5).describe().round(2).to_dict()); print('redemption premium level', (100 * rd.lp_m5).describe().round(2).to_dict())
res['redemptions_at_premium'] = rd[rd.lp_m5 > 0.05][['symbol', 'date', 'cu', 'lp_m5', 'nav_value_cr']].assign(date=lambda z: z.date.dt.strftime('%Y-%m-%d')).round(4).to_dict('records')
print(pd.DataFrame(res['redemptions_at_premium']))
# ---- Motilal headroom accounting: rupees at NAV
mo = ev[ev.symbol.isin(['MON100', 'MONQ50'])].copy(); mo['ym'] = mo.date.dt.to_period('Q')
hh = mo.pivot_table(index='ym', columns='symbol', values='nav_value_cr', aggfunc='sum').fillna(0); hh['net'] = hh.sum(axis=1); hh['cum'] = hh.net.cumsum(); print(hh.round(0))
res['motilal_headroom'] = hh.round(1).reset_index().astype({'ym': str}).to_dict('records')
mi = ev[ev.symbol.isin(['MAFANG', 'MASPTOP50', 'MAHKTECH'])].copy(); mi['ym'] = mi.date.dt.to_period('Q')
h2 = mi.pivot_table(index='ym', columns='symbol', values='nav_value_cr', aggfunc='sum').fillna(0); h2['net'] = h2.sum(axis=1); h2['cum'] = h2.net.cumsum(); print(h2.round(0))
res['mirae_headroom'] = h2.round(1).reset_index().astype({'ym': str}).to_dict('records')

# ---- P3b: cross-section, premium vs float at NAV
post = d[(d.s >= 1) & d.symbol.isin(INTL)].copy(); post['float_cr'] = post.units * post.nav_known / 1e7
from scipy.stats import spearmanr, kendalltau
rows = []
for s_, g in post.groupby('s'):
    u = g[g.symbol.isin(US)]; r4 = spearmanr(u.float_cr, u.lp)[0]; r6 = spearmanr(g.float_cr, g.lp)[0]
    rows.append(dict(s=int(s_), date=str(g.date.iloc[0].date()), rho_us=r4, rho_six=r6))
cs = pd.DataFrame(rows); print(cs.rho_us.value_counts().sort_index(), cs.rho_six.describe())
res['float_rank'] = dict(us_share_minus1=float((cs.rho_us <= -0.999).mean()), us_mean=float(cs.rho_us.mean()), six_mean=float(cs.rho_six.mean()), n=len(cs),
                         first_of_month=cs[cs.date.isin(['2026-09-07', '2026-10-01', '2026-11-02'])].to_dict('records'))
snap = post[post.date == post.date.max()][['symbol', 'close', 'nav_known', 'prem_known', 'units', 'float_cr', 'turnover', 'deliv_share', 'avg_trade', 'x_april']]
snap['above_cr'] = snap.units * (snap.close - snap.nav_known) / 1e7; print(snap.round(3)); res['snapshot_last'] = snap.round(5).to_dict('records')
# turnover-premium cross-section, post averages
pa = post.groupby('symbol').agg(lp=('lp', 'mean'), prem=('prem_known', 'mean'), to=('turnover', 'mean'), cumto=('turnover', 'sum'), deliv=('deliv_share', 'mean'), fl=('float_cr', 'mean'), trades=('trades', 'sum'), value=('value', 'sum'))
print(pa.round(3)); res['post_avgs'] = pa.round(5).reset_index().to_dict('records')

# ---- money
m = d[(d.s >= 1) & d.symbol.isin(INTL)].copy()
m['bse_volume'] = m.bse_volume.fillna(0); m['bse_value'] = m.bse_value.fillna(0)
m['paid_above_nav'] = m.volume * (m.avg_price - m.nav_known).clip(lower=0) + (m.bse_value - m.bse_volume * m.nav_known).clip(lower=0)
m['excess_all'] = m.excess_rupees
m['mcap_above'] = m.units * (m.close - m.nav_known)
tab = m.groupby('symbol').agg(value_cr=('value', lambda v: v.sum() / 1e7), bse_value_cr=('bse_value', lambda v: v.sum() / 1e7), paid_above_nav_cr=('paid_above_nav', lambda v: v.sum() / 1e7),
                              excess_cr=('excess_all', lambda v: v.sum() / 1e7), cum_turnover=('turnover', 'sum'), trades=('trades', 'sum'))
tab.loc['Six funds'] = tab.sum(); print(tab.round(1)); res['money'] = tab.round(3).reset_index().to_dict('records')
agg = m.groupby('date').agg(above=('mcap_above', 'sum'), nav=('units', lambda u: 0)).above / 1e7
navv = (m.units * m.nav_known).groupby(m.date).sum() / 1e7
print('value above NAV: max', agg.max().round(0), agg.idxmax().date(), 'last', agg.iloc[-1].round(0), 'on 4 Sep', None)
x4 = d[(d.date == T0) & d.symbol.isin(INTL)]; print('4 Sep above', (x4.units * (x4.close - x4.nav_known)).sum() / 1e7, 'nav', (x4.units * x4.nav_known).sum() / 1e7)
x9 = d[(d.date == '2026-11-09') & d.symbol.isin(INTL)]; print('9 Nov above (known NAV)', (x9.units * (x9.close - x9.nav_known)).sum() / 1e7, 'same-date', (x9.units * (x9.close - x9.nav_same_date)).sum() / 1e7, 'nav', (x9.units * x9.nav_same_date).sum() / 1e7)
res['above_series'] = [dict(date=str(k.date()), above=float(v), nav=float(navv.loc[k])) for k, v in agg.items()]
res['above_T0'] = float((x4.units * (x4.close - x4.nav_known)).sum() / 1e7)
# weekly excess rupees
m['wk'] = m.date.dt.to_period('W').astype(str); wk = m.pivot_table(index='wk', columns='symbol', values='excess_all', aggfunc='sum') / 1e7; print(wk.round(1))
res['excess_weekly'] = wk.round(2).reset_index().to_dict('records')
# pre-change year for comparison: turnover and value
pre = d[(d.s <= 0) & (d.s > -47) & d.symbol.isin(INTL)]; print(pre.groupby('symbol').agg(value_cr=('value', lambda v: v.sum() / 1e7), cum_turnover=('turnover', 'sum'), trades=('trades', 'sum')).round(2))
res['pre47'] = pre.groupby('symbol').agg(value_cr=('value', lambda v: v.sum() / 1e7), cum_turnover=('turnover', 'sum'), trades=('trades', 'sum')).round(4).reset_index().to_dict('records')
save('a4', res)
