"""Gmail thread tracking (read-only) + CC/BCC inbound logging.

Nightly job (spec: 22:00 PT):
  1. Search inbox for threads involving contact_email of open applications,
     or whose subject/snippet contains those companies.
  2. Upsert into email_threads; needs_reply=true when the last message is from
     the recruiter and no reply was sent within 48h.
  3. If the candidate replied last: clear needs_reply, push next_followup +7 days.
  4. Scheduling language ("interview", "availability", "calendar") proposes a
     stage change — the engine NEVER changes stage on email content alone.
     Confirm with: python -m engine.main confirm-stage <app_id> interview

Outbound logging: CC/BCC RESEND_TRACKING_ADDRESS from any client; Resend's
inbound webhook posts to `python -m engine.main ingest-email` (see README).

Guardrails: gmail.readonly scope, no auto-replies, no auto-send.

Setup (one time):
  1. pip install '.[gmail]'
  2. Create an OAuth client (desktop app) in Google Cloud Console, enable the
     Gmail API, and set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env.
  3. Run `python -m engine.main gmail-auth` to authorize; the token is stored
     at GMAIL_TOKEN_PATH (chmod 600).
"""
from __future__ import annotations

import datetime
import json
import os
import re

from ..db import tx

ME = "owner@example.com"
SCHEDULING_RE = re.compile(r"\b(interview|availability|calendar|schedule a|time slots)\b", re.IGNORECASE)


def _service():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit("gmail extras not installed: pip install '.[gmail]'")

    token_path = os.environ.get("GMAIL_TOKEN_PATH", ".gmail_token.json")
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, ["https://www.googleapis.com/auth/gmail.readonly"])
    if not creds or not creds.valid:
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        if not client_id:
            raise SystemExit("Gmail not configured: set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET (see module docstring)")
        flow = InstalledAppFlow.from_client_config(
            {"installed": {"client_id": client_id, "client_secret": client_secret,
                           "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                           "token_uri": "https://oauth2.googleapis.com/token",
                           "redirect_uris": ["http://localhost"]}},
            ["https://www.googleapis.com/auth/gmail.readonly"])
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        os.chmod(token_path, 0o600)
    return build("gmail", "v1", credentials=creds)


def gmail_auth() -> None:
    _service()
    print("gmail: authorized")


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def run() -> None:
    service = _service()
    now = datetime.datetime.now(datetime.timezone.utc)
    with tx() as conn:
        apps = conn.execute(
            """SELECT a.id, a.contact_email, a.next_followup, p.company, p.title
               FROM applications a JOIN postings p ON p.id = a.posting_id
               WHERE a.stage IN ('submitted','screening','interview','offer')""").fetchall()
        if not apps:
            print("gmail_sync: no open applications")
            return

        # gmail search ORs are capped; run one query per contact, one per company
        queries = []
        for a in apps:
            if a["contact_email"]:
                queries.append((a, f"from:{a['contact_email']}"))
        for a in apps:
            queries.append((a, f'"{a["company"]}"'))

        seen_threads: set[str] = set()
        hits = 0
        for app, q in queries:
            try:
                resp = service.users().threads().list(userId="me", q=q, maxResults=10).execute()
            except Exception as e:
                print(f"gmail_sync: query failed ({q}): {e}")
                continue
            for t in (resp.get("threads") or []):
                tid = t["id"]
                if tid in seen_threads:
                    continue
                seen_threads.add(tid)
                hits += _process_thread(conn, service, tid, app, apps, now)
        conn.commit()
    print(f"gmail_sync: {hits} thread updates across {len(seen_threads)} threads")


def _process_thread(conn, service, tid: str, anchor_app: dict, apps: list, now) -> int:
    try:
        th = service.users().threads().get(userId="me", id=tid, format="metadata",
                                            metadataHeaders=["From", "To", "Subject", "Date"]).execute()
    except Exception:
        return 0
    msgs = th.get("messages") or []
    if not msgs:
        return 0
    hdrs = [_headers(m) for m in msgs]
    subject = hdrs[-1].get("subject", "")
    participants = sorted({h.get("from", "") for h in hdrs} | {h.get("to", "") for h in hdrs})
    last_from_me = ME in (hdrs[-1].get("from", "").lower())
    last_at = datetime.datetime.fromtimestamp(int(msgs[-1]["internalDate"]) / 1000, datetime.timezone.utc)
    snippet = msgs[-1].get("snippet", "")

    app_id = anchor_app["id"]
    for a in apps:
        blob = f"{subject} {snippet}".lower()
        if a["company"].lower() in blob or (a["contact_email"] and a["contact_email"].lower() in blob):
            app_id = a["id"]
            break

    stale = (now - last_at) > datetime.timedelta(hours=48)
    needs_reply = (not last_from_me) and stale
    conn.execute(
        """INSERT INTO email_threads (application_id, gmail_thread_id, subject, participants,
                                      last_message_at, snippet, needs_reply)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (gmail_thread_id) DO UPDATE SET
             application_id = EXCLUDED.application_id, last_message_at = EXCLUDED.last_message_at,
             snippet = EXCLUDED.snippet, needs_reply = EXCLUDED.needs_reply""",
        (app_id, tid, subject, participants, last_at, snippet, needs_reply))

    if last_from_me:
        conn.execute(
            "UPDATE applications SET next_followup = CURRENT_DATE + INTERVAL '7 days', updated_at = now() "
            "WHERE id = %s AND stage IN ('submitted','screening')", (app_id,))
    elif SCHEDULING_RE.search(subject + " " + snippet):
        existing = conn.execute(
            """SELECT 1 FROM events WHERE application_id = %s AND kind = 'stage_proposal'
               AND payload->>'thread_id' = %s AND payload->>'confirmed' IS NULL""",
            (app_id, tid)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO events (application_id, kind, payload) VALUES (%s, 'stage_proposal', %s::jsonb)",
                (app_id, json.dumps({"thread_id": tid, "proposed_stage": "interview",
                                     "subject": subject, "confirmed": None})))
    return 1


def ingest_email(payload: dict) -> dict:
    """Resend inbound webhook (CC/BCC tracking address). Logs an email_sent event
    by matching recipient + subject against open applications."""
    from_addr = payload.get("from", "")
    subject = re.sub(r"^(re|fw):\s*", "", payload.get("subject", ""), flags=re.IGNORECASE)
    body = (payload.get("text") or "")[:2000]
    matched = 0
    with tx() as conn:
        apps = conn.execute(
            """SELECT a.id, p.company, p.title FROM applications a
               JOIN postings p ON p.id = a.posting_id
               WHERE a.stage NOT IN ('rejected','withdrawn')""").fetchall()
        blob = f"{subject} {body}".lower()
        # company match wins; title fallback only when it identifies one app
        company_hits = [a for a in apps if a["company"].lower() in blob]
        title_hits = [a for a in apps if a["title"].lower() in blob]
        targets = company_hits or (title_hits if len(title_hits) == 1 else [])
        for a in targets:
            conn.execute(
                "INSERT INTO events (application_id, kind, payload) VALUES (%s, 'email_sent', %s::jsonb)",
                (a["id"], json.dumps({"from": from_addr, "subject": subject,
                                      "channel": "cc_tracking",
                                      "matched_on": a["company"]})))
            matched += 1
        conn.commit()
    return {"matched": matched}
