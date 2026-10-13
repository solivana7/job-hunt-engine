"""Unified JobPosting schema + normalization helpers (spec section 5.1)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass
class JobPosting:
    source: str
    source_id: str
    company: str
    title: str
    location: str | None
    remote: bool | None
    salary_min: int | None
    salary_max: int | None
    url: str
    description: str

    def content_hash(self) -> str:
        desc_md5 = hashlib.md5((self.description or "").encode()).hexdigest()
        base = f"{self.title.lower()}|{self.company.lower()}|{desc_md5}"
        return hashlib.sha1(base.encode()).hexdigest()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(html: str) -> str:
    text = html and __import__("html").unescape(html) or ""
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


_SALARY_RE = re.compile(
    r"\$\s*(\d{2,3})(?:([,.\s]?)(\d{3}))?\s*([kK])?"
    r"\s*(?:-|–|—|to)\s*"
    r"\$?\s*(\d{2,3})(?:([,.\s]?)(\d{3}))?\s*([kK])?"
)

# values like "225-300k" where the leading $ anchors the range
_SALARY_K_RE = re.compile(r"\$\s*(\d{2,3})\s*[-–—]\s*(\d{2,3})\s*[kK]\b")


def _to_int(left_hi: str | None, left_lo: str | None, k_flag: str | None) -> int:
    """$225,000 -> 225000 ; $225k -> 225000 ; 225k -> 225000"""
    if left_lo:
        return int(left_hi + left_lo)
    val = int(left_hi)
    if k_flag or val < 1000:
        val *= 1000
    return val


def parse_salary(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    m = _SALARY_RE.search(text)
    if m:
        lo = _to_int(m.group(1), m.group(3), m.group(4))
        hi = _to_int(m.group(5), m.group(7), m.group(8))
        return (min(lo, hi), max(lo, hi))
    m = _SALARY_K_RE.search(text)
    if m:
        lo, hi = int(m.group(1)) * 1000, int(m.group(2)) * 1000
        return (min(lo, hi), max(lo, hi))
    return None, None


_REMOTE_TRUE = re.compile(r"\bremote\b", re.IGNORECASE)
_REMOTE_FALSE = re.compile(r"\b(hybrid|on[- ]?site)\b", re.IGNORECASE)


def infer_remote(location: str | None, description: str | None) -> bool | None:
    text = f"{location or ''} {description or ''}"
    if location and _REMOTE_TRUE.search(location):
        return True
    if location and _REMOTE_FALSE.search(location):
        return False
    if _REMOTE_TRUE.search(text):
        return True
    if _REMOTE_FALSE.search(text):
        return False
    return None
