-- Staging first; never auto-apply to main.
CREATE TABLE IF NOT EXISTS careeros_receipts (
  job_key TEXT PRIMARY KEY REFERENCES careeros_jobs(job_key),
  provider_reference TEXT NOT NULL,
  evidence_url TEXT NOT NULL,
  evidence_type TEXT NOT NULL CHECK(evidence_type IN ('provider_confirmation_page','provider_confirmation_email','provider_authenticated_applied')),
  submitted_at_utc TIMESTAMPTZ NOT NULL,
  submitted_ist_day DATE NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  recorder TEXT NOT NULL,
  UNIQUE(job_key, provider_reference)
);