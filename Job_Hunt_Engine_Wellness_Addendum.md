# Job Hunt Engine - Wellness Lane Addendum (v2 Spec for Kimi CLI)

**Owner:** Candidate Name
**Depends on:** `Job_Hunt_Engine_Spec.md` (v1). This document extends the existing engine; it does not replace it. Build v1 first or build both together.
**What this adds:** (1) a second job lane for wellness/wellbeing roles with its own fit profile, sources, and resume template; (2) email thread tracking so recruiter replies and outreach CCs are logged automatically against applications.

---

## 1. Multi-Lane Architecture

The engine now supports N lanes. A lane = one fit profile + one resume template + one scoring config. v2 ships two lanes:

| Lane key | Name | Resume template |
|---|---|---|
| `data_ai` | Data & AI Executive (v1) | `resume_template_data_ai.docx` |
| `wellness` | Wellness & Wellbeing Executive | `resume_template_wellness.docx` |

### Schema migration (`db/migration_v2.sql`)

```sql
ALTER TABLE postings     ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
ALTER TABLE scores       ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
CREATE INDEX IF NOT EXISTS idx_postings_lane ON postings(lane, first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_apps_lane     ON applications(lane, stage);

CREATE TABLE IF NOT EXISTS email_threads (
  id             BIGSERIAL PRIMARY KEY,
  application_id BIGINT REFERENCES applications(id) ON DELETE SET NULL,
  gmail_thread_id TEXT UNIQUE,
  subject        TEXT,
  participants   TEXT[],
  last_message_at TIMESTAMPTZ,
  snippet        TEXT,
  needs_reply    BOOLEAN DEFAULT false
);
```

A posting can match both lanes (e.g. "Head of Wellbeing Data Strategy" at a healthtech company). Store one row per (posting, lane) pair in `scores`; keep `postings` itself lane-agnostic by setting `lane` to the first lane that matched.

---

## 2. Wellness Fit Profile (`config/profile_wellness.yaml`)

```yaml
lane: wellness
resume_template: artifacts/../tailor/resume_template_wellness.docx
candidate: *same identity block as profile.yaml*

must_have:
  remote_ok: true                 # remote, hybrid <=2 days Bay Area, or flagship exception
  salary_floor: 150000            # lane floor; corp wellbeing roles regularly clear $236k
  titles_include:
    - "VP of Wellbeing"
    - "Vice President Wellbeing"
    - "Director of Wellbeing"
    - "Director of Wellness"
    - "Head of Wellness"
    - "Head of Wellbeing"
    - "Corporate Wellness Director"
    - "Employee Wellness"
    - "Workforce Health"
    - "Wellness Strategy"
    - "Wellness Practice Lead"
    - "Director of Spa"           # flagship/equity only, see exceptions
    - "Clinical Performance"
    - "Clinical Strategy"

fit_signals:
  industries: [wellness_tech, healthtech, hospitality_luxury, benefits_consulting,
               corporate_wellness, spa, mental_health_tech]
  keywords: ["nervous system", "breathwork", "somatic", "employee wellbeing",
             "behavior change", "burnout", "stress reduction", "resilience",
             "program design", "workshop", "facilitation", "population health",
             "biomarker", "outcomes measurement", "engagement"]

kill_criteria:
  - "requires relocation"
  - "commission only"
  - salary_max < 100000           # generic program-director tier, undervalues candidate

exceptions:                       # override salary floor when flagged
  flagship_brand: true            # e.g. Four Seasons, Canyon Ranch, Miraval, Setai
  equity_component: true

watchlist_companies: [Lyra Health, Spring Health, Modern Health, Headspace, Calm,
  Two Chairs, Midi Health, ŌURA, BetterHelp, Teladoc, Omada Health, Included Health,
  Zenoti, Mindbody, Boulevard, Mercer, WTW, Aon, Canyon Ranch, Miraval, Four Seasons]
```

---

## 3. Lane-Specific Sources

Add to the aggregator; all run under the same dedupe and normalization rules as v1.

| Source | Type | Notes |
|---|---|---|
| ISPA Careers (ispastaff.com) | HTML board | spa/wellness executive listings, weekly fetch |
| Spa & Wellness Careers (spaandwellnesscareers.com) | HTML board | executive spa director roles |
| Hutchinson Consulting | HTML board | hospitality wellness recruiters |
| Fitt Jobs (fitt.co/jobs) | HTML board | wellness tech: ŌURA, Two Chairs, BetterHelp, Mindbody |
| SerpAPI queries | Google Jobs | add queries: `"VP wellbeing" remote`, `"director corporate wellness" remote`, `"head of wellness strategy"`, `"director of spa" luxury`, `"employee wellness director" remote` |
| Greenhouse/Lever slugs | API | add wellness watchlist slugs (lyrahealth, springhealth, modernhealth, headspace, calm, twochairs, omada, includedhealth, teladoc, mindbody, boulevard) |

SerpAPI budget: the wellness lane adds 5 queries per day. Combined with v1's 6, still inside the 100/month free tier if run 3x/week instead of daily; set `serpapi_frequency: mon_wed_fri` in `scoring.yaml`.

---

## 4. Matcher Changes

- `score.py` loads BOTH profiles and scores every new posting against each lane independently. A posting may enter both digests with different scores; that is correct behavior.
- Lane-specific scoring notes for the LLM pass: for `wellness`, reward practitioner credibility signals (clinical language, program ownership, population served) and penalize pure-admin spa manager roles below director level.
- `digest.py` renders two sections: **Data & AI** and **Wellness**, each sorted by score. Instant alerts (>= 90) still apply per lane.

## 5. Tailor Changes

- `tailor.py` gains a template registry: `lane -> resume_template.docx`.
- Wellness lane reweighting rules: pull posting keywords into the Core Competencies order and the summary's first sentence; mirror the posting's title vocabulary in the Target roles line. Never fabricate credentials - the Oxygen Advantage line stays "(certification in progress)" until the candidate confirms completion.
- Hybrid postings (wellness tech strategy roles) may blend language from both templates; the LLM pass decides which template is primary and must state why in the diff summary.

---

## 6. Email Thread Tracking (the "CC" module)

Problem: recruiter replies arrive in Gmail and the tracker never sees them, so follow-up dates and stage changes go stale.

Module: `engine/tracker/gmail_sync.py`

1. Auth: Gmail API OAuth (`gmail.readonly` scope only), token stored encrypted in `.env` path, refresh handled by google-auth.
2. Nightly job (10:00 PT): search inbox for threads involving any `contact_email` in `applications`, plus any thread whose subject contains a company name from open applications.
3. For each hit: upsert into `email_threads`, attach to the application, and:
   - If the last message is from the recruiter and no reply sent within 48h -> set `needs_reply=true` and include in the morning digest under **"Waiting on you"**.
   - If the candidate replied last -> clear `needs_reply`, push `next_followup` to +7 days.
4. Outbound logging: when the candidate CCs or BCCs a designated tracking address (e.g. `track@her-domain` via Resend inbound), the message is matched by recipient + subject to an application and logged as an `events` row of kind `email_sent`. This covers outreach she sends from any client, including her phone.
5. Stage inference is suggestive only: if a thread contains scheduling language ("interview", "availability", "calendar"), propose stage `interview` in the digest for one-tap confirmation. The engine never changes a stage on email content alone.

Guardrail: read-only scope, no auto-replies, no auto-send. Drafts are written to `artifacts/followups/` for the candidate to send themselves.

---

## 7. Scheduling (updated)

| Time (PT) | Command |
|---|---|
| 06:00 Mon/Wed/Fri | `aggregate --lane=all && score --lane=all && digest` |
| 06:30 daily | `aggregate --source=greenhouse,lever` (API sources are free, run daily) |
| 08:00 Monday | `funnel --by-lane` |
| 09:00 daily | `followups` |
| 22:00 daily | `gmail_sync` |

## 8. Build Order (delta from v1)

- **M7 (day 6):** run `migration_v2.sql`; add `profile_wellness.yaml`; add lane field end to end; confirm dual scoring on 10 seeded postings.
- **M8 (day 6-7):** wellness sources live (ISPA, Fitt, spa boards, new SerpAPI queries); digest renders two sections.
- **M9 (day 7):** template registry in tailor; wellness resume variant generation tested against one real posting.
- **M10 (day 8):** Gmail sync + tracking address inbound; "Waiting on you" digest section live.

Acceptance test: the Two Chairs Senior Director of Product (remote) and Greater Good Health Director of Clinical Performance postings must be discovered or seeded, scored >= 75 on the wellness lane, tailored against the wellness resume, and visible in the digest under Wellness.

## 9. Guardrails (additions to v1)

6. Gmail access is read-only; the engine drafts but never sends.
7. Stage changes from email content require one-tap human confirmation.
8. The wellness floor ($150k) can only be overridden by the flagship/equity exceptions, which the candidate confirms per posting.
