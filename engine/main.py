"""Job Hunt Engine CLI.

Commands:
  init-db                      apply schema + v2 migration
  seed                         insert known-good target postings (spec section 10)
  aggregate [--lane] [--source]  pull postings from configured sources
  score [--lane]               two-pass scoring per lane
  digest                       render/send the daily digest
  target <posting_id> [...]    open applications from digest items
  tailor <application_id>      generate tailored resume + cover note
  review <application_id>      approval gate — typed APPROVE required
  submit <application_id> [--no-dry-run]
  followups | funnel           tracker reports
  gmail-auth | gmail_sync      email thread tracking (read-only)
  confirm-stage <app_id> <stage>   accept a proposed stage change
  status                       pipeline snapshot
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

from .config import profiles
from .db import init_db, log_event, tx


def cmd_init_db(_args):
    init_db()
    print("db: schema + migration_v2 applied")


def cmd_seed(_args):
    from .aggregator import JobPosting, insert_posting

    seeds = [
        # spec section 10 — v1 acceptance rows
        JobPosting("seed", "steris-cdo", "STERIS", "Chief Data Officer", "Remote", True,
                   225000, 300000,
                   "https://jobs.steris.com/",
                   "Chief Data Officer for regulated healthcare. Remote. Build the enterprise "
                   "AI Center of Excellence, data governance operating model, and analytics "
                   "transformation across Fortune 500 healthcare markets. $225,000-$300,000 + bonus and long-term incentives."),
        JobPosting("seed", "optum-cdo", "Optum / UnitedHealth Group", "Chief Data Officer", "Remote", True,
                   225000, 375000,
                   "https://careers.unitedhealthgroup.com/",
                   "Chief Data Officer, enterprise data strategy across UnitedHealth Group. "
                   "Healthtech scale, data governance, GenAI and LLM programs, data monetization, "
                   "decision intelligence for population health. Remote (US). $225,000-$375,000."),
        JobPosting("seed", "spglobal-head-ai", "S&P Global", "Head of AI Product Transformation", "New York, NY", False,
                   176000, 336000,
                   "https://careers.spglobal.com/",
                   "Head of AI Product Transformation. NYC hybrid 2 days. AI-first operating model, "
                   "GenAI/LLM product strategy, transformation at enterprise scale. $176,000-$336,000."),
        JobPosting("seed", "netatwork-dir-ai", "Net at Work", "Director, AI Transformation", "Remote", True,
                   175000, 200000,
                   "https://www.netatwork.com/careers/",
                   "Director of AI Transformation. Remote. Operating-role transformation mandate "
                   "below the candidate's salary floor; benchmark only. $175,000-$200,000."),
        # v2 acceptance rows (wellness addendum section 8)
        JobPosting("seed", "twochairs-sr-dir-product", "Two Chairs", "Senior Director of Product — Wellness Programs", "Remote", True,
                   170000, 200000,
                   "https://www.twochairs.com/careers/",
                   "Senior Director of Product at a mental-health tech company. Remote. Employee "
                   "wellbeing, clinical program design, outcomes measurement, behavior change at "
                   "scale. Part of the wellness watchlist. (Placeholder comp estimate — replace "
                   "with the live posting's range when SerpAPI discovers it.)"),
        JobPosting("seed", "ggh-dir-clinical-perf", "Greater Good Health", "Director of Clinical Performance", "Remote", True,
                   160000, 190000,
                   "https://www.greatergoodhealth.com/careers/",
                   "Director of Clinical Performance. Remote. Population health, outcomes "
                   "measurement, clinical strategy, workforce health programs, engagement and "
                   "resilience program design. $160,000-$190,000."),
    ]
    new = dupes = 0
    with tx() as conn:
        for p in seeds:
            _, created = insert_posting(conn, p)
            new, dupes = new + created, dupes + (not created)
    print(f"seed: new={new} dupes={dupes} (6 expected rows)")


def cmd_aggregate(args):
    from .aggregator import aggregate
    aggregate(lane=args.lane, sources=tuple(args.source.split(",")) if args.source else
              ("greenhouse", "lever", "serpapi", "boards"))


def cmd_score(args):
    from .matcher.score import run
    run(lane=args.lane)


def cmd_digest(_args):
    from .notify.digest import run
    run()


def cmd_target(args):
    with tx() as conn:
        for pid in args.ids:
            row = conn.execute(
                "SELECT p.*, s.score FROM postings p LEFT JOIN scores s "
                "ON s.posting_id = p.id AND s.lane = %s WHERE p.id = %s",
                ("data_ai", pid)).fetchone()
            if not row:
                print(f"posting {pid}: not found")
                continue
            lane = row["lane"] or "data_ai"
            existing = conn.execute(
                "SELECT id FROM applications WHERE posting_id = %s", (pid,)).fetchone()
            if existing:
                print(f"posting {pid}: application A{existing['id']} already exists")
                continue
            new = conn.execute(
                "INSERT INTO applications (posting_id, lane) VALUES (%s, %s) RETURNING id",
                (pid, lane)).fetchone()
            log_event(conn, new["id"], "stage_change",
                      {"stage": "targeted", "via": "digest", "score": row["score"]})
            print(f"posting {pid}: application A{new['id']} opened (lane={lane})")
        conn.commit()


def cmd_tailor(args):
    from .tailor.tailor import run
    run(args.application_id)


def cmd_review(args):
    """Approval gate: no path past 'tailored' without a typed APPROVE."""
    with tx() as conn:
        app = conn.execute(
            "SELECT a.*, p.company, p.title, p.url FROM applications a "
            "JOIN postings p ON p.id = a.posting_id WHERE a.id = %s",
            (args.application_id,)).fetchone()
        if not app:
            raise SystemExit(f"application {args.application_id} not found")
        print(f"Application A{app['id']}  [{app['lane']}]  stage={app['stage']}")
        print(f"  {app['title']} @ {app['company']}")
        print(f"  {app['url']}")
        print(f"  resume: {app['resume_path'] or '(not tailored yet)'}")
        print(f"  note:   {app['note_path'] or '(not tailored yet)'}")
        for e in conn.execute(
            "SELECT * FROM events WHERE application_id = %s ORDER BY created_at", (app["id"],)
        ):
            print(f"  event[{e['kind']}]: {json.dumps(e['payload'])[:200]}")

        if app["stage"] != "tailored":
            print(f"\nstage is '{app['stage']}' — nothing to approve.")
            return
        print("\nType APPROVE to advance to 'approved' (anything else cancels): ", end="")
        sys.stdout.flush()
        if sys.stdin.readline().strip() != "APPROVE":
            print("cancelled.")
            return
        conn.execute(
            "UPDATE applications SET stage = 'approved', updated_at = now() WHERE id = %s",
            (app["id"],))
        log_event(conn, app["id"], "stage_change", {"stage": "approved", "via": "approval_gate"})
        conn.commit()
    print("approved. Next: python -m engine.main submit "
          f"{args.application_id} [--no-dry-run]")


def cmd_submit(args):
    from .submitter import playwright_fill

    with tx() as conn:
        app = conn.execute(
            "SELECT a.*, p.company, p.title, p.url FROM applications a "
            "JOIN postings p ON p.id = a.posting_id WHERE a.id = %s",
            (args.application_id,)).fetchone()
        if not app:
            raise SystemExit(f"application {args.application_id} not found")
        if app["stage"] == "approved":
            pass  # cleared for submission
        elif app["stage"] == "submitted":
            raise SystemExit("already submitted")
        elif args.dry_run:
            print(f"note: stage is '{app['stage']}' — dry-run fill proceeds; "
                  "live submission requires stage 'approved'")
        else:
            raise SystemExit(f"stage is '{app['stage']} — run review {args.application_id} and APPROVE first")

        if not app["resume_path"]:
            raise SystemExit("no tailored resume — run tailor first")
        candidate = profiles()[app["lane"]]["candidate"]
        try:
            status = playwright_fill.fill(dict(app), dict(app), candidate, dry_run=args.dry_run)
        except ImportError:
            raise SystemExit("playwright not installed — pip install playwright && playwright install chromium")
        except Exception as e:
            raise SystemExit(f"submit failed: {e}")

        if status.startswith("manual_required"):
            kind = "linkedin" if status.endswith("linkedin") else "manual_ats"
            log_event(conn, app["id"], "manual_required",
                      {"ats": status.split(":", 1)[1], "url": app["url"]})
            print(f"manual_required ({status.split(':')[1]}): {app['url']}")
            if status.endswith("linkedin"):
                from .submitter.linkedin_assist import open_posting
                open_posting(app["url"])
        elif status.startswith("dry_run_filled"):
            print(status)
            log_event(conn, app["id"], "note", {"dry_run": status})
        else:  # human_submitted
            conn.execute(
                """UPDATE applications SET stage = 'submitted', submitted_at = now(),
                   next_followup = CURRENT_DATE + INTERVAL '7 days', updated_at = now()
                   WHERE id = %s""", (app["id"],))
            log_event(conn, app["id"], "stage_change", {"stage": "submitted", "via": "human"})
            print("stage -> submitted; follow-up scheduled for +7 days")
        conn.commit()


def cmd_followups(_args):
    from .tracker.followups import run
    run()


def cmd_funnel(_args):
    from .tracker.funnel import run
    run()


def cmd_gmail_sync(_args):
    from .tracker.gmail_sync import run
    run()


def cmd_gmail_auth(_args):
    from .tracker.gmail_sync import gmail_auth
    gmail_auth()


def cmd_confirm_stage(args):
    with tx() as conn:
        app = conn.execute("SELECT * FROM applications WHERE id = %s",
                           (args.application_id,)).fetchone()
        if not app:
            raise SystemExit(f"application {args.application_id} not found")
        prop = conn.execute(
            """SELECT * FROM events WHERE application_id = %s AND kind = 'stage_proposal'
               AND payload->>'confirmed' IS NULL ORDER BY created_at DESC LIMIT 1""",
            (args.application_id,)).fetchone()
        if prop and prop["payload"].get("proposed_stage") != args.stage:
            print(f"note: latest proposal was '{prop['payload'].get('proposed_stage')}'")
        conn.execute(
            "UPDATE applications SET stage = %s, updated_at = now() WHERE id = %s",
            (args.stage, args.application_id))
        conn.execute(
            """UPDATE events SET payload = jsonb_set(payload, '{confirmed}', 'true'::jsonb)
               WHERE application_id = %s AND kind = 'stage_proposal' AND payload->>'confirmed' IS NULL""",
            (args.application_id,))
        log_event(conn, args.application_id, "stage_change",
                  {"stage": args.stage, "via": "email_proposal_confirmed"})
        conn.commit()
    print(f"A{args.application_id}: stage -> {args.stage}")


def cmd_ingest_email(args):
    from .tracker.gmail_sync import ingest_email
    payload = json.loads(args.json_payload) if args.json_payload else json.load(sys.stdin)
    print(json.dumps(ingest_email(payload)))


def cmd_status(_args):
    with tx() as conn:
        for r in conn.execute(
            """SELECT a.id, a.stage, a.lane, p.company, p.title, a.next_followup
               FROM applications a JOIN postings p ON p.id = a.posting_id
               ORDER BY a.id"""):
            print(f"A{r['id']:<3} {r['stage']:<10} {r['lane']:<8} "
                  f"{r['company']} — {r['title']}  (followup: {r['next_followup'] or '-'})")


def main() -> None:
    p = argparse.ArgumentParser(prog="engine", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)

    a = sub.add_parser("aggregate")
    a.add_argument("--lane", default="all", choices=["all", "data_ai", "wellness"])
    a.add_argument("--source", default=None, help="comma list: greenhouse,lever,serpapi,boards")
    a.set_defaults(fn=cmd_aggregate)

    s = sub.add_parser("score")
    s.add_argument("--lane", default="all", choices=["all", "data_ai", "wellness"])
    s.set_defaults(fn=cmd_score)

    sub.add_parser("digest").set_defaults(fn=cmd_digest)

    t = sub.add_parser("target")
    t.add_argument("ids", nargs="+", type=int)
    t.set_defaults(fn=cmd_target)

    tl = sub.add_parser("tailor")
    tl.add_argument("application_id", type=int)
    tl.set_defaults(fn=cmd_tailor)

    rv = sub.add_parser("review")
    rv.add_argument("application_id", type=int)
    rv.set_defaults(fn=cmd_review)

    sb = sub.add_parser("submit")
    sb.add_argument("application_id", type=int)
    sb.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="fill the form and screenshot only; never submit (default)")
    sb.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="open a headed browser for human-confirmed submission")
    sb.set_defaults(fn=cmd_submit, dry_run=True)

    sub.add_parser("followups").set_defaults(fn=cmd_followups)
    sub.add_parser("funnel").set_defaults(fn=cmd_funnel)
    sub.add_parser("gmail_sync").set_defaults(fn=cmd_gmail_sync)
    sub.add_parser("gmail-auth").set_defaults(fn=cmd_gmail_auth)

    cs = sub.add_parser("confirm-stage")
    cs.add_argument("application_id", type=int)
    cs.add_argument("stage", choices=["targeted", "tailored", "approved", "submitted",
                                      "screening", "interview", "offer", "rejected", "withdrawn"])
    cs.set_defaults(fn=cmd_confirm_stage)

    ie = sub.add_parser("ingest-email")
    ie.add_argument("json_payload", nargs="?", default=None,
                    help='Resend inbound JSON (or pipe via stdin)')
    ie.set_defaults(fn=cmd_ingest_email)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
