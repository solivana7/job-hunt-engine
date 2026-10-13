"""Lever public postings API — free, no auth.

GET https://api.lever.co/v0/postings/{slug}?mode=json
"""
from __future__ import annotations

import httpx

from ..config import scoring
from .normalize import JobPosting, infer_remote, parse_salary, strip_html

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


def fetch(watchlist: dict):
    scr = scoring()
    headers = {"User-Agent": scr.get("user_agent", "JobHuntEngine")}
    for slug, company in (watchlist.get("lever_slugs") or {}).items():
        try:
            resp = httpx.get(API.format(slug=slug), headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"lever/{slug}: HTTP {resp.status_code}")
                continue
            yield from _parse(company, resp.json())
        except Exception as e:
            print(f"lever/{slug}: {e}")


def _parse(company: str, data: list):
    for job in data:
        desc = strip_html(job.get("descriptionPlain") or job.get("description") or "")
        cats = job.get("categories") or {}
        loc = cats.get("location")
        lo, hi = parse_salary(desc)
        yield JobPosting(
            source="lever",
            source_id=job.get("id") or "",
            company=company,
            title=job.get("text") or "Unknown",
            location=loc,
            remote=infer_remote(loc, desc),
            salary_min=lo,
            salary_max=hi,
            url=job.get("hostedUrl") or job.get("applyUrl") or "",
            description=desc,
        )
