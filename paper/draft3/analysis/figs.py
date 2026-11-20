import numpy as np, pandas as pd, json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt, matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch, Rectangle
from common import *
plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Liberation Serif', 'Times New Roman', 'DejaVu Serif'], 'font.size': 8, 'axes.linewidth': 0.6,
    'axes.spines.top': False, 'axes.spines.right': False, 'legend.frameon': False, 'legend.fontsize': 7, 'xtick.labelsize': 7, 'ytick.labelsize': 7,
    'axes.labelsize': 8, 'lines.linewidth': 1.0, 'savefig.dpi': 300, 'figure.dpi': 120, 'mathtext.fontset': 'stix'})
W1, W2 = 3.45, 7.1
COL = {'MONQ50': '#b2182b', 'MASPTOP50': '#ef8a62', 'MAFANG': '#8073ac', 'MAHKTECH': '#1b7837', 'MON100': '#2166ac', 'HNGSNGBEES': '#7f7f7f', 'dom': '#bbbbbb'}
LS = {'MONQ50': '-', 'MASPTOP50': '-', 'MAFANG': '--', 'MAHKTECH': '-.', 'MON100': '-', 'HNGSNGBEES': ':'}
d = load(); J = lambda n: json.load(open(f'{OUT}/{n}.json'))
def sv(fig, name): fig.savefig(f'{FIG}/{name}.png', bbox_inches='tight', pad_inches=0.03); plt.close(fig)
pct = lambda x: 100 * (np.exp(x) - 1)
def pax(ax, ticks):  # axis in log(1+premium) labelled in per cent
    ax.set_yticks([np.log(1 + t / 100) for t in ticks]); ax.set_yticklabels([str(t) for t in ticks])

# ---- F: history 2021-2026 (wide)
def f_history():
    fig, ax = plt.subplots(figsize=(W2, 2.6))
    for s in INTL:
        x = d[d.symbol == s].set_index('date').lp.rolling(5, min_periods=1).mean()
        ax.plot(x.index, x.values, color=COL[s], ls=LS[s], lw=0.9, label=s)
    pax(ax, [0, 10, 20, 50, 100, 200]); ax.set_ylim(-0.06, np.log(3.4)); ax.set_ylabel('Premium over last known NAV (%, log scale)')
    ax.axhline(np.log(1.2), color='k', lw=0.5, ls=(0, (1, 2))); ax.text(pd.Timestamp('2021-01-20'), np.log(1.2) + 0.01, 'about 20%: ceiling of the NAV-anchored band', fontsize=6.5, va='bottom')
    ev = [('2022-02-01', 'Overseas\nsubscriptions\nstopped'), ('2022-06-21', 'Resumption\nwithin frozen\nroom'), ('2024-01-05', 'Mirae stops\ncreation'), ('2024-04-01', 'Overseas-ETF\npool full'), ('2025-11-17', 'Last creation\nin any fund'), ('2026-09-07', 'Band\nre-anchored')]
    hs = [3.3, 2.35, 3.3, 2.35, 3.3, 3.3]
    for (dt, lab), hh in zip(ev, hs):
        ax.axvline(pd.Timestamp(dt), color='0.45', lw=0.5); ax.text(pd.Timestamp(dt) + pd.Timedelta(days=8), np.log(hh), lab, fontsize=6, va='top', ha='left', color='0.25')
    ax.axvspan(pd.Timestamp('2026-09-07'), d.date.max(), color='0.9', zorder=0)
    ax.legend(ncol=6, loc='upper center', bbox_to_anchor=(0.5, -0.1), handlelength=2.2, columnspacing=1.2); ax.set_xlim(pd.Timestamp('2021-01-01'), pd.Timestamp('2027-02-15'))
    sv(fig, 'fig_history')

# ---- F: timing diagram (1 col)
def f_timing():
    fig, ax = plt.subplots(figsize=(W1, 1.95)); ax.set_xlim(-4, 60); ax.set_ylim(-0.3, 5.0); ax.axis('off')
    for i, lab in enumerate(['Day t−1', 'Day t', 'Day t+1']):
        ax.text(24 * i + 3.5, 4.75, lab, ha='center', fontsize=6.5); ax.plot([24 * i, 24 * i], [0.2, 4.9], color='0.8', lw=0.5)
    for h in range(0, 61, 6): ax.text(h, -0.25, f'{h % 24:02d}', ha='center', fontsize=5.5, color='0.4')
    for i in range(3):
        if 24 * i + 15.5 <= 60: ax.add_patch(Rectangle((24 * i + 9.25, 3.0), 6.25, 0.5, color='#2166ac', alpha=.85))
    for i in range(-1, 3):
        a, b = 24 * i + 19, 24 * i + 25.5
        a, b = max(a, 0), min(b, 60)
        if b > a: ax.add_patch(Rectangle((a, 2.0), b - a, 0.5, color='#b2182b', alpha=.85))
    ax.text(-0.5, 3.25, 'India', ha='right', fontsize=6.5); ax.text(-0.5, 2.25, 'US', ha='right', fontsize=6.5); ax.text(-0.5, 1.2, 'NAV out', ha='right', fontsize=6.5)
    for i, lab in enumerate(['NAV$_{t-2}$', 'NAV$_{t-1}$', 'NAV$_{t}$']):
        x = 24 * i + 10; ax.plot([x], [1.2], marker='v', color='k', ms=4); ax.text(x, 0.75, lab + '\nby 10:00', ha='center', va='top', fontsize=6)
    ax.annotate('band for day t is set before 09:15,\nwhen the newest NAV is NAV$_{t-2}$', xy=(33.2, 3.55), xytext=(33.5, 3.95), fontsize=5.8, ha='center', va='bottom', arrowprops=dict(arrowstyle='->', lw=0.5))
    ax.text(47, 1.72, 'this close sets NAV$_t$', fontsize=5.8, ha='center', color='#b2182b')
    sv(fig, 'fig_timing')

# ---- F: mechanism diagram (wide)
def f_mechanism():
    fig, ax = plt.subplots(figsize=(W2, 2.35)); ax.set_xlim(0, 100); ax.set_ylim(0, 34); ax.axis('off')
    def box(x, y, w, h, txt, fc='#f2f2f2', ec='0.3', fs=6.6, bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.25,rounding_size=0.8', fc=fc, ec=ec, lw=0.7))
        ax.text(x + w / 2, y + h / 2, txt, ha='center', va='center', fontsize=fs, fontweight='bold' if bold else 'normal')
    def arr(x1, y1, x2, y2, txt=None, dy=0.8, col='0.2', ls='-'):
        ax.annotate('', xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle='->', lw=0.8, color=col, ls=ls))
        if txt: ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, txt, ha='center', va='bottom', fontsize=6, color=col)
    box(1, 24, 17, 8, 'Overseas cap\nUS$7 bn industry;\neach house frozen at its\n1 Feb 2022 room', fc='#fddbc7')
    box(1, 8, 17, 9, 'Fund house\n(room shared across\nits overseas schemes)')
    box(30, 8, 17, 9, 'Market maker /\nlarge investor\n(Rs 25 crore and above)')
    box(53, 8, 17, 9, 'Exchange\n(NSE, BSE)\nprice P', fc='#d1e5f0')
    box(80, 8, 19, 9, 'Retail buyers\nand sellers;\nfeeder funds')
    box(26, 24, 44, 8, 'Daily price band on the exchange\n(1) to 4 Sep 2026: 1.2 × NAV(t−2)   →   fence on P/NAV\n(2) from 7 Sep 2026: P(t−1) × 1.1 … 1.2   →   speed limit only', fc='#e0f3db')
    arr(9.5, 24, 9.5, 17.2); ax.text(10.5, 20.3, 'binds', fontsize=6, va='center')
    arr(18.3, 14.5, 29.7, 14.5, col='#b2182b', ls='--'); ax.text(24, 15.2, 'creation at NAV', fontsize=5.8, ha='center', color='#b2182b'); ax.text(24, 12.6, '(rationed)', fontsize=5.8, ha='center', color='#b2182b', style='italic')
    arr(29.7, 10.5, 18.3, 10.5); ax.text(24, 8.3, 'redemption at NAV', fontsize=5.8, ha='center')
    arr(47.3, 12.5, 52.7, 12.5); ax.text(50, 13.6, 'sells', fontsize=5.8, ha='center'); arr(79.7, 14.5, 70.3, 14.5, 'demand', dy=0.3); arr(70.3, 10.5, 79.7, 10.5, 'supply of\nexisting units', dy=-3.6)
    arr(61.5, 24, 61.5, 17.2); ax.text(62.5, 20.3, 'limits P', fontsize=6, va='center')
    ax.text(50, 2.2, 'With creation rationed the stock of units is fixed and P is set by demand on the exchange; the band is the only rule between P and NAV.', ha='center', fontsize=6.6, style='italic')
    sv(fig, 'fig_mechanism')

# ---- F: cliff + envelope (1 col, two panels stacked)
def f_cliff():
    a5 = J('a5')['april']; fig, (ax, ax2) = plt.subplots(2, 1, figsize=(W1, 3.9), gridspec_kw=dict(hspace=0.55))
    P = np.linspace(0, 2.5, 300); ax.plot(100 * P, 100 * np.clip(1 - 1.2 / (1 + P), 0, None), color='k', label='band widened twice (20%)'); ax.plot(100 * P, 100 * np.clip(1 - 1.1 / (1 + P), 0, None), color='k', ls='--', label='first trade (10% band)')
    off = {'MONQ50': (10, -9), 'MASPTOP50': (26, -3), 'MAFANG': (-14, 6), 'MAHKTECH': (24, -7), 'MON100': (22, 5), 'HNGSNGBEES': (-2, 12)}
    for r in a5:
        y = max(0, 100 * r['fall_widened']); ax.plot(100 * r['Pi'], y, 'o', color=COL[r['symbol']], ms=4, zorder=5)
        ax.annotate(r['symbol'], (100 * r['Pi'], y), xytext=off[r['symbol']], textcoords='offset points', fontsize=6, color=COL[r['symbol']], ha='center')
    ax.plot(232.6, 100 * (1 - 1.2 / 3.326), 's', color=COL['MONQ50'], ms=3.5, mfc='none'); ax.annotate('MONQ50,\n18 Sep peak', (232.6, 100 * (1 - 1.2 / 3.326)), xytext=(-4, -16), textcoords='offset points', fontsize=6, ha='center')
    ax.set_xlabel('Premium over the anchoring NAV on the eve of the switch (%)'); ax.set_ylabel('Smallest one-day fall (%)'); ax.legend(loc='lower right'); ax.set_xlim(0, 250); ax.set_ylim(0, 72)
    ax.set_title('(a) The cliff', fontsize=7.5, loc='left', fontweight='bold')
    k = np.arange(0, 9); env = 100 * (1.2 / 0.8 ** k - 1); ax2.step(k, env, where='post', color='k'); ax2.fill_between(k, env, 400, step='post', color='0.9')
    ax2.set_ylim(0, 320); ax2.set_xlim(0, 7.5); ax2.set_xlabel('Sessions before the switch'); ax2.set_ylabel('Highest premium from which the\nband can still be reached (%)')
    for r in a5:
        if r['Pi'] > 0.2: ax2.axhline(100 * r['Pi'], color=COL[r['symbol']], lw=0.7, ls=':')
    ax2.text(7.45, 106, 'MONQ50', color=COL['MONQ50'], fontsize=6, ha='right'); ax2.text(7.45, 56, 'MASPTOP50, MAFANG, MAHKTECH', color='0.3', fontsize=6, ha='right')
    ax2.text(1.4, 215, 'no orderly path:\na jump is forced', fontsize=6.5, ha='center'); ax2.set_title('(b) Feasibility envelope', fontsize=7.5, loc='left', fontweight='bold')
    sv(fig, 'fig_cliff')

# ---- F: glide path (1 col)
def f_glide():
    fig, ax = plt.subplots(figsize=(W1, 2.1)); dl = np.linspace(0.01, 0.2, 200)
    for Pi, ls in [(0.5, ':'), (1.0, '-'), (2.0, '--')]:
        ax.plot(100 * dl, np.ceil(np.log((1 + Pi) / 1.2) / np.log(1 / (1 - dl))), color='k', ls=ls, label=f'premium {int(100 * Pi)}%', drawstyle='steps-mid')
    ax.set_yscale('log'); ax.set_yticks([1, 2, 5, 10, 20, 50]); ax.set_yticklabels(['1', '2', '5', '10', '20', '50']); ax.set_xlabel('Largest fall in the ceiling allowed per session (%)'); ax.set_ylabel('Sessions of notice needed'); ax.legend()
    sv(fig, 'fig_glide')

# ---- F: fence in the data (wide): heatmap + ceiling histogram
def f_fence():
    pre = d[(d.date >= '2024-02-06') & (d.date <= T0) & d.symbol.isin(INTL)].copy(); pre['q'] = pre.date.dt.to_period('Q').astype(str)
    H = (100 * pre.pivot_table(index='symbol', columns='q', values='capped', aggfunc='mean')).reindex(['MAFANG', 'MASPTOP50', 'MAHKTECH', 'MONQ50', 'MON100', 'HNGSNGBEES'])
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(W2, 2.0), gridspec_kw=dict(width_ratios=[1.75, 1], wspace=0.28))
    im = ax.imshow(H.values, aspect='auto', cmap='Greys', vmin=0, vmax=100)
    for i in range(H.shape[0]):
        for j in range(H.shape[1]):
            v = H.values[i, j]; ax.text(j, i, f'{v:.0f}', ha='center', va='center', fontsize=6, color='w' if v > 55 else 'k')
    ax.set_xticks(range(H.shape[1])); ax.set_xticklabels([c[2:4] + c[4:] for c in H.columns], rotation=45, ha='right'); ax.set_yticks(range(H.shape[0])); ax.set_yticklabels(H.index)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.tick_params(length=0); ax.set_title('(a) Sessions closed at the upper limit, % of sessions in the quarter', fontsize=7.5, loc='left')
    pp = pre[(pre.band_base_kind == 'nav_t2') & pre.symbol.isin(US)]; cq = 100 * (pp.band_high / pp.nav_same_date - 1).dropna()
    ax2.hist(cq.clip(10, 30), bins=np.arange(10, 30.5, 0.5), color='0.55', ec='w', lw=0.3); ax2.axvline(20, color='k', lw=0.6, ls='--')
    ax2.set_xlabel('Ceiling on the quoted premium (%)'); ax2.set_ylabel('Fund-days'); ax2.set_title('(b) The ceiling moved every day', fontsize=7.5, loc='left')
    q = cq.quantile([.05, .95]); ax2.text(0.97, 0.9, f'5th–95th percentile:\n{q.iloc[0]:.1f}% to {q.iloc[1]:.1f}%', transform=ax2.transAxes, ha='right', va='top', fontsize=6.5)
    sv(fig, 'fig_fence')

# ---- F: episode (wide), two panels
def f_episode():
    x = d[(d.s >= -9) & d.symbol.isin(INTL)]; fig, (ax, ax2) = plt.subplots(1, 2, figsize=(W2, 2.55), gridspec_kw=dict(wspace=0.2))
    def shade(a):
        a.axvline(0.5, color='k', lw=0.6); a.axvspan(15.5, 40.5, color='0.92', zorder=0)
        for s_, lab in [(14, 'exchange\ncautions'), (21, '')]: a.axvline(s_, color='0.5', lw=0.5, ls=':')
    m = x[x.symbol == 'MONQ50'].set_index('s'); ax.plot(m.index, m.lp, color=COL['MONQ50'], lw=1.1)
    up = m[m.hit_up_max & (m.index > 0)]; dn = m[m.hit_down_max]
    ax.plot(up.index, up.lp, '^', color=COL['MONQ50'], ms=4, label='maximum upper limit reached'); ax.plot(dn.index, dn.lp, 'v', color='k', ms=4, label='maximum lower limit reached')
    pax(ax, [20, 50, 100, 150, 233]); shade(ax); ax.set_xlabel('Sessions from the change of anchor (7 Sep 2026 = 1)'); ax.set_ylabel('Premium over last known NAV (%, log scale)')
    ax.legend(ncol=2, loc='upper center', fontsize=6, bbox_to_anchor=(0.5, -0.2), columnspacing=0.8); ax.set_ylim(np.log(1.12), np.log(3.6)); ax.set_title('(a) MONQ50', fontsize=7.5, loc='left'); ax.text(28, np.log(3.3), 'sessions 16–40', fontsize=6, ha='center', color='0.3'); ax.text(13.6, np.log(1.18), '25 Sep', fontsize=5.8, color='0.3', ha='right'); ax.text(21.4, np.log(1.18), '7 Oct', fontsize=5.8, color='0.3')
    for s in ['MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']:
        y = x[x.symbol == s].set_index('s'); ax2.plot(y.index, y.lp, color=COL[s], ls=LS[s], label=s)
    pax(ax2, [0, 10, 20, 40, 60, 84]); shade(ax2); ax2.set_xlabel('Sessions from the change of anchor'); ax2.legend(ncol=5, loc='upper center', bbox_to_anchor=(0.5, -0.2), columnspacing=0.8, handlelength=1.5, fontsize=6); ax2.set_ylim(-0.02, np.log(2.0)); ax2.set_title('(b) The other five funds', fontsize=7.5, loc='left')
    sv(fig, 'fig_episode')

# ---- F: MONQ50 price vs band vs old ceiling (1 col)
def f_band():
    m = d[(d.symbol == 'MONQ50') & (d.s >= -15)].set_index('s'); fig, ax = plt.subplots(figsize=(W1, 2.45))
    ax.fill_between(m.index, m.band_low_max.where(m.index > 0, m.band_low), m.band_high_max.where(m.index > 0, m.band_high), step='mid', color='0.87', label='official band (widest)')
    ax.vlines(m.index, m.low, m.high, color=COL['MONQ50'], lw=0.6); ax.plot(m.index, m.close, color=COL['MONQ50'], lw=1.0, label='close (bars: day range)')
    ax.plot(m.index, 1.2 * m.nav_t2, color='k', ls='--', lw=0.8, label='ceiling under the old rule, 1.2 × NAV(t−2)'); ax.plot(m.index, m.nav_known, color='0.4', lw=0.8, label='last known NAV')
    ax.set_yscale('log'); ax.set_yticks([100, 150, 200, 300, 400, 500]); ax.set_yticklabels(['100', '150', '200', '300', '400', '500']); ax.minorticks_off(); ax.set_ylabel('Rs per unit (log scale)'); ax.set_xlabel('Sessions from the change of anchor'); ax.axvline(0.5, color='k', lw=0.5)
    ax.legend(loc='upper right', fontsize=6); ax.set_ylim(85, 620)
    sv(fig, 'fig_band')

# ---- F: P1 (1 col, two panels)
def f_p1():
    a2 = J('a2'); h = pd.DataFrame(a2['holiday']); fig, (ax, ax2) = plt.subplots(2, 1, figsize=(W1, 3.6), gridspec_kw=dict(hspace=0.62, height_ratios=[1.15, 1]))
    order = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES', 'NIFTYBEES', 'NIFTYIETF', 'JUNIORBEES', 'BANKBEES']
    for i, s in enumerate(order):
        x = d[(d.symbol == s) & (d.date >= '2023-01-01') & (d.date < T1)].ret.dropna() * 100
        ax.scatter(x, np.full(len(x), i) + np.random.default_rng(i).uniform(-0.28, 0.28, len(x)), s=1, color='0.7', rasterized=True)
        r0 = 100 * d[(d.symbol == s) & (d.date == T1)].ret.iloc[0]; ax.plot(r0, i, 'D', color=COL.get(s, 'k'), ms=4.5, mec='k', mew=0.4, zorder=5)
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order, fontsize=6.3); ax.invert_yaxis(); ax.set_xlabel('Daily log return (%). Diamond: 7 Sep 2026; dots: sessions since Jan 2023', fontsize=7); ax.axvline(0, color='k', lw=0.4)
    ax.set_title('(a) The holiday session against each fund\'s own history', fontsize=7.5, loc='left')
    P = d.pivot(index='date', columns='symbol', values='lp'); P = P[P.index >= '2023-01-01']; a = P.rolling(5).mean(); S = a.shift(-4) - a.shift(1)
    z = (S[['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100']].mean(axis=1) - S[['NIFTYBEES', 'JUNIORBEES', 'BANKBEES']].mean(axis=1)).dropna() * 100
    ax2.plot(z.index, z.values, color='0.35', lw=0.6); ax2.plot(T1, z.loc[T1], 'D', color=COL['MONQ50'], ms=4.5, mec='k', mew=0.4)
    ax2.annotate(f'7 Sep 2026: {z.loc[T1]:.1f}', (T1, z.loc[T1]), xytext=(-70, -4), textcoords='offset points', fontsize=6.5, arrowprops=dict(arrowstyle='-', lw=0.4))
    ax2.set_ylabel('Five-session change in premium,\ntreated minus domestic (log points)'); ax2.xaxis.set_major_locator(mdates.YearLocator()); ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax2.set_title('(b) The same statistic computed at every date', fontsize=7.5, loc='left')
    sv(fig, 'fig_p1')

# ---- F: P2 dumbbell (1 col)
def f_p2():
    a3 = J('a3'); fr = {r['symbol']: r for r in a3['free']}; c = a3['censar']
    try: bt = J('p2_final')
    except Exception: bt = None
    order = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']; fig, ax = plt.subplots(figsize=(W1, 2.9))
    for i, s in enumerate(order):
        o = c[s]; y = i
        lo, hi = (bt[s]['band_lo'], bt[s]['band_hi']) if bt else (pct(o['f_lo']), pct(o['f_hi']))
        ax.plot([lo, hi], [y + 0.18, y + 0.18], color='0.55', lw=3.5, solid_capstyle='butt', label='forecast of latent premium, sessions 16–40 (90% band)' if i == 0 else None)
        if o['hidden_T0_q']: ax.plot([pct(o['hidden_T0_q'][0]), pct(o['hidden_T0_q'][2])], [y - 0.18, y - 0.18], color='#92c5de', lw=3.5, solid_capstyle='butt', label='hidden premium on 4 Sep (model, 90% interval)' if i == 0 else None)
        ax.plot(fr[s]['ceil_T0'] if fr[s]['capped_T0'] else fr[s]['prem_T0'], y, '|', color='k', ms=11, mew=1.2, label='premium at the 4 Sep close' if i == 0 else None)
        ax.plot(fr[s]['prem_free'], y - 0.18, 'o', color='#2166ac', ms=4, label='first free close after the change' if i == 0 else None)
        ax.plot(o['real_lev'], y + 0.18, 'D', color='#b2182b', ms=4, label='realised, sessions 16–40' if i == 0 else None)
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order, fontsize=6.5); ax.invert_yaxis(); ax.set_xlabel('Premium over last known NAV (%)'); ax.set_xlim(-5, 100)
    ax.legend(loc='upper center', bbox_to_anchor=(0.45, -0.17), ncol=1, fontsize=6.3)
    sv(fig, 'fig_p2')

# ---- F: units and premium (1 col)
def f_units():
    fig, axs = plt.subplots(2, 1, figsize=(W1, 3.3), sharex=True, gridspec_kw=dict(hspace=0.28))
    for ax, s, sc, lab in [(axs[0], 'MONQ50', 1e6, 'Units, million'), (axs[1], 'MON100', 1e6, 'Units, million')]:
        x = d[(d.symbol == s) & (d.date >= '2024-02-06')].set_index('date'); ax.step(x.index, x.units / sc, where='post', color='k', lw=0.9); ax.set_ylabel(lab)
        b = ax.twinx(); b.plot(x.index, 100 * x.prem_known.clip(upper=0.6), color=COL[s], lw=0.6, alpha=0.9); b.set_ylabel('Premium (%)', color=COL[s]); b.spines['right'].set_visible(True); b.set_ylim(-8, 62)
        b.axvline(T1, color='0.5', lw=0.5); ax.set_title(s, fontsize=7.5, fontweight='bold', loc='left', pad=2)
    axs[0].set_ylim(2, 16); axs[1].set_ylim(496, 513); axs[1].xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7])); axs[1].xaxis.set_major_formatter(mdates.DateFormatter('%b\n%Y'))
    axs[0].text(0.5, 0.03, 'premium axis cut at 60%', transform=axs[0].transAxes, fontsize=5.8, ha='right', color=COL['MONQ50'])
    sv(fig, 'fig_units')

# ---- F: trading (1 col) MONQ50 turnover and delivered share + before/after bars
def f_trading():
    a5 = J('a5')['intensity']; fig, (ax, ax2) = plt.subplots(2, 1, figsize=(W1, 3.6), gridspec_kw=dict(hspace=0.62))
    m = d[(d.symbol == 'MONQ50') & (d.s >= -30)].set_index('s'); ax.bar(m.index, 100 * m.turnover, color=COL['MONQ50'], width=0.8); ax.set_ylabel('Units traded,\n% of units outstanding'); ax.axvline(0.5, color='k', lw=0.5)
    b = ax.twinx(); b.plot(m.index, 100 * m.deliv_share, color='k', lw=0.8); b.set_ylabel('Delivered share (%)'); b.spines['right'].set_visible(True); b.set_ylim(0, 105); ax.set_xlabel('Sessions from the change of anchor'); ax.set_title('(a) MONQ50: turnover (bars) and delivered share (line)', fontsize=7.5, loc='left')
    order = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES', 'NIFTYBEES']; xs = np.arange(len(order))
    ax2.bar(xs - 0.2, [a5[s]['trades_pre'] for s in order], 0.4, color='0.75', label='60 sessions before'); ax2.bar(xs + 0.2, [a5[s]['trades_post'] for s in order], 0.4, color='0.25', label='47 sessions after')
    ax2.set_yscale('log'); ax2.set_xticks(xs); ax2.set_xticklabels(['MON\nQ50', 'MASP\nTOP50', 'MA\nFANG', 'MAHK\nTECH', 'MON\n100', 'HNGSNG\nBEES', 'NIFTY\nBEES'], fontsize=5.6); ax2.set_ylabel('Trades per day (median)'); ax2.legend(loc='upper left', ncol=2, fontsize=6); ax2.set_ylim(80, 4e5); ax2.set_title('(b) Number of trades', fontsize=7.5, loc='left')
    sv(fig, 'fig_trading')

# ---- F: Monte Carlo (1 col) from draft-2 results
def f_mc():
    R = json.load(open('/mnt/user-data/uploads/ETF_Premiums/paper/draft2_ieee/analysis/mc_results.json'))
    return R
# ---- F: money (1 col)
def f_money():
    a4 = J('a4'); fig, (ax, ax2) = plt.subplots(2, 1, figsize=(W1, 3.6), gridspec_kw=dict(hspace=0.62))
    x = d[(d.s >= -40) & d.symbol.isin(INTL)].copy(); x['ab'] = x.units * (x.close - x.nav_known) / 1e7; P = x.pivot(index='s', columns='symbol', values='ab')[['MON100', 'MAFANG', 'MASPTOP50', 'MONQ50', 'MAHKTECH', 'HNGSNGBEES']]
    ax.stackplot(P.index, P.values.T, colors=[COL[c] for c in P.columns], labels=P.columns, alpha=0.9, lw=0); ax.axvline(0.5, color='k', lw=0.5); ax.set_ylabel('Rs crore'); ax.legend(ncol=3, fontsize=5.6, loc='upper left', columnspacing=0.8, handlelength=1.2); ax.set_ylim(0, 8200); ax.set_title('(a) Market value above NAV, six funds', fontsize=7.5, loc='left'); ax.set_xlabel('Sessions from the change of anchor')
    wk = pd.DataFrame(a4['excess_weekly']).set_index('wk')[['MONQ50', 'MAFANG', 'MASPTOP50', 'MON100', 'MAHKTECH']]; bot = np.zeros(len(wk))
    for c in wk.columns: ax2.bar(range(len(wk)), wk[c], bottom=bot, color=COL[c], label=c, width=0.75); bot += wk[c].values
    ax2.set_xticks(range(len(wk))); ax2.set_xticklabels([pd.Timestamp(w[:10]).strftime('%d %b') for w in wk.index], rotation=45, ha='right', fontsize=6); ax2.set_ylabel('Rs crore'); ax2.legend(ncol=1, fontsize=5.6, loc='upper right'); ax2.set_title('(b) Paid above the old-rule ceiling, by week (NSE)', fontsize=7.5, loc='left')
    sv(fig, 'fig_money')

# ---- F: float (1 col)
def f_float():
    a4 = J('a4'); a3 = J('a3'); fr = {r['symbol']: r for r in a3['free']}; sn = {r['symbol']: r for r in a4['snapshot_last']}; fig, ax = plt.subplots(figsize=(W1, 2.4))
    for s in INTL:
        r = sn[s]; mk = 'o' if s in US else 's'; ax.plot(r['float_cr'], 100 * r['prem_known'], mk, color=COL[s], ms=5); ax.plot(r['float_cr'], fr[s]['prem_free'], mk, color=COL[s], ms=5, mfc='none')
        ax.plot([r['float_cr']] * 2, [fr[s]['prem_free'], 100 * r['prem_known']], color=COL[s], lw=0.5); ax.annotate(s, (r['float_cr'], 100 * r['prem_known']), xytext=(5, 2), textcoords='offset points', fontsize=6, color=COL[s])
    ax.plot([], [], 'o', color='k', ms=4, label='13 Nov 2026 (circles: US indices; squares: Hong Kong)'); ax.plot([], [], 'o', color='k', mfc='none', ms=4, label='first free close after the change')
    ax.set_xscale('log'); ax.set_xticks([100, 300, 1000, 3000, 10000, 30000]); ax.set_xticklabels(['100', '300', '1,000', '3,000', '10,000', '30,000']); ax.minorticks_off(); ax.set_xlabel('Units outstanding valued at NAV, Rs crore (log scale)'); ax.set_ylabel('Premium over last known NAV (%)'); ax.legend(loc='upper right', fontsize=6); ax.set_ylim(-3, 125)
    sv(fig, 'fig_float')

# ---- F: precedents (1 col)
def f_precedents():
    items = [('US natural-gas fund, 2009', 19, 'registered shares exhausted'), ('Egypt ETF (US-listed), 2011', 25, 'home market closed'), ('China overseas ETF, Jan 2024', 43, 'foreign-exchange quota'), ('Infosys receipts, 2002–03 (average)', 50, 'one-way conversion'), ('China overseas ETF, Jan 2025', 52, 'foreign-exchange quota'),
             ('US volatility note, 2012', 90, 'issuer stopped issuance'), ('Germany Fund, 1989–90', 100, 'closed-end country fund'), ('Bitcoin trust (US), peak', 133, 'no redemption; lock-up'), ('India: MONQ50, 18 Sep 2026', 233, 'overseas cap; band re-anchored')]
    fig, ax = plt.subplots(figsize=(W1, 2.6)); y = np.arange(len(items)); ax.barh(y, [i[1] for i in items], color=['0.6'] * (len(items) - 1) + [COL['MONQ50']], height=0.62)
    for i, it in enumerate(items): ax.text(it[1] + 3, i, f'{it[1]}%  ({it[2]})', va='center', fontsize=5.8)
    ax.set_yticks(y); ax.set_yticklabels([i[0] for i in items], fontsize=6.2); ax.set_xlim(0, 400); ax.set_xlabel('Peak premium over net asset or indicative value (%)'); ax.axvline(20, color='k', lw=0.5, ls=':')
    sv(fig, 'fig_precedents')

if __name__ == '__main__':
    import sys
    fs = dict(history=f_history, timing=f_timing, mechanism=f_mechanism, cliff=f_cliff, glide=f_glide, fence=f_fence, episode=f_episode, band=f_band, p1=f_p1, p2=f_p2, units=f_units, trading=f_trading, money=f_money, float=f_float, precedents=f_precedents)
    for k in (sys.argv[1:] or fs.keys()):
        try: fs[k](); print('ok', k)
        except Exception as e:
            import traceback; print('FAIL', k); traceback.print_exc()
