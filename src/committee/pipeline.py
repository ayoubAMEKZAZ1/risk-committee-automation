"""One committee cycle: ingest -> data quality -> universe -> actions -> controls -> pack; then, once the
committee has met, historise its minutes and regenerate the follow-up tracker."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import ingest as I
from .db import DB
from .simulate import committee_date

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw"


def _prev_month(month: str) -> str:
    return str(pd.Period(month, "M") - 1)


def ensure_history_tables(db: DB):
    if not db.has_table("decisions"):
        db.create("decisions")


# ------------------------------------------------------------------ data quality
def run_quality(db: DB, month: str, cfg: dict) -> pd.DataFrame:
    sql = db.render((ROOT / "sql/checks/exposures.sql").read_text(), mandatory=cfg["mandatory"]["exposures"],
                    tolerance=cfg["data_quality"]["overdraw_tolerance"], ratings=cfg["ratings"])
    dq = db.query(sql)
    db.run_file("sql/views/clean_exposures.sql")
    # orphans: names on the watch list after the last committee that are missing from this extract
    orph = db.query("SELECT w.counterparty_id FROM v_active_watch w "
                    "LEFT JOIN clean_exposures e ON e.counterparty_id = w.counterparty_id WHERE e.counterparty_id IS NULL")
    extra = [("DQ10", "Warning", cp, None, "Watch-list name missing from the extract") for cp in orph.counterparty_id]
    # completeness: number of names vs last month
    h = db.query("SELECT month, COUNT(DISTINCT counterparty_id) AS n FROM exposures_history GROUP BY month ORDER BY month")
    h = h.set_index("month").n
    prior = h[h.index < month].tail(3)           # median of up to three previous months: one bad month
    if len(prior):                              # does not make the next normal month look abnormal
        ref = float(prior.median())
        change = h[month] / ref - 1
        if abs(change) > cfg["data_quality"]["extract_size_change"]:
            extra.append(("DQ11", "Warning", None, None,
                          f"Extract has {h[month]:,} names vs {ref:,.0f} usual (median of the last "
                          f"{len(prior)} months, {change:+.1%})"))
    dq = pd.concat([dq, pd.DataFrame(extra, columns=dq.columns)], ignore_index=True)
    dq.insert(0, "month", month)
    dq["row_no"] = pd.to_numeric(dq.row_no, errors="coerce")
    db.delete_where("dq_issues", "month", month)
    db.write("dq_issues", dq, mode="append")
    return dq


# ------------------------------------------------------------------ universe
def build_universe(db: DB, month: str, cfg: dict) -> pd.DataFrame:
    asof = committee_date(month, cfg["committee"]["meeting_day"])
    cooloff = committee_date(str(pd.Period(month, "M") - 2), cfg["committee"]["meeting_day"])
    U = db.query_file("sql/views/universe.sql", m=I.month_idx(month), month=month, asof=asof, cooloff_from=cooloff,
                      exit_months=cfg["exit_rule"]["months_without_trigger"])
    U = U.drop_duplicates("counterparty_id")
    U["months_in_recovery"] = (pd.to_numeric(U.days_in_recovery, errors="coerce") // 30).astype("Int64")
    U.insert(0, "month", month)
    db.delete_where("universe_history", "month", month)
    db.write("universe_history", U.drop(columns=["days_in_recovery"]).astype({"months_in_recovery": "float"}), mode="append")
    return U


def flow_bridge(db: DB, U: pd.DataFrame) -> pd.DataFrame:
    """Watch list after the last committee -> proposed watch list if every proposal is accepted."""
    opening = db.query("SELECT COUNT(*) AS n FROM v_active_watch").n.iloc[0]
    w = U[U.was_on_watch == 1]
    seen = set(w.counterparty_id)
    missing = int(opening) - len(seen)
    rows = [("Watch list after last committee", int(opening)),
            ("Proposed exits", -int((w.category == "Proposed exits").sum())),
            ("Proposed transfers to recovery", -int((w.category == "Proposed transfers to recovery").sum())),
            ("Already with the recovery team", -int((w.category == "Recovery follow-up").sum())),
            ("Left the extract (closed or repaid)", -missing),
            ("New entries proposed", int((U.category == "New entries").sum())),
            ("Held for data correction, not on the watch list", int(((U.category == "Held for data correction") & (U.was_on_watch == 0)).sum()))]
    closing = int((U.category.isin(["Under follow-up", "New entries", "Held for data correction"])).sum())
    B = pd.DataFrame(rows, columns=["line", "names"])
    unexplained = closing - B.names.sum()
    B.loc[len(B)] = ("Unexplained", int(unexplained))
    B.loc[len(B)] = ("Proposed watch list after this committee", closing)
    return B


# ------------------------------------------------------------------ actions
def action_aging(db: DB, month: str, cfg: dict) -> pd.DataFrame:
    asof = committee_date(month, cfg["committee"]["meeting_day"])
    return db.query_file("sql/views/actions.sql", asof=asof, buckets=cfg["aging_buckets"])


# ------------------------------------------------------------------ pre-submission controls
def controls(db: DB, month: str, cfg: dict, dq: pd.DataFrame, U: pd.DataFrame, B: pd.DataFrame,
             A: pd.DataFrame, first_month: bool) -> pd.DataFrame:
    res = []

    def add(cid, name, ok, detail, fail="FAIL"):
        res.append({"control": cid, "description": name, "result": "PASS" if ok else fail, "detail": detail})

    held = int((U.category == "Held for data correction").sum())
    add("C1", "Cases held for data correction are listed and excluded from decisions", True,
        f"{held} case(s) held; correction list sent to data owners")
    size = dq[dq.rule_id == "DQ11"]
    add("C2", "Extract is complete (name count within tolerance of recent months)", size.empty,
        size.detail.iloc[0] if len(size) else "Name count within tolerance")
    raw_total = db.query("SELECT SUM(on_balance_mad) AS s FROM clean_exposures WHERE counterparty_id IN "
                         "(SELECT counterparty_id FROM universe_history WHERE month = ?)", [month]).s.iloc[0] or 0.0
    pack_total = float(U.on_balance_mad.sum())
    add("C3", "Pack totals reconcile to the extract", np.isclose(raw_total, pack_total, rtol=1e-9),
        f"Pack MAD {pack_total:,.0f} vs extract MAD {raw_total:,.0f}")
    unexplained = int(B.loc[B.line == "Unexplained", "names"].iloc[0])
    add("C4", "Watch-list flow bridge balances", unexplained == 0, f"Unexplained movement: {unexplained} name(s)")
    if first_month:
        add("C5", "Previous minutes historised and tracker in sync", True, "First cycle: nothing to compare")
    else:
        prev = committee_date(_prev_month(month), cfg["committee"]["meeting_day"])
        n_prev = db.query("SELECT COUNT(*) AS n FROM decisions WHERE committee_date = ?", [prev]).n.iloc[0]
        trk = I.read_tracker()
        hist = db.query("SELECT counterparty_id, committee_date, decision FROM decisions")
        merged = trk.merge(hist, on=["counterparty_id", "committee_date"], how="outer", suffixes=("_t", "_h"), indicator=True)
        diff = int((merged._merge != "both").sum() + (merged.decision_t != merged.decision_h).sum())
        add("C5", "Previous minutes historised and tracker in sync", n_prev > 0 and diff == 0,
            f"{int(n_prev)} decisions from {prev}; {diff} tracker/history difference(s)")
    complete = U[["rm", "on_balance_mad", "category"]].replace("", np.nan).notna().all(axis=1)
    held_ok = complete | (U.category == "Held for data correction")
    add("C6", "Every case has an RM, an exposure and a category", bool(held_ok.all()),
        f"{int((~held_ok).sum())} incomplete case(s)")
    esc = A[(A.status == "Overdue") & (A.days_overdue > cfg["escalation_days"])]
    add("C7", f"Actions overdue by more than {cfg['escalation_days']} days are escalated", esc.empty,
        f"{len(esc)} action(s) on the escalation list", fail="WARN")
    return pd.DataFrame(res)


# ------------------------------------------------------------------ after the committee
def historise(db: DB, minutes: pd.DataFrame, cfg: dict, tracker_path=RAW / "followup_tracker.xlsx"):
    """Load the committee's minutes into history and regenerate the wide follow-up tracker."""
    ensure_history_tables(db)
    date = minutes.committee_date.iloc[0] if len(minutes) else None
    if date is None:
        return
    db.delete_where("decisions", "committee_date", date)
    db.write("decisions", minutes[["committee_date", "counterparty_id", "category", "decision", "actions", "comment"]],
             mode="append")
    hist = db.query("SELECT d.counterparty_id, d.committee_date, d.decision, e.company_name, e.rm "
                    "FROM decisions d LEFT JOIN clean_exposures e ON e.counterparty_id = d.counterparty_id")
    names = hist.dropna(subset=["company_name"]).drop_duplicates("counterparty_id").set_index("counterparty_id")
    W = hist.pivot_table(index="counterparty_id", columns="committee_date", values="decision", aggfunc="first")
    W = W.reindex(sorted(W.columns), axis=1)
    W.insert(0, "Relationship manager", names.rm.reindex(W.index))
    W.insert(0, "Company name", names.company_name.reindex(W.index))
    W.index.name = "Counterparty ID"
    W.reset_index().to_excel(tracker_path, index=False)


# ------------------------------------------------------------------ one month, end to end (before the meeting)
def prepare(db: DB, month: str, cfg: dict, first_month=False, raw=RAW) -> dict:
    ensure_history_tables(db)
    loaded = I.load_month(db, month, cfg, raw)
    dq = run_quality(db, month, cfg)
    U = build_universe(db, month, cfg)
    B = flow_bridge(db, U)
    A = action_aging(db, month, cfg)
    C = controls(db, month, cfg, dq, U, B, A, first_month)
    status = "READY" if (C.result != "FAIL").all() else "DRAFT - controls failed"
    return {"month": month, "committee_date": committee_date(month, cfg["committee"]["meeting_day"]), "dq": dq,
            "universe": U, "bridge": B, "actions": A, "controls": C, "status": status,
            "extract": loaded["exposures"]}
