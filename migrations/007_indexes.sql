CREATE INDEX IF NOT EXISTS idx_careeros_events_job ON careeros_application_events(job_key, recorded_at);
CREATE INDEX IF NOT EXISTS idx_careeros_receipts_day ON careeros_receipts(submitted_ist_day);
CREATE INDEX IF NOT EXISTS idx_careeros_jobs_candidate ON careeros_jobs(candidate_id);