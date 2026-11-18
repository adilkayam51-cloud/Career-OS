"""Route-first preflight. Pure validation: no browser, email or ATS actions."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreflightResult:
    decision: str
    blockers: tuple[str, ...]


def validate_route(job: dict[str, Any], route: dict[str, Any]) -> PreflightResult:
    """Validate documented constraints, then request separate policy review.

    PASS here is explicitly not approval to submit. All unknown mandatory
    claims and access states fail closed without inventing candidate history.
    """
    issues: list[str] = []
    if job.get("official_live_jd_verified") is not True:
        issues.append("SOURCE_UNVERIFIED")
    if job.get("mandatory_experience_fit") is not True:
        issues.append("MANDATORY_EXPERIENCE_UNKNOWN_OR_UNMET")
    if job.get("dedupe_45d_clear") is not True:
        issues.append("DUPLICATE_CHECK_NOT_CLEAR")
    if job.get("lane") == "remote" and "IN" not in job.get("hiring_countries", []):
        issues.append("INDIA_HIRING_UNVERIFIED")
    if job.get("lane") == "geographic" and job.get("location_feasible") is not True:
        issues.append("LOCATION_UNVERIFIED")
    if job.get("requires_timezone_alignment") and job.get("timezone_compatible") is not True:
        issues.append("TIMEZONE_REQUIREMENT_UNVERIFIED_OR_UNMET")
    if job.get("unsupported_required_declaration"):
        issues.append("UNSUPPORTED_DECLARATION")
    if route.get("access") != "available":
        issues.append("ROUTE_BLOCKED" if route.get("access") == "blocked" else "ROUTE_UNVERIFIED")
    channel = route.get("channel")
    if channel == "employer_email":
        if route.get("vacancy_specific_recipient_verified") is not True:
            issues.append("RECIPIENT_UNVERIFIED")
    elif channel == "ats":
        if route.get("authenticated_session") is not True:
            issues.append("ATS_SESSION_UNVERIFIED")
    else:
        issues.append("ROUTE_CHANNEL_UNSUPPORTED")
    if issues:
        return PreflightResult("HOLD", tuple(dict.fromkeys(issues)))
    return PreflightResult("READY_FOR_POLICY_REVIEW", ())
