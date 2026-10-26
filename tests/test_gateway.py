from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from src import main
from src.consent import issue_for_trusted_tests
from src.core import packet_hash

client = TestClient(main.app)
API='A'*40
APPROVAL='B'*40
RECEIPT='C'*40
JOB_URL='https://job-boards.greenhouse.io/example/jobs/123456'
HEAD={'x-careeros-key':API}
APP_HEAD={**HEAD,'x-approval-key':APPROVAL}
RECEIPT_HEAD={**HEAD,'x-receipt-key':RECEIPT}


def setup_function(_):
    main.store.cache_clear()


def provision(monkeypatch, tmp_path):
    monkeypatch.setenv('CAREEROS_ALLOW_LOCAL_DB','1')
    monkeypatch.setenv('CAREEROS_TEST_DB_PATH',str(tmp_path/'gateway.sqlite3'))
    monkeypatch.setenv('CAREEROS_API_KEY',API)
    monkeypatch.setenv('CAREEROS_APPROVAL_VERIFIER_KEY',APPROVAL)
    monkeypatch.setenv('CAREEROS_RECEIPT_VERIFIER_KEY',RECEIPT)
    monkeypatch.setenv('CAREEROS_CONSENT_HMAC_KEY','D'*40)
    main.store.cache_clear()


def a_job(lane='geographic'):
    return {'candidate_id':'test-candidate', 'company':'Example Co', 'role':'Enterprise Partnerships Manager',
            'provider_job_id':'123456', 'jd_url':JOB_URL, 'lane':lane,
            'hiring_countries':['IN'], 'official_live_jd_verified':True,
            'hard_requirements_verified':True, 'hard_fit_evidence':['verified B2B partner negotiations'],
            'prior_application_state':'clear_45d', 'work_location_feasible':True,
            'evidence_checked_at':datetime.now(timezone.utc).isoformat()}


def get_job_key():
    r=client.post('/v1/jobs',json=a_job(),headers=HEAD)
    assert r.status_code==200, r.text
    assert r.json()['eligible_for_packet_review'] is True
    return r.json()['job_key']


def approve(key):
    from src.store import SQLiteStore
    from src.core import role_key
    from src.consent import issue_for_trusted_tests
    with main.store().connection() as conn:
        job=main.store().get_job(conn,key)
    ph=packet_hash(job,'a'*64,{'notice_period':'user supplied'},
                   'The exact visible terms presented to this candidate')
    token=issue_for_trusted_tests(secret='D'*40, job_key=key,packet_hash=ph,
                                  candidate_id=job['candidate_id'],session_id='trusted-session-123')
    return client.post('/v1/approvals',headers=APP_HEAD,json={
        'job_key':key,'cv_sha256':'a'*64,'form_answers':{'notice_period':'user supplied'},
        'terms_text':'The exact visible terms presented to this candidate',
        'consent_to_terms':True,'consent_to_bgv_if_applicable':True,
        'approver':'independently-verified-human','authorizes_exact_packet':True,
        'consent_proof':token,'verified_session_id':'trusted-session-123'})


def test_missing_secrets_fail_closed(monkeypatch):
    monkeypatch.delenv('CAREEROS_API_KEY',raising=False)
    assert client.post('/v1/jobs',json=a_job()).status_code==503


def test_unauthenticated_job_denied(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    assert client.post('/v1/jobs',json=a_job()).status_code==401


def test_remote_india_hard_gate(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    data=a_job('remote')
    data['hiring_countries']=['US']
    r=client.post('/v1/jobs',json=data,headers=HEAD)
    assert r.status_code==200
    assert 'india_hiring_unverified' in r.json()['blockers']
    bad=approve(r.json()['job_key'])
    assert bad.status_code==409


def test_approval_requires_independent_secret(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    payload={'job_key':key,'cv_sha256':'a'*64, 'form_answers':{},
        'terms_text':'BGV and final agreement','consent_to_terms':True,'consent_to_bgv_if_applicable':True,
        'approver':'human','authorizes_exact_packet':True,'consent_proof':'invalid-proof-for-test',
        'verified_session_id':'trusted-session-123'}
    r=client.post('/v1/approvals',headers=HEAD,json=payload)
    assert r.status_code==401
    assert approve(key).status_code==200


def test_full_authorization_and_receipt_are_distinct(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    r=approve(key)
    assert r.status_code==200
    packet=r.json()['packet_hash']
    assert r.json()['ats_submitted'] is False
    pre=client.get('/v1/scorecard/test-candidate',headers=HEAD)
    assert pre.json()['total_confirmed']==0
    attempt=client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':packet},headers=HEAD)
    assert attempt.status_code==200
    assert attempt.json()['executor_must_submit_separately'] is True
    duplicate=client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':packet},headers=HEAD)
    assert duplicate.status_code==409
    assert client.get('/v1/scorecard/test-candidate',headers=HEAD).json()['total_confirmed']==0
    data={'job_key':key,'packet_hash':packet,'provider_reference':'provider-verified-test-789',
        'evidence_url':'https://provider.example/confirmation/test-789',
        'evidence_type':'provider_confirmation_page','submitted_at':datetime.now(timezone.utc).isoformat(),
        'recorder':'independent-provider-checker','explicit_provider_identity_checked':True,
        'exact_job_identity_checked':True}
    assert client.post('/v1/receipts/attest',json=data,headers=HEAD).status_code==401
    yes=client.post('/v1/receipts/attest',json=data,headers=RECEIPT_HEAD)
    assert yes.status_code==200,yes.text
    check=client.get('/v1/scorecard/test-candidate',headers=HEAD).json()
    assert check['total_confirmed']==1 and check['geographic_confirmed']==1
    assert client.post('/v1/receipts/attest',json=data,headers=RECEIPT_HEAD).status_code==409
    log=client.get('/v1/audit/'+key,headers=HEAD).json()
    assert log['receipt_present'] is True
    assert [x['event'] for x in log['events']][-1]=='RECEIPT_ATTESTED'


def test_unknown_forbids_repeat(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    p=approve(key).json()['packet_hash']
    client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':p},headers=HEAD)
    response=client.post('/v1/attempts/unknown',json={'job_key':key,'browser_run_id':'browser-1',
        'reason':'ATS response timed out; Applied state unknown'},headers=HEAD)
    assert response.status_code==200
    assert response.json()['retry_allowed'] is False
    repeat=client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':p},headers=HEAD)
    assert repeat.status_code==409


def test_material_change_invalidates_packet(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    p=approve(key).json()['packet_hash']
    jd=a_job();jd['jd_url']='https://job-boards.greenhouse.io/example/jobs/123456-new'
    assert client.post('/v1/jobs',json=jd,headers=HEAD).status_code==200
    attempt=client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':p},headers=HEAD)
    assert attempt.status_code==403


def test_no_fake_receipt_before_authorization(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    r=approve(key)
    data={'job_key':key,'packet_hash':r.json()['packet_hash'],
        'provider_reference':'some-reference','evidence_url':'https://provider.example/check',
        'evidence_type':'provider_confirmation_page',
        'submitted_at':datetime.now(timezone.utc).isoformat(),
        'recorder':'verified-checker','explicit_provider_identity_checked':True,
        'exact_job_identity_checked':True}
    result=client.post('/v1/receipts/attest',json=data,headers=RECEIPT_HEAD)
    assert result.status_code==403


def test_signed_consent_rejects_tamper_and_expiry():
    from src.consent import issue_for_trusted_tests, verify
    now=datetime.now(timezone.utc)
    fields={'secret':'D'*40,'job_key':'a'*64,'packet_hash':'b'*64,
            'candidate_id':'test-candidate','session_id':'trusted-session-123'}
    valid=issue_for_trusted_tests(**fields,now=now)
    assert verify(valid,**fields,now=now)
    assert not verify(valid,**{**fields,'packet_hash':'c'*64},now=now)
    assert not verify(valid,**{**fields,'session_id':'another-session-888'},now=now)
    assert not verify(valid[:-3]+'abc',**fields,now=now)
    assert not verify(valid,**fields,now=now+timedelta(minutes=11))


def test_changed_job_after_attempt_must_be_reconciled(monkeypatch,tmp_path):
    provision(monkeypatch,tmp_path)
    key=get_job_key()
    ph=approve(key).json()['packet_hash']
    assert client.post('/v1/attempts/authorize',json={'job_key':key,'packet_hash':ph},headers=HEAD).status_code==200
    changed=a_job();changed['jd_url']='https://job-boards.greenhouse.io/example/jobs/new'
    assert client.post('/v1/jobs',json=changed,headers=HEAD).status_code==409


def test_bridge_fails_closed_and_no_browser_result_is_counted():
    from src.tinyfish_bridge import guarded_handoff,classify_browser_result
    role={'job_key':'x','provider_job_id':'123','jd_url':'https://example.com/job'}
    import pytest
    with pytest.raises(PermissionError):
        guarded_handoff(approved_packet_hash='abc',actual_packet_hash='changed',status='AUTHORIZED',role=role,form={},run_id='run-1')
    handoff=guarded_handoff(approved_packet_hash='abc',actual_packet_hash='abc',status='AUTHORIZED',role=role,form={},run_id='run-1')
    assert handoff.approval_checked
    for obj in ({'run_id':'run-1','reported_success_url':'https://example.com/success'},
                {'run_id':'run-1','captcha_required':True},
                {'run_id':'run-1','uncertain_submission':True}):
        assert classify_browser_result(obj)['countable'] is False


def test_gmail_classification_only_queues_for_independent_receipt_review():
    from src.gmail_receipts import classify
    msg={'message_id':'gmail-123','received_at':'2026-10-26T00:00:00Z',
         'subject':'Your Example Co application was received',
         'body':'Example Co receipt, job id 1234: thank you for applying'}
    result=classify(msg,{'1234':'Example Co'})
    assert result['candidate_job_ids']==['1234']
    assert result['status']=='REVIEW_PROVIDER_EMAIL'
    assert not result['provider_confirmed']