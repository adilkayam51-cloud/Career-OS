"""Transactional SQLite for LOCAL DEVELOPMENT/TEST ONLY. Production needs Neon migration."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .core import canonical_json

DDL = '''
CREATE TABLE IF NOT EXISTS jobs (
 job_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, company TEXT NOT NULL,
 role TEXT NOT NULL, provider_job_id TEXT NOT NULL,
 lane TEXT NOT NULL CHECK(lane IN ('remote','geographic')),
 job_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
 job_key TEXT PRIMARY KEY REFERENCES jobs(job_key), packet_hash TEXT NOT NULL,
 cv_sha256 TEXT NOT NULL, answers_json TEXT NOT NULL, terms_hash TEXT NOT NULL,
 approved_at TEXT NOT NULL, approver TEXT NOT NULL, invalidated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attempts (
 job_key TEXT PRIMARY KEY REFERENCES jobs(job_key), packet_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('AUTHORIZED','UNKNOWN','PROVIDER_CONFIRMED','BLOCKED')),
 browser_run_id TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
 job_key TEXT PRIMARY KEY REFERENCES jobs(job_key), provider_reference TEXT NOT NULL,
 evidence_url TEXT NOT NULL, evidence_type TEXT NOT NULL,
 submitted_at_utc TEXT NOT NULL, submitted_ist_day TEXT NOT NULL,
 verified_at TEXT NOT NULL, recorder TEXT NOT NULL,
 UNIQUE(provider_reference, job_key)
);
CREATE TABLE IF NOT EXISTS event_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL,
 event_type TEXT NOT NULL, metadata_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL
);
'''

class SQLiteStore:
    def __init__(self, file: str):
        self.file = file
        Path(file).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript(DDL)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.file, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def get_job(self, con, key):
        item = con.execute('SELECT job_json FROM jobs WHERE job_key=?',(key,)).fetchone()
        return json.loads(item['job_json']) if item else None

    def event(self, con, key, kind, when, data=None):
        con.execute('INSERT INTO event_log(job_key,event_type,metadata_json,recorded_at) VALUES(?,?,?,?)',
                    (key,kind,canonical_json(data or {}),when))