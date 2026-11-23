"""Read the month's raw files, standardise them and load the staging tables.

The exposure extract never arrives the same way twice: header spellings and number formats change.
Headers are mapped through the synonyms in config.yaml, and amounts are parsed whether they use
"1234.56" or "1 234,56". The follow-up tracker kept by the team is a wide sheet (one column per
committee date); it is transposed to one row per name and date before it is compared with history.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .db import DB

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw"


def load_config(path=None) -> dict:
    return yaml.safe_load(Path(path or ROOT / "config.yaml").read_text())


def month_idx(month: str) -> int:
    y, m = map(int, month.split("-"))
    return y * 12 + m


def map_columns(df: pd.DataFrame, synonyms: dict) -> pd.DataFrame:
    lookup = {s.strip().lower(): std for std, alts in synonyms.items() for s in alts}
    ren = {c: lookup[c.strip().lower()] for c in df.columns if c.strip().lower() in lookup}
    missing = set(synonyms) - set(ren.values())
    if missing:
        raise ValueError(f"Columns not recognised for: {sorted(missing)}. Add the new spelling to config.yaml.")
    return df.rename(columns=ren)[list(synonyms)]


def parse_amount(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    t = np.where(t.str.contains(",") & ~t.str.contains(r"\."), t.str.replace(",", ".", regex=False), t)
    return pd.to_numeric(pd.Series(t, index=s.index).replace({"": np.nan, "nan": np.nan}), errors="coerce")


def load_exposures(db: DB, month: str, cfg: dict, raw=RAW):
    df = pd.read_csv(raw / month / "exposures.csv", sep=";", dtype=str, keep_default_na=False)
    df = map_columns(df, cfg["columns"]["exposures"])
    raw_id = df.counterparty_id
    df["counterparty_id"] = raw_id.str.strip().str.upper()
    df["id_was_normalised"] = (raw_id != df.counterparty_id).astype(int)
    for c in ("limit_mad", "on_balance_mad", "off_balance_mad"):
        df[c] = parse_amount(df[c])
    df["days_past_due"] = pd.to_numeric(df.days_past_due, errors="coerce")
    for c in ("internal_rating", "status", "rm", "company_name"):
        df[c] = df[c].astype(str).str.strip()
    df.insert(0, "row_no", np.arange(1, len(df) + 1))
    df.insert(0, "month", month)
    db.write("stg_exposures", df)
    db.delete_where("exposures_history", "month", month)
    db.write("exposures_history", df[["month", "counterparty_id", "on_balance_mad"]], mode="append")
    return df


def load_candidates(db: DB, month: str, cfg: dict, raw=RAW):
    df = map_columns(pd.read_csv(raw / month / "candidates.csv", dtype=str), cfg["columns"]["candidates"])
    df["counterparty_id"] = df.counterparty_id.str.strip().str.upper()
    df.insert(0, "month_idx", month_idx(month))
    df.insert(0, "month", month)
    db.delete_where("trigger_history", "month", month)
    db.write("trigger_history", df, mode="append")
    return df


def load_recovery(db: DB, month: str, cfg: dict, raw=RAW):
    df = pd.read_excel(raw / month / "recovery.xlsx", dtype=str)
    df = map_columns(df, cfg["columns"]["recovery"]) if len(df.columns) else pd.DataFrame(columns=list(cfg["columns"]["recovery"]))
    df["recovered_to_date_mad"] = pd.to_numeric(df.recovered_to_date_mad, errors="coerce")
    df["transfer_date"] = df.transfer_date.astype(str).str[:10]
    db.write("stg_recovery", df)
    return df


def load_actions(db: DB, month: str, raw=RAW):
    df = pd.read_excel(raw / month / "actions.xlsx", dtype=str).fillna("")
    df.columns = ["action_id", "counterparty_id", "committee_date", "action", "owner", "due_date", "completed_date"]
    for c in ("committee_date", "due_date", "completed_date"):
        df[c] = df[c].astype(str).str[:10]
    db.write("stg_actions", df)
    return df


def read_tracker(path=RAW / "followup_tracker.xlsx") -> pd.DataFrame:
    """Wide tracker (one column per committee date) -> long table (name, date, decision)."""
    if not Path(path).exists():
        return pd.DataFrame(columns=["counterparty_id", "committee_date", "decision"])
    w = pd.read_excel(path, dtype=str)
    date_cols = [c for c in w.columns if str(c)[:2] == "20"]
    long = w.melt(id_vars=["Counterparty ID"], value_vars=date_cols, var_name="committee_date", value_name="decision")
    long = long.dropna(subset=["decision"]).rename(columns={"Counterparty ID": "counterparty_id"})
    long["committee_date"] = long.committee_date.astype(str).str[:10]
    return long[["counterparty_id", "committee_date", "decision"]]


def load_month(db: DB, month: str, cfg: dict, raw=RAW):
    return {"exposures": load_exposures(db, month, cfg, raw), "candidates": load_candidates(db, month, cfg, raw),
            "recovery": load_recovery(db, month, cfg, raw), "actions": load_actions(db, month, raw)}
