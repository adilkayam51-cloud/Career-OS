"""Deterministic job and approval policy. No external network calls or ATS submits."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def sha(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode('utf-8')).hexdigest()


def role_key(candidate_id: str, company: str, provider_job_id: str) -> str:
    return sha([candidate_id.strip(), company.strip().casefold(), provider_job_id.strip()])


def packet_hash(job: dict[str, Any], cv_sha256: str, form_answers: dict[str, Any], notice_text: str) -> str:
    return sha({'job_key': role_key(job['candidate_id'], job['company'], job['provider_job_id']),
                'jd_url': job['jd_url'], 'role': job['role'], 'lane': job['lane'],
                'cv_sha256': cv_sha256, 'answers': form_answers, 'notice_text': notice_text})


def eligibility_issues(job: dict[str, Any]) -> list[str]:
    issues = []
    title = job.get('role', '').lower()
    if not job.get('official_live_jd_verified'):
        issues.append('official_live_jd_unverified')
    if not job.get('hard_requirements_verified') or not job.get('hard_fit_evidence'):
        issues.append('mandatory_skill_fit_unverified')
    if job.get('prior_application_state', 'unknown') != 'clear_45d':
        issues.append('45_day_deduplication_not_clear')
    if job.get('lane') == 'remote' and 'IN' not in job.get('hiring_countries', []):
        issues.append('india_hiring_unverified')
    if job.get('lane') == 'geographic' and not job.get('work_location_feasible'):
        issues.append('location_not_confirmed')
    if job.get('is_pure_technical_architect') and not job.get('verified_technical_architecture_experience'):
        issues.append('unsupported_technical_architect_requirements')
    if job.get('compensation_incompatible'):
        issues.append('compensation_incompatible')
    if job.get('additional_required_input'):
        issues.append('unanswered_hard_question')
    return issues


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ist_day(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt.astimezone(IST).date().isoformat()


def is_within_45_days(previous_iso: str, reference: datetime | None = None) -> bool:
    previous = datetime.fromisoformat(previous_iso.replace('Z', '+00:00'))
    if previous.tzinfo is None:
        raise ValueError('timezone required')
    today = reference or now_utc()
    delta = today - previous
    return timedelta(0) <= delta < timedelta(days=45)