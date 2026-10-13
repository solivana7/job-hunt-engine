"""LLM access (Kimi/Moonshot OpenAI-compatible API) with a deterministic fallback.

The engine is fully functional without an API key: `score_fit` and `write_cover_note`
fall back to heuristic scoring / template notes so every module can be exercised
end to end. Set KIMI_API_KEY (+ optional KIMI_MODEL) in .env to enable the LLM pass.
"""
from __future__ import annotations

import json
import os
import re

import httpx

API_BASE = "https://api.moonshot.ai/v1"


def _keys() -> list[str]:
    return [k for k in (os.environ.get("KIMI_API_KEY"), os.environ.get("MOONSHOT_API_KEY")) if k]


def llm_available() -> bool:
    return bool(_keys())


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _json_block(raw: str) -> str:
    """Extract the first {...} block, tolerating markdown fences and prose."""
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = _JSON_OBJ_RE.search(cleaned)
    return m.group(0) if m else cleaned


def _chat(prompt: str, system: str, max_tokens: int = 1200) -> str | None:
    keys = _keys()
    if not keys:
        return None
    model = os.environ.get("KIMI_MODEL", "kimi-k2.6")
    for key in keys:
        try:
            resp = httpx.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    # thinking off: 30x faster, no max_tokens blowups on k2.6
                    "chat_template_kwargs": {"thinking": False},
                    "max_tokens": max_tokens,
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            continue  # failover to the next configured key
    return None


# ---------------------------------------------------------------- scoring ----

_INDUSTRY_TERMS = {
    "data_ai": ["health", "fintech", "financial", "retail", "saas", "insurance",
                "enterprise", "tech", "software", "banking"],
    "wellness": ["wellness", "wellbeing", "spa", "health", "mental", "hospitality",
                 "hotel", "resort", "fitness", "benefits", "clinical"],
}

_SCOPE_RE = re.compile(
    r"(\d{2,3}[,.]?\d{3}\+?\s*(employees|people|clients|members|lives))"
    r"|(enterprise|fortune\s?\d+|global|organization[- ]wide)",
    re.IGNORECASE,
)


def _heuristic_fit(posting: dict, prof: dict) -> dict:
    """Deterministic stand-in for the LLM pass. Same output shape, same scale."""
    text = f"{posting.get('title', '')}\n{posting.get('description') or ''}".lower()
    lane = prof.get("lane", "data_ai")

    industry_hits = [t for t in _INDUSTRY_TERMS.get(lane, []) if t in text]
    industry = min(15, 5 * len(industry_hits))

    title = posting.get("title", "").lower()
    seniority = 0
    if re.search(r"\b(chief|vp|vice president|head of)\b", title):
        seniority += 10
    elif re.search(r"\b(director|senior director)\b", title):
        seniority += 7
    if re.search(r"\b(global|enterprise|executive)\b", text):
        seniority += 5
    seniority = min(15, seniority)

    kw_hits = [k for k in prof["fit_signals"]["keywords"] if k.lower() in text]
    keyword_density = min(20, 5 * len(kw_hits))

    scale_match = 10 if _SCOPE_RE.search(text) else 4

    return {
        "points": {
            "industry": industry,
            "seniority": seniority,
            "keyword_density": keyword_density,
            "scale_match": scale_match,
        },
        "reasons": [
            f"industry terms: {', '.join(industry_hits) or 'none'}",
            f"title seniority: {'executive' if seniority >= 10 else 'director' if seniority >= 7 else 'other'}",
            f"profile keywords matched: {', '.join(kw_hits) or 'none'}",
            "enterprise scale signals present" if scale_match == 10 else "limited scale signals",
        ],
        "provider": "heuristic",
    }


def score_fit(posting: dict, prof: dict) -> dict:
    """LLM pass. Returns {points:{industry,seniority,keyword_density,scale_match}, reasons}.

    Falls back to heuristic scoring when no API key or on API failure.
    """
    if not llm_available():
        return _heuristic_fit(posting, prof)

    desc = (posting.get("description") or "")[:6000]
    prompt = f"""Score this job posting's fit for the candidate profile below.
Reply with ONLY JSON: {{"points": {{"industry": 0-15, "seniority": 0-15, "keyword_density": 0-20, "scale_match": 0-10}}, "reasons": [short strings]}}

JOB TITLE: {posting.get('title')}
COMPANY: {posting.get('company')}
DESCRIPTION:
{desc}

PROFILE:
Target titles: {prof['must_have'].get('titles_include') or [g['topics'] for g in prof.get('title_groups', [])]}
Industries: {prof['fit_signals']['industries']}
Keywords: {prof['fit_signals']['keywords']}

Scoring guidance:
- industry: how well the company's sector matches the profile industries
- seniority: VP/Chief/Head level = high; Director = medium; IC = low
- keyword_density: occurrences of the profile keywords in the description
- scale_match: evidence the role operates at enterprise scale (employees, Fortune 500, global)
For a wellness-lane role, reward practitioner credibility (program ownership, population served)
and penalize pure-admin spa manager roles below director level.
Reply with ONLY the JSON object, no markdown fences, no commentary."""
    raw = _chat(prompt, "You are a precise recruiting analyst. Output JSON only.", max_tokens=4096)
    if raw:
        try:
            m = _json_block(raw)
            parsed = json.loads(m)
            pts = parsed["points"]
            parsed["points"] = {
                "industry": max(0, min(15, int(pts.get("industry", 0)))),
                "seniority": max(0, min(15, int(pts.get("seniority", 0)))),
                "keyword_density": max(0, min(20, int(pts.get("keyword_density", 0)))),
                "scale_match": max(0, min(10, int(pts.get("scale_match", 0)))),
            }
            parsed["provider"] = "kimi"
            return parsed
        except Exception:
            pass
    return _heuristic_fit(posting, prof)


# ------------------------------------------------------------- cover note ----

def write_cover_note(posting: dict, prof: dict, highlights: list[str]) -> str:
    """~150-word cover note in the candidate's voice: direct, numbers-led, no hype.
    References one specific company initiative from the posting when visible."""
    if llm_available():
        desc = (posting.get("description") or "")[:4000]
        prompt = f"""Write a ~150 word cover note for this application.

Rules: direct, numbers-led, no hype, no exclamation marks. Reference one specific
initiative or detail from the job description. End with a plain call to action.
Voice: senior executive, first person.

CANDIDATE HIGHLIGHTS (use only these, do not invent):
{chr(10).join('- ' + h for h in highlights)}

JOB: {posting.get('title')} at {posting.get('company')}
DESCRIPTION:
{desc}"""
        note = _chat(prompt, "You write terse, credible executive cover notes.", max_tokens=4096)
        if note:
            return note.strip()

    title = posting.get("title", "the role")
    company = posting.get("company", "your company")
    top = highlights[:3]
    lines = [
        f"Hello,",
        "",
        f"I'm applying for {title} at {company}. " + (top[0] if top else ""),
        "",
        (top[1] if len(top) > 1 else ""),
        (top[2] if len(top) > 2 else ""),
        "",
        "I'd welcome a conversation about what this role needs in its first 90 days.",
        "",
        "Candidate Name",
        "owner@example.com | (555) 010-0000 | linkedin.com/in/example",
    ]
    return "\n".join(l for l in lines if l != "" or True).strip() + "\n"
