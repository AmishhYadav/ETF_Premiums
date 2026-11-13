"""Figures for the paper. Run: python3 figures.py  (writes ../figures/*.pdf and *.png)"""
import json, os, math, datetime as dt
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager as fm
for f in ["/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyretermes-regular.otf",
          "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyretermes-italic.otf",
          "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyretermes-bold.otf"]:
    if os.path.exists(f): fm.fontManager.addfont(f)
plt.rcParams.update({"font.family": ["Liberation Serif", "DejaVu Serif"], "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 0.6, "axes.edgecolor": "#52514e",
    "xtick.color": "#52514e", "ytick.color": "#52514e", "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "axes.labelcolor": "#0b0b0b", "text.color": "#0b0b0b", "grid.color": "#e3e2dd", "grid.linewidth": 0.5,
    "legend.frameon": False, "pdf.fonttype": 42, "mathtext.fontset": "stix", "savefig.bbox": "tight", "savefig.pad_inches": 0.03})
SEED3 = 3
BLUE, ORANGE, AQUA, INK2, MUTE = "#2a78d6", "#eb6834", "#1baf7a", "#52514e", "#9a9992"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "figures")
os.makedirs(OUT, exist_ok=True)
W = 6.3
def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".pdf")); fig.savefig(os.path.join(OUT, name + ".png"), dpi=300); plt.close(fig)

# ---------------- Figure 1: MONQ50 price, NAV and the old ceiling ----------------
def fig1():
    D = lambda m, d: dt.date(2026, m, d)
    obs = [(D(9,4),141.93,118.7371),(D(9,7),164.07,118.7326),(D(9,9),213.01,116.4200),(D(9,16),275.28,116.69),(D(9,17),330.25,119.1493),
           (D(9,18),396.30,118.1381),(D(9,21),317.04,120.2333),(D(9,22),253.64,120.17),(D(9,23),202.92,120.59),(D(9,30),194.62,119.68),
           (D(10,7),258.57,123.09),(D(10,14),249.83,117.96),(D(11,4),219.38,117.76),(D(11,9),229.67,114.3849)]
    x = [o[0] for o in obs]; p = np.array([o[1] for o in obs]); n = np.array([o[2] for o in obs])
    fig, ax = plt.subplots(figsize=(W, 3.1))
    ax.axvline(D(9,5) + dt.timedelta(days=0.5), color=INK2, lw=0.6, ls=(0, (2, 2)))
    ax.plot(x, n, color=MUTE, lw=1.2, marker="o", ms=2.5)
    ax.plot(x, 1.2 * n, color=ORANGE, lw=1.2, ls=(0, (4, 2)))
    ax.plot(x, p, color=BLUE, lw=1.6, marker="o", ms=3.5, mfc=BLUE, mec="white", mew=0.5)
    ax.plot([D(9,21)], [471.99], marker="^", ms=5, color=BLUE, mec="white", mew=0.5, ls="none")
    ax.plot([D(9,21), D(9,21)], [317.04, 471.99], color=BLUE, lw=0.6)
    ax.set_yscale("log"); ax.set_yticks([100, 150, 200, 300, 400, 500]); ax.set_yticklabels(["100", "150", "200", "300", "400", "500"])
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator()); ax.set_ylim(95, 560)
    ax.set_xlim(D(9,1), D(11,24)); ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    ax.grid(axis="y"); ax.set_ylabel("Rs per unit (log scale)")
    ax.text(D(11,10), 229.67, " Closing price", color="#0b0b0b", va="center", fontsize=8.5)
    ax.text(D(11,10), 1.2*114.3849*1.02, " Ceiling under the\n NAV-anchored band", color="#0b0b0b", va="center", fontsize=8.5, linespacing=1.0)
    ax.text(D(11,10), 114.3849*0.965, " NAV", color="#0b0b0b", va="center", fontsize=8.5)
    ax.annotate("18 Sep: Rs 396.30, 235% above NAV", xy=(D(9,18), 396.30), xytext=(D(9,24), 392), fontsize=8, color=INK2, linespacing=1.05,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK2), va="center")
    ax.annotate("21 Sep: high Rs 471.99, close Rs 317.04\n(upper band to lower limit in one session)", xy=(D(9,21), 471.99), xytext=(D(9,26), 505), fontsize=8, color=INK2, va="center", linespacing=1.05,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK2))
    ax.text(D(9,6) + dt.timedelta(days=0.2), 100.5, "7 Sep: band re-anchored from lagged NAV to the previous close", fontsize=7.5, color=INK2, va="bottom")
    save(fig, "fig1_monq50")

# ---------------- Figure 2: information timeline ----------------
def fig2():
    fig, ax = plt.subplots(figsize=(W, 2.3))
    ax.set_xlim(-9.5, 62); ax.set_ylim(-0.75, 3.45); ax.axis("off")      # x: hours from 00:00 IST on day t-1
    YI, YU, YN = 2.25, 1.30, 0.42
    for k, lab in enumerate(["Day $t-1$", "Day $t$", "Day $t+1$"]):
        ax.plot([24*k, 24*k], [-0.25, 3.12], color="#d9d8d2", lw=0.6)
        ax.text(24*k + 0.5, 3.16, lab, fontsize=8.5, color=INK2, va="bottom")
    ax.plot([0, 61], [-0.25, -0.25], color=INK2, lw=0.6)
    for h in range(0, 61, 6):
        ax.plot([h, h], [-0.25, -0.31], color=INK2, lw=0.5); ax.text(h, -0.37, f"{h % 24:02d}:00", ha="center", va="top", fontsize=7, color=INK2)
    def bar(x0, x1, y, col, lab):
        ax.add_patch(plt.Rectangle((x0, y - 0.16), x1 - x0, 0.32, color=col, lw=0))
        if lab: ax.text((x0 + x1) / 2, y, lab, ha="center", va="center", fontsize=7.5, color="white")
    for y, lab in [(YI, "Indian session"), (YU, "US session"), (YN, "NAV published")]:
        ax.text(-1.0, y, lab, fontsize=8, va="center", ha="right", color="#0b0b0b")
    bar(9.25, 15.5, YI, BLUE, "$t-1$"); bar(33.25, 39.5, YI, BLUE, "$t$"); bar(57.25, 61, YI, BLUE, "")
    bar(0, 1.5, YU, ORANGE, ""); ax.text(1.9, YU, "$t-2$", fontsize=7.5, va="center", color=INK2)
    bar(19, 25.5, YU, ORANGE, "$t-1$"); bar(43, 49.5, YU, ORANGE, "$t$")
    for x, lab in [(10, "NAV$_{t-2}$"), (34, "NAV$_{t-1}$"), (58, "NAV$_{t}$")]:
        ax.plot([x], [YN + 0.08], marker="v", ms=5, color=INK2); ax.text(x + 0.9, YN + 0.08, lab + ", by 10:00", ha="left", va="center", fontsize=7.5)
    ax.text(33.25, YI + 0.26, "band for day $t$ is set before 09:15,\nwhen the newest NAV is NAV$_{t-2}$", fontsize=7.5, color=INK2, ha="center", va="bottom", linespacing=1.05)
    ax.text(46.25, YU + 0.26, "this close sets NAV$_{t}$, ten hours\nafter the Indian close of day $t$", fontsize=7.5, color=INK2, ha="center", va="bottom", linespacing=1.05)
    save(fig, "fig2_timeline")

# ---------------- Figure 3: a fence and a speed limit (simulated) ----------------
def fig3():
    rng = np.random.default_rng(SEED3)
    T0, H = 80, 45; mu, rho, sig, sn = 0.58, 0.965, 0.024, 0.010
    ys = np.empty(T0 + H); prev = 0.02
    for t in range(T0 + H):
        prev = mu * (1 - rho) + rho * prev + sig * rng.normal(); ys[t] = prev
    n = rng.normal(0, sn, T0 + H + 2); c = math.log(1.2) - (n[2:] + n[1:-1])
    obs = np.minimum(ys, c).copy(); obs2 = obs.copy(); step = math.log(1.2)
    for t in range(T0, T0 + H):            # speed limit: at most +/- ln(1.2) a day from the previous close
        obs[t] = min(max(ys[t], obs[t-1] - step), obs[t-1] + step)
    ind = np.zeros(T0 + H)                 # induced demand: buying that follows the previous day's rise, then decays
    for t in range(T0, T0 + H):
        ind[t] = 0.90 * ind[t-1] + 0.50 * max(obs2[t-1] - obs2[t-2], 0.0)
        obs2[t] = min(max(ys[t] + ind[t], obs2[t-1] - step), obs2[t-1] + step)
    P = lambda v: 100 * np.expm1(v)
    x = np.arange(T0 + H) - T0
    fig, ax = plt.subplots(figsize=(W, 3.0))
    top = max(P(obs2).max(), P(ys).max()) * 1.12
    ax.axvline(-0.5, color=INK2, lw=0.6, ls=(0, (2, 2)))
    ax.plot(x[:T0], P(c[:T0]), color=ORANGE, lw=1.0, ls=(0, (4, 2)))
    ax.plot(x, P(ys), color=MUTE, lw=1.2)
    ax.plot(x[T0-1:], P(obs2[T0-1:]), color=AQUA, lw=1.5, ls=(0, (1, 1)))
    ax.plot(x, P(obs), color=BLUE, lw=1.6)
    ax.set_xlim(-T0, H + 22); ax.set_ylim(-4, top)
    ax.set_xticks([-80, -60, -40, -20, 0, 20, 40])
    ax.set_xlabel("Sessions from the change of anchor"); ax.set_ylabel("Premium over fair value (%)"); ax.grid(axis="y")
    e = T0 + H - 1
    ax.text(H + 1, P(obs2[e]) + 6, "Price with\ninduced demand", fontsize=8, va="center", linespacing=1.0)
    ax.text(H + 1, P(obs[e]) - 12, "Price under pure\nrevelation (equals\nlatent premium)", fontsize=8, va="center", linespacing=1.0)
    k = T0 - 30
    ax.annotate("Latent premium, hidden while\nthe price sits at the ceiling", xy=(x[k], P(ys[k])), xytext=(-78, top * 0.78), fontsize=8, color=INK2, linespacing=1.05,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK2), va="center")
    ax.annotate("Observed price: capped at the\nmoving ceiling of the value anchor", xy=(x[T0 - 14], P(obs[T0 - 14])), xytext=(-66, 5.5), fontsize=8, color=INK2, linespacing=1.05,
                arrowprops=dict(arrowstyle="-", lw=0.5, color=INK2), va="center")
    ax.text(-2.0, top * 0.985, "value anchor", ha="right", va="top", fontsize=7.5, color=INK2)
    ax.text(1.0, top * 0.985, "price anchor", ha="left", va="top", fontsize=7.5, color=INK2)
    save(fig, "fig3_fence_speed")

# ---------------- Figure 4: Monte Carlo ----------------
def fig4():
    R = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc_results.json")))
    fig, axes = plt.subplots(1, 3, figsize=(W, 2.5), sharey=True)
    for ax, mu in zip(axes, ["0.1", "0.2", "0.3"]):
        rs = R[mu]; share = np.mean([r["share"] for r in rs])
        data = [[100 * (np.expm1(r[k]["hid"]) - np.expm1(r["truth_hid"])) for r in rs] for k in ("naive", "fixed", "moving")]
        bp = ax.boxplot(data, widths=0.5, patch_artist=True, showfliers=False, medianprops=dict(color="white", lw=1.4),
                        whiskerprops=dict(color=INK2, lw=0.7), capprops=dict(color=INK2, lw=0.7), boxprops=dict(lw=0))
        for patch, col in zip(bp["boxes"], [MUTE, ORANGE, BLUE]): patch.set_facecolor(col)
        ax.axhline(0, color=INK2, lw=0.6); ax.grid(axis="y")
        ax.set_xticks([1, 2, 3]); ax.set_xticklabels(["Censoring\nignored", "Fixed\nceiling", "Moving\nceiling"], fontsize=7.5)
        ax.set_title(f"{100*share:.0f}% of days capped", fontsize=8.5)
    axes[0].set_ylabel("Estimated minus true hidden premium\non the last capped day (points)")
    fig.subplots_adjust(wspace=0.08)
    save(fig, "fig4_montecarlo")

# ---------------- Figure 5: the cliff ----------------
def fig5():
    S = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "spot_checks.json")))["C"]
    pi = np.linspace(0, 2.5, 500)
    fig, ax = plt.subplots(figsize=(W, 2.9))
    ax.plot(100 * pi, 100 * np.maximum(0, 1 - 1.2 / (1 + pi)), color=BLUE, lw=1.6)
    ax.plot(100 * pi, 100 * np.maximum(0, 1 - 1.1 / (1 + pi)), color=ORANGE, lw=1.2, ls=(0, (4, 2)))
    lab = {"MONQ50": (113, 38.5, "left", 0), "MAFANG": (47, 28.5, "right", 0), "MASPTOP50": (58, 17.5, "left", 0),
           "MAHKTECH": (38.5, 8.0, "left", 0), "MON100": (26.5, 2.6, "left", 0), "HNGSNGBEES": (4.2, 3.5, "center", 90)}
    for s in ["MONQ50", "MAFANG", "MASPTOP50", "MAHKTECH", "MON100", "HNGSNGBEES"]:
        k = S[s]["k"] / 100; y = 100 * max(0, 1 - 1.2 / (1 + k))
        ax.plot([100 * k], [y], marker="o", ms=5, color=BLUE, mec="white", mew=0.7, zorder=5, clip_on=False)
        ax.text(lab[s][0], lab[s][1], s, fontsize=7.5, color="#0b0b0b", ha=lab[s][2], va="bottom" if lab[s][3] else "center", rotation=lab[s][3])
    ax.plot([235.5], [100 * (1 - 1.2 / 3.355)], marker="D", ms=4.5, color=INK2, mec="white", mew=0.7, zorder=5)
    ax.text(236, 100 * (1 - 1.2 / 3.355) - 9.5, "MONQ50 at its\n18 Sep peak", fontsize=7.5, ha="center", va="center", color=INK2, linespacing=1.0)
    ax.text(170, 100 * (1 - 1.2 / 2.7) - 6.5, "after the band has widened twice (20%)", fontsize=8, color="#0b0b0b", rotation=13, ha="center", va="center")
    ax.text(150, 100 * (1 - 1.1 / 2.5) + 6.0, "at the first trade (10% band)", fontsize=8, color="#0b0b0b", rotation=14.5, ha="center", va="center")
    ax.set_xlim(0, 250); ax.set_ylim(0, 75); ax.grid(axis="y")
    ax.set_xlabel("Premium over the anchoring NAV on the eve of the switch (%)"); ax.set_ylabel("Smallest possible one-day fall (%)")
    save(fig, "fig5_cliff")

# ---------------- Figure 6: cross-section on 9 Nov 2026 ----------------
def fig6():
    S = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "spot_checks.json")))["C"]
    fig, ax = plt.subplots(figsize=(W, 2.9))
    us = ["MONQ50", "MASPTOP50", "MAFANG", "MON100"]; hk = ["MAHKTECH", "HNGSNGBEES"]
    ax.plot([S[s]["navval"] for s in us], [S[s]["k"] for s in us], ls="none", marker="o", ms=7, color=BLUE, mec="white", mew=0.8, label="US indices")
    ax.plot([S[s]["navval"] for s in hk], [S[s]["k"] for s in hk], ls="none", marker="s", ms=6.5, color=ORANGE, mec="white", mew=0.8, label="Hong Kong indices")
    off = {"MONQ50": (1.12, 0, "left"), "MASPTOP50": (1.12, 3, "left"), "MAFANG": (1.12, -2, "left"), "MON100": (0.9, 6, "right"), "MAHKTECH": (1.12, 0, "left"), "HNGSNGBEES": (1.0, 7, "center")}
    for s in us + hk:
        ax.text(S[s]["navval"] * off[s][0], S[s]["k"] + off[s][1], s, fontsize=7.5, ha=off[s][2], va="center")
    ax.set_xscale("log"); ax.set_xlim(100, 30000); ax.set_ylim(-5, 120); ax.grid(axis="y")
    ax.set_xticks([100, 300, 1000, 3000, 10000, 30000]); ax.set_xticklabels(["100", "300", "1,000", "3,000", "10,000", "30,000"]); ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_xlabel("Units outstanding at NAV, Rs crore (log scale)"); ax.set_ylabel("Premium over last known NAV (%)")
    ax.legend(loc="upper right", fontsize=8, handletextpad=0.2)
    save(fig, "fig6_cross_section")

if __name__ == "__main__":
    import sys
    which = sys.argv[1:] or ["1", "2", "3", "4", "5", "6"]
    for w in which: globals()["fig" + w]()
    print("figures written:", which)
