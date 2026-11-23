"""Thin database layer: DuckDB by default, SQLite (standard library) as a fallback.

The bank version of this process ran on a Dremio data lake. DuckDB plays the same role here: a SQL
engine over files, with tables for staging, history and controls. All SQL in sql/ is written to run
unchanged on both engines; the only engine-specific piece is date arithmetic, injected through the
{{ days(a, b) }} template function.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
from jinja2 import Template

ROOT = Path(__file__).resolve().parents[2]

# Explicit schemas for tables that can start empty: a table created from an empty DataFrame has no
# column types, and DuckDB would then refuse to compare its columns with text.
SCHEMAS = {
    "decisions": {"committee_date": "VARCHAR", "counterparty_id": "VARCHAR", "category": "VARCHAR",
                  "decision": "VARCHAR", "actions": "VARCHAR", "comment": "VARCHAR"},
    "stg_actions": {"action_id": "VARCHAR", "counterparty_id": "VARCHAR", "committee_date": "VARCHAR",
                    "action": "VARCHAR", "owner": "VARCHAR", "due_date": "VARCHAR", "completed_date": "VARCHAR"},
    "stg_recovery": {"counterparty_id": "VARCHAR", "transfer_date": "VARCHAR", "litigation_stage": "VARCHAR",
                     "recovered_to_date_mad": "DOUBLE"},
}


class DB:
    def __init__(self, path=":memory:", backend: str | None = None):
        backend = (backend or os.environ.get("COMMITTEE_DB_BACKEND", "duckdb")).lower()
        if backend == "duckdb":
            try:
                import duckdb
                self.con = duckdb.connect(str(path))
            except ImportError:
                backend = "sqlite"
        if backend == "sqlite":
            self.con = sqlite3.connect(str(path))
        self.backend = backend

    # ------------------------------------------------------------------ SQL helpers
    def days(self, a: str, b: str) -> str:
        """SQL for the number of days from date a to date b (ISO strings)."""
        if self.backend == "duckdb":
            return f"date_diff('day', CAST({a} AS DATE), CAST({b} AS DATE))"
        return f"CAST(julianday({b}) - julianday({a}) AS INTEGER)"

    def render(self, sql: str, **ctx) -> str:
        return Template(sql).render(days=self.days, **ctx)

    def execute(self, sql: str, params=None):
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            self.con.execute(stmt, params or [])
        if self.backend == "sqlite":
            self.con.commit()

    def query(self, sql: str, params=None) -> pd.DataFrame:
        if self.backend == "duckdb":
            return self.con.execute(sql, params or []).df()
        return pd.read_sql_query(sql, self.con, params=params)

    def run_file(self, rel_path: str, **ctx):
        self.execute(self.render((ROOT / rel_path).read_text(), **ctx))

    def query_file(self, rel_path: str, params=None, **ctx) -> pd.DataFrame:
        return self.query(self.render((ROOT / rel_path).read_text(), **ctx), params)

    # ------------------------------------------------------------------ tables
    def create(self, name: str):
        cols = ", ".join(f"{c} {t}" for c, t in SCHEMAS[name].items())
        self.execute(f"DROP TABLE IF EXISTS {name}; CREATE TABLE {name} ({cols})")

    def write(self, name: str, df: pd.DataFrame, mode: str = "replace"):
        """Create (replace) or append a table from a DataFrame."""
        if name in SCHEMAS and (mode == "replace" or not self.has_table(name)):
            self.create(name)                    # typed table, then rows appended
            if df.empty:
                return
            df = df[list(SCHEMAS[name])]
            mode = "append"
        if self.backend == "duckdb":
            self.con.register("_incoming", df)
            if mode == "replace" or not self.has_table(name):
                self.con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _incoming")
            else:
                self.con.execute(f"INSERT INTO {name} SELECT * FROM _incoming")
            self.con.unregister("_incoming")
        else:
            df.to_sql(name, self.con, if_exists="replace" if mode == "replace" else "append", index=False)

    def has_table(self, name: str) -> bool:
        if self.backend == "duckdb":
            q = "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = ?"
        else:
            q = "SELECT COUNT(*) AS n FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?"
        return int(self.query(q, [name]).n.iloc[0]) > 0

    def delete_where(self, table: str, column: str, value):
        if self.has_table(table):
            self.execute(f"DELETE FROM {table} WHERE {column} = ?", [value])

    def close(self):
        self.con.close()
