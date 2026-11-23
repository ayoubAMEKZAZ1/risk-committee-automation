"""Tests run on whichever engine is available (DuckDB if installed, else SQLite).
CI runs them on both: COMMITTEE_DB_BACKEND=duckdb and COMMITTEE_DB_BACKEND=sqlite."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.committee import ingest as I  # noqa: E402
from src.committee import report  # noqa: E402
from src.committee.db import DB  # noqa: E402
from src.committee.run import run_year  # noqa: E402


@pytest.fixture(scope="module")
def year(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    db, results = run_year(out=out, db_path=out / "test.db",
                           months=["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
                                   "2025-07", "2025-08", "2025-09", "2025-10"])
    return db, results, out


def test_header_mapping_and_amount_parsing():
    cfg = I.load_config()
    df = pd.DataFrame({h: ["x"] for h in ["CPTY_ID", "COMPANY", "SEGMENT", "SECTOR", "REGION", "BRANCH", "RM",
                                          "LIMIT_MAD", "ON_BAL_MAD", "OFF_BAL_MAD", "DPD", "RATING", "STATUS"]})
    assert list(I.map_columns(df, cfg["columns"]["exposures"]).columns) == list(cfg["columns"]["exposures"])
    with pytest.raises(ValueError):
        I.map_columns(df.rename(columns={"RATING": "Notation"}), cfg["columns"]["exposures"])
    s = I.parse_amount(pd.Series(["1 234 567,89", "1234567.89", "", "-12,5"]))
    assert s.iloc[0] == pytest.approx(1234567.89) and s.iloc[1] == pytest.approx(1234567.89)
    assert pd.isna(s.iloc[2]) and s.iloc[3] == pytest.approx(-12.5)


def test_db_days_function():
    db = DB()
    n = db.query(f"SELECT {db.days(chr(39) + '2025-01-15' + chr(39), chr(39) + '2025-03-01' + chr(39))} AS d").d.iloc[0]
    assert int(n) == 45


def test_every_injected_defect_is_caught(year):
    db, _, _ = year
    R, false_pos, extra_months = report.dq_detection(db)
    assert (R.recall_pct == 100).all(), R
    assert false_pos == 0 and extra_months == 0


def test_truncated_month_fails_controls_and_others_pass(year):
    _, results, _ = year
    status = {r["month"]: r["status"] for r in results}
    assert status["2025-09"].startswith("DRAFT")
    assert all(s == "READY" for m, s in status.items() if m != "2025-09"), status


def test_categories_are_exclusive_and_bridge_balances(year):
    _, results, _ = year
    for r in results:
        U = r["universe"]
        assert U.counterparty_id.is_unique
        assert int(r["bridge"].loc[r["bridge"].line == "Unexplained", "names"].iloc[0]) == 0


def test_held_cases_have_blocking_issues_and_get_no_decision(year):
    _, results, _ = year
    for r in results:
        held = set(r["universe"].query("category == 'Held for data correction'").counterparty_id)
        blocking = set(r["dq"].query("severity == 'Blocking'").counterparty_id)
        assert held <= blocking
        m = r["minutes"]
        assert (m[m.counterparty_id.isin(held)].decision == "Deferred (data correction)").all()


def test_history_and_tracker_in_sync(year):
    db, results, _ = year
    hist = db.query("SELECT counterparty_id, committee_date, decision FROM decisions")
    trk = I.read_tracker()
    merged = trk.merge(hist, on=["counterparty_id", "committee_date"], how="outer", indicator=True)
    assert (merged._merge == "both").all()
    n_dates = hist.committee_date.nunique()
    assert n_dates == len(results) + 1            # bootstrap committee + one per month


def test_pack_files_written(year):
    _, results, out = year
    r = results[-1]
    assert Path(r["files"]["xlsx"]).exists() and Path(r["files"]["docx"]).exists()
    assert len(list((out / r["month"] / "by_category").glob("*.csv"))) == 6
