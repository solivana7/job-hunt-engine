"""Follow-up cadence: drafts a short note for every application whose
next_followup is due. Drafts land in artifacts/followups/ — the engine never
sends. (Guardrail 5.)

Command: python -m engine.main followups
"""
from __future__ import annotations

import datetime

from ..config import artifacts_dir
from ..db import log_event, tx


def run() -> None:
    today = datetime.date.today()
    with tx() as conn:
        rows = conn.execute(
            """SELECT a.*, p.company, p.title, p.url, a.submitted_at
               FROM applications a JOIN postings p ON p.id = a.posting_id
               WHERE a.next_followup IS NOT NULL AND a.next_followup <= %s
                 AND a.stage IN ('submitted','screening','interview')
               ORDER BY a.next_followup""", (today,)).fetchall()
        if not rows:
            print("followups: nothing due")
            return
        for r in rows:
            submitted = r["submitted_at"].date() if r["submitted_at"] else None
            when = f"on {submitted:%b %d}" if submitted else "recently"
            body = f"""Subject: Following up — {r['title']}

Hello,

I submitted my application for the {r['title']} role at {r['company']} {when}
and wanted to reaffirm my interest. My background maps directly to the mandate:
enterprise-scale program leadership with measured, board-visible outcomes.

Happy to provide anything further that would help move the conversation forward.

Best,
Candidate Name
owner@example.com | (555) 010-0000
"""
            path = artifacts_dir() / "followups" / f"app{r['id']}_{today:%Y-%m-%d}.txt"
            path.write_text(body, encoding="utf-8")
            conn.execute(
                "UPDATE applications SET next_followup = %s + INTERVAL '7 days', updated_at = now() "
                "WHERE id = %s", (today, r["id"]))
            log_event(conn, r["id"], "note", {"followup_draft": str(path)})
            print(f"app {r['id']} ({r['company']} — {r['title']}): draft -> {path}")
        conn.commit()
