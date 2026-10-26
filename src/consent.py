"""Verify exact-packet, session-bound HMAC proof from a separate trusted approval service.

Deliberately no signing endpoint. The signing key must never reach browsers or
ordinary executor credentials. Deployment must supply an authenticated UI that
verifies the human before it issues a proof.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from .core import canonical_json


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))


def issue_for_trusted_tests(*, secret: str, job_key: str, packet_hash: str,
                            candidate_id: str, session_id: str,
                            now: datetime | None = None) -> str:
    """Unit-test fixture helper; never expose as an application endpoint."""
    if len(secret) < 32 or len(session_id) < 12:
        raise ValueError('Strong secret and verified session id are required')
    now = now or datetime.now(timezone.utc)
    record = {'job_key': job_key, 'packet_hash': packet_hash,
              'candidate_id': candidate_id, 'session_id': session_id,
              'issued_at': int(now.timestamp()),
              'expires_at': int((now + timedelta(minutes=10)).timestamp()),
              'purpose': 'exact_job_application_approval_v1'}
    body = _encode(canonical_json(record).encode('utf-8'))
    signature = _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f'{body}.{signature}'


def verify(proof: str, *, secret: str, job_key: str, packet_hash: str,
           candidate_id: str, session_id: str,
           now: datetime | None = None) -> bool:
    if len(secret) < 32 or not proof or proof.count('.') != 1:
        return False
    body, signature = proof.split('.', 1)
    if len(body) > 2500 or len(signature) > 120:
        return False
    expected = _encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        data = json.loads(_decode(body).decode('utf-8'))
        time_now = int((now or datetime.now(timezone.utc)).timestamp())
        return (data.get('purpose') == 'exact_job_application_approval_v1'
                and hmac.compare_digest(str(data['job_key']), job_key)
                and hmac.compare_digest(str(data['packet_hash']), packet_hash)
                and hmac.compare_digest(str(data['candidate_id']), candidate_id)
                and hmac.compare_digest(str(data['session_id']), session_id)
                and len(session_id) >= 12
                and time_now >= int(data['issued_at'])
                and int(data['expires_at']) >= time_now
                and 0 < int(data['expires_at']) - int(data['issued_at']) <= 600)
    except (ValueError, KeyError, TypeError, OverflowError):
        return False