"""Classifier for Gmail receipt candidates; does not mark applications confirmed.

Emails and message bodies can be forged. The output is a verification queue only;
trusted receipt attestation must match official ATS job identity and timestamps.
"""
from __future__ import annotations
import re
from typing import Any

STRONG = re.compile(r'(?i)(application (?:was )?(?:successfully )?(?:submitted|received)|thank you for applying|we (?:have )?received your application)')
MARKETING = re.compile(r'(?i)(recommended jobs|daily job alerts|unlock your career|courses for you|job suggestions)')


def classify(email: dict[str, Any], known_jobs: dict[str, str]) -> dict[str, Any]:
    subject = str(email.get('subject',''))[:500]
    body = str(email.get('body',''))[:50000]
    if not email.get('message_id') or not email.get('received_at'):
        return {'status':'INSUFFICIENT_HEADERS','provider_confirmed':False}
    if MARKETING.search(subject) or MARKETING.search(body[:300]):
        return {'status':'MARKETING_OR_ALERT','provider_confirmed':False}
    if not STRONG.search(subject+' '+body):
        return {'status':'NOT_A_CONFIRMATION','provider_confirmed':False}
    # Company and exact role IDs must match; prefer official ATS readback too.
    matched = [job_id for job_id, company in known_jobs.items()
               if job_id.casefold() in (subject+' '+body).casefold() and company.casefold() in (subject+' '+body).casefold()]
    return {'status':'REVIEW_PROVIDER_EMAIL' if matched else 'UNMATCHED_CONFIRMATION',
            'candidate_job_ids':matched,'provider_confirmed':False,
            'resume_from':'INDEPENDENT_ATS_AND_MESSAGE_IDENTITY_CHECK'}