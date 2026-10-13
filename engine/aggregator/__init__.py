"""Aggregator: pull postings from all sources, normalize, dedupe, store.

Command: python -m engine.main aggregate [--lane all|data_ai|wellness] [--source ...]
"""
from __future__ import annotations

import datetime
import re

from ..config import scoring, watchlist
from ..db import tx

from .normalize import JobPosting, infer_remote, parse_salary, strip_html  # noqa: F401  (re-exported)
from . import boards, greenhouse, lever, serpapi_jobs

ALL_SOURCES = ("greenhouse", "lever", "serpapi", "boards")


def insert_posting(conn, p: JobPosting) -> tuple[int, bool]:
    """Insert with dedupe (content_hash unique + (company,title) in last 60 days).
    Returns (posting_id, created)."""
    chash = p.content_hash()
    row = conn.execute(
        "SELECT id FROM postings WHERE content_hash = %s", (chash,)
    ).fetchone()
    if row:
        return row["id"], False
    norm_co = re.sub(r"\W+", "", p.company.lower())
    norm_ti = re.sub(r"\W+", "", p.title.lower())
    row = conn.execute(
        """SELECT id FROM postings
           WHERE regexp_replace(lower(company), '\\W', '', 'g') = %s
             AND regexp_replace(lower(title), '\\W', '', 'g') = %s
             AND first_seen >= CURRENT_DATE - INTERVAL '60 days'""",
        (norm_co, norm_ti),
    ).fetchone()
    if row:
        return row["id"], False
    try:
        row = conn.execute(
            """INSERT INTO postings (source, source_id, company, title, location, remote,
                                      salary_min, salary_max, url, description, content_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source, source_id) DO NOTHING
               RETURNING id""",
            (p.source, p.source_id, p.company, p.title, p.location, p.remote,
             p.salary_min, p.salary_max, p.url, p.description, chash),
        ).fetchone()
        conn.commit()
        if row:
            return row["id"], True
        row = conn.execute(
            "SELECT id FROM postings WHERE source = %s AND source_id = %s",
            (p.source, p.source_id),
        ).fetchone()
        return row["id"], False
    except Exception:
        conn.rollback()
        row = conn.execute(
            "SELECT id FROM postings WHERE content_hash = %s", (chash,)
        ).fetchone()
        if row:
            return row["id"], False
        raise


def aggregate(lane: str = "all", sources: tuple[str, ...] = ALL_SOURCES) -> dict:
    wl = watchlist()
    scr = scoring()
    freq_ok = scr.get("serpapi_frequency") != "mon_wed_fri" or datetime.date.today().weekday() in (0, 2, 4)
    stats = {"new": 0, "dupes": 0, "errors": []}

    fetchers = []
    if "greenhouse" in sources:
        fetchers.append(greenhouse.fetch(wl))
    if "lever" in sources:
        fetchers.append(lever.fetch(wl))
    if "serpapi" in sources and freq_ok:
        fetchers.append(serpapi_jobs.fetch(wl, lane))
    elif "serpapi" in sources:
        print("serpapi: skipped (schedule: mon/wed/fri)")
    if "boards" in sources:
        fetchers.append(boards.fetch(wl))

    with tx() as conn:
        for gen in fetchers:
            try:
                for p in gen:
                    try:
                        _, created = insert_posting(conn, p)
                        stats["new" if created else "dupes"] += 1
                    except Exception as e:  # keep aggregating; report at the end
                        conn.rollback()
                        stats["errors"].append(f"{p.source}:{p.source_id} {e}")
            except Exception as e:
                conn.rollback()
                stats["errors"].append(f"source {gen}: {e}")
    print(f"aggregate: new={stats['new']} dupes={stats['dupes']} errors={len(stats['errors'])}")
    for err in stats["errors"][:10]:
        print(f"  ! {err}")
    return stats
