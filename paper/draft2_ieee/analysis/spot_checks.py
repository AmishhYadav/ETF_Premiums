"""Spot checks from exchange and AMFI files saved in tests/fixtures (primary sources).
Prints the numbers used in Tables 3, 5, 7 and 8 and Section 2.4 of the paper. No network access.
Usage: python3 spot_checks.py [path-to-fixtures]"""
import gzip, csv, io, math, json, sys, os
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
HERE = os.path.dirname(os.path.abspath(__file__))
FX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "..", "tests", "fixtures")   # repo: tests/fixtures
SYMS = ["MONQ50","MON100","MASPTOP50","MAFANG","MAHKTECH","HNGSNGBEES","NIFTYBEES"]
CODE = {"MONQ50":"149438","MON100":"114984","MASPTOP50":"149169","MAFANG":"148927","MAHKTECH":"149379","HNGSNGBEES":"140095","NIFTYBEES":"140084"}

def secmaster(d):
    out = {}
    with gzip.open(f"{FX}/nse_secmaster/NSE_CM_security_{d}.csv.gz","rt") as f:
        for r in csv.DictReader(f):
            if r["TckrSymb"] in SYMS and r["SctySrs"]=="EQ":
                lo,hi = r["PricRg"].split("-")
                out[r["TckrSymb"]] = dict(units=int(float(r["IssdCptl"])), lo=Decimal(lo), hi=Decimal(hi))
    return out
def navhist():
    out = {}
    for line in open(f"{FX}/amfi/navhistory_20260901_20260910.txt", encoding="utf-8", errors="ignore"):
        p = line.strip().split(";")
        if len(p) >= 8 and p[0] in CODE.values():
            out.setdefault(p[0], {})[p[7]] = Decimal(p[6])
    return out
def fl(x): return x.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
def ce(x): return x.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
def base_from_band(lo, hi, f_lo=Decimal("0.9"), f_hi=Decimal("1.1")):
    """all 2-decimal bases b with ceil(b*f_lo)=lo and floor(b*f_hi)=hi"""
    c = (lo/f_lo).quantize(Decimal("0.01")); sols=[]
    for k in range(-30,31):
        b = c + Decimal(k)/100
        if ce(b*f_lo)==lo and fl(b*f_hi)==hi: sols.append(b)
    return sols

print("=== A. Last session of the NAV-anchored regime: 4 Sep 2026 ===")
sm = secmaster("04092026"); nav = navhist()
print(f"{'fund':11s} {'NAV 2Sep':>9s} {'limit':>8s} {'close':>8s} {'at lim':>6s} {'NAV 4Sep':>9s} {'quoted%':>8s} {'cap-on-quoted%':>8s}")
resA = {}
for s in SYMS:
    b = base_from_band(sm[s]["lo"], sm[s]["hi"])
    n2 = nav[CODE[s]]["02-Sep-2026"]; n4 = nav[CODE[s]]["04-Sep-2026"]
    lim = fl(n2*Decimal("1.2"))
    close = b[0] if len(b)==1 else None
    at = (lim in b)
    if at: close = lim
    q = float(close/n4-1)*100; cap = float(lim/n4-1)*100
    resA[s] = dict(nav2=float(n2), limit=float(lim), close=float(close), at=at, nav4=float(n4), quoted=q, cap=cap, cands=[str(x) for x in b])
    print(f"{s:11s} {n2:9.4f} {lim:8.2f} {close:8.2f} {str(at):>6s} {n4:9.4f} {q:8.2f} {cap:8.2f}   candidates={[str(x) for x in b]}")
print("two-day NAV change 2->4 Sep (%):", {s: round((resA[s]['nav4']/resA[s]['nav2']-1)*100,3) for s in SYMS})

print("\n=== B. NAV dates published 1-10 Sep 2026 ===")
for s in SYMS: print(s, sorted(nav[CODE[s]].keys()))
print("MONQ50 NAV 4->7 Sep change (%):", float(nav[CODE['MONQ50']]['07-Sep-2026']/nav[CODE['MONQ50']]['04-Sep-2026']-1)*100)
print("MON100 NAV 4->7 Sep change (%):", float(nav[CODE['MON100']]['07-Sep-2026']/nav[CODE['MON100']]['04-Sep-2026']-1)*100)
# first-day band 7 Sep for MONQ50
b = resA["MONQ50"]["close"]; print("MONQ50 7 Sep: initial band", sm["MONQ50"]["lo"], sm["MONQ50"]["hi"], "max upper", math.floor(b*1.2*100)/100, "1st widen upper", math.floor(b*1.15*100)/100, "+15.6% =", round(b*1.156,2))

print("\n=== C. Cross-section on 9 Nov 2026 ===")
sm9 = secmaster("09112026")
bh = {}
for r in csv.reader(open(f"{FX}/nse_bhavcopy/sec_bhavdata_full_09112026.csv")):
    r=[x.strip() for x in r]
    if r[0] in SYMS and r[1]=="EQ": bh[r[0]] = dict(prev=float(r[3]),open=float(r[4]),high=float(r[5]),low=float(r[6]),last=float(r[7]),close=float(r[8]),qty=int(r[10]),val_lacs=float(r[11]),trades=int(r[12]),deliv=float(r[14]))
navall = {}
for line in open(f"{FX}/amfi/NAVAll.txt", encoding="utf-8", errors="ignore"):
    p=line.strip().split(";")
    if len(p)>=8 and p[0] in CODE.values(): navall[p[0]] = (float(p[6]), p[7])
api = {r["symbol"]: float(r["nav"]) for r in json.load(open(f"{FX}/nse_etf_api/api_etf.json"))["data"] if r["symbol"] in SYMS}
tot_prem=0; tot_nav=0
print(f"{'fund':11s} {'close':>7s} {'NAV9':>9s} {'q.prem%':>7s} {'NAVknown':>9s} {'k.prem%':>7s} {'units(m)':>9s} {'NAVval cr':>9s} {'mktval cr':>9s} {'excess cr':>9s} {'turn%':>6s} {'deliv%':>6s} {'Rs/trade':>8s} {'range%':>6s}")
resC={}
for s in SYMS:
    c=bh[s]["close"]; n9=navall[CODE[s]][0]; nk=api[s]; u=sm9[s]["units"]
    navval=u*n9/1e7; mkt=u*c/1e7; ex=mkt-navval
    turn=bh[s]["qty"]/u*100; pertrade=bh[s]["val_lacs"]*1e5/bh[s]["trades"]; rng=(bh[s]["high"]-bh[s]["low"])/bh[s]["prev"]*100
    resC[s]=dict(close=c,nav9=n9,q=(c/n9-1)*100,navk=nk,k=(c/nk-1)*100,units=u/1e6,navval=navval,mkt=mkt,excess=ex,turn=turn,deliv=bh[s]["deliv"],pertrade=pertrade,rng=rng,navchg=(n9/nk-1)*100)
    if s!="NIFTYBEES": tot_prem+=ex; tot_nav+=navval
    print(f"{s:11s} {c:7.2f} {n9:9.4f} {(c/n9-1)*100:7.1f} {nk:9.4f} {(c/nk-1)*100:7.1f} {u/1e6:9.2f} {navval:9.1f} {mkt:9.1f} {ex:9.1f} {turn:6.2f} {bh[s]['deliv']:6.1f} {pertrade:8.0f} {rng:6.1f}")
print("NAV dates in NAVAll:", {s:navall[CODE[s]][1] for s in SYMS})
print(f"Six funds: value at NAV Rs {tot_nav:,.0f} crore; market value above NAV Rs {tot_prem:,.0f} crore ({tot_prem/tot_nav*100:.1f}% of NAV value)")
print("NAV change known->dated 9 Nov (%):", {s: round(resC[s]['navchg'],2) for s in SYMS})
print("next-session band from 9 Nov file:", {s:(str(sm9[s]['lo']),str(sm9[s]['hi'])) for s in SYMS[:2]})

print("\n=== D. iNAV check, Motilal (frozen end-of-day values of 9 Nov session) ===")
d=json.load(open(f"{FX}/amc_inav/motilal_GetINAVandPrice.json"))["data"]["data"]
for grp in d.values():
    for r in grp:
        if r.get("nseSymbol") in ("MONQ50","MON100") and "iNAV" in r["secname"]:
            print(r["nseSymbol"], "iNAV", r["currNav"], "prevNAV", r["prevNAV"], "change % =", round((float(r["currNav"])/float(r["prevNAV"])-1)*100,4))
m=json.load(open(f"{FX}/amc_inav/mirae_ticker.json"))
for r in m:
    if r["NSE_Symbol"] in ("MAFANG","MASPTOP50","MAHKTECH"):
        print(r["NSE_Symbol"], "iNAV", r["INAV"], "vs NSE-displayed NAV", api[r["NSE_Symbol"]], "change % =", round((float(r["INAV"])/api[r["NSE_Symbol"]]-1)*100,4))

print("\n=== E. Units, creation units ===")
u = {d: secmaster(d) for d in ["01072024","02012025","04092026","09112026"]}
cu = {"MONQ50":600000,"MON100":250000,"MAHKTECH":800000,"HNGSNGBEES":25000}
for s in SYMS[:6]:
    a=[u[d][s]["units"] for d in ["01072024","02012025","04092026","09112026"]]
    msg = f"{s:11s} {a}  change Jul24->Sep26 {a[2]-a[0]:+,}"
    if s in cu: msg += f"  = {(a[2]-a[0])/cu[s]:.2f} creation units; Jul24->Jan25 {(a[1]-a[0])/cu[s]:.2f}; Jan25->Sep26 {(a[2]-a[1])/cu[s]:.2f}"
    print(msg)
json.dump(dict(A=resA,C=resC), open(os.path.join(HERE, "spot_checks.json"),"w"), indent=1)
