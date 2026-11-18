"""Deterministic Gmail evidence planning and an idempotent *local* event ledger.

This module never contacts Gmail, sends an application, or writes to the
canonical Excel/Postgres tracker. An authenticated caller supplies verified
Gmail metadata, and a separate version-checked writer must apply the proposed
tracker patch after reviewing the relevant policy and source records.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID, uuid5


_EVENT_NAMESPACE = UUID("8429214e-3229-4b12-93a3-0b7d5dfb3c52")


_MARKETING = re.compile(
    r"\b(?:recommended jobs|daily job alerts|job suggestions|unlock your career)\b", re.I
)
_REJECTION = re.compile(
    r"\b(?:not moving forward|not selected|regret to inform|"
    r"will not be proceeding|unfortunately.{0,80}(?:application|position))\b", re.I
)
_INTERVIEW = re.compile(
    r"\b(?:invite you (?:for|to) (?:an? )?interview|"
    r"schedule (?:an? |your )?interview|interview invitation)\b", re.I
)
_ACK = re.compile(
    r"\b(?:thank you for applying|thank you for your application|"
    r"we (?:have )?received your application|"
    r"we will review your application|application (?:has been )?received)\b", re.I
)


@dataclass(frozen=True)
class ApplicationRef:
    application_id: str
    role: str
    employer_domains: tuple[str, ...]
    sent_at: datetime
    status: str
    outbound_rfc_message_id: str | None = None


@dataclass(frozen=True)
class GmailMessage:
    mailbox_key: str
    message_id: str
    sender_email: str
    received_at: datetime
    subject: str
    body: str
    dkim_pass: bool
    dmarc_pass: bool
    in_reply_to: str | None = None


@dataclass(frozen=True)
class ReconciliationPlan:
    decision: str
    reason: str
    event_id: str | None = None
    event_type: str | None = None
    application_id: str | None = None
    previous_status: str | None = None
    proposed_status: str | None = None
    received_at: str | None = None

    def audit(self, reconciled_at: datetime) -> dict[str, str | None]:
        """Required audit shape; do not suggest tracker write was completed."""
        _require_aware(reconciled_at)
        return {
            "Event_ID": self.event_id,
            "Matched_ID": self.application_id,
            "Previous_Status": self.previous_status,
            "Proposed_Status": self.proposed_status,
            "Decision": self.decision,
            "Reason": self.reason,
            "Reconciliation_Timestamp": reconciled_at.astimezone(timezone.utc).isoformat(),
        }


def _require_aware(when: datetime) -> None:
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Timezone-aware timestamps are required")


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text.casefold())).strip()


def _sender_domain(address: str) -> str | None:
    if not re.fullmatch(r"[^\s@<>]+@(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}", address):
        return None
    return address.rsplit("@", 1)[-1].casefold()


def _domain_matches(domain: str, registered: Iterable[str]) -> bool:
    # Deliberately require an exact registered employer domain. Subdomains,
    # recruiter agencies and generic mail services need separate review.
    return any(domain == item.casefold() for item in registered)


def classify(subject: str, body: str) -> str:
    combined = (subject[:500] + "\n" + body[:50000]).strip()
    if _MARKETING.search(subject) or _MARKETING.search(body[:300]):
        return "MARKETING"
    if _REJECTION.search(combined):
        return "REJECTION"
    if _INTERVIEW.search(combined):
        return "INTERVIEW_INVITE"
    if _ACK.search(combined):
        return "EMPLOYER_ACK"
    return "UNKNOWN"


def _proposed_ack_status(status: str) -> str:
    if "EMPLOYER_ACK_VERIFIED" in status:
        return status
    if "EMPLOYER_ACK_PENDING" in status:
        return status.replace("EMPLOYER_ACK_PENDING", "EMPLOYER_ACK_VERIFIED", 1)
    return f"{status} / EMPLOYER_ACK_VERIFIED"


def plan_gmail_event(
    message: GmailMessage, applications: Iterable[ApplicationRef]
) -> ReconciliationPlan:
    """Plan a safe, single-match event from authenticated metadata.

    DKIM/DMARC booleans must originate from a trusted Gmail connector, never
    from a text body or a sender-supplied header. A match is not proof of an
    ATS submission or authority to send further emails.
    """
    _require_aware(message.received_at)
    if not message.message_id.strip() or not message.mailbox_key.strip():
        return ReconciliationPlan("REVIEW_REQUIRED", "MISSING_MESSAGE_IDENTITY")
    kind = classify(message.subject, message.body)
    if kind == "MARKETING":
        return ReconciliationPlan("IGNORED", "MARKETING_MESSAGE")
    if kind == "UNKNOWN":
        return ReconciliationPlan("REVIEW_REQUIRED", "UNCLASSIFIED_MESSAGE")
    if not (message.dkim_pass and message.dmarc_pass):
        return ReconciliationPlan("REVIEW_REQUIRED", "SENDER_AUTH_UNVERIFIED")
    domain = _sender_domain(message.sender_email)
    if domain is None:
        return ReconciliationPlan("REVIEW_REQUIRED", "INVALID_SENDER_ADDRESS")

    normalized_text = _normal(message.subject + " " + message.body)
    matches: list[ApplicationRef] = []
    for app in applications:
        _require_aware(app.sent_at)
        if message.received_at < app.sent_at:
            continue
        if not _domain_matches(domain, app.employer_domains):
            continue
        role_name = _normal(app.role)
        role_in_message = bool(role_name) and (
            f" {role_name} " in f" {normalized_text} "
        )
        same_rfc_thread = bool(
            app.outbound_rfc_message_id
            and message.in_reply_to
            and app.outbound_rfc_message_id.strip() == message.in_reply_to.strip()
        )
        if role_in_message or same_rfc_thread:
            matches.append(app)
    if len(matches) != 1:
        return ReconciliationPlan(
            "REVIEW_REQUIRED", "AMBIGUOUS_MATCH" if matches else "NO_EXACT_MATCH"
        )
    app = matches[0]
    proposed = _proposed_ack_status(app.status) if kind == "EMPLOYER_ACK" else app.status
    # Interview/rejection events require a separate human or trusted provider
    # decision; one email cannot make the application provider-confirmed.
    decision = "PATCH_PROPOSED" if kind == "EMPLOYER_ACK" else "REVIEW_REQUIRED"
    reason = "ACK_ONLY_NOT_COUNTED" if kind == "EMPLOYER_ACK" else "SENSITIVE_STATUS_REVIEW"
    return ReconciliationPlan(
        decision=decision,
        reason=reason,
        # The existing Neon staging schema requires UUID event IDs. UUIDv5
        # deterministically maps a mailbox-scoped Gmail ID without exposing
        # the message ID in a public repository or an event primary key.
        event_id=str(uuid5(_EVENT_NAMESPACE, f"gmail\0{message.mailbox_key}\0{message.message_id}")),
        event_type=kind,
        application_id=app.application_id,
        previous_status=app.status,
        proposed_status=proposed,
        received_at=message.received_at.astimezone(timezone.utc).isoformat(),
    )


class LocalEventLedger:
    """Test/staging-only SQLite store; never used as serverless production DB."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS mail_events (
                    event_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    proposed_status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )"""
            )

    def record(self, plan: ReconciliationPlan, now: datetime) -> str:
        """Atomically deduplicate an eligible event; never apply Excel edits."""
        _require_aware(now)
        if plan.decision != "PATCH_PROPOSED" or not all(
            (plan.event_id, plan.application_id, plan.event_type,
             plan.previous_status, plan.proposed_status, plan.received_at)
        ):
            return "NOT_RECORDED"
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("BEGIN IMMEDIATE")
            inserted = conn.execute(
                """INSERT INTO mail_events
                   (event_id, application_id, event_type, previous_status,
                    proposed_status, received_at, recorded_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO NOTHING""",
                (plan.event_id, plan.application_id, plan.event_type,
                 plan.previous_status, plan.proposed_status, plan.received_at,
                 now.astimezone(timezone.utc).isoformat()),
            ).rowcount
            if not inserted:
                existing = conn.execute(
                    """SELECT application_id, event_type, received_at
                       FROM mail_events WHERE event_id=?""", (plan.event_id,)
                ).fetchone()
                if existing != (plan.application_id, plan.event_type, plan.received_at):
                    raise ValueError("EVENT_ID_CONFLICT_REQUIRES_REVIEW")
        return "EVENT_RECORDED_PATCH_PENDING" if inserted else "ALREADY_RECORDED"

    def count(self) -> int:
        with sqlite3.connect(self.path) as conn:
            return conn.execute("SELECT COUNT(*) FROM mail_events").fetchone()[0]

    def get_event(self, event_id: str) -> dict[str, str] | None:
        """Exact post-commit readback of the private local event record."""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM mail_events WHERE event_id=?", (event_id,)
            ).fetchone()
            return dict(row) if row else None


def audit_json(plan: ReconciliationPlan, now: datetime) -> str:
    return json.dumps(plan.audit(now), sort_keys=True, ensure_ascii=False)
