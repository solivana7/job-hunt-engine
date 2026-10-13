"""Export the job-hunt tracker to a formatted Excel workbook.

Usage: python -m scripts.export_tracker [output.xlsx]
Data source: the engine's Postgres (DATABASE_URL). Rerun anytime for a fresh copy.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from engine.db import tx

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name="Arial", size=10)
TITLE_FONT = Font(name="Arial", size=13, bold=True, color="1F3864")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
STAGE_FILLS = {
    "targeted": "FFF2CC", "tailored": "DDEBF7", "approved": "C6E0B4",
    "submitted": "BDD7EE", "screening": "FFE699", "interview": "A9D08E",
    "offer": "70AD47", "rejected": "F8CBAD", "withdrawn": "D9D9D9",
}


def _sheet_title(ws, text, note=None):
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    if note:
        ws["A2"] = note
        ws["A2"].font = Font(name="Arial", size=9, italic=True, color="808080")


def _write_table(ws, start_row: int, headers: list[str], rows: list[list]):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=c, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r, row in enumerate(rows, start_row + 1):
        for c, val in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(c in (2, 3, 13)))
    return start_row + len(rows)


def _autosize(ws, widths: dict[int, int]):
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def export(out: Path) -> None:
    wb = Workbook()
    today = datetime.date.today()

    with tx() as conn:
        apps = conn.execute(
            """SELECT a.id, p.company, p.title, a.lane, a.stage, s.score,
                      p.salary_min, p.salary_max, p.location, p.remote, p.url,
                      a.resume_path, a.note_path, a.submitted_at::date AS submitted,
                      a.next_followup, a.contact_email, a.notes
               FROM applications a
               JOIN postings p ON p.id = a.posting_id
               LEFT JOIN scores s ON s.posting_id = a.posting_id AND s.lane = a.lane
               ORDER BY a.id""").fetchall()
        matches = conn.execute(
            """SELECT DISTINCT ON (p.id) p.id, p.company, p.title, p.source, p.first_seen,
                      s.lane, s.score, p.salary_min, p.salary_max, p.location, p.remote, p.url
               FROM scores s JOIN postings p ON p.id = s.posting_id
               LEFT JOIN applications a ON a.posting_id = p.id
               WHERE s.hard_pass AND s.score >= 70 AND a.id IS NULL
               ORDER BY p.id, s.score DESC""").fetchall()
        stages = [r["stage"] for r in conn.execute(
            "SELECT stage, COUNT(*) FROM applications GROUP BY stage")]

    # --- Pipeline ------------------------------------------------------------
    ws = wb.active
    ws.title = "Pipeline"
    _sheet_title(ws, "Application Pipeline",
                 "Live mirror of the tracker database. Re-run export_tracker.py to refresh.")
    headers = ["App ID", "Company", "Title", "Lane", "Stage", "Score", "Comp min", "Comp max",
               "Location", "Remote", "URL", "Resume", "Cover letter", "Submitted",
               "Next follow-up", "Contact", "Notes"]
    rows = [[f"A{a['id']}", a["company"], a["title"], a["lane"], a["stage"], a["score"],
             a["salary_min"], a["salary_max"], a["location"],
             {True: "remote", False: "hybrid/onsite", None: ""}[a["remote"]],
             a["url"], a["resume_path"], a["note_path"],
             a["submitted"].isoformat() if a["submitted"] else "",
             a["next_followup"].isoformat() if a["next_followup"] else "",
             a["contact_email"], a["notes"]] for a in apps]
    last = _write_table(ws, 4, headers, rows)
    for r in range(5, last + 1):
        fill = STAGE_FILLS.get(ws.cell(row=r, column=5).value or "")
        if fill:
            ws.cell(row=r, column=5).fill = PatternFill("solid", fgColor=fill)
    _autosize(ws, {1: 7, 2: 24, 3: 42, 4: 9, 5: 11, 6: 7, 7: 10, 8: 10, 9: 18, 10: 12,
                   11: 40, 12: 34, 13: 34, 14: 11, 15: 12, 16: 22, 17: 20})
    ws.freeze_panes = "A5"

    # --- New matches ------------------------------------------------------------
    ws2 = wb.create_sheet("Matched Not Applied")
    _sheet_title(ws2, "Scored Matches With No Application Yet",
                 "Everything scoring >= 70 that you have not opened an application for. "
                 "Sorted best-first; open one with: python -m engine.main target <posting_id>")
    headers2 = ["Posting ID", "Company", "Title", "Source", "First seen", "Best lane",
                "Score", "Comp min", "Comp max", "Location", "Remote", "URL"]
    rows2 = [[f"P{m['id']}", m["company"], m["title"], m["source"],
              m["first_seen"].isoformat(), m["lane"], m["score"], m["salary_min"],
              m["salary_max"], m["location"],
              {True: "remote", False: "hybrid/onsite", None: ""}[m["remote"]], m["url"]]
             for m in sorted(matches, key=lambda m: -(m["score"] or 0))]
    last2 = _write_table(ws2, 4, headers2, rows2)
    _autosize(ws2, {1: 10, 2: 24, 3: 46, 4: 12, 5: 11, 6: 10, 7: 7, 8: 10, 9: 10,
                    10: 18, 11: 12, 12: 44})
    ws2.freeze_panes = "A5"

    # --- Funnel -----------------------------------------------------------------
    ws3 = wb.create_sheet("Funnel")
    _sheet_title(ws3, "Funnel", "Live formulas over the Pipeline sheet.")
    ws3["A4"] = "Stage"
    ws3["B4"] = "Count"
    ws3["C4"] = "% of pipeline"
    for c in ("A4", "B4", "C4"):
        ws3[c].font = HEADER_FONT
        ws3[c].fill = HEADER_FILL
        ws3[c].border = BORDER
    all_stages = ["targeted", "tailored", "approved", "submitted", "screening",
                  "interview", "offer", "rejected", "withdrawn"]
    for i, st in enumerate(all_stages, 5):
        ws3.cell(row=i, column=1, value=st).font = BODY_FONT
        ws3.cell(row=i, column=2, value=f'=COUNTIF(Pipeline!$E$5:$E${last or 5},A{i})').font = BODY_FONT
        ws3.cell(row=i, column=3,
                 value=f'=IFERROR(B{i}/SUM($B$5:$B$13),0)').font = BODY_FONT
        ws3.cell(row=i, column=3).number_format = "0.0%"
        for c in range(1, 4):
            ws3.cell(row=i, column=c).border = BORDER
        fill = STAGE_FILLS.get(st)
        if fill:
            ws3.cell(row=i, column=1).fill = PatternFill("solid", fgColor=fill)
    ws3.cell(row=15, column=1, value="Total").font = Font(name="Arial", size=10, bold=True)
    ws3.cell(row=15, column=2, value="=SUM(B5:B13)").font = Font(name="Arial", size=10, bold=True)
    ws3.cell(row=17, column=1, value="Active pipeline (submitted..offer):").font = BODY_FONT
    ws3.cell(row=17, column=2, value='=SUM(B8:B11)').font = Font(name="Arial", size=10, bold=True)
    ws3.cell(row=18, column=1, value=f"Exported {today:%Y-%m-%d} from the job-hunt engine Postgres").font = Font(
        name="Arial", size=9, italic=True, color="808080")
    _autosize(ws3, {1: 34, 2: 10, 3: 14})

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"exported {len(apps)} applications, {len(matches)} matches -> {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "artifacts" / f"JobHunt_Tracker_{datetime.date.today():%Y-%m-%d}.xlsx"
    export(target)
