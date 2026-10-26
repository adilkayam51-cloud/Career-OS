-- Staging first; never auto-apply to main.
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