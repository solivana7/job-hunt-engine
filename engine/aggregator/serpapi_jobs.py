"""Aggregated job search via SerpAPI (Google Jobs engine) or Serper.dev (/search).

Both are optional; the module skips cleanly without a key. SerpAPI is the spec'd
provider with a real jobs engine. Serper.dev has no jobs endpoint, so it falls back
to /search and keeps results whose links point at job boards (LinkedIn/Indeed/
Greenhouse/Lever) — lower fidelity, generous free tier (2,500 queries/mo).
Queries rotate daily through the per-lane query lists in watchlist.yaml.
Run frequency is gated in aggregator.aggregate().
"""
from __future__ import annotations

import datetime
import os
import re

import httpx

from ..config import scoring
from .normalize import JobPosting, infer_remote, parse_salary

_JOB_BOARD_RE = re.compile(
    r"(linkedin\.com/jobs/view|indeed\.com/viewjob"
    r"|ziprecruiter\.com/c/|greenhouse\.io/[^/]+/jobs/|lever\.co/[a-z0-9-]+$"
    r"|dice\.com/job-detail|monster\.com/job-openings/)",
    re.IGNORECASE,
)

_COMPANYISH_RE = re.compile(
    r"(Inc|LLC|Corp|Group|Bank|Chase|Capital|Partners|Technologies|Solutions|Services"
    r"|Health|Hotels|Consultants|Advisors|Mellon|Barclays|JPMorgan|Enterprises|Holdings|Worldwide)",
    re.IGNORECASE,
)


def _key() -> tuple[str, str] | None:
    """Returns (provider, key) for the first configured service."""
    if os.environ.get("SERPAPI_KEY"):
        return ("serpapi", os.environ["SERPAPI_KEY"])
    if os.environ.get("SERPER_API_KEY"):
        return ("serper", os.environ["SERPER_API_KEY"])
    return None


def fetch(watchlist: dict, lane: str = "all"):
    cfg = _key()
    if not cfg:
        print("serpapi/serper: no SERPAPI_KEY or SERPER_API_KEY, skipping")
        return
    provider, key = cfg
    scr = scoring()
    headers = {"User-Agent": scr.get("user_agent", "JobHuntEngine")}
    if provider == "serper":
        headers["X-API-KEY"] = key
        headers["Content-Type"] = "application/json"

    day_index = datetime.date.today().toordinal()
    lanes = ("data_ai", "wellness") if lane == "all" else (lane,)
    for ln in lanes:
        general = watchlist.get(f"serpapi_queries_{ln}") or []
        # one rotating general query + the site-targeted queries, every fetch
        queries = ([general[day_index % len(general)]] if general else []) + \
                  list(watchlist.get(f"serper_site_queries_{ln}") or [])
        for q in queries:
            try:
                if provider == "serpapi":
                    rows = [_serpapi_to_posting(ln, j) for j in _serpapi_search(key, headers, q)]
                else:
                    rows = [_serper_to_posting(ln, r) for r in _serper_search(key, headers, q)]
                for p in rows:
                    yield p
                print(f"{provider} ({ln}): {len(rows)} results for '{q}'", flush=True)
            except Exception as e:
                print(f"{provider} ({ln}): {e}")


def _serpapi_search(key: str, headers: dict, q: str) -> list[dict]:
    resp = httpx.get(
        "https://serpapi.com/search.json",
        params={"engine": "google_jobs", "q": q, "location": "United States",
                "num": 20, "api_key": key},
        headers=headers, timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("jobs_results", [])


def _serpapi_to_posting(lane: str, job: dict) -> JobPosting:
    ext = job.get("detected_extensions") or {}
    loc = job.get("location")
    desc = " ".join(x for x in [job.get("description"), ext.get("salary")] if x)
    lo, hi = parse_salary(desc)
    return JobPosting(
        source="serpapi",
        source_id=job.get("job_id") or job.get("share_link") or "",
        company=job.get("company_name") or "Unknown",
        title=job.get("title") or "Unknown",
        location=loc,
        remote=infer_remote(loc, desc),
        salary_min=lo, salary_max=hi,
        url=job.get("share_link") or job.get("apply_link") or "",
        description=desc,
    )


def _serper_search(key: str, headers: dict, q: str) -> list[dict]:
    # Serper free accounts reject double-quoted query patterns; Google treats
    # the quotes softly anyway, so send the bare phrase.
    q = q.replace('"', "")
    resp = httpx.post(
        "https://google.serper.dev/search",
        headers=headers,
        # no num: free tier rejects num+site: combos; default page size is fine
        json={"q": q, "gl": "us", "hl": "en"},
        timeout=30,
    )
    resp.raise_for_status()
    organic = resp.json().get("organic", [])
    return [r for r in organic if _JOB_BOARD_RE.search(r.get("link", ""))]


def _serper_to_posting(lane: str, r: dict) -> JobPosting:
    url = r.get("link", "")
    title = r.get("title", "") or "Unknown"
    title = re.sub(r"\s+[-|–—]\s+(LinkedIn|Indeed|Glassdoor|ZipRecruiter|Dice|Monster).*$", "",
                   title, flags=re.IGNORECASE).strip() or "Unknown"
    snippet = r.get("snippet") or ""

    company = "Unknown"
    # LinkedIn slugs carry the employer: /jobs/view/<role>-at-<company>-<id>
    m = re.search(r"at-([a-z0-9]+(?:-[a-z0-9]+)+)-\d{6,}$", url) if "linkedin" in url else None
    if m:
        company = m.group(1).replace("-", " ").title()
    # Dice/Monster titles embed the employer after " - ": "<role> - <company>"
    if company == "Unknown":
        parts = re.split(r"\s+-\s+", title)
        for i in range(1, len(parts)):
            if _COMPANYISH_RE.search(parts[i]):
                company = parts[i].strip()
                title = " - ".join(parts[:i])
                break
    title = re.sub(r"\s+Job\s*$", "", title, flags=re.IGNORECASE).strip(" -") or "Unknown"
    lo, hi = parse_salary(snippet)
    return JobPosting(
        source="serper",
        source_id=url,
        company=company,
        title=title,
        location=None,
        remote=infer_remote(None, f"{title} {snippet}"),
        salary_min=lo, salary_max=hi,
        url=url,
        description=f"[serper: {lane}] {snippet} ({url})",
    )
