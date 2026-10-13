-- v3: per-lane "already emailed" marker so digests only contain new items
ALTER TABLE scores ADD COLUMN IF NOT EXISTS digested_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_scores_undigested ON scores (lane, digested_at) WHERE digested_at IS NULL;
