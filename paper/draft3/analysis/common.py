"""Shared loader for the draft-3 analysis. Reads only the research export."""
import numpy as np, pandas as pd, json, os
EXPORT = os.environ.get('ETFP_EXPORT', '/mnt/user-data/uploads/ETF_Premiums/exports/research/2026-11-14/')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'out')
FIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'figures')
INTL = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MAHKTECH', 'MON100', 'HNGSNGBEES']
US = ['MONQ50', 'MASPTOP50', 'MAFANG', 'MON100']
DOM = ['NIFTYBEES', 'NIFTYIETF', 'JUNIORBEES', 'BANKBEES']
BULL = ['GOLDBEES', 'SILVERBEES']
T0 = pd.Timestamp('2026-09-04')   # last session of the NAV-anchored band
T1 = pd.Timestamp('2026-09-07')   # first session of the previous-close band
BOOLS = ['hit_up', 'hit_up_max', 'last_at_up', 'close_at_upper', 'hit_up_official', 'hit_down',
         'hit_down_max', 'last_at_down', 'close_at_lower', 'band_mismatch', 'rationed', 'widened_up', 'widened_down']

def load():
    d = pd.read_parquet(EXPORT + 'etf_day.parquet')
    d['date'] = pd.to_datetime(d['date'])
    for c in BOOLS:
        if c in d: d[c] = d[c].astype('boolean').fillna(False).astype(bool)
    d = d.sort_values(['symbol', 'date']).reset_index(drop=True)
    d['lp'] = np.log(d['close'] / d['nav_known'])            # log premium over last known NAV
    d['ceil'] = np.log(d['band_high'] / d['nav_known'])      # official ceiling on lp
    d['close_eq'] = d['close'] >= d['band_high'] - 0.0101    # close equals the official upper limit
    d['capped'] = (d['close_eq'] | d['last_at_up']) & (d['date'] <= T0)
    d['ret'] = d.groupby('symbol')['close'].transform(lambda s: np.log(s).diff())
    d['dlp'] = d.groupby('symbol')['lp'].diff()
    # session index relative to the change (7 Sep 2026 = 1, 4 Sep = 0)
    sess = sorted(d.loc[d.symbol == 'NIFTYBEES', 'date'].unique())
    idx = {pd.Timestamp(x): i for i, x in enumerate(sess)}
    z = idx[T1]
    d['s'] = d['date'].map(lambda x: idx.get(x, np.nan) - z + 1)
    return d

def save(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name + '.json'), 'w') as f:
        json.dump(obj, f, indent=1, default=lambda o: (o.item() if hasattr(o, 'item') else str(o)))
