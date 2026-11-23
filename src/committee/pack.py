"""Committee pack: an Excel workbook (summary, one sheet per category, actions, data quality), one CSV
per category for the teams that prepare each section, and a Word draft of the minutes."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CATEGORY_COLUMNS = {
    "counterparty_id": "Counterparty", "company_name": "Company", "segment": "Segment", "sector": "Sector",
    "branch": "Branch", "rm": "RM", "internal_rating": "Rating", "days_past_due": "Days past due",
    "limit_mad": "Limit (MAD)", "on_balance_mad": "On-balance (MAD)", "off_balance_mad": "Off-balance (MAD)",
    "triggers": "Triggers this month", "last_review": "Last review", "last_decision": "Last decision",
    "months_since_last_trigger": "Months since last trigger", "litigation_stage": "Litigation stage",
    "months_in_recovery": "Months in recovery", "recovered_to_date_mad": "Recovered to date (MAD)",
    "open_actions": "Open actions", "overdue_actions": "Overdue actions"}
SHEET = {"New entries": "New entries", "Under follow-up": "Follow-up", "Proposed exits": "Exits",
         "Proposed transfers to recovery": "Transfers", "Recovery follow-up": "Recovery",
         "Held for data correction": "Held (data)"}


def _rows(df):
    """DataFrame rows with missing values as empty cells."""
    from openpyxl.utils.dataframe import dataframe_to_rows
    return dataframe_to_rows(df.astype(object).where(df.notna(), None), index=False, header=True)


def _style_sheet(ws, money_cols=(), widths=None):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2A3B4C")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Arial", size=9)
            if c.column in money_cols:
                c.number_format = '#,##0;(#,##0);"-"'
    for j in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(j)].width = (widths or {}).get(j, 14)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False


def write_pack(res: dict, cfg: dict, out_dir: Path) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    out_dir = Path(out_dir)
    (out_dir / "by_category").mkdir(parents=True, exist_ok=True)
    U, C, B, A, dq = res["universe"], res["controls"], res["bridge"], res["actions"], res["dq"]
    cats = cfg["categories"] + ["Held for data correction"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    f = lambda **k: Font(name="Arial", **k)  # noqa: E731
    ws["A1"] = cfg["committee"]["name"]
    ws["A1"].font = f(bold=True, size=14)
    ws["A2"] = f"Reporting date {res['month']} | committee {res['committee_date']}"
    ws["A2"].font = f(color="52514E")
    ws["A3"] = f"Pack status: {res['status']}"
    ok = res["status"] == "READY"
    ws["A3"].font = f(bold=True, color="FFFFFF")
    ws["A3"].fill = PatternFill("solid", fgColor="1BAF7A" if ok else "EB6834")
    r = 5
    ws.cell(r, 1, "Cases by category").font = f(bold=True)
    ws.cell(r + 1, 1, "Category").font = f(bold=True)
    ws.cell(r + 1, 2, "Names").font = f(bold=True)
    ws.cell(r + 1, 3, "On-balance (MAD)").font = f(bold=True)
    for i, c in enumerate(cats):
        sheet = SHEET[c]
        rr = r + 2 + i
        ws.cell(rr, 1, c).font = f()
        ws.cell(rr, 2, f"=COUNTA('{sheet}'!A:A)-1").font = f()
        cell = ws.cell(rr, 3, f"=SUM('{sheet}'!J:J)")
        cell.font, cell.number_format = f(), '#,##0;(#,##0);"-"'
    tot = r + 2 + len(cats)
    ws.cell(tot, 1, "Total").font = f(bold=True)
    ws.cell(tot, 2, f"=SUM(B{r + 2}:B{tot - 1})").font = f(bold=True)
    c3 = ws.cell(tot, 3, f"=SUM(C{r + 2}:C{tot - 1})")
    c3.font, c3.number_format = f(bold=True), '#,##0;(#,##0);"-"'

    r = tot + 2
    ws.cell(r, 1, "Watch-list flow").font = f(bold=True)
    for i, row in enumerate(B.itertuples(), r + 1):
        ws.cell(i, 1, row.line).font = f(bold=row.line.startswith(("Watch list after", "Proposed watch")))
        ws.cell(i, 2, int(row.names)).font = f()
    r = r + len(B) + 2
    ws.cell(r, 1, "Pre-submission controls").font = f(bold=True)
    for i, row in enumerate(C.itertuples(), r + 1):
        ws.cell(i, 1, f"{row.control}  {row.description}").font = f()
        cc = ws.cell(i, 2, row.result)
        cc.font = f(bold=True, color={"PASS": "1BAF7A", "WARN": "B88400", "FAIL": "EB6834"}[row.result])
        ws.cell(i, 3, row.detail).font = f(color="52514E")
    ws.column_dimensions["A"].width = 70
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 60
    ws.sheet_view.showGridLines = False

    cols = list(CATEGORY_COLUMNS)
    for c in cats:
        d = U[U.category == c][cols].rename(columns=CATEGORY_COLUMNS)
        d.to_csv(out_dir / "by_category" / f"{c.lower().replace(' ', '_')}.csv", index=False)
        s = wb.create_sheet(SHEET[c])
        for row in _rows(d):
            s.append(row)
        _style_sheet(s, money_cols=(9, 10, 11, 18), widths={2: 30, 12: 40, 14: 20})

    s = wb.create_sheet("Actions")
    act = A[A.status.isin(["Overdue", "Open, not yet due"])].sort_values("days_overdue", ascending=False)
    act = act[["action_id", "counterparty_id", "action", "owner", "committee_date", "due_date", "status",
               "days_overdue", "aging_bucket"]]
    for row in _rows(act):
        s.append(row)
    _style_sheet(s, widths={3: 40})
    s = wb.create_sheet("Escalations")
    esc = act[(act.status == "Overdue") & (act.days_overdue > cfg["escalation_days"])]
    for row in _rows(esc):
        s.append(row)
    _style_sheet(s, widths={3: 40})
    s = wb.create_sheet("Data quality")
    for row in _rows(dq[["rule_id", "severity", "counterparty_id", "row_no", "detail"]]):
        s.append(row)
    _style_sheet(s, widths={5: 70})
    xlsx = out_dir / f"committee_pack_{res['month']}.xlsx"
    wb.save(xlsx)
    docx = write_minutes_draft(res, cfg, out_dir / f"minutes_draft_{res['committee_date']}.docx")
    return {"xlsx": xlsx, "docx": docx}


def write_minutes_draft(res: dict, cfg: dict, path: Path) -> Path:
    """Agenda and minutes template: each case with its proposal, and a blank decision column."""
    from docx import Document
    from docx.shared import Pt

    U = res["universe"]
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(9)
    doc.add_heading(cfg["committee"]["name"], level=1)
    doc.add_paragraph(f"Minutes (draft) - committee of {res['committee_date']} - reporting date {res['month']}")
    if res["status"] != "READY":
        doc.add_paragraph(f"PACK STATUS: {res['status']}. See the controls before circulating.").runs[0].bold = True
    doc.add_paragraph("Attendees: ______________________    Secretary: ______________________")
    proposal = {"New entries": "Place on watch list", "Under follow-up": "Keep on watch list",
                "Proposed exits": "Exit watch list", "Proposed transfers to recovery": "Transfer to recovery",
                "Recovery follow-up": "Continue recovery", "Held for data correction": "Defer (data correction)"}
    for i, c in enumerate(cfg["categories"] + ["Held for data correction"], 1):
        d = U[U.category == c]
        doc.add_heading(f"{i}. {c} ({len(d)})", level=2)
        if d.empty:
            doc.add_paragraph("No case this month.")
            continue
        t = doc.add_table(rows=1, cols=6)
        t.style = "Light Grid Accent 1"
        for j, h in enumerate(["Counterparty", "Company", "RM", "On-balance (MAD m)", "Proposal", "Decision"]):
            t.rows[0].cells[j].text = h
        for row in d.head(40).itertuples():
            cells = t.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = row.counterparty_id, row.company_name, row.rm
            cells[3].text = f"{row.on_balance_mad / 1e6:,.1f}"
            cells[4].text, cells[5].text = proposal[c], ""
        if len(d) > 40:
            doc.add_paragraph(f"... and {len(d) - 40} more cases: see the '{SHEET[c]}' sheet of the pack.")
    doc.add_heading("Actions decided", level=2)
    doc.add_paragraph("Record each action with its owner and due date; they feed the action tracker.")
    doc.save(path)
    return path
