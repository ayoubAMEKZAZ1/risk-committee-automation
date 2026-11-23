"""Year-end view of the process: caseload, flows, actions, data quality caught vs injected, controls."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .synth import RAW  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eba800"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True,
                     "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11,
                     "axes.titleweight": "bold", "axes.titlelocation": "left", "lines.linewidth": 2})
RULE_FOR = {"duplicate_row": "DQ02", "missing_rating": "DQ03", "missing_rm": "DQ03", "negative_amount": "DQ04",
            "overdrawn": "DQ05", "invalid_rating": "DQ06", "negative_dpd": "DQ07", "status_dpd_mismatch": "DQ08",
            "unnormalised_id": "DQ09"}


def dq_detection(db) -> pd.DataFrame:
    """Injected defects vs issues raised, by defect type (row-level), plus issues raised on clean rows."""
    truth = pd.read_csv(RAW / "_truth/defects.csv")
    truth = truth[truth.month >= "2025-01"]
    dq = db.query("SELECT month, rule_id, counterparty_id FROM dq_issues WHERE month >= '2025-01'")
    raised = set(zip(dq.month, dq.rule_id, dq.counterparty_id))
    rows = []
    for d, g in truth[truth.defect != "truncated_extract"].groupby("defect"):
        hit = sum((m, RULE_FOR[d], cp) in raised for m, cp in zip(g.month, g.counterparty_id))
        rows.append({"defect": d, "rule": RULE_FOR[d], "injected": len(g), "caught": hit})
    trunc = truth[truth.defect == "truncated_extract"].month.unique()
    caught_months = set(dq[dq.rule_id == "DQ11"].month)
    rows.append({"defect": "truncated_extract (months)", "rule": "DQ11", "injected": len(trunc),
                 "caught": len(set(trunc) & caught_months)})
    R = pd.DataFrame(rows)
    R["recall_pct"] = 100 * R.caught / R.injected
    expected = {(m, RULE_FOR[d], cp) for m, d, cp in zip(truth.month, truth.defect, truth.counterparty_id) if d in RULE_FOR}
    row_level = dq[dq.rule_id.isin(set(RULE_FOR.values()) | {"DQ01"})]
    false_pos = sum((m, r, cp) not in expected for m, r, cp in zip(row_level.month, row_level.rule_id, row_level.counterparty_id))
    extra_months = len(caught_months - set(trunc))
    return R, int(false_pos), extra_months


def time_on_watch(db) -> pd.DataFrame:
    d = db.query("SELECT counterparty_id, committee_date, decision FROM decisions ORDER BY counterparty_id, committee_date")
    out = []
    for cp, g in d.groupby("counterparty_id"):
        start = None
        for r in g.itertuples():
            if r.decision == "Place on watch list" and start is None:
                start = r.committee_date
            elif r.decision in ("Exit watch list", "Transfer to recovery") and start is not None:
                months = (pd.Timestamp(r.committee_date) - pd.Timestamp(start)).days / 30.4
                out.append({"counterparty_id": cp, "entered": start, "left": r.committee_date, "outcome": r.decision,
                            "months_on_watch": round(months)})
                start = None
    return pd.DataFrame(out)


def monthly_kpis(results) -> pd.DataFrame:
    rows = []
    for r in results:
        U, A, dq, mnt = r["universe"], r["actions"], r["dq"], r["minutes"]
        rows.append({"month": r["month"], "committee": r["committee_date"], "pack_status": r["status"],
                     "cases": len(U), **{c: int((U.category == c).sum()) for c in
                                         ["New entries", "Under follow-up", "Proposed exits", "Proposed transfers to recovery",
                                          "Recovery follow-up", "Held for data correction"]},
                     "placed_on_watch": int((mnt.decision == "Place on watch list").sum()),
                     "exited": int((mnt.decision == "Exit watch list").sum()),
                     "transferred": int((mnt.decision == "Transfer to recovery").sum()),
                     "watch_list_after": int(mnt.decision.isin(["Place on watch list", "Keep on watch list", "Restructure"]).sum()),
                     "dq_blocking": int((dq.severity == "Blocking").sum()), "dq_warning": int((dq.severity == "Warning").sum()),
                     "dq_info": int((dq.severity == "Info").sum()),
                     "actions_open": int(A.status.isin(["Overdue", "Open, not yet due"]).sum()),
                     "actions_overdue": int((A.status == "Overdue").sum()),
                     **{f"overdue_{b}": int((A.aging_bucket == b).sum()) for b in
                        ["0-30 days", "31-60 days", "61-90 days", "Over 90 days"]},
                     "controls_failed": ", ".join(r["controls"].loc[r["controls"].result == "FAIL", "control"]) or "-"})
    return pd.DataFrame(rows)


def _save(fig, p):
    fig.tight_layout()
    fig.savefig(p, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def figures(K: pd.DataFrame, R: pd.DataFrame, out: Path):
    fd = out / "figures"
    fd.mkdir(parents=True, exist_ok=True)
    x = K.month.str[5:].map(lambda m: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m) - 1])
    cats = ["New entries", "Under follow-up", "Proposed exits", "Proposed transfers to recovery", "Recovery follow-up",
            "Held for data correction"]
    fig, axes = plt.subplots(2, 3, figsize=(11, 5.4), sharex=True)
    for ax, c in zip(axes.ravel(), cats):
        ax.bar(x, K[c], color=C[0], width=0.65)
        ax.set_title(c, fontsize=10)
        ax.tick_params(axis="x", labelsize=7.5, rotation=0)
        ax.grid(axis="x", visible=False)
    fig.suptitle("Committee cases by category, each monthly committee of 2025", x=0.01, ha="left", fontweight="bold")
    _save(fig, fd / "fig1_cases_by_category.png")

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    axes[0].plot(x, K.watch_list_after, color=C[0], marker="o", ms=4)
    axes[0].set_ylim(0, K.watch_list_after.max() * 1.25)
    axes[0].set_title("Watch list after each committee (names)")
    w = 0.38
    xi = np.arange(len(K))
    axes[1].bar(xi - w / 2, K.placed_on_watch, width=w, color=C[1], label="Placed on watch")
    axes[1].bar(xi + w / 2, K.exited + K.transferred, width=w, color=C[0], label="Exited or transferred to recovery")
    axes[1].set_xticks(xi, x)
    axes[1].legend(frameon=False, fontsize=8.5, loc="upper left")
    axes[1].set_ylim(0, (K.exited + K.transferred).max() * 1.45)
    axes[1].set_title("Flows decided at each committee (names)")
    axes[1].grid(axis="x", visible=False)
    _save(fig, fd / "fig2_watch_list_flows.png")

    fig, ax = plt.subplots(figsize=(9, 4))
    bottom = np.zeros(len(K))
    for i, b in enumerate(["0-30 days", "31-60 days", "61-90 days", "Over 90 days"]):
        v = K[f"overdue_{b}"].to_numpy()
        ax.bar(x, v, bottom=bottom, color=C[i], width=0.65, label=b, edgecolor=SURFACE, linewidth=2)
        bottom += v
    ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="upper left")
    ax.set_ylim(0, bottom.max() * 1.2)
    ax.set_title("Overdue committee actions by age, on each committee date")
    ax.grid(axis="x", visible=False)
    _save(fig, fd / "fig3_action_aging.png")

    fig, ax = plt.subplots(figsize=(8.5, 4))
    r = R.sort_values("recall_pct")
    lab = r.defect.str.replace("_", " ")
    ax.barh(lab, r.recall_pct, color=C[2], height=0.6)
    for y, (v, n, k) in enumerate(zip(r.recall_pct, r.injected, r.caught)):
        ax.text(v + 1, y, f"{k}/{n}", va="center", fontsize=8.5)
    ax.set_xlim(0, 115)
    ax.set_xlabel("% of injected defects caught")
    ax.grid(axis="y", visible=False)
    ax.set_title("Data-quality checks vs defects injected into the 2025 extracts")
    _save(fig, fd / "fig4_dq_detection.png")


def write(db, results, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    K = monthly_kpis(results)
    R, false_pos, extra_months = dq_detection(db)
    T = time_on_watch(db)
    K.to_csv(out / "monthly_kpis.csv", index=False)
    R.to_csv(out / "dq_detection.csv", index=False)
    T.to_csv(out / "time_on_watch.csv", index=False)
    figures(K, R, out)
    last = results[-1]
    C_ = last["controls"]
    T25 = T[T.left >= "2025-01-01"]
    md = f"""# Year report: 12 monthly committees on synthetic data (2025)

Generated by `python scripts/run_all.py`. Every number is recomputed on each run.

## Caseload and outcomes

{_md(K[["month", "committee", "pack_status", "cases", "New entries", "Under follow-up", "Proposed exits",
        "Proposed transfers to recovery", "Recovery follow-up", "Held for data correction"]])}

{_md(K[["month", "placed_on_watch", "exited", "transferred", "watch_list_after", "actions_open", "actions_overdue",
        "controls_failed"]])}

- Names that left the watch list during the year: {len(T25)}; median time on the watch list {T25.months_on_watch.median():.0f} months
  ({(T25.outcome == 'Exit watch list').mean():.0%} exited, {(T25.outcome == 'Transfer to recovery').mean():.0%} went to recovery).

## Data quality: injected defects vs issues raised

{_md(R)}

Row-level issues raised on rows with no injected defect: {false_pos}. Extract-size alerts in months without a
truncated extract: {extra_months}. On synthetic data the clean rows are clean by construction, so the
false-positive figure only shows that the rules do not fire on valid records; real extracts will be noisier.

## Controls on the last committee ({last['committee_date']})

{_md(C_)}
"""
    (out / "year_report.md").write_text(md)
    return K, R, T


def _md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        lines.append("| " + " | ".join(f"{v:.1f}" if isinstance(v, float) else str(v) for v in r) + " |")
    return "\n".join(lines)
