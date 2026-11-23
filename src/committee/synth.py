"""Synthetic monthly inputs for the committee, built on the shared synthetic portfolio.

Each month produces the files a risk team would collect before the committee:
  exposures.csv    counterparty extract from the core banking system (semicolon-separated, headers and
                   number formats vary from month to month, with injected data defects)
  candidates.csv   names hit by an early-warning trigger this month (one row per trigger)
  recovery.xlsx    names already transferred to the recovery / litigation team
  actions.xlsx     export of the action-tracking tool: actions decided in past committees and their status

Injected defects are recorded in data/raw/_truth/defects.csv. The pipeline never reads that file; the
report uses it to measure how many defects the data-quality checks catch. All names are invented.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .portfolio import generate

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw"

FIRST = ["Atlas", "Cedar", "Oasis", "Sahara", "Argan", "Saffron", "Medina", "Anfa", "Draa", "Tafilalt", "Ifrane",
         "Souss", "Zagora", "Ouarzazate", "Chefchaouen", "Essaouira", "Tichka", "Bouregreg", "Sebou", "Moulouya",
         "Nador", "Azrou", "Toubkal", "Massa", "Ziz", "Agadir", "Tetouan", "Kenitra", "Larache", "Dakhla"]
SECOND = {"Construction": "Builders", "Manufacturing": "Industries", "Wholesale & retail trade": "Trading",
          "Tourism & hospitality": "Hotels", "Agriculture & agrifood": "Foods", "Business services": "Services",
          "Transport & logistics": "Logistics", "Energy & utilities": "Energy", "Real estate services": "Properties",
          "Real estate development": "Developments"}
FORMS = ["SA", "Group", "Holding", "Company"]
HEADER_STYLES = 4                     # index into each column's list of accepted spellings in config.yaml
DEFECTS = {"duplicate_row": 0.004, "missing_rating": 0.003, "negative_amount": 0.002, "overdrawn": 0.002,
           "invalid_rating": 0.002, "negative_dpd": 0.001, "status_dpd_mismatch": 0.002, "unnormalised_id": 0.005,
           "missing_rm": 0.002}
TRUNCATED = {"2025-09": "Fez-Meknes"}  # one month arrives with a region missing from the extract


class Portfolio:
    """Static attributes (names, branches, RMs) on top of the shared synthetic panel."""

    def __init__(self, seed=42):
        D = generate(seed=seed)
        self.panel = D["monthly_panel"]
        cp = D["counterparties"].copy()
        rng = np.random.default_rng(seed + 5000)
        names = set()
        out = []
        for sec in cp.sector:
            while True:
                n = f"{rng.choice(FIRST)} {SECOND[sec]} {rng.choice(FORMS)}"
                if n not in names:
                    names.add(n)
                    break
                a, b = rng.choice(FIRST, 2, replace=False)
                n = f"{a} {b} {SECOND[sec]}"
                if n not in names:
                    names.add(n)
                    break
            out.append(n)
        cp["company_name"] = out
        cp["branch"] = cp.region.str.split("-").str[0] + " Business Centre"
        rms = {r: [f"RM-{i:02d}" for i in range(k * 10 + 1, k * 10 + 11)] for k, r in enumerate(sorted(cp.region.unique()))}
        cp["rm"] = [rng.choice(rms[r]) for r in cp.region]
        self.cp = cp.set_index("counterparty_id")
        P = self.panel
        P["util_prev"] = P.groupby("counterparty_id").overdraft_utilisation.shift(1)
        P["rating_num"] = pd.to_numeric(P.internal_rating, errors="coerce")
        P["rating_12m_ago"] = P.groupby("counterparty_id").rating_num.shift(12)
        self.seed = seed


def _fmt_amount(x, style):
    if style == 1:   # thousands separated by spaces, comma decimals, as some extracts arrive
        return f"{x:,.2f}".replace(",", " ").replace(".", ",")
    return f"{x:.2f}"


def exposures(pf: Portfolio, month: str, rng, written_off=frozenset()) -> tuple[pd.DataFrame, list]:
    P = pf.panel[(pf.panel.month == month) & ~pf.panel.counterparty_id.isin(written_off)]
    cp = pf.cp.loc[P.counterparty_id]
    limit = P.exposure_mad_m.to_numpy() * 1e6
    on_bal = limit * (0.50 + 0.35 * np.minimum(P.overdraft_utilisation.to_numpy(), 1.1))
    X = pd.DataFrame({
        "counterparty_id": P.counterparty_id.to_numpy(), "company_name": cp.company_name.to_numpy(),
        "segment": cp.segment.to_numpy(), "sector": cp.sector.to_numpy(), "region": cp.region.to_numpy(),
        "branch": cp.branch.to_numpy(), "rm": cp.rm.to_numpy(), "limit_mad": limit, "on_balance_mad": on_bal,
        "off_balance_mad": limit * 0.15, "days_past_due": P.days_past_due.to_numpy(),
        "internal_rating": P.internal_rating.to_numpy(),
        "status": np.where(P.status.eq("default"), "Default", "Performing")}).reset_index(drop=True)
    truth = []
    if month in TRUNCATED:
        lost = X.region.eq(TRUNCATED[month])
        truth += [(month, i, "truncated_extract") for i in X.counterparty_id[lost]]
        X = X[~lost].reset_index(drop=True)
    X["rating_txt"] = X.internal_rating.astype(str)
    X["status_txt"] = X.status
    X["rm_txt"] = X.rm
    used = set()

    def pick(p):
        idx = [i for i in np.flatnonzero(rng.random(len(X)) < p) if i not in used]
        used.update(idx)
        return idx

    for i in pick(DEFECTS["missing_rating"]):
        X.at[i, "rating_txt"] = ""
        truth.append((month, X.at[i, "counterparty_id"], "missing_rating"))
    for i in pick(DEFECTS["invalid_rating"]):
        X.at[i, "rating_txt"] = rng.choice(["10", "X", "0"])
        truth.append((month, X.at[i, "counterparty_id"], "invalid_rating"))
    for i in pick(DEFECTS["negative_amount"]):
        X.at[i, "on_balance_mad"] *= -1
        truth.append((month, X.at[i, "counterparty_id"], "negative_amount"))
    for i in pick(DEFECTS["overdrawn"]):
        X.at[i, "on_balance_mad"] = X.at[i, "limit_mad"] * rng.uniform(1.4, 2.0)
        truth.append((month, X.at[i, "counterparty_id"], "overdrawn"))
    for i in pick(DEFECTS["negative_dpd"]):
        X.at[i, "days_past_due"] = -int(rng.integers(1, 30))
        truth.append((month, X.at[i, "counterparty_id"], "negative_dpd"))
    for i in pick(DEFECTS["status_dpd_mismatch"]):
        X.at[i, "status_txt"] = "Performing" if X.at[i, "status"] == "Default" else "Default"
        truth.append((month, X.at[i, "counterparty_id"], "status_dpd_mismatch"))
    for i in pick(DEFECTS["missing_rm"]):
        X.at[i, "rm_txt"] = ""
        truth.append((month, X.at[i, "counterparty_id"], "missing_rm"))
    ids = X.counterparty_id.astype(object).copy()
    for i in pick(DEFECTS["unnormalised_id"]):
        ids.iat[i] = f" {ids.iat[i].lower()} "
        truth.append((month, X.at[i, "counterparty_id"], "unnormalised_id"))
    X["id_txt"] = ids
    dup = pick(DEFECTS["duplicate_row"])
    truth += [(month, X.at[i, "counterparty_id"], "duplicate_row") for i in dup]
    X = pd.concat([X, X.loc[dup]]).sort_values("id_txt").reset_index(drop=True)
    return X, truth


def write_month(pf: Portfolio, month: str, state, cfg) -> None:
    """Write the month's raw input files under data/raw/<month>/."""
    rng = np.random.default_rng(pf.seed + int(month.replace("-", "")))
    out = RAW / month
    out.mkdir(parents=True, exist_ok=True)
    X, truth = exposures(pf, month, rng, state.written_off)
    style = int(rng.integers(HEADER_STYLES))
    amount_style = int(rng.integers(2))
    hdr = {k: v[style % len(v)] for k, v in cfg["columns"]["exposures"].items()}
    E = pd.DataFrame({
        hdr["counterparty_id"]: X.id_txt, hdr["company_name"]: X.company_name, hdr["segment"]: X.segment,
        hdr["sector"]: X.sector, hdr["region"]: X.region, hdr["branch"]: X.branch, hdr["rm"]: X.rm_txt,
        hdr["limit_mad"]: [_fmt_amount(v, amount_style) for v in X.limit_mad],
        hdr["on_balance_mad"]: [_fmt_amount(v, amount_style) for v in X.on_balance_mad],
        hdr["off_balance_mad"]: [_fmt_amount(v, amount_style) for v in X.off_balance_mad],
        hdr["days_past_due"]: X.days_past_due, hdr["internal_rating"]: X.rating_txt, hdr["status"]: X.status_txt})
    E.to_csv(out / "exposures.csv", sep=";", index=False)
    candidates(pf, month).to_csv(out / "candidates.csv", index=False)
    state.recovery_file(month).to_excel(out / "recovery.xlsx", index=False)
    state.action_export(month).to_excel(out / "actions.xlsx", index=False)
    tp = RAW / "_truth/defects.csv"
    tp.parent.mkdir(parents=True, exist_ok=True)
    T = pd.DataFrame(truth, columns=["month", "counterparty_id", "defect"])
    old = pd.read_csv(tp) if tp.exists() else pd.DataFrame(columns=T.columns)
    pd.concat([old[old.month != month], T]).to_csv(tp, index=False)


def candidates(pf: Portfolio, month: str) -> pd.DataFrame:
    """Early-warning triggers for the month, one row per (name, trigger)."""
    P = pf.panel[(pf.panel.month == month) & (pf.panel.status == "performing")]
    rows = []
    rules = [("Rating 8-9", P.rating_num >= 8, P.internal_rating),
             ("30-89 days past due", P.days_past_due.between(30, 89), P.days_past_due),
             ("Downgrade of 2+ notches in 12 months", (P.rating_num - P.rating_12m_ago) >= 2,
              (P.rating_num - P.rating_12m_ago)),
             ("Overdraft above limit two months running", (P.overdraft_utilisation > 1.0) & (P.util_prev > 1.0),
              P.overdraft_utilisation.round(2)),
             ("3+ payment incidents in the month", P.payment_incidents >= 3, P.payment_incidents)]
    for name, mask, val in rules:
        s = P[mask.fillna(False)]
        rows.append(pd.DataFrame({"counterparty_id": s.counterparty_id, "trigger": name,
                                  "trigger_value": val[mask.fillna(False)].astype(str)}))
    return pd.concat(rows, ignore_index=True)
