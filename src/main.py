"""CareerOS staging gateway. NO API endpoint can submit to an employer ATS.

Fail closed: no API key or no explicit local test database => not ready.
This service is a separate, optional backend, NOT the production Vercel v0.4.0 app.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .core import eligibility_issues, ist_day, now_utc, packet_hash, role_key, sha
from .store import SQLiteStore, PostgresStore
from .consent import verify as verify_consent

app = FastAPI(title='CareerOS Guarded Integration — Staging', version='0.1.0',
              description='Approval/receipt state gateway only. This API DOES NOT apply to jobs.')

@lru_cache(maxsize=1)
def store():
    db_url = os.getenv('DATABASE_URL','').strip()
    if db_url:
        return PostgresStore(db_url)
    if os.getenv('CAREEROS_ALLOW_LOCAL_DB') != '1':
        raise HTTPException(status_code=503, detail='No configured PostgreSQL database; local SQLite disabled.')
    return SQLiteStore(os.getenv('CAREEROS_TEST_DB_PATH','/tmp/careeros_approval_staging.sqlite3'))


def api_guard(x_careeros_key: Annotated[str | None, Header()] = None):
    expected = os.getenv('CAREEROS_API_KEY','')
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail='API authentication not configured')
    if not x_careeros_key or not hmac.compare_digest(expected, x_careeros_key):
        raise HTTPException(status_code=401, detail='Unauthorized')


def approval_guard(x_approval_key: Annotated[str | None, Header()] = None):
    """A separate trusted, out-of-band human-approval service holds this key.

    A caller claiming approval via a boolean/short string is not proof of consent.
    """
    expected = os.getenv('CAREEROS_APPROVAL_VERIFIER_KEY', '')
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail='Independent approval verifier not configured')
    if not x_approval_key or not hmac.compare_digest(expected, x_approval_key):
        raise HTTPException(status_code=401, detail='Approval verifier unauthorized')


def receipt_guard(x_receipt_key: Annotated[str | None, Header()] = None):
    expected = os.getenv('CAREEROS_RECEIPT_VERIFIER_KEY','')
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail='Independent receipt attester not configured')
    if not x_receipt_key or not hmac.compare_digest(expected,x_receipt_key):
        raise HTTPException(status_code=401, detail='Receipt verifier unauthorized')


class Job(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str = Field(min_length=3,max_length=100)
    company: str = Field(min_length=2,max_length=120)
    role: str = Field(min_length=3,max_length=160)
    provider_job_id: str = Field(min_length=3,max_length=100)
    jd_url: str
    lane: Literal['remote','geographic']
    hiring_countries: list[str] = Field(default_factory=list)
    official_live_jd_verified: bool = False
    hard_requirements_verified: bool = False
    hard_fit_evidence: list[str] = Field(default_factory=list)
    prior_application_state: Literal['clear_45d','duplicate','unknown'] = 'unknown'
    work_location_feasible: bool = False
    compensation_incompatible: bool = False
    additional_required_input: bool = False
    is_pure_technical_architect: bool = False
    verified_technical_architecture_experience: bool = False
    evidence_checked_at: datetime

    @field_validator('jd_url')
    @classmethod
    def https_only(cls, v):
        x = urlparse(v)
        if x.scheme!='https' or not x.netloc or x.username or x.password:
            raise ValueError('Official job URL must be HTTPS without embedded credentials')
        return v

    @field_validator('evidence_checked_at')
    @classmethod
    def timezone_required(cls, v):
        if not v.tzinfo:
            raise ValueError('Evidence timestamp needs timezone')
        return v


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_key: str = Field(min_length=64,max_length=64)
    cv_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    form_answers: dict[str,Any]
    terms_text: str = Field(min_length=1)
    consent_to_terms: bool
    consent_to_bgv_if_applicable: bool
    approver: str = Field(min_length=3,max_length=120)
    authorizes_exact_packet: bool
    consent_proof: str = Field(min_length=20,max_length=2800)
    verified_session_id: str = Field(min_length=12,max_length=160)


class Attempt(BaseModel):
    job_key: str
    packet_hash: str
    browser_run_id: str | None = None


class UnknownResult(BaseModel):
    job_key: str
    browser_run_id: str
    reason: str = Field(min_length=5,max_length=600)


class VerifiedReceipt(BaseModel):
    job_key: str
    packet_hash: str
    provider_reference: str = Field(min_length=6,max_length=300)
    evidence_url: str
    evidence_type: Literal['provider_confirmation_page','provider_confirmation_email','provider_authenticated_applied']
    submitted_at: datetime
    recorder: str = Field(min_length=3,max_length=120)
    explicit_provider_identity_checked: bool
    exact_job_identity_checked: bool

    @field_validator('evidence_url')
    @classmethod
    def evidence_url_valid(cls,v):
        u=urlparse(v)
        if u.scheme not in ('https',) or not u.netloc:
            raise ValueError('Evidence requires HTTPS URL')
        return v

    @field_validator('submitted_at')
    @classmethod
    def receipt_timestamp_valid(cls,v):
        if not v.tzinfo:
            raise ValueError('Provider submit time must include timezone')
        if v > now_utc():
            raise ValueError('Cannot confirm future submission')
        return v


@app.get('/health')
def health():
    return {'app':'careeros-guarded-staging', 'version':'0.1.0',
            'api_auth_configured':len(os.getenv('CAREEROS_API_KEY',''))>=32,
            'receipt_auth_configured':len(os.getenv('CAREEROS_RECEIPT_VERIFIER_KEY',''))>=32,
            'approval_auth_configured':len(os.getenv('CAREEROS_APPROVAL_VERIFIER_KEY',''))>=32,
            'local_db_test_enabled':os.getenv('CAREEROS_ALLOW_LOCAL_DB')=='1',
            'ats_submit_capability':False,
            'postgres_configured':bool(os.getenv('DATABASE_URL','').strip()),
            'receipt_provider_verification_external':True}

@app.post('/v1/jobs',dependencies=[Depends(api_guard)])
def upsert_job(job:Job):
    data=job.model_dump(mode='json')
    issues=eligibility_issues(data)
    key=role_key(job.candidate_id,job.company,job.provider_job_id)
    stamp=now_utc().isoformat()
    with store().connection() as db:
        db.execute('BEGIN IMMEDIATE')
        old=store().get_job(db,key)
        if old and old!=data:
            # Once an external attempt may exist, role identity and lane are immutable.
            attempted = db.execute('SELECT 1 FROM attempts WHERE job_key=?',(key,)).fetchone()
            receipted = db.execute('SELECT 1 FROM receipts WHERE job_key=?',(key,)).fetchone()
            if attempted or receipted:
                raise HTTPException(status_code=409,detail='Existing attempt/receipt requires explicit reconciliation, not job mutation')
            # Updating a JD or hiring terms invalidates any consent and previous packet.
            db.execute('UPDATE approvals SET invalidated=1 WHERE job_key=?',(key,))
            store().event(db,key,'PACKET_INVALIDATED',stamp,{'reason':'job_changed'})
        db.execute('INSERT INTO jobs(job_key,candidate_id,company,role,provider_job_id,lane,job_json,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET job_json=excluded.job_json',
                 (key,job.candidate_id,job.company,job.role,job.provider_job_id,job.lane,__import__('json').dumps(data),stamp))
        store().event(db,key,'JOB_CHECKED',stamp,{'eligibility_issues':issues})
    return {'job_key':key,'eligible_for_packet_review':not issues,'blockers':issues}

@app.post('/v1/approvals',dependencies=[Depends(api_guard),Depends(approval_guard)])
def approve(approval:Approval):
    stamp=now_utc().isoformat()
    with store().connection() as db:
        db.execute('BEGIN IMMEDIATE')
        job=store().get_job(db,approval.job_key)
        if not job: raise HTTPException(status_code=404,detail='No such job')
        issues=eligibility_issues(job)
        if issues: raise HTTPException(status_code=409,detail={'eligibility_blockers':issues})
        if not (approval.authorizes_exact_packet and approval.consent_to_terms and approval.consent_to_bgv_if_applicable):
            raise HTTPException(status_code=409,detail='Exact application and applicable consent required')
        # Never permit reapproval after an existing verified receipt or unknown in-flight attempt.
        r=db.execute('SELECT 1 FROM receipts WHERE job_key=?',(approval.job_key,)).fetchone()
        if r: raise HTTPException(status_code=409,detail='Previously confirmed application')
        a=db.execute('SELECT status FROM attempts WHERE job_key=?',(approval.job_key,)).fetchone()
        if a: raise HTTPException(status_code=409,detail='Existing attempt must be reconciled first')
        ph=packet_hash(job,approval.cv_sha256,approval.form_answers,approval.terms_text)
        consent_secret = os.getenv('CAREEROS_CONSENT_HMAC_KEY','')
        if len(consent_secret) < 32:
            raise HTTPException(status_code=503,detail='Independent consent-signing service not configured')
        if not verify_consent(approval.consent_proof,secret=consent_secret,
                              job_key=approval.job_key,packet_hash=ph,
                              candidate_id=job['candidate_id'],
                              session_id=approval.verified_session_id):
            raise HTTPException(status_code=403,detail='Invalid, expired, or non-matching signed consent')
        db.execute('INSERT INTO approvals(job_key,packet_hash,cv_sha256,answers_json,terms_hash,approved_at,approver,invalidated) VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(job_key) DO UPDATE SET packet_hash=excluded.packet_hash,cv_sha256=excluded.cv_sha256,answers_json=excluded.answers_json,terms_hash=excluded.terms_hash,approved_at=excluded.approved_at,approver=excluded.approver,invalidated=0',
                   (approval.job_key,ph,approval.cv_sha256,__import__('json').dumps(approval.form_answers),sha(approval.terms_text),stamp,approval.approver))
        store().event(db,approval.job_key,'EXACT_PACKET_APPROVED',stamp,{'packet_hash':ph,'approver':approval.approver})
    return {'approved':True,'job_key':approval.job_key,'packet_hash':ph,'ats_submitted':False}

@app.post('/v1/attempts/authorize',dependencies=[Depends(api_guard)])
def authorize(attempt:Attempt):
    with store().connection() as db:
        db.execute('BEGIN IMMEDIATE')
        approval=db.execute('SELECT packet_hash,invalidated FROM approvals WHERE job_key=?',(attempt.job_key,)).fetchone()
        if not approval or approval['invalidated'] or approval['packet_hash'] != attempt.packet_hash:
            raise HTTPException(status_code=403,detail='Exact packet not currently approved')
        prior=db.execute('SELECT status FROM attempts WHERE job_key=?',(attempt.job_key,)).fetchone()
        if prior: raise HTTPException(status_code=409,detail='Prior attempt exists; reconcile before retry')
        if db.execute('SELECT 1 FROM receipts WHERE job_key=?',(attempt.job_key,)).fetchone():
            raise HTTPException(status_code=409,detail='Already confirmed')
        stamp=now_utc().isoformat()
        db.execute('INSERT INTO attempts(job_key,packet_hash,status,browser_run_id,updated_at) VALUES(?,?,?,?,?)',
                  (attempt.job_key,attempt.packet_hash,'AUTHORIZED',attempt.browser_run_id,stamp))
        store().event(db,attempt.job_key,'ATTEMPT_AUTHORIZED',stamp,{'browser_run_id':attempt.browser_run_id})
    return {'authorized':True,'executor_must_submit_separately':True,'status':'AUTHORIZED'}

@app.post('/v1/attempts/unknown',dependencies=[Depends(api_guard)])
def unknown(result:UnknownResult):
    with store().connection() as db:
        db.execute('BEGIN IMMEDIATE')
        a=db.execute('SELECT status,browser_run_id FROM attempts WHERE job_key=?',(result.job_key,)).fetchone()
        if not a: raise HTTPException(status_code=404,detail='No attempt')
        if a['status']=='PROVIDER_CONFIRMED': raise HTTPException(status_code=409,detail='Receipt already confirmed')
        stamp=now_utc().isoformat()
        db.execute('UPDATE attempts SET status=?,browser_run_id=?,updated_at=? WHERE job_key=?',('UNKNOWN',result.browser_run_id,stamp,result.job_key))
        store().event(db,result.job_key,'UNKNOWN_SUBMISSION',stamp,{'reason':result.reason,'browser_run_id':result.browser_run_id})
    return {'status':'UNKNOWN','retry_allowed':False,'resume_from':'CHECK_PROVIDER_APPLIED'}

@app.post('/v1/receipts/attest',dependencies=[Depends(api_guard),Depends(receipt_guard)])
def attest_receipt(data:VerifiedReceipt):
    if not data.explicit_provider_identity_checked or not data.exact_job_identity_checked:
        raise HTTPException(status_code=409,detail='Independent provider and job identity check required')
    with store().connection() as db:
        db.execute('BEGIN IMMEDIATE')
        a=db.execute('SELECT packet_hash,status FROM attempts WHERE job_key=?',(data.job_key,)).fetchone()
        if not a or a['packet_hash'] != data.packet_hash:
            raise HTTPException(status_code=403,detail='Receipt not tied to authorized immutable packet')
        if db.execute('SELECT 1 FROM receipts WHERE job_key=?',(data.job_key,)).fetchone():
            raise HTTPException(status_code=409,detail='Duplicate confirmed receipt')
        job=store().get_job(db,data.job_key)
        if not job: raise HTTPException(status_code=404,detail='Missing role')
        stamp=now_utc().isoformat()
        db.execute('INSERT INTO receipts(job_key,provider_reference,evidence_url,evidence_type,submitted_at_utc,submitted_ist_day,verified_at,recorder) VALUES(?,?,?,?,?,?,?,?)',
                  (data.job_key,data.provider_reference,data.evidence_url,data.evidence_type,data.submitted_at.astimezone(timezone.utc).isoformat(),ist_day(data.submitted_at),stamp,data.recorder))
        db.execute('UPDATE attempts SET status=?,updated_at=? WHERE job_key=?',('PROVIDER_CONFIRMED',stamp,data.job_key))
        store().event(db,data.job_key,'RECEIPT_ATTESTED',stamp,{'provider_reference_hash':sha(data.provider_reference),'submitted_ist_day':ist_day(data.submitted_at)})
    return {'provider_confirmed_attested':True,'job_key':data.job_key,'submitted_ist_day':ist_day(data.submitted_at),'note':'Trusted attester must independently validate evidence against actual ATS; API cannot verify provider itself.'}

@app.get('/v1/scorecard/{candidate_id}',dependencies=[Depends(api_guard)])
def scorecard(candidate_id:str,day:str|None=None):
    day = day or ist_day(now_utc())
    try: datetime.strptime(day,'%Y-%m-%d')
    except ValueError: raise HTTPException(status_code=422,detail='Date must be YYYY-MM-DD')
    with store().connection() as db:
        rows=db.execute('SELECT j.lane,COUNT(*) AS n FROM receipts r JOIN jobs j ON j.job_key=r.job_key WHERE j.candidate_id=? AND r.submitted_ist_day=? GROUP BY j.lane',(candidate_id,day)).fetchall()
    lanes={r['lane']:r['n'] for r in rows}
    a=lanes.get('remote',0);b=lanes.get('geographic',0)
    return {'day_ist':day,'remote_confirmed':a,'remote_goal':10,'geographic_confirmed':b,'geographic_goal':10,'total_confirmed':a+b,'daily_goal':20,'backend':'neon' if os.getenv('DATABASE_URL') else 'local-test-only'}

@app.get('/v1/audit/{job_key}',dependencies=[Depends(api_guard)])
def audit(job_key:str):
    with store().connection() as db:
        events=db.execute('SELECT event_type,metadata_json,recorded_at FROM event_log WHERE job_key=? ORDER BY id',(job_key,)).fetchall()
        state=db.execute('SELECT status FROM attempts WHERE job_key=?',(job_key,)).fetchone()
        receipt=db.execute('SELECT submitted_ist_day,evidence_type,verified_at FROM receipts WHERE job_key=?',(job_key,)).fetchone()
    return {'job_key':job_key,'attempt_state':state['status'] if state else None,
            'receipt_present':bool(receipt),'receipt_metadata':dict(receipt) if receipt else None,
            'events':[{'event':r['event_type'],'data':(__import__('json').loads(r['metadata_json']) if isinstance(r['metadata_json'],str) else r['metadata_json']),'at':r['recorded_at']} for r in events]}