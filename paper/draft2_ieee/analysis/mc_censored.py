"""Monte Carlo check of the hidden-premium estimator (Section 6.2, Appendix B).

Latent log premium  y*_t = mu(1-rho) + rho y*_{t-1} + sigma e_t.
NAV-anchored band:  observed y_t = min(y*_t, c_t),  c_t = ln(1.2) - (n_t + n_{t-1}),
where n_t is the daily log change in fair value (the ceiling on the fair-value premium moves
with the two-day change in value).  After the re-anchoring the price is free: y_t = y*_t + delta,
delta = induced demand (0 under pure revelation).

Estimators of (mu, rho, sigma) on the pre-period:
  naive  : OLS AR(1) on the observed premium, censoring ignored
  fixed  : censored AR(1) on the band-reference premium ln(P_t/NAV_{t-2}) with a fixed ceiling ln(1.2)
  moving : censored AR(1) on the fair-value premium with the day's ceiling c_t   (the paper's estimator)
Censored likelihoods are exact up to grid error: a forward filter integrates the latent state over
runs of capped days on a grid.  Forecast bands are reported two ways: plug-in (parameters fixed at their
estimates) and with parameter uncertainty (one draw per simulated path from the asymptotic normal
distribution of the estimates, from the numerical Hessian).
"""
import numpy as np, json, sys, time
from scipy.optimize import minimize
from scipy.stats import norm
from multiprocessing import Pool

LN12 = np.log(1.2)
T0, H = 400, 40
RHO, SIG, SN = 0.985, 0.02, 0.012
GRID = np.linspace(0.02, 1.30, 193)      # support of the latent state on capped days
DG = GRID[1] - GRID[0]

def simulate(mu, rng):
    sd0 = SIG / np.sqrt(1 - RHO**2)
    n = rng.normal(0, SN, T0 + H + 2)
    ys = np.empty(T0 + H); prev = mu + sd0 * rng.normal()
    for t in range(T0 + H):
        prev = mu * (1 - RHO) + RHO * prev + SIG * rng.normal(); ys[t] = prev
    r2 = n[2:] + n[1:-1]                      # two-day change in value, aligned with t
    c = LN12 - r2[:T0 + H]
    return ys, c, r2[:T0 + H]

def negll(theta, y, c, cens):
    """exact censored AR(1) likelihood by forward filtering on a grid"""
    mu, rho, sig = theta[0], np.tanh(theta[1]), np.exp(theta[2])
    a = mu * (1 - rho)
    K = norm.pdf(GRID[None, :], a + rho * GRID[:, None], sig) * DG     # K[i,j] = p(x_j | x_i) dx
    ll = 0.0; state = None                   # None -> last obs uncensored (point state)
    ylag = y[0]
    if cens[0]:
        w = norm.pdf(GRID, mu, sig / np.sqrt(max(1 - rho**2, 1e-8))) * DG
        w = w * (GRID >= c[0]); s = w.sum(); ll += np.log(max(s, 1e-300)); state = w / max(s, 1e-300)
    for t in range(1, len(y)):
        if cens[t]:
            if state is None: prior = norm.pdf(GRID, a + rho * ylag, sig) * DG
            else: prior = state @ K
            w = prior * (GRID >= c[t]); s = w.sum()
            ll += np.log(max(s, 1e-300)); state = w / max(s, 1e-300)
        else:
            if state is None: ll += norm.logpdf(y[t], a + rho * ylag, sig)
            else: ll += np.log(max(state @ norm.pdf(y[t], a + rho * GRID, sig), 1e-300))
            state = None; ylag = y[t]
    return -ll, state, ylag

def fit(y, c, cens, start):
    f = lambda th: negll(th, y, c, cens)[0]
    r = minimize(f, start, method="Nelder-Mead", options=dict(xatol=1e-4, fatol=1e-5, maxiter=600))
    th = r.x; _, state, ylag = negll(th, y, c, cens)
    return (th[0], np.tanh(th[1]), np.exp(th[2])), state, ylag, th, cov_theta(f, th)

def cov_theta(f, th):
    """inverse numerical Hessian of the negative log-likelihood in (mu, atanh rho, ln sigma)"""
    h = np.array([2e-3, 2e-2, 1e-2]); k = len(th); H = np.zeros((k, k)); f0 = f(th)
    for i in range(k):
        for j in range(i, k):
            ei = np.zeros(k); ej = np.zeros(k); ei[i] = h[i]; ej[j] = h[j]
            if i == j: H[i, i] = (f(th + ei) - 2 * f0 + f(th - ei)) / h[i] ** 2
            else: H[i, j] = H[j, i] = (f(th + ei + ej) - f(th + ei - ej) - f(th - ei + ej) + f(th - ei - ej)) / (4 * h[i] * h[j])
    try:
        V = np.linalg.inv(H)
        if np.all(np.linalg.eigvalsh(V) > 0): return V
    except np.linalg.LinAlgError: pass
    return None

def forecast(par, state, ylag, rng, nsim=1500, draws=None):
    """simulate the latent premium forward; return mean and 5/95% of the session 16-40 average.
    draws: optional (nsim x 3) array of (mu, rho, sigma), one parameter draw per path (parameter uncertainty)."""
    mu, rho, sig = par if draws is None else (draws[:, 0], draws[:, 1], draws[:, 2])
    if state is None: x = np.full(nsim, ylag); hid = ylag
    else:
        x = rng.choice(GRID, size=nsim, p=state / state.sum()); hid = float(state @ GRID)
    acc = np.zeros(nsim); accl = np.zeros(nsim)
    for h in range(1, H + 1):
        x = mu * (1 - rho) + rho * x + sig * rng.normal(size=nsim)
        if h >= 16: acc += np.expm1(x); accl += x
    lev = acc / 25; lg = accl / 25
    return hid, lev.mean(), np.quantile(lg, 0.05), np.quantile(lg, 0.95)

def one(args):
    mu, seed = args
    rng = np.random.default_rng(seed)
    while True:
        ys, c, r2 = simulate(mu, rng)
        cens = ys[:T0] >= c[:T0]
        sh = cens.mean()
        if cens[-1] and 0.10 <= sh <= 0.95: break     # as in the data: capped on the last day, both regimes present
    y = np.minimum(ys[:T0], c[:T0])
    truth_hid = ys[T0 - 1]
    out = dict(mu=mu, share=float(sh), truth_hid=float(truth_hid))
    # naive OLS AR(1)
    X = np.column_stack([np.ones(T0 - 1), y[:-1]]); b = np.linalg.lstsq(X, y[1:], rcond=None)[0]
    res = y[1:] - X @ b; rho_n = min(b[1], 0.9999); par_n = (b[0] / (1 - rho_n), rho_n, res.std(ddof=2))
    start = np.array([max(par_n[0], 0.05), np.arctanh(min(max(par_n[1], 0.5), 0.995)), np.log(par_n[2])])
    XtXi = np.linalg.inv(X.T @ X) * res.var(ddof=2)
    bd = rng.multivariate_normal(b, XtXi, size=1500); rd = np.clip(bd[:, 1], -0.999, 0.9995)
    draws_n = np.column_stack([bd[:, 0] / (1 - rd), rd, np.full(1500, par_n[2])])
    fits = {"naive": (par_n, None, y[-1], None, None)}
    fits["moving"] = fit(y, c[:T0], cens, start)
    xb = y + r2[:T0]                                   # band-reference premium ln(P_t / NAV_{t-2})
    censb = xb >= LN12 - 1e-9
    fits["fixed"] = fit(xb, np.full(T0, LN12), censb, start)
    post = ys[T0:]                                     # latent path after the change
    for k, (par, state, ylag, th, V) in fits.items():
        hid, flev, q05, q95 = forecast(par, state, ylag, rng)
        if k == "naive": draws = draws_n
        elif V is not None:
            td = rng.multivariate_normal(th, V, size=1500)
            draws = np.column_stack([td[:, 0], np.clip(np.tanh(td[:, 1]), -0.999, 0.9995), np.exp(td[:, 2])])
        else: draws = None
        if draws is not None: _, _, p05, p95 = forecast(par, state, ylag, rng, draws=draws)
        else: p05, p95 = q05, q95
        out[k] = dict(mu=float(par[0]), rho=float(par[1]), sig=float(par[2]), hid=float(hid), flev=float(flev), q05=float(q05), q95=float(q95),
                      p05=float(p05), p95=float(p95), hess_ok=bool(draws is not None))
    out["post_lat_lev"] = float(np.expm1(post[15:40]).mean()); out["post_lat_log"] = float(post[15:40].mean())
    return out

if __name__ == "__main__":
    # usage: python3 mc_censored.py MU R   -> writes mc_part_MU.json (run once per design; each run is a few minutes)
    mu = float(sys.argv[1]); R = int(sys.argv[2]) if len(sys.argv) > 2 else 240
    t0 = time.time()
    with Pool() as p: res = p.map(one, [(mu, 1000 * int(round(mu * 100)) + i) for i in range(R)], chunksize=4)
    json.dump(res, open(f"mc_part_{mu}.json", "w"))
    print("mu", mu, "reps", R, "done in", round(time.time() - t0), "s", flush=True)
