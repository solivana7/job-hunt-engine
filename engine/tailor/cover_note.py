"""150-word cover note generation (LLM when configured, template otherwise)."""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from ..config import artifacts_dir
from ..llm import write_cover_note as _llm_note
from .tailor import HIGHLIGHTS


def write_note(app: dict, prof: dict, application_id: int) -> Path:
    lane = prof["lane"]
    posting = {"title": app["title"], "company": app["company"], "description": app.get("description")}
    body = _llm_note(posting, prof, HIGHLIGHTS[lane])
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{app['company']}_{app['title']}")[:60]
    path = artifacts_dir() / f"{slug}_cover_{datetime.date.today():%Y-%m-%d}.txt"
    path.write_text(body, encoding="utf-8")
    return path
