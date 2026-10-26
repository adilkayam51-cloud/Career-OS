-- CareerOS V1: private staging/production approval and receipt ledger.
-- This is CREATE-ONLY; do not write sensitive application material into public PathLattice.
-- Execute only on the intentionally selected private Neon database/branch.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

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
CREATE TABLE IF NOT EXISTS careeros_approvals (
  job_key TEXT PRIMARY KEY REFERENCES careeros_jobs(job_key),
  packet_hash TEXT NOT NULL CHECK(packet_hash ~ '^[0-9a-f]{64}$'),
  cv_sha256 TEXT NOT NULL CHECK(cv_sha256 ~ '^[0-9a-f]{64}$'),
  answers_json JSONB NOT NULL,
  terms_hash TEXT NOT NULL CHECK(terms_hash ~ '^[0-9a-f]{64}$'),
  approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approver TEXT NOT NULL,
  invalidated BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS careeros_attempts (
  job_key TEXT PRIMARY KEY REFERENCES careeros_jobs(job_key),
  packet_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('AUTHORIZED','UNKNOWN','PROVIDER_CONFIRMED','BLOCKED')),
  browser_run_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
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
CREATE TABLE IF NOT EXISTS careeros_application_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_key TEXT NOT NULL REFERENCES careeros_jobs(job_key),
  event_type TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_careeros_events_job ON careeros_application_events(job_key, recorded_at);
CREATE INDEX IF NOT EXISTS idx_careeros_receipts_day ON careeros_receipts(submitted_ist_day);
CREATE INDEX IF NOT EXISTS idx_careeros_jobs_candidate ON careeros_jobs(candidate_id);
-- Every receipt has unique job_key. A deterministic receipt hash alone never proves provider confirmation.