"""Daily digest: NEW high-score postings per lane + 'Waiting on you' threads.

Only items never previously emailed appear in the job sections: each included
row is marked digested_at after the digest goes out, so a posting appears in
exactly one email (across whichever lane first carried it).

Renders to console + artifacts/digest_<date>.txt always; emails via Resend when
RESEND_API_KEY is set. Reply to the email with P<id> codes, or run
`python -m engine.main target <posting_id> [...]` to open applications.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import httpx

from ..config import artifacts_dir, scoring
from ..db import tx

LANE_TITLES = {"data_ai": "Data & AI", "wellness": "Wellness"}


def _fmt_money(lo, hi):
    if lo and hi:
        return f"${lo//1000}k-${hi//1000}k"
    return "comp not listed"


def _fmt_remote(r):
    return {True: "remote", False: "hybrid/onsite", None: "unknown"}[r]


def _cover_for(posting: dict, lane: str, today) -> Path:
    """Write the digest-time cover letter for a posting; returns its path."""
    import re

    from ..config import profile
    from ..llm import write_cover_note
    from ..tailor.tailor import HIGHLIGHTS

    prof = profile(lane)
    note = write_cover_note(posting, prof, HIGHLIGHTS[lane])
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{posting['company']}_{posting['title']}")[:60]
    path = artifacts_dir() / f"{slug}_cover_{today:%Y-%m-%d}.txt"
    path.write_text(note, encoding="utf-8")
    return path


def build(generate_notes: bool = False) -> tuple[str, list[tuple[int, str]]]:
    """Returns (body, included) where included is [(posting_id, lane), ...].

    With generate_notes=True, each included posting gets a cover letter
    (LLM-written when an API key is set, template otherwise), embedded inline
    and saved to artifacts/.
    """
    scr = scoring()
    today = datetime.date.today()
    included: list[tuple[int, str]] = []
    lines: list[str] = [
        f"JOB HUNT DIGEST — {today:%A %Y-%m-%d}",
        "",
    ]
    with tx() as conn:
        for lane, heading in LANE_TITLES.items():
            threshold = scr.get(f"digest_min_{lane}", scr["digest_min"])
            rows = conn.execute(
                """SELECT p.id AS pid, p.*, s.score, s.rationale FROM scores s
                   JOIN postings p ON p.id = s.posting_id
                   WHERE s.lane = %s AND s.hard_pass AND s.score >= %s
                     AND s.digested_at IS NULL
                     AND p.first_seen >= %s - INTERVAL '7 days'
                   ORDER BY s.score DESC LIMIT 15""",
                (lane, threshold, today),
            ).fetchall()
            lines.append(f"=== {heading} (score >= {threshold}, new since last email) ===")
            if not rows:
                lines.append("  (no new matches today)")
            for r in rows:
                included.append((r["pid"], lane))
                why = ""
                try:
                    why = r["rationale"]["reasons"][0]
                except Exception:
                    pass
                flag = " *** INSTANT ALERT ***" if r["score"] >= scr["instant_alert"] else ""
                lines.append(
                    f"[P{r['pid']}] {r['score']}/100{flag}  {r['title']} @ {r['company']}"
                )
                lines.append(
                    f"    {_fmt_money(r['salary_min'], r['salary_max'])} | "
                    f"{_fmt_remote(r['remote'])} | {r['location'] or 'n/a'}"
                )
                lines.append(f"    why: {why}")
                lines.append(f"    {r['url']}")
                if generate_notes:
                    note_path = _cover_for(dict(r), lane, today)
                    lines.append("")
                    lines.append("    --- cover letter (review and personalize the sign-off) ---")
                    for note_line in note_path.read_text(encoding="utf-8").strip().splitlines():
                        lines.append(f"    {note_line}" if note_line else "")
                lines.append("")
            lines.append("")

        waiting = conn.execute(
            """SELECT t.*, a.id AS app_id, p.company, p.title
               FROM email_threads t
               JOIN applications a ON a.id = t.application_id
               JOIN postings p ON p.id = a.posting_id
               WHERE t.needs_reply
               ORDER BY t.last_message_at""").fetchall()
        if waiting:
            lines.append('=== Waiting on you (recruiter thread idle > 48h) ===')
            for w in waiting:
                lines.append(
                    f"[A{w['app_id']}] {w['company']} — {w['title']}: "
                    f"{w['subject']} ({w['last_message_at']:%Y-%m-%d})"
                )
            lines.append("")

        proposals = conn.execute(
            """SELECT e.*, a.id AS app_id, p.company, p.title
               FROM events e
               JOIN applications a ON a.id = e.application_id
               JOIN postings p ON p.id = a.posting_id
               WHERE e.kind = 'stage_proposal' AND e.payload->>'confirmed' IS NULL
               ORDER BY e.created_at DESC LIMIT 10""").fetchall()
        if proposals:
            lines.append("=== Suggested stage changes (confirm: python -m engine.main confirm-stage <app_id> <stage>) ===")
            for pr in proposals:
                st = pr["payload"].get("proposed_stage")
                lines.append(f"[A{pr['app_id']}] {pr['company']} — {pr['title']}: -> {st}?")
            lines.append("")

    lines.append("Next: python -m engine.main target <posting_id> [...]")
    return "\n".join(lines), included


def mark_digested(included: list[tuple[int, str]]) -> None:
    with tx() as conn:
        for posting_id, lane in included:
            conn.execute(
                "UPDATE scores SET digested_at = now() WHERE posting_id = %s AND lane = %s",
                (posting_id, lane))
        conn.commit()


def run() -> None:
    body, included = build(generate_notes=True)
    print(body)
    out: Path = artifacts_dir() / f"digest_{datetime.date.today():%Y-%m-%d}.txt"
    out.write_text(body, encoding="utf-8")

    key = os.environ.get("RESEND_API_KEY")
    to = os.environ.get("NOTIFY_EMAIL")
    emailed = False
    if key and to:
        sender = os.environ.get("DIGEST_FROM") or f"Job Hunt Engine <engine@{to.split('@')[1]}>"
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from": sender,
                "to": [to],
                "subject": f"Job Hunt Digest — {datetime.date.today():%Y-%m-%d}",
                "text": body,
            },
            timeout=30,
        )
        emailed = resp.status_code in (200, 201, 202)
        print(f"digest: emailed (id {resp.json().get('id')})" if emailed
              else f"digest: email failed {resp.status_code} {resp.text[:200]}")
    else:
        print("digest: RESEND_API_KEY/NOTIFY_EMAIL not set — console only")

    # Mark rendered items as digested once the digest actually went out. If the
    # email failed, leave them undigested so the next run retries them.
    if included and (emailed or not (key and to)):
        mark_digested(included)
        print(f"digest: {len(included)} items marked digested")
