"""The human side of the process, simulated so the pipeline can run for a whole year:
committee decisions on each prepared case, relationship managers completing (or not) the actions they
were given, and the recovery team's progress. None of this is part of the automation itself."""
from __future__ import annotations

import numpy as np
import pandas as pd

ACTIONS = ["Obtain latest audited financial statements", "Update collateral valuation",
           "Meet the client and report back", "Submit a restructuring proposal", "Reduce the overdraft limit",
           "Obtain a repayment plan"]
STAGES = [(0, "Formal notice"), (3, "Legal proceedings"), (12, "Enforcement")]


def committee_date(month: str, day=15) -> str:
    return str((pd.Period(month, "M") + 1).to_timestamp() + pd.Timedelta(days=day - 1))[:10]


class State:
    """What the bank's other tools would hold: the recovery book and the action-tracking tool."""

    def __init__(self, seed=42):
        self.rng = np.random.default_rng(seed + 9000)
        self.recovery = {}          # counterparty_id -> transfer date
        self.recovered = {}         # counterparty_id -> recovered amount to date
        self.written_off = set()    # derecognised: no longer in the exposure extract
        self.actions = []           # dicts, including a hidden completion date

    def recovery_file(self, month: str) -> pd.DataFrame:
        asof = pd.Period(month, "M").to_timestamp("M")
        rows = []
        for cp, d in sorted(self.recovery.items()):
            months_in = (asof.year - pd.Timestamp(d).year) * 12 + asof.month - pd.Timestamp(d).month
            stage = [s for m, s in STAGES if max(months_in, 0) >= m][-1]
            rows.append({"Counterparty ID": cp, "Transfer date": d, "Litigation stage": stage,
                         "Recovered to date (MAD)": round(self.recovered.get(cp, 0.0), 2)})
        return pd.DataFrame(rows, columns=["Counterparty ID", "Transfer date", "Litigation stage",
                                           "Recovered to date (MAD)"])

    def action_export(self, month: str) -> pd.DataFrame:
        """What the tracking tool shows on the committee date: completion only if it has happened."""
        asof = committee_date(month)
        rows = []
        for a in self.actions:
            if a["committee_date"] >= asof:
                continue
            done = a["_completion"] if a["_completion"] and a["_completion"] <= asof else ""
            rows.append({"Action ID": a["action_id"], "Counterparty ID": a["counterparty_id"],
                         "Committee date": a["committee_date"], "Action": a["action"], "Owner": a["owner"],
                         "Due date": a["due_date"], "Completed date": done})
        return pd.DataFrame(rows, columns=["Action ID", "Counterparty ID", "Committee date", "Action", "Owner",
                                           "Due date", "Completed date"])

    def progress_recovery(self, exposures: pd.DataFrame):
        for cp in self.recovery:
            row = exposures[exposures.counterparty_id == cp]
            if len(row):
                self.recovered[cp] = self.recovered.get(cp, 0.0) + float(row.on_balance_mad.iloc[0]) * self.rng.uniform(0, 0.01)


def decide(universe: pd.DataFrame, month: str, state: State) -> pd.DataFrame:
    """Committee decisions on the prepared cases (the minutes, one row per name)."""
    rng = state.rng
    date = committee_date(month)
    rows = []
    open_now = {a["counterparty_id"] for a in state.actions if not a["_completion"] or a["_completion"] > date}
    for r in universe.itertuples():
        c = r.category
        if c == "New entries":
            mild = str(r.triggers).startswith(("Overdraft", "3+ payment")) and ";" not in str(r.triggers)
            decision = "No action" if (mild and rng.random() < 0.5) else "Place on watch list"
        elif c == "Under follow-up":
            decision = "Restructure" if rng.random() < 0.08 else "Keep on watch list"
        elif c == "Proposed exits":
            decision = "Exit watch list" if rng.random() < 0.85 else "Keep on watch list"
        elif c == "Held for data correction":
            decision = "Deferred (data correction)"
        elif c == "Proposed transfers to recovery":
            decision = "Transfer to recovery"
        else:
            decision = "Propose write-off" if r.months_in_recovery >= 18 else "Continue recovery"
        acts = []
        follow_up_only = decision == "Keep on watch list" and (r.counterparty_id in open_now or rng.random() < 0.5)
        if decision in ("Place on watch list", "Keep on watch list", "Restructure") and not follow_up_only:
            k = 1 if decision == "Keep on watch list" and rng.random() < 0.6 else 2
            pool = ACTIONS if decision != "Restructure" else ["Submit a restructuring proposal", "Obtain a repayment plan"]
            acts = list(rng.choice(pool, size=min(k, len(pool)), replace=False))
        rows.append({"committee_date": date, "counterparty_id": r.counterparty_id, "category": c,
                     "decision": decision, "actions": " | ".join(acts), "comment": ""})
        for a in acts:
            due = (pd.Timestamp(date) + pd.Timedelta(days=int(rng.choice([30, 45, 60])))).date()
            late = rng.normal(-3, 25)
            never = rng.random() < 0.12
            state.actions.append({"action_id": f"A{len(state.actions) + 1:05d}", "counterparty_id": r.counterparty_id,
                                  "committee_date": date, "action": a, "owner": r.rm, "due_date": str(due),
                                  "_completion": "" if never else str((pd.Timestamp(due) + pd.Timedelta(days=int(late))).date())})
        if decision == "Transfer to recovery":
            state.recovery[r.counterparty_id] = date
        if decision == "Propose write-off":
            state.recovery.pop(r.counterparty_id, None)
            state.written_off.add(r.counterparty_id)
    return pd.DataFrame(rows, columns=["committee_date", "counterparty_id", "category", "decision", "actions", "comment"])
