-- Staging first; never auto-apply to main.
CREATE TABLE IF NOT EXISTS careeros_jobs (
  job_key TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL,
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  provider_job_id TEXT NOT NULL,
  lane TEXT NOT NULL CHECK (lane IN ('remote', 'geographic')),
  job_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT careeros_identity_unique UNIQUE(candidate_id, company, provider_job_id)
);