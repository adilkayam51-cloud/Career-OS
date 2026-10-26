"""TinyFish/Work handoff contract: gated execution, no forged provider receipts.

This module deliberately does not pretend to call a browser tool from Vercel.
The browser controller runs in an authorized connected runtime and must obtain
exact approved state, then report outcomes to a separate trusted attester.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BrowserHandoff:
    job_key: str
    packet_hash: str
    run_id: str
    official_url: str
    approval_checked: bool
    immutable_packet: dict[str, Any]


def guarded_handoff(*, approved_packet_hash: str | None, actual_packet_hash: str,
                    status: str, role: dict[str, Any], form: dict[str, Any],
                    run_id: str) -> BrowserHandoff:
    """Enforces the boundary before external ATS interaction.

    The caller is trusted only after reading the durable authorized attempt.
    No credential or form is sent to an employer by this function.
    """
    if status != 'AUTHORIZED' or not approved_packet_hash or approved_packet_hash != actual_packet_hash:
        raise PermissionError('Exact application packet authorization missing or changed')
    if not run_id or not role.get('jd_url') or not role.get('provider_job_id'):
        raise ValueError('Verified provider job and browser run identity required')
    return BrowserHandoff(job_key=role['job_key'],packet_hash=actual_packet_hash,run_id=run_id,
                          official_url=role['jd_url'],approval_checked=True,
                          immutable_packet={'role':dict(role),'form':dict(form)})


def classify_browser_result(result: dict[str, Any]) -> dict[str, str | bool]:
    """Untrusted browser text or screenshots cannot independently prove submission.

    Capture outcome for reconciliation; a separate provider verifier subsequently
    checks official ATS identity and receipt evidence. No fake success status.
    """
    run_id = str(result.get('run_id','')).strip()
    if not run_id:
        raise ValueError('Missing browser run ID')
    if result.get('otp_required') or result.get('captcha_required'):
        return {'state':'BLOCKED_HUMAN_CHALLENGE','requires_human':True,'countable':False,
                'resume_from':'USER_COMPLETES_PROTECTED_STEP'}
    if result.get('form_changed') or result.get('consent_changed'):
        return {'state':'REAPPROVAL_REQUIRED','requires_human':True,'countable':False,
                'resume_from':'REVIEW_CHANGED_PACKET'}
    if result.get('uncertain_submission'):
        return {'state':'UNKNOWN','requires_human':False,'countable':False,
                'resume_from':'CHECK_PROVIDER_APPLIED_BEFORE_RETRY'}
    if result.get('reported_success_url') or result.get('reported_success_text'):
        return {'state':'POTENTIAL_PROVIDER_RECEIPT','requires_human':False,'countable':False,
                'resume_from':'INDEPENDENT_PROVIDER_RECEIPT_VERIFICATION'}
    return {'state':'PREFILL_OR_REVIEW','requires_human':False,'countable':False,
            'resume_from':'AUTHORIZE_AND_SUBMIT_ONLY_AFTER_APPROVAL'}