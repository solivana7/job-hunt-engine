-- Job Hunt Engine v1 schema (spec section 3). Applied before migration_v2.sql.

CREATE TABLE IF NOT EXISTS postings (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL,              -- greenhouse | lever | serpapi | career_page | board | seed
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
  kind          TEXT NOT NULL,              -- stage_change | email_sent | note | manual_required | stage_proposal
  payload       JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
