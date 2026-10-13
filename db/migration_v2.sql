-- v2: multi-lane (data_ai | wellness) + email thread tracking (wellness addendum section 1 & 6)

ALTER TABLE postings     ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
ALTER TABLE scores       ADD COLUMN IF NOT EXISTS lane TEXT DEFAULT 'data_ai';
CREATE INDEX IF NOT EXISTS idx_postings_lane ON postings(lane, first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_apps_lane     ON applications(lane, stage);

-- one row per (posting, lane); postings.lane records the first lane that matched
ALTER TABLE scores DROP CONSTRAINT IF EXISTS scores_pkey;
ALTER TABLE scores ADD PRIMARY KEY (posting_id, lane);

CREATE TABLE IF NOT EXISTS email_threads (
  id              BIGSERIAL PRIMARY KEY,
  application_id  BIGINT REFERENCES applications(id) ON DELETE SET NULL,
  gmail_thread_id TEXT UNIQUE,
  subject         TEXT,
  participants    TEXT[],
  last_message_at TIMESTAMPTZ,
  snippet         TEXT,
  needs_reply     BOOLEAN DEFAULT false
);
