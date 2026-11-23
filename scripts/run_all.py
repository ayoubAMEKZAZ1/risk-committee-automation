"""Simulate twelve monthly committees end to end and write the packs, the year report and the figures.

    python scripts/run_all.py                 # DuckDB if installed, otherwise SQLite
    COMMITTEE_DB_BACKEND=sqlite python scripts/run_all.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.committee import report  # noqa: E402
from src.committee.run import ROOT, run_year  # noqa: E402

if __name__ == "__main__":
    t = time.time()
    db, results = run_year()
    print(f"Database engine: {db.backend}")
    K, R, T = report.write(db, results, ROOT / "outputs")
    print(f"Packs in outputs/runs/<month>/, year report in outputs/year_report.md ({time.time() - t:.0f}s)")
