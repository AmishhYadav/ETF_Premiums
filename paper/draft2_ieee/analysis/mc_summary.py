"""Summarise mc_results.json into mc_table.tex and mc_macros.tex."""
import json, numpy as np
R = json.load(open("mc_results.json"))   # built by: python3 mc_censored.py 0.1 240 (then 0.2, 0.3) and merged, see README
EST = [("naive", "Censoring ignored"), ("fixed", "Fixed ceiling"), ("moving", "Moving ceiling")]
MU = ["0.1", "0.2", "0.3"]; DELTA = [0.0, 0.3, 0.6]
S = {}
for mu in MU:
    rs = R[mu]; n = len(rs)
    truth = np.array([np.expm1(r["truth_hid"]) for r in rs]) * 100
    S[mu] = dict(n=n, share=100 * np.mean([r["share"] for r in rs]), truth=truth.mean())
    for k, _ in EST:
        hid = np.array([np.expm1(r[k]["hid"]) for r in rs]) * 100
        d = dict(bias=(hid - truth).mean(), rmse=np.sqrt(((hid - truth) ** 2).mean()), rho=np.mean([r[k]["rho"] for r in rs]))
        lat_log = np.array([r["post_lat_log"] for r in rs]); lat_lev = np.array([r["post_lat_lev"] for r in rs])
        q05 = np.array([r[k]["p05"] for r in rs]); q95 = np.array([r[k]["p95"] for r in rs]); flev = np.array([r[k]["flev"] for r in rs])
        g05 = np.array([r[k]["q05"] for r in rs]); g95 = np.array([r[k]["q95"] for r in rs])
        d["plug_above"] = 100 * np.mean(lat_log > g95); d["plug_below"] = 100 * np.mean(lat_log < g05)
        for dl in DELTA:
            real_log = lat_log + dl; real_lev = np.exp(dl) * (lat_lev + 1) - 1
            d[f"above{dl}"] = 100 * np.mean(real_log > q95); d[f"below{dl}"] = 100 * np.mean(real_log < q05)
            d[f"iota{dl}"] = 100 * np.median(1 - flev / real_lev); d[f"iotatrue{dl}"] = 100 * np.median(1 - lat_lev / real_lev)
        S[mu][k] = d
f = lambda x, p=1: f"{x:.{p}f}".replace("-", "$-$")
L = []
L.append(r"\begin{table*}[!t]")
L.append(r"\centering\footnotesize\setlength{\tabcolsep}{4.2pt}")
L.append(r"\caption{Monte Carlo: recovering a known hidden premium and testing for induced demand}\label{tab:mc}")
L.append(r"\begin{tabular}{lrrrrrrrrr}")
L.append(r"\toprule")
L.append(r" & \multicolumn{3}{c}{" + f(S['0.1']['share'],0) + r"\% of days capped} & \multicolumn{3}{c}{" + f(S['0.2']['share'],0) + r"\% of days capped} & \multicolumn{3}{c}{" + f(S['0.3']['share'],0) + r"\% of days capped}\\")
L.append(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
L.append(r" & Ignored & Fixed & Moving & Ignored & Fixed & Moving & Ignored & Fixed & Moving\\")
L.append(r"\midrule")
L.append(r"\multicolumn{10}{l}{\emph{A. Hidden premium on the last capped day, percentage points}}\\")
L.append("True value, mean & " + " & ".join(r"\multicolumn{3}{c}{" + f(S[mu]["truth"]) + "}" for mu in MU) + r"\\")
L.append("Mean error & " + " & ".join(f(S[mu][k]["bias"]) for mu in MU for k, _ in EST) + r"\\")
L.append("Root mean squared error & " + " & ".join(f(S[mu][k]["rmse"]) for mu in MU for k, _ in EST) + r"\\")
L.append("Estimated $\\rho$ (true 0.985) & " + " & ".join(f(S[mu][k]["rho"], 3) for mu in MU for k, _ in EST) + r"\\")
L.append(r"\addlinespace")
L.append(r"\multicolumn{10}{l}{\emph{B. Realised premium above the 90\% forecast band, \% of replications}}\\")
L.append("No induced demand, plug-in & " + " & ".join(f(S[mu][k]["plug_above"]) for mu in MU for k, _ in EST) + r"\\")
L.append("No induced demand (nominal 5) & " + " & ".join(f(S[mu][k]["above0.0"]) for mu in MU for k, _ in EST) + r"\\")
L.append("Induced, 30 log points & " + " & ".join(f(S[mu][k]["above0.3"]) for mu in MU for k, _ in EST) + r"\\")
L.append("Induced, 60 log points & " + " & ".join(f(S[mu][k]["above0.6"]) for mu in MU for k, _ in EST) + r"\\")
L.append(r"\addlinespace")
L.append(r"\multicolumn{10}{l}{\emph{C. Realised premium below the band, no induced demand (nominal 5)}}\\")
L.append(r"\% of replications & " + " & ".join(f(S[mu][k]["below0.0"]) for mu in MU for k, _ in EST) + r"\\")
L.append(r"\addlinespace")
L.append(r"\multicolumn{10}{l}{\emph{D. Induced share, median, \%}}\\")
for dl, lab in [(0.0, "No induced demand: true 0"), (0.3, "30 log points: true "), (0.6, "60 log points: true ")]:
    tv = "" if dl == 0 else "/".join(f(S[mu]["moving"][f"iotatrue{dl}"], 0) for mu in MU)
    L.append(lab + tv + " & " + " & ".join(f(S[mu][k][f"iota{dl}"], 0) for mu in MU for k, _ in EST) + r"\\")
L.append(r"\bottomrule")
L.append(r"\end{tabular}")
L.append(r"\tabnote{" + f"{S['0.2']['n']}" + r" replications per design; 400 pre-change sessions; forecast window sessions 16 to 40. `Ignored': autoregression on the observed premium. `Fixed': censored autoregression on the premium over the band's base with ceiling $\ln 1.2$. `Moving': censored autoregression on the fair-value premium with the day's ceiling. Panel A compares the estimated hidden premium on the last pre-change day, which is capped in every replication, with the true latent premium. Panels B and C count replications in which the realised average log premium lies outside the 90\% forecast band; except in the row marked plug-in, the band allows for parameter uncertainty by drawing parameters from their estimated asymptotic distribution. In Panel D the three true values are for the three designs in order. Simulated data; details in Appendix~\ref{app:mc}.}")
L.append(r"\end{table*}")
open("mc_table.tex", "w").write("\n".join(L) + "\n")
mv = [S[mu]["moving"] for mu in MU]; nv = [S[mu]["naive"] for mu in MU]; fx = [S[mu]["fixed"] for mu in MU]
out = lambda a: f"{a:.0f}"
M = {
 "mcReps": str(S["0.2"]["n"]), "mcShareA": out(S["0.1"]["share"]), "mcShareB": out(S["0.2"]["share"]), "mcShareC": out(S["0.3"]["share"]),
 "mcBiasNaiveA": f"{min(abs(d['bias']) for d in nv):.0f}", "mcBiasNaiveC": f"{max(abs(d['bias']) for d in nv):.0f}",
 "mcBiasMovMin": f"{min(d['bias'] for d in mv):.1f}".replace("-", "$-$"), "mcBiasMovMax": f"{max(d['bias'] for d in mv):.1f}".replace("-", "$-$"),
 "mcBiasFixMin": f"{min(d['bias'] for d in fx):.1f}".replace("-", "$-$"), "mcBiasFixMax": f"{max(d['bias'] for d in fx):.1f}".replace("-", "$-$"),
 "mcRmseMovA": f"{mv[0]['rmse']:.1f}", "mcRmseFixA": f"{fx[0]['rmse']:.1f}", "mcRmseMovC": f"{mv[2]['rmse']:.1f}", "mcRmseFixC": f"{fx[2]['rmse']:.1f}",
 "mcSizeMovMin": out(min(d["above0.0"] for d in mv)), "mcSizeMovMax": out(max(d["above0.0"] for d in mv)),
 "mcBelowMovMin": out(min(d["below0.0"] for d in mv)), "mcBelowMovMax": out(max(d["below0.0"] for d in mv)),
 "mcPlugMovMin": out(min(d["plug_above"] for d in mv)), "mcPlugMovMax": out(max(d["plug_above"] for d in mv)),
 "mcPowMovLoMin": out(min(d["above0.3"] for d in mv)), "mcPowMovLoMax": out(max(d["above0.3"] for d in mv)),
 "mcPowMovHiMin": out(min(d["above0.6"] for d in mv)), "mcPowMovHiMax": out(max(d["above0.6"] for d in mv)),
 "mcSizeNaiveMin": out(min(d["above0.0"] for d in nv)), "mcSizeNaiveMax": out(max(d["above0.0"] for d in nv)),
 "mcIotaNaiveMin": out(min(d["iota0.0"] for d in nv)), "mcIotaNaiveMax": out(max(d["iota0.0"] for d in nv)),
}
open("mc_macros.tex", "w").write("".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in M.items()))
for mu in MU:
    print(mu, {k: (round(v, 2) if not isinstance(v, dict) else {a: round(b, 2) for a, b in v.items()}) for k, v in S[mu].items()})
