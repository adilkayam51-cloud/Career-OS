-- Staging first; never auto-apply to main.
CREATE TABLE IF NOT EXISTS careeros_application_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_key TEXT NOT NULL REFERENCES careeros_jobs(job_key),
  event_type TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);