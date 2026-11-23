"""Synthetic corporate credit portfolio (copied unchanged from credit-early-warning-system,
so the portfolio repositories all run on the same book).

Synthetic corporate credit portfolio: monthly counterparty panel, 2021-01 to 2025-12.

Every counterparty has a latent credit quality q (higher = healthier) that follows an AR(1) process
around its own long-run level, hit by sector cycles and by idiosyncratic distress episodes. What the
bank observes is derived from q with realistic delays:

  account behaviour (monthly)  : reacts within 1-2 months (overdraft use, limit breaches, payment
                                 incidents, turnover through the account, days past due)
  financial statements (annual): fiscal-year figures, available about 6 months after year end
  internal rating              : reviewed once a year, or downgraded ad hoc after 30+ days past due

Default (90+ days past due) is drawn from a monthly hazard that rises as q falls. Because behaviour
moves months before the rating, an early-warning system has something real to find; because the
hazard is random, it cannot find everything.

All names, numbers and identifiers are synthetic. No real counterparty or bank data is used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONTHS = pd.period_range("2021-01", "2025-12", freq="M")
SEGMENTS = {  # share, median exposure (MAD million), long-run quality shift
    "SME": (0.55, 3.0, -0.15),
    "Mid-corporate": (0.25, 25.0, 0.10),
    "Large corporate": (0.08, 150.0, 0.85),
    "Property developer": (0.12, 60.0, -0.35),
}
SECTORS = {  # share among non-developers
    "Construction": 0.10, "Manufacturing": 0.17, "Wholesale & retail trade": 0.20, "Tourism & hospitality": 0.08,
    "Agriculture & agrifood": 0.11, "Business services": 0.14, "Transport & logistics": 0.09, "Energy & utilities": 0.04,
    "Real estate services": 0.07,
}
REGIONS = {"Casablanca-Settat": 0.42, "Rabat-Sale-Kenitra": 0.17, "Tangier-Tetouan-Al Hoceima": 0.11,
           "Marrakech-Safi": 0.10, "Fez-Meknes": 0.08, "Souss-Massa": 0.06, "Oriental": 0.03, "Other regions": 0.03}
LGD = 0.45
PD_BY_RATING = {1: 0.0005, 2: 0.001, 3: 0.002, 4: 0.004, 5: 0.008, 6: 0.016, 7: 0.035, 8: 0.08, 9: 0.18}


def _sector_shocks(rng, sectors):
    """Monthly additive shocks to q by sector (stylised cycles)."""
    T = len(MONTHS)
    t = np.arange(T)
    shock = {s: rng.normal(0, 0.02, T) for s in sectors}
    shock["Tourism & hospitality"][:6] -= 0.08          # 2021 travel restrictions, then recovery
    shock["Tourism & hospitality"][9:18] += 0.04
    shock["Agriculture & agrifood"][12:24] -= 0.04      # 2022 drought
    for s in ("Construction", "Real estate development", "Real estate services"):
        shock[s][(t >= 30) & (t < 48)] -= 0.035           # construction slowdown, mid-2023 to end-2024
        shock[s][(t >= 48) & (t < 51)] -= 0.05            # weak start to 2025
    shock["Energy & utilities"][13:20] += 0.02          # price windfall
    return shock


def generate(n=3000, seed=42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    T = len(MONTHS)

    # ---------------------------------------------------------------- counterparties
    seg = rng.choice(list(SEGMENTS), n, p=[v[0] for v in SEGMENTS.values()])
    sec = np.where(seg == "Property developer", "Real estate development",
                   rng.choice(list(SECTORS), n, p=list(SECTORS.values())))
    reg = rng.choice(list(REGIONS), n, p=list(REGIONS.values()))
    shift = np.array([SEGMENTS[s][2] for s in seg])
    mu = rng.normal(0.35, 0.85, n) + shift                     # long-run quality
    ead0 = np.array([SEGMENTS[s][1] for s in seg]) * rng.lognormal(0, 0.8, n)
    review_month = rng.integers(1, 13, n)                      # month of the annual rating review
    start = np.where(rng.random(n) < 0.85, 0, rng.integers(1, 36, n))   # some relationships start later
    cp = pd.DataFrame({"counterparty_id": [f"CP{i:05d}" for i in rng.permutation(n) + 1], "segment": seg,
                       "sector": sec, "region": reg, "onboarding_month": [str(MONTHS[s]) for s in start],
                       "annual_review_month": review_month})

    # ---------------------------------------------------------------- latent quality
    shocks = _sector_shocks(rng, list(SECTORS) + ["Real estate development"])
    S = np.stack([shocks[s] for s in sec])                     # n x T
    q = np.zeros((n, T))
    distress = np.zeros((n, T))
    q_prev = mu + rng.normal(0, 0.4, n)
    ep_left = np.zeros(n, int)
    ep_drift = np.zeros(n)
    for t in range(T):
        start_ep = (ep_left == 0) & (rng.random(n) < 0.004 * np.exp(-0.6 * mu))
        ep_left[start_ep] = rng.integers(6, 16, start_ep.sum())
        ep_drift[start_ep] = rng.uniform(0.06, 0.16, start_ep.sum())
        d = np.where(ep_left > 0, ep_drift, 0.0)
        q_t = 0.93 * q_prev + 0.07 * mu + S[:, t] - d + rng.normal(0, 0.11, n)
        distress[:, t] = ep_left > 0
        ep_left = np.maximum(ep_left - 1, 0)
        q[:, t] = q_t
        q_prev = q_t

    # ---------------------------------------------------------------- defaults
    default_month = np.full(n, -1)
    hazard = 1 / (1 + np.exp(-(-6.4 - 1.5 * q)))
    for t in range(T):
        alive = (default_month < 0) & (t >= start)
        hit = alive & (rng.random(n) < hazard[:, t])
        default_month[hit] = t

    # ---------------------------------------------------------------- behaviour (monthly)
    q_obs = np.concatenate([q[:, :1], q[:, :-1]], axis=1)       # behaviour reacts with a one-month lag
    util = 1 / (1 + np.exp(-(0.2 - 1.2 * q_obs + rng.normal(0, 0.35, (n, T)))))
    util = np.clip(util * rng.uniform(0.85, 1.05, (n, 1)), 0, 1.15)
    breach_days = rng.poisson(np.clip((util - 0.92) * 120, 0, 25))
    incidents = rng.poisson(np.exp(-3.0 - 1.1 * q_obs))
    season = 0.06 * np.sin(2 * np.pi * (np.arange(T) % 12) / 12)
    log_turn = np.log(ead0 * 0.12)[:, None] + 0.18 * q_obs + season + rng.normal(0, 0.07, (n, T))
    turnover = np.exp(log_turn)
    dpd = np.zeros((n, T))
    late = (q_obs < -1.1) & (rng.random((n, T)) < 1 / (1 + np.exp(2.2 * (q_obs + 1.8))))
    dpd[late] = rng.choice([5, 15, 25, 40, 55, 75], late.sum(), p=[0.25, 0.25, 0.2, 0.15, 0.1, 0.05])
    for i in np.flatnonzero(default_month >= 0):           # the run-up to default shows in arrears
        dm = default_month[i]
        for k, v in zip((3, 2, 1), (30, 45, 70)):
            if dm - k >= 0 and rng.random() < 0.55:
                dpd[i, dm - k] = max(dpd[i, dm - k], v + rng.integers(-5, 10))
        dpd[i, dm:] = 90 + np.arange(T - dm) * 30

    # ---------------------------------------------------------------- annual financials
    years = np.arange(2020, 2026)
    fin = []
    for yi, y in enumerate(years):
        cols = slice(max(0, (y - 2021) * 12), max(0, (y - 2021) * 12) + 12) if y >= 2021 else slice(0, 1)
        qy = q[:, cols].mean(axis=1) if y >= 2021 else mu + rng.normal(0, 0.3, n)
        fin.append(pd.DataFrame({
            "counterparty_id": cp.counterparty_id, "fiscal_year": y,
            "available_from": str(pd.Period(f"{y + 1}-07", "M")),
            "net_debt_to_ebitda": np.round(np.exp(1.1 - 0.42 * qy + rng.normal(0, 0.25, n)), 2),
            "interest_coverage": np.round(np.exp(1.2 + 0.45 * qy + rng.normal(0, 0.25, n)), 2),
            "current_ratio": np.round(np.exp(0.25 + 0.15 * qy + rng.normal(0, 0.15, n)), 2),
            "revenue_growth_pct": np.round(100 * (0.04 + 0.05 * qy + rng.normal(0, 0.08, n)), 1)}))
    fin = pd.concat(fin, ignore_index=True)

    # ---------------------------------------------------------------- ratings (1 best .. 9 worst, D = default)
    # The annual review rates the counterparty on its last AUDITED fiscal year (published about six
    # months after year end), so the rating looks 6-18 months back. Between reviews it only moves on
    # an ad hoc downgrade after 30+ days past due.
    q_fy = {y: (q[:, (y - 2021) * 12:(y - 2021) * 12 + 12].mean(axis=1) if y >= 2021
                else mu + rng.normal(0, 0.3, n)) for y in range(2019, 2026)}
    rating = np.zeros((n, T), int)
    bias = rng.normal(0, 0.35, n)                   # each name's persistent rating-model error
    r = np.clip(np.round(5.0 - 1.4 * (mu + bias)), 1, 9).astype(int)
    for t in range(T):
        month, year = MONTHS[t].month, MONTHS[t].year
        audited_fy = year - 1 if month >= 7 else year - 2
        review = review_month == month
        new = np.clip(np.round(5.0 - 1.4 * (q_fy[audited_fy] + bias + rng.normal(0, 0.12, n))), 1, 9).astype(int)
        r = np.where(review, new, r)
        r = np.where(dpd[:, t] >= 30, np.maximum(r, 8), r)
        rating[:, t] = r

    # ---------------------------------------------------------------- exposures
    growth = np.cumprod(1 + rng.normal(0.003, 0.01, (n, T)), axis=1)
    ead = ead0[:, None] * growth

    # ---------------------------------------------------------------- long panel
    ii, tt = np.meshgrid(np.arange(n), np.arange(T), indexing="ij")
    ii, tt = ii.ravel(), tt.ravel()
    keep = tt >= start[ii]
    dm = default_month[ii]
    status = np.where((dm >= 0) & (tt >= dm), "default", "performing")
    writeoff = (dm >= 0) & (tt >= dm + 24)                        # NPLs written off after 24 months
    keep &= ~writeoff
    P = pd.DataFrame({
        "counterparty_id": cp.counterparty_id.values[ii], "month": MONTHS.astype(str)[tt],
        "status": status, "exposure_mad_m": np.round(ead[ii, tt], 3),
        "overdraft_utilisation": np.round(util[ii, tt], 3), "limit_breach_days": breach_days[ii, tt],
        "payment_incidents": incidents[ii, tt], "account_turnover_mad_m": np.round(turnover[ii, tt], 4),
        "days_past_due": dpd[ii, tt].astype(int),
        "internal_rating": np.where(status == "default", "D", rating[ii, tt].astype(str)),
    })[keep].reset_index(drop=True)
    truth = pd.DataFrame({"counterparty_id": cp.counterparty_id, "default_month":
                          [str(MONTHS[m]) if m >= 0 else "" for m in default_month],
                          "long_run_quality": np.round(mu, 3)})
    latent = pd.DataFrame({"counterparty_id": cp.counterparty_id.values[ii][keep], "month": MONTHS.astype(str)[tt][keep],
                           "latent_quality": np.round(q[ii, tt][keep], 3),
                           "in_distress_episode": distress[ii, tt][keep].astype(int)})
    return {"counterparties": cp, "monthly_panel": P, "financials": fin, "ground_truth": truth, "latent": latent}


def save(out_dir, n=3000, seed=42):
    import os
    os.makedirs(out_dir, exist_ok=True)
    D = generate(n, seed)
    for name, df in D.items():
        df.to_csv(os.path.join(out_dir, f"{name}.csv.gz" if len(df) > 50000 else f"{name}.csv"), index=False)
    return D


if __name__ == "__main__":
    D = generate()
    P, truth = D["monthly_panel"], D["ground_truth"]
    print(P.shape, P.status.value_counts().to_dict())
    dm = truth[truth.default_month != ""].default_month.str[:4].value_counts().sort_index()
    perf = P[P.status == "performing"].groupby(P.month.str[:4]).counterparty_id.nunique()
    print(pd.DataFrame({"defaults": dm, "performing_counterparties": perf, "rate_%": (100 * dm / perf).round(2)}))
    print(D["counterparties"].merge(truth, on="counterparty_id").assign(d=lambda x: x.default_month != "")
          .groupby("segment").d.mean().mul(100).round(1))
