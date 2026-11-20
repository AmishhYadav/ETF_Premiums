"""Censored AR(1) with a known, time-varying upper censoring point and a latent lag.
pi*_t = a + rho pi*_{t-1} + x_t'g + sigma e_t ;  observed y_t = pi*_t if not capped, else pi*_t >= c_t.
Likelihood by a forward filter on a grid (the state is one-dimensional)."""
import numpy as np
from scipy.stats import norm
from scipy.special import ndtr
import math
from scipy.optimize import minimize

SQ2PI = np.sqrt(2 * np.pi)

class CensAR:
    def __init__(self, y, c, capped, X=None, lo=None, hi=None, step=0.004):
        self.y = np.asarray(y, float); self.c = np.asarray(c, float); self.k = np.asarray(capped, bool)
        self.X = None if X is None else np.asarray(X, float)
        lo = min(self.y.min(), 0) - 0.08 if lo is None else lo
        hi = max(self.c[self.k].max() if self.k.any() else self.y.max(), self.y.max()) + 1.0 if hi is None else hi
        self.g = np.arange(lo, hi + step, step); self.step = step
        self.n = len(self.y)

    def unpack(self, th):
        a, r, ls = th[0], th[1], th[2]
        rho = 1 / (1 + np.exp(-r))            # (0,1)
        gam = th[3:] if self.X is not None else np.zeros(0)
        return a, rho, np.exp(ls), gam

    def filter(self, th, return_state=False, store=False):
        a, rho, s, gam = self.unpack(th)
        y, c, k, g = self.y, self.c, self.k, self.g
        xb = self.X @ gam if self.X is not None else np.zeros(self.n)
        ll = 0.0
        # start: first observation must be uncapped
        t0 = int(np.argmax(~k)); state = ('pt', y[t0]); K0 = None; Kshift = None
        hidden = np.full(self.n, np.nan)
        for t in range(t0 + 1, self.n):
            sh = xb[t]
            if state[0] == 'pt':
                m = a + rho * state[1] + sh
                if not k[t]:
                    z = (y[t] - m) / s; ll += -0.5 * z * z - math.log(s * SQ2PI); state = ('pt', y[t])
                else:
                    p = 0.5 * math.erfc((c[t] - m) / s / math.sqrt(2)); ll += math.log(max(p, 1e-300))
                    f = np.exp(-0.5 * ((g - m) / s) ** 2) * (g >= c[t])
                    tot = f.sum()
                    if tot <= 0:   # ceiling beyond mass: put mass at the ceiling
                        f = np.zeros_like(g); f[min(np.searchsorted(g, c[t]), len(g) - 1)] = 1.0; tot = 1.0
                    state = ('den', f / tot)
            else:
                f = state[1]
                mv = a + rho * g + sh              # mean for each previous grid value v
                if not k[t]:
                    dens = (f * np.exp(-0.5 * ((y[t] - mv) / s) ** 2)).sum() / (s * SQ2PI)
                    ll += np.log(max(dens, 1e-300)); state = ('pt', y[t])
                else:
                    p = float((f * ndtr(-(c[t] - mv) / s)).sum()); ll += math.log(max(p, 1e-300))
                    if Kshift != sh or K0 is None:
                        K0 = np.exp(-0.5 * ((g[:, None] - mv[None, :]) / s) ** 2); Kshift = sh
                    fn = (K0 @ f) * (g >= c[t]); tot = fn.sum()
                    if tot <= 0:
                        fn = np.zeros_like(g); fn[min(np.searchsorted(g, c[t]), len(g) - 1)] = 1.0; tot = 1.0
                    state = ('den', fn / tot)
            if store: hidden[t] = state[1] if state[0] == 'pt' else (state[1] * g).sum()
        if store: return ll, state, hidden
        return (ll, state) if return_state else ll

    def fit(self, start=None, maxiter=600, xatol=1e-4, fatol=1e-3):
        y = self.y; k = self.k
        if start is None:
            yy = y[1:]; yl = y[:-1]; A = np.column_stack([np.ones(len(yl)), yl]); b = np.linalg.lstsq(A, yy, rcond=None)[0]
            rho0 = min(max(b[1], 0.5), 0.995); s0 = max(np.std(yy - A @ b), 1e-3)
            start = [b[0], np.log(rho0 / (1 - rho0)), np.log(s0)] + ([0.0] * (self.X.shape[1]) if self.X is not None else [])
        f = lambda th: -self.filter(th)
        r = minimize(f, start, method='Nelder-Mead', options=dict(maxiter=maxiter, xatol=xatol, fatol=fatol))
        self.th = r.x; self.ll = -r.fun
        return r

    def forecast(self, th, state, H=40, npaths=4000, rng=None, Xf=None):
        rng = np.random.default_rng(0) if rng is None else rng
        a, rho, s, gam = self.unpack(th)
        if state[0] == 'pt': p0 = np.full(npaths, state[1])
        else:
            cdf = np.cumsum(state[1]); u = rng.random(npaths); p0 = self.g[np.searchsorted(cdf, u).clip(0, len(self.g) - 1)]
        paths = np.empty((npaths, H)); p = p0
        for h in range(H):
            sh = (Xf[h] @ gam) if Xf is not None else 0.0
            p = a + rho * p + sh + s * rng.standard_normal(npaths); paths[:, h] = p
        return p0, paths

def simulate(th_nat, c, y0, H, rng):
    """th_nat = (a, rho, sigma). Latent path over len(c) pre days from y0 and H later days."""
    a, rho, s = th_nat; n = len(c); z = np.empty(n + H); p = y0
    e = rng.standard_normal(n + H)
    for t in range(n + H):
        if t == 0: z[t] = y0; continue
        p = a + rho * p + s * e[t]; z[t] = p
    lat = z[:n]; k = lat >= c; k[0] = False
    y = np.where(k, c, lat)
    return y, k, lat, z[n:]

if __name__ == '__main__':
    import time
    rng = np.random.default_rng(1)
    n = 640; nav = rng.normal(0, 0.012, n + 2); c = np.log(1.2) - (nav[2:] + nav[1:-1])
    err = []
    for rep in range(12):
        y, k, lat, fut = simulate((0.004, 0.985, 0.02), c, 0.05, 40, rng)
        m = CensAR(y, c, k); t = time.time(); r = m.fit(); a, rho, s, _ = m.unpack(m.th)
        ll, st, hid = m.filter(m.th, store=True)
        hT = hid[-1]
        err.append((rho, s, k.mean(), lat[-1], hT, time.time() - t, r.nfev))
    print(np.round(np.array(err), 4))
