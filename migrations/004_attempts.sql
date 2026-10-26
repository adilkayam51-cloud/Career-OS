-- Staging first; never auto-apply to main.
CREATE TABLE IF NOT EXISTS careeros_attempts (
  job_key TEXT PRIMARY KEY REFERENCES careeros_jobs(job_key),
  packet_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('AUTHORIZED','UNKNOWN','PROVIDER_CONFIRMED','BLOCKED')),
  browser_run_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);