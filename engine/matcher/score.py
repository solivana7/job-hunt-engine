"""Matcher: two-pass scoring (deterministic rules, then LLM) per lane.

Command: python -m engine.main score [--lane all|data_ai|wellness]
"""
from __future__ import annotations

import json
import re

from ..config import profiles, scoring
from ..db import tx
from ..llm import score_fit

_ONSITE_5 = re.compile(r"(5|five)\s+days?\s+(on[- ]?site)|(on[- ]?site)\s+(5|five)\s+days?", re.IGNORECASE)


def _kill_reasons(posting: dict, prof: dict) -> list[str]:
    text = " ".join(
        str(posting.get(k) or "") for k in ("title", "location", "description")
    ).lower()
    kills = []
    for crit in prof["must_have"].get("kill_criteria", []):
        c = crit.lower()
        if c == "onsite 5 days":
            if _ONSITE_5.search(text):
                kills.append(crit)
        elif c in text:
            kills.append(crit)
    if prof.get("exceptions"):
        # numeric kill: generic program-director tier (salary_max < 100000)
        smax = posting.get("salary_max")
        if smax is not None and smax < 100000:
            kills.append("salary_max < 100000")
    return kills


def _exception_flag(posting: dict, prof: dict) -> str | None:
    """Returns the exception reason if a below-floor posting may proceed."""
    exc = prof.get("exceptions") or {}
    company = (posting.get("company") or "").lower()
    text = (posting.get("description") or "").lower()
    for brand in exc.get("flagship_brands", []):
        if brand.lower() in company:
            return f"flagship brand: {brand}"
    for kw in exc.get("equity_keywords", []):
        if kw.lower() in text:
            return f"equity signal: {kw}"
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("&", " and ")).strip()


def _match_title_groups(norm_title: str, groups: list) -> tuple[str | None, str | None]:
    """Returns (group_name, matched_label) for the first passing group.
    A group passes when any topic appears in the title AND (any level appears,
    or the group declares no levels because its topics are inherently senior)."""
    padded = f" {norm_title} "
    for g in groups:
        topic = next((t for t in g["topics"] if _norm(t) in norm_title), None)
        if not topic:
            continue
        if not g.get("levels"):
            return g["name"], topic
        for lvl in g["levels"]:
            nl = _norm(lvl)
            # short tokens ("vp", "bi") must match on word boundaries
            hit = f" {nl} " in padded if len(nl) <= 3 else nl in norm_title
            if hit:
                return g["name"], f"{lvl} {topic}"
        return None, None  # junior/IC role in this field — reject
    return None, None


_EXEC_TOKENS = ("chief", "vice president", "vp", "head of", "managing director",
                "partner", "principal", "president", "cio", "cdo", "cao")


def _level_tier(norm_title: str) -> str:
    padded = f" {norm_title} "
    for t in _EXEC_TOKENS:
        nt = _norm(t)
        hit = f" {nt} " in padded if len(nt) <= 3 else nt in norm_title
        if hit:
            return "exec"
    return "senior"


def score_posting(posting: dict, prof: dict, scr: dict) -> dict:
    """Full two-pass score. Returns {score, hard_pass, rationale}."""
    lane = prof["lane"]
    mh = prof["must_have"]
    reasons: list[str] = []
    caps: list[int] = []

    kills = _kill_reasons(posting, prof)
    if kills:
        return {"score": 0, "hard_pass": False,
                "rationale": {"killed_by": kills, "lane": lane}}

    norm_title = _norm(posting.get("title") or "")
    group_name, matched_title = (None, None)
    if prof.get("title_groups"):
        group_name, matched_title = _match_title_groups(norm_title, prof["title_groups"])
    if not matched_title:
        matched_title = next(
            (t for t in mh.get("titles_include", []) if _norm(t) in norm_title), None
        )
    if not matched_title:
        # Wellness-watchlist companies (e.g. mental-health tech) post domain roles
        # under product/clinical titles. Waive the title gate when the description
        # independently shows strong keyword fit; the waiver is recorded.
        # Opt-in per lane (title_waiver) — the data_ai gate stands on its own.
        wl = [c.lower() for c in prof.get("watchlist_companies", [])]
        company = (posting.get("company") or "").lower()
        desc = (posting.get("description") or "").lower()
        kws = [k for k in prof["fit_signals"]["keywords"] if k.lower() in desc]
        if prof.get("title_waiver") and any(c in company for c in wl) and len(kws) >= 2:
            salesy = re.search(r"\b(sales|account executive|business development|sdr|bdr)\b",
                               norm_title)
            senior = re.search(r"\b(senior|director|vp|vice president|head|principal"
                               r"|chief|president|managing)\b", norm_title)
            if salesy or not senior:
                why = f"sales title: {salesy.group(1)}" if salesy else "below senior level"
                return {"score": 0, "hard_pass": False,
                        "rationale": {"killed_by": [f"watchlist waiver declined: {why}"],
                                      "lane": lane}}
            matched_title = f"(watchlist fit: {', '.join(kws[:3])})"
            reasons.append(f"title gate waived — watchlist company + {len(kws)} keyword hits")
        else:
            return {"score": 0, "hard_pass": False,
                    "rationale": {"killed_by": ["title mismatch"], "lane": lane}}

    # tiered salary floor (opt-in per lane): VP/Chief/partner-track keeps the
    # exec floor; Senior Manager/Director tier uses the broader-search floor.
    if mh.get("tiered_floors"):
        tier = _level_tier(norm_title)
        floor = (scr.get("salary_floor_exec") if tier == "exec"
                 else scr.get("salary_floor_senior"))
    else:
        tier, floor = None, mh.get("salary_floor")
    smax = posting.get("salary_max")
    if smax is not None and floor and smax < floor:
        exc = _exception_flag(posting, prof) if prof.get("exceptions") else None
        if not exc:
            return {"score": 0, "hard_pass": False,
                    "rationale": {"killed_by": [f"salary_max {smax} < floor {floor}"],
                                  "matched_title": matched_title, "lane": lane}}
        caps.append(scr["exception_cap"])
        reasons.append(f"exception ({exc}) — below floor {floor}, confirm with the candidate")

    if smax is None:
        caps.append(scr["salary_null_cap"])
        reasons.append("no salary data — capped")

    base = int(scr.get("rules_base_points", 40))
    fit = score_fit(posting, prof)
    llm_points = sum(fit["points"].values())
    score = base + llm_points
    if caps:
        score = min(score, *caps)

    reasons.append(f"matched title: {matched_title}")
    reasons += fit.get("reasons", [])
    rationale = {"lane": lane, "base": base, "llm": fit["points"],
                 "provider": fit.get("provider"), "reasons": reasons}
    return {"score": min(100, score), "hard_pass": True, "rationale": rationale}


def _score_chunk(rows: list, prof: dict, scr: dict) -> int:
    """Score a chunk with a dedicated connection (psycopg conns are not thread-safe)."""
    from ..db import connect
    n = 0
    conn = connect()
    try:
        for p in rows:
            result = score_posting(p, prof, scr)
            conn.execute(
                """INSERT INTO scores (posting_id, lane, score, hard_pass, rationale)
                   VALUES (%s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (posting_id, lane) DO UPDATE
                     SET score = EXCLUDED.score, hard_pass = EXCLUDED.hard_pass,
                         rationale = EXCLUDED.rationale, scored_at = now()""",
                (p["id"], prof["lane"], result["score"], result["hard_pass"],
                 json.dumps(result["rationale"])),
            )
            if result["hard_pass"]:
                conn.execute(
                    """UPDATE postings SET lane = %s
                       WHERE id = %s AND lane = 'data_ai'""",
                    (prof["lane"], p["id"]),
                )
            n += 1
            conn.commit()  # per-row: LLM passes are slow; keep progress durable
    finally:
        conn.close()
    return n


def run(lane: str = "all", workers: int = 6) -> None:
    scr = scoring()
    profs = profiles()
    lanes = list(profs) if lane == "all" else [lane]
    total = 0
    with tx() as conn:
        pending = {}
        for ln in lanes:
            pending[ln] = conn.execute(
                """SELECT p.* FROM postings p
                   WHERE NOT EXISTS (SELECT 1 FROM scores s
                                     WHERE s.posting_id = p.id AND s.lane = %s)""",
                (ln,),
            ).fetchall()
    from concurrent.futures import ThreadPoolExecutor
    for ln in lanes:
        rows = pending[ln]
        if not rows:
            continue
        # one score_posting per row; LLM calls run in parallel across workers
        chunk = max(1, (len(rows) + workers - 1) // workers)
        chunks = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for n in ex.map(lambda c: _score_chunk(c, profs[ln], scr), chunks):
                total += n
                print(f"  ... lane={ln} cumulative {total}", flush=True)
    print(f"score: {total} postings scored across lanes {lanes}")
