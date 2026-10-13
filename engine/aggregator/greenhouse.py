"""Greenhouse public boards API — free, no auth, full descriptions.

GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
from __future__ import annotations

import httpx

from ..config import scoring
from .normalize import JobPosting, infer_remote, parse_salary, strip_html

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch(watchlist: dict):
    scr = scoring()
    headers = {"User-Agent": scr.get("user_agent", "JobHuntEngine")}
    for slug, company in (watchlist.get("greenhouse_slugs") or {}).items():
        try:
            resp = httpx.get(API.format(slug=slug), headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"greenhouse/{slug}: HTTP {resp.status_code}")
                continue
            yield from _parse(company, resp.json())
        except Exception as e:
            print(f"greenhouse/{slug}: {e}")


def _parse(company: str, data: dict):
    for job in data.get("jobs", []):
        desc = strip_html(job.get("content") or "")
        loc = (job.get("location") or {}).get("name")
        lo, hi = parse_salary(desc)
        yield JobPosting(
            source="greenhouse",
            source_id=str(job.get("id")),
            company=company,
            title=job.get("title") or "Unknown",
            location=loc,
            remote=infer_remote(loc, desc),
            salary_min=lo,
            salary_max=hi,
            url=job.get("absolute_url") or "",
            description=desc,
        )
