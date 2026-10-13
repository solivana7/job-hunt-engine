"""Wellness HTML boards + generic career-page fallback (weekly, conservative).

Fetches listing pages with a 1 req/sec ceiling and naive robots.txt checks.
Parsing is best-effort: job-board HTML varies, so rows with unparsable company
are skipped rather than stored dirty. Disable a board via watchlist.yaml.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.robotparser

import httpx

from ..config import scoring
from .normalize import JobPosting, infer_remote, parse_salary, strip_html

_HREF_RE = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<text>.*?)</a>', re.IGNORECASE | re.DOTALL
)
_JOBISH = re.compile(r"(job|career|position|opening|posting|apply)", re.IGNORECASE)
_TITLE_SPLIT_RE = re.compile(r"\s+[-|–—@:]\s+")


class PoliteFetcher:
    def __init__(self):
        scr = scoring()
        self.headers = {"User-Agent": scr.get("user_agent", "JobHuntEngine")}
        self.delay = float(scr.get("request_delay_seconds", 1.0))
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_hit: dict[str, float] = {}

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            try:
                rp.read()
            except Exception:
                pass  # if robots is unreachable, proceed cautiously
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.headers["User-Agent"], url)

    def get(self, url: str) -> str | None:
        parts = urllib.parse.urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        wait = self.delay - (time.monotonic() - self._last_hit.get(base, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[base] = time.monotonic()
        if not self.allowed(url):
            print(f"boards: robots.txt disallows {url}")
            return None
        try:
            resp = httpx.get(url, headers=self.headers, timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                print(f"boards: {url} -> HTTP {resp.status_code}")
                return None
            return resp.text
        except Exception as e:
            print(f"boards: {url} -> {e}")
            return None


def _guess_company(text: str, hint: str | None) -> str | None:
    if hint:
        return hint
    m = _TITLE_SPLIT_RE.split(strip_html(text))
    return m[-1].strip() if len(m) > 1 else None


def fetch(watchlist: dict):
    f = PoliteFetcher()
    for key, cfg in (watchlist.get("boards") or {}).items():
        if not cfg.get("enabled", True):
            continue
        html = f.get(cfg["list_url"])
        if not html:
            continue
        count = 0
        for m in _HREF_RE.finditer(html):
            href, text = m.group("href"), strip_html(m.group("text"))
            if not _JOBISH.search(href) and not _JOBISH.search(text):
                continue
            if len(text) < 8 or len(text) > 120:
                continue
            url = urllib.parse.urljoin(cfg["list_url"], href)
            company = _guess_company(text, cfg.get("company_hint"))
            if not company:
                continue
            title = _TITLE_SPLIT_RE.split(text)[0].strip()
            if not title:
                continue
            yield JobPosting(
                source=f"board:{key}",
                source_id=url,
                company=company,
                title=title,
                location=None,
                remote=None,
                salary_min=None,
                salary_max=None,
                url=url,
                description=f"Listing from {key} board. Detail page: {url}",
            )
            count += 1
        print(f"boards/{key}: {count} listings")
