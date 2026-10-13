"""Weekly funnel report: counts by stage per lane, conversion, velocity.

Command: python -m engine.main funnel
"""
from __future__ import annotations

import datetime

from ..db import tx

STAGES = ["targeted", "tailored", "approved", "submitted", "screening",
          "interview", "offer", "rejected", "withdrawn"]


def run() -> None:
    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)
    with tx() as conn:
        lines = [f"FUNNEL REPORT — week of {week_ago} to {today}", ""]
        lanes = conn.execute("SELECT DISTINCT lane FROM applications").fetchall()
        for lane_row in lanes:
            lane = lane_row["lane"]
            lines.append(f"=== {lane} ===")
            counts = {s: 0 for s in STAGES}
            for r in conn.execute(
                "SELECT stage, COUNT(*) n FROM applications WHERE lane = %s GROUP BY 1", (lane,)
            ):
                counts[r["stage"]] = r["n"]
            total = sum(counts.values()) or 1
            for s in STAGES:
                if counts[s]:
                    lines.append(f"  {s:<11} {counts[s]:>3}  ({100*counts[s]/total:.0f}%)")
            active = sum(counts[s] for s in ("submitted", "screening", "interview"))
            inflow = sum(counts[s] for s in ("targeted", "tailored", "approved"))
            lines.append(f"  active pipeline: {active} | awaiting submission: {inflow}")
            lines.append("")

        week = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM postings WHERE first_seen >= %s)      AS new_postings,
                 (SELECT COUNT(*) FROM scores s JOIN postings p ON p.id = s.posting_id
                   WHERE p.first_seen >= %s)                                  AS matched,
                 (SELECT COUNT(*) FROM applications WHERE submitted_at >= %s) AS applied""",
            (week_ago, week_ago, week_ago)).fetchone()
        lines.append("This week: "
                     f"{week['new_postings']} new postings, "
                     f"{week['matched']} scored, "
                     f"{week['applied']} applications submitted")
        out = "\n".join(lines)
    print(out)
