"""Run the process for a full year of monthly committees on synthetic data.

    bootstrap  : watch list and recovery book as of the December 2024 committee
    each month : synthetic inputs -> prepare (ingest, checks, universe, controls) -> pack
                 -> simulated committee -> historise minutes -> tracker regenerated
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from . import ingest as I
from . import pipeline as PL
from . import simulate as SIM
from . import synth as SY
from .db import DB
from .pack import write_pack

ROOT = Path(__file__).resolve().parents[2]


def bootstrap(db: DB, pf: SY.Portfolio, state: SIM.State, cfg: dict):
    """Opening position: names triggered in Q4 2024 are on the watch list, 2024 defaults are in recovery."""
    for m in ("2024-10", "2024-11", "2024-12"):
        d = SY.RAW / m
        d.mkdir(parents=True, exist_ok=True)
        SY.candidates(pf, m).to_csv(d / "candidates.csv", index=False)
        I.load_candidates(db, m, cfg)
    P = pf.panel
    defaulted = P[(P.month <= "2024-12") & (P.status == "default")].groupby("counterparty_id").month.min()
    still_booked = set(P[P.month == "2024-12"].counterparty_id)
    for cp, m in defaulted[defaulted.index.isin(still_booked)].items():
        state.recovery[cp] = SIM.committee_date(m)
    SY.write_month(pf, "2024-12", state, cfg)
    I.load_exposures(db, "2024-12", cfg)
    PL.ensure_history_tables(db)
    db.run_file("sql/views/clean_exposures.sql")
    trig = db.query("SELECT DISTINCT counterparty_id FROM trigger_history WHERE month = '2024-12'").counterparty_id
    perf = set(P[(P.month == "2024-12") & (P.status == "performing")].counterparty_id)
    names = sorted(set(trig) & perf)
    minutes = pd.DataFrame({"committee_date": SIM.committee_date("2024-12"), "counterparty_id": names,
                            "category": "Opening watch list", "decision": "Place on watch list", "actions": "", "comment": ""})
    PL.historise(db, minutes, cfg)


def run_year(seed=42, out=None, db_path=None, backend=None, clean=True, months=None):
    cfg = I.load_config()
    out = Path(out or ROOT / "outputs/runs")
    if clean:
        shutil.rmtree(SY.RAW, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    SY.RAW.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    db_path = Path(db_path or ROOT / "data/committee.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if clean and Path(db_path).exists():
        Path(db_path).unlink()
    db = DB(db_path, backend)
    pf, state = SY.Portfolio(seed), SIM.State(seed)
    bootstrap(db, pf, state, cfg)
    results = []
    months = months or cfg["committee"]["months"]
    for i, m in enumerate(months):
        SY.write_month(pf, m, state, cfg)
        res = PL.prepare(db, m, cfg, first_month=(i == 0))
        files = write_pack(res, cfg, out / m)
        minutes = SIM.decide(res["universe"], m, state)
        minutes.loc[minutes.category == "Held for data correction", ["decision", "actions"]] = ["Deferred (data correction)", ""]
        mdir = SY.RAW / "minutes"
        mdir.mkdir(exist_ok=True)
        minutes.to_excel(mdir / f"minutes_{res['committee_date']}.xlsx", index=False)
        PL.historise(db, pd.read_excel(mdir / f"minutes_{res['committee_date']}.xlsx", dtype=str).fillna(""), cfg)
        state.progress_recovery(res["universe"])
        results.append({**res, "files": files, "minutes": minutes})
        print(f"{m}: {len(res['universe']):>4} cases | {res['status']}")
    return db, results
