# Job Hunt Engine - Buildable Spec for Kimi CLI

**Owner:** Candidate Name
**Goal:** A semi-automated executive job search system. It finds matching openings every day, scores them against a fit profile, generates tailored ATS-safe application materials, pre-fills application forms, and tracks the full funnel. Nothing submits without explicit human approval.
**Stack:** Kimi CLI (agent runtime + scheduler), Railway Postgres (state), Python 3.12 + Playwright (browser automation), SerpAPI (aggregated job search), Greenhouse/Lever public board APIs (structured sources), SMTP or Resend (notifications).

---

## 1. Architecture Overview

```
[cron: 06:00 PT daily]
    |
    v
+-------------+     +-----------+     +------------------+
| AGGREGATOR  | --> |  MATCHER  | --> |  DIGEST (email)  |
| (sources)   |     | (scoring) |     |  score>=80 list  |
+-------------+     +-----------+     +------------------+
                                           | human picks targets
                                           v
                                    +-------------+     +-----------------+
                                    |   TAILOR    | --> | APPROVAL GATE   |
                                    | resume+note |     | (CLI confirm)   |
                                    +-------------+     +-----------------+
                                                               | approved
                                                               v
                                    +-------------+     +-----------------+
                                    |  SUBMITTER  | --> |    TRACKER      |
                                    | (Playwright)|     | (funnel, follow |
                                    +-------------+     |  -up cadence)   |
                                                        +-----------------+
```

Principles:
1. **Human-in-the-loop by design.** The engine prepares; the candidate approves and presses submit. This protects her LinkedIn account and her executive reputation.
2. **Everything in Postgres.** No state in files except generated resume artifacts.
3. **Idempotent runs.** Re-running any module on the same day produces no duplicates.

---

## 2. Repository Layout

```
job-hunt-engine/
  README.md
  pyproject.toml            # deps: playwright, psycopg[binary], pyyaml, httpx, jinja2, python-dotenv, resend
  .env.example
  config/
    profile.yaml            # fit profile (Section 4)
    watchlist.yaml          # target companies + board slugs
    scoring.yaml            # weights and thresholds
  db/
    schema.sql              # DDL (Section 3)
  engine/
    main.py                 # CLI entry: `python -m engine.main <command>`
    aggregator/
      greenhouse.py         # public boards API
      lever.py              # public postings API
      serpapi_jobs.py       # Google Jobs via SerpAPI
      career_pages.py       # watchlist HTML fallback
      normalize.py          # unified JobPosting schema + dedupe
    matcher/
      score.py              # rubric scoring + LLM rationale
    tailor/
      resume_template.docx  # the ATS-safe master resume (already built)
      tailor.py             # keyword reweight + variant generation
      cover_note.py         # 150-word note generator
    submitter/
      playwright_fill.py    # Greenhouse/Lever form filler (dry-run default)
      linkedin_assist.py    # Easy Apply prefill, human clicks submit
    tracker/
      funnel.py             # weekly funnel report
      followups.py          # day-7 / day-14 follow-up drafts
    notify/
      digest.py             # email digest via Resend
  artifacts/                # generated resumes per application
  tests/
```

---

## 3. Database Schema (Railway Postgres)

`db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS postings (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL,              -- greenhouse | lever | serpapi | career_page
  source_id     TEXT NOT NULL,              -- external id or URL
  company       TEXT NOT NULL,
  title         TEXT NOT NULL,
  location      TEXT,
  remote        BOOLEAN,
  salary_min    INT,
  salary_max    INT,
  url           TEXT NOT NULL,
  description   TEXT,
  first_seen    DATE NOT NULL DEFAULT CURRENT_DATE,
  content_hash  TEXT NOT NULL,              -- sha1(lower(title)||lower(company)||md5(description))
  UNIQUE (source, source_id),
  UNIQUE (content_hash)
);

CREATE TABLE IF NOT EXISTS scores (
  posting_id    BIGINT REFERENCES postings(id) ON DELETE CASCADE,
  score         INT NOT NULL,               -- 0-100
  hard_pass     BOOLEAN NOT NULL,           -- passed kill criteria
  rationale     JSONB NOT NULL,             -- per-criterion breakdown
  scored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posting_id)
);

CREATE TYPE app_stage AS ENUM
  ('targeted','tailored','approved','submitted','screening','interview','offer','rejected','withdrawn');

CREATE TABLE IF NOT EXISTS applications (
  id            BIGSERIAL PRIMARY KEY,
  posting_id    BIGINT REFERENCES postings(id),
  stage         app_stage NOT NULL DEFAULT 'targeted',
  resume_path   TEXT,                       -- artifacts/<company>_<date>.docx
  note_path     TEXT,
  contact_name  TEXT,
  contact_email TEXT,
  submitted_at  TIMESTAMPTZ,
  next_followup DATE,
  notes         TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  id            BIGSERIAL PRIMARY KEY,
  application_id BIGINT REFERENCES applications(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,              -- stage_change | email_sent | note
  payload       JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. Fit Profile (`config/profile.yaml`)

```yaml
candidate:
  name: Candidate Name
  email: owner@example.com
  phone: "(555) 010-0000"
  linkedin: "https://linkedin.com/in/example"
  location: "City, State"
  work_auth: "US Citizen"
  clearance_eligible: true

must_have:
  remote_ok: true               # remote OR hybrid <= 2 days in Bay Area
  salary_floor: 240000          # base or midpoint; null salary -> score cap 79
  titles_include:
    - "VP Data"
    - "Vice President Data"
    - "Chief Data Officer"
    - "Head of Data"
    - "Head of AI"
    - "AI Transformation"
    - "Data & Analytics"
    - "Chief Analytics Officer"
    - "VP Wellbeing"            # wellness-bridge lane
    - "Head of Workforce Health"

fit_signals:                    # +points each, see scoring.yaml
  industries: [healthtech, healthcare, fintech, retail, enterprise_saas, insurance, wellness]
  keywords: ["AI Center of Excellence", "data governance", "AI-first operating model",
             "board", "data monetization", "decision intelligence", "GenAI", "LLM",
             "Snowflake", "Databricks", "transformation", "Fortune 500"]

kill_criteria:                  # hard_pass=false, never digested
  - "requires relocation"
  - "security clearance required"   # eligible != holding active clearance
  - "individual contributor only"
  - "onsite 5 days"

watchlist_companies: [Lyra Health, Optum, UnitedHealth Group, STERIS, Omada Health,
  Included Health, Teladoc, Headspace, Modern Health, Spring Health, Zenoti,
  Mindbody, Boulevard, S&P Global, Charles Schwab, Levi Strauss]
```

---

## 5. Module Specs

### 5.1 Aggregator

Command: `python -m engine.main aggregate`

Sources, in priority order:

1. **Greenhouse public API** - `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` for each watchlist slug. Free, no auth, full descriptions. Keep slugs in `watchlist.yaml`.
2. **Lever public API** - `GET https://api.lever.co/v0/postings/{slug}?mode=json`.
3. **SerpAPI Google Jobs** - queries per title group, e.g. `q="VP Data Analytics" remote`, `location=United States`, `num=20`. Env var `SERPAPI_KEY`. Rotate 6 query variants daily to stay inside quota.
4. **Career page fallback** - plain HTML fetch + extraction for watchlist companies not on Greenhouse/Lever. Lowest priority, runs weekly.

Normalization (`normalize.py`):
- Unify into JobPosting: company, title, location, remote (bool inference from location text + description), salary (regex `$###,###` ranges, both USD formats), url, description.
- Dedupe: `content_hash` unique constraint; also treat (normalized company, normalized title) seen in last 60 days as duplicate.
- Parse salary to ints; store null when absent.

Output: new rows in `postings`. Summary printed: `new=N dupes=M`.

### 5.2 Matcher

Command: `python -m engine.main score`

Two passes:
1. **Rules pass (deterministic).** Apply `kill_criteria` first (hard fail). Then title match (must match at least one `titles_include` substring, case-insensitive). Then salary floor: if `salary_max < floor` -> hard fail; if salary null -> allowed but capped at 79.
2. **LLM pass (Kimi).** For rules survivors, prompt Kimi with the posting description + `profile.yaml` and ask for JSON: `{points: {industry: 0-15, seniority: 0-15, keyword_density: 0-20, scale_match: 0-10}, reasons: [...]}`. Rules contribute 40 base points; LLM adds up to 60.

Thresholds (`scoring.yaml`): `digest_min: 80`, `instant_alert: 90`.

### 5.3 Digest

Command: `python -m engine.main digest`

Sends one email at 07:00 PT listing new postings scoring >= 80, sorted desc, each with: title, company, comp range, remote status, one-line rationale, URL, and a reply code (`A12` = approve tailoring for application id 12). the candidate replies with codes, or runs `python -m engine.main target <ids>`.

### 5.4 Tailor

Command: `python -m engine.main tailor <application_id>`

1. Extract keyword phrases from the posting (exact strings the ATS will match: skills, tools, title variants).
2. Load `resume_template.docx` (the ATS-safe master already built). Rules:
   - Never invent experience. Only reweight: reorder skills lines so posting keywords appear first; adjust the Professional Summary's first sentence and Target roles line to mirror the posting title vocabulary.
   - Keep formatting identical (same styles, same bullet scheme) so every variant stays ATS-safe.
3. Save `artifacts/{company}_{role}_{date}.docx` + PDF export.
4. Generate 150-word cover note in the candidate's voice: direct, numbers-led, no hype, references one specific company initiative from the posting.

Output for review: paths + diff summary of what changed vs. master.

### 5.5 Approval Gate

Command: `python -m engine.main review <application_id>`

Prints the posting URL, the tailored diff, and the cover note. Requires typed `APPROVE` to move stage to `approved`. No other path can advance an application past this stage.

### 5.6 Submitter

Command: `python -m engine.main submit <application_id> [--dry-run]`

- Detect ATS from URL. **Greenhouse/Lever:** Playwright fills the standard form fields (name, email, phone, LinkedIn, resume upload, work auth, clearance) from `profile.yaml`, then PAUSES headed (visible browser) for the candidate to review and press submit themselves. Default `--dry-run` fills but never submits.
- **LinkedIn Easy Apply:** `linkedin_assist.py` opens the posting in her logged-in browser profile, pre-fills where possible, and stops. Automated clicking through Easy Apply violates LinkedIn ToS and risks account restriction; the assist mode is the ceiling here.
- **Workday/Taleo/custom:** do not automate. Log `manual_required` with the URL; these forms have fragile multi-page flows and screening questions that deserve human answers.

On confirmed submission: stage -> `submitted`, `submitted_at` set, `next_followup` = +7 days.

### 5.7 Tracker

- `python -m engine.main followups` - lists applications where `next_followup <= today`, drafts a short follow-up email per application (references role + submission date), logs as events.
- `python -m engine.main funnel` - weekly report: counts by stage, conversion percentages, average days per stage, and the week's new/matched/applied numbers. Sent every Monday 08:00 PT.

---

## 6. Scheduling

Kimi CLI cron entries (Pacific Time):

| Time | Command |
|---|---|
| 06:00 daily | `aggregate && score && digest` |
| 08:00 Monday | `funnel` |
| 09:00 daily | `followups` |

---

## 7. Environment Variables (`.env.example`)

```
DATABASE_URL=postgresql://...railway...
SERPAPI_KEY=
RESEND_API_KEY=
NOTIFY_EMAIL=owner@example.com
ENGINE_TZ=America/Los_Angeles
PLAYWRIGHT_HEADLESS=false
```

---

## 8. Guardrails (non-negotiable)

1. No application submits without the Approval Gate. Ever.
2. No automated actions inside linkedin.com beyond opening pages in her own session.
3. Respect `robots.txt` and a 1 req/sec ceiling on career-page fetching.
4. Tailoring never fabricates experience, titles, or dates. Reweight and reorder only.
5. Every outreach email is a draft until the candidate sends it.

---

## 9. Build Order (milestones)

1. **M1 (day 1):** schema.sql on Railway Postgres; Greenhouse + Lever aggregators; normalize + dedupe; manual `aggregate` run proves >= 50 postings stored.
2. **M2 (day 1-2):** rules pass of matcher; digest email working end to end.
3. **M3 (day 2):** LLM scoring pass; thresholds tuned so a day's digest is 5-15 items, not 50.
4. **M4 (day 3):** tailor module against the master ATS template; diff review loop.
5. **M5 (day 4):** Playwright Greenhouse/Lever filler in dry-run; approval gate wiring.
6. **M6 (day 5):** tracker, follow-ups, funnel report; cron live.

Acceptance test for the whole system: run one full cycle on the STERIS Chief Data Officer and Optum Chief Data Officer postings. Both must be discovered (or manually seeded), scored >= 85, tailored, gated, and dry-run filled without errors.

---

## 10. Seeded Targets (as of 2026-10-13)

Seed these directly into `postings` so M1 has known-good rows:

| Company | Role | Comp | Notes |
|---|---|---|---|
| STERIS | Chief Data Officer | $225-300k + LTI | Remote; AI CoE + governance mandate |
| Optum / UnitedHealth Group | Chief Data Officer | $225-375k | Remote; enterprise data strategy |
| S&P Global | Head of AI Product Transformation | $176-336k | NYC hybrid 2 days |
| Net at Work | Director, AI Transformation | $175-200k | Remote; below floor, benchmark only |
