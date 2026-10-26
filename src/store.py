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


class PgConnection:
    """Small SQLAlchemy connection facade for the existing audited query paths.

    Input SQL here is STATIC server-owned statements, never user-provided SQL.
    Production tables have careeros_ prefix and JSONB/boolean/date types.
    """
    import re as _re
    _tables = _re.compile(r'\b(jobs|approvals|attempts|receipts|event_log)\b', _re.I)
    _names = {'jobs': 'careeros_jobs', 'approvals': 'careeros_approvals',
              'attempts': 'careeros_attempts', 'receipts': 'careeros_receipts',
              'event_log': 'careeros_application_events'}

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, args=()):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            return None  # SQLAlchemy engine.begin manages ACID transaction
        sql = self._tables.sub(lambda m: self._names[m.group(0).lower()], sql)
        sql = sql.replace('ORDER BY id', 'ORDER BY recorded_at, event_id')
        sql = sql.replace('invalidated=1', 'invalidated=TRUE').replace('invalidated=0', 'invalidated=FALSE')
        sql = sql.replace('invalidated) VALUES(?,?,?,?,?,?,?,0)', 'invalidated) VALUES(?,?,?,?,?,?,?,FALSE)')
        # psycopg3 DBAPI positional bind, preserving server-only SQL.
        sql = sql.replace('?', '%s')
        # Strings passed by the validated FastAPI caller must bind as typed JSONB.
        if 'INSERT INTO careeros_jobs(' in sql:
            sql = sql.replace('VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                              'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)')
        if 'INSERT INTO careeros_approvals(' in sql:
            sql = sql.replace('VALUES(%s,%s,%s,%s,%s,%s,%s,FALSE)',
                              'VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,FALSE)')
        if 'INSERT INTO careeros_application_events(' in sql:
            sql = sql.replace('VALUES(%s,%s,%s,%s)',
                              'VALUES(%s,%s,%s::jsonb,%s)')
        if 'INSERT INTO careeros_receipts(' in sql:
            sql = sql.replace('VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                              'VALUES(%s,%s,%s,%s,%s::timestamptz,%s::date,%s::timestamptz,%s)')
        result = self._conn.exec_driver_sql(sql, tuple(args))
        return result.mappings() if result.returns_rows else result


class PostgresStore:
    """Neon-backed transactional store using a bounded SQLAlchemy connection pool.

    Never creates schema automatically; migration is a separate reviewed release.
    """
    def __init__(self, database_url: str):
        if not database_url.startswith(('postgresql://','postgresql+psycopg://','postgres://')):
            raise ValueError('Production DATABASE_URL must be PostgreSQL')
        from sqlalchemy import create_engine
        url = database_url.replace('postgres://', 'postgresql://', 1)
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
        self.engine = create_engine(url, pool_size=3, max_overflow=2,
                                    pool_timeout=8, pool_recycle=180,
                                    pool_pre_ping=True, connect_args={'connect_timeout':8})

    @contextmanager
    def connection(self):
        with self.engine.begin() as conn:
            yield PgConnection(conn)

    def get_job(self, con, key):
        item = con.execute('SELECT job_json FROM jobs WHERE job_key=?',(key,)).fetchone()
        if item is None:
            return None
        value = item['job_json']
        return json.loads(value) if isinstance(value, str) else value

    def event(self, con, key, kind, when, data=None):
        con.execute('INSERT INTO event_log(job_key,event_type,metadata_json,recorded_at) VALUES(?,?,?,?)',
                    (key,kind,canonical_json(data or {}),when))