"""Synthetic fixtures only. No real applicant identities or message IDs."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from src.evidence_reconciliation import (
    ApplicationRef, GmailMessage, LocalEventLedger,
    audit_json, classify, plan_gmail_event,
)
from src.route_preflight import validate_route


SENT = datetime(2026, 11, 15, 2, 35, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 11, 17, 4, 46, tzinfo=timezone.utc)


def original_application():
    return ApplicationRef(
        application_id="SYNTHETIC-APP-001",
        role="Business Development Manager",
        employer_domains=("example.org",),
        sent_at=SENT,
        status="EMAIL_SENT_VERIFIED / EMPLOYER_ACK_PENDING / NOT_COUNTED",
        outbound_rfc_message_id="<outbound-001@example.net>",
    )


def employer_reply():
    return GmailMessage(
        mailbox_key="private-mailbox-key",
        message_id="synthetic-msg-9001",
        sender_email="jobs@example.org",
        received_at=RECEIVED,
        subject="Thank you for applying – Business Development Manager",
        body="We will review your application and get back to you.",
        dkim_pass=True,
        dmarc_pass=True,
    )


class ReconciliationTests(unittest.TestCase):
    def test_exact_ack_plans_patch_without_changing_count(self):
        app = original_application()
        plan = plan_gmail_event(employer_reply(), [app])
        self.assertEqual(plan.decision, "PATCH_PROPOSED")
        self.assertEqual(plan.application_id, app.application_id)
        self.assertIn("EMPLOYER_ACK_VERIFIED", plan.proposed_status)
        self.assertIn("NOT_COUNTED", plan.proposed_status)
        self.assertEqual(plan.previous_status, app.status)
        self.assertEqual(UUID(plan.event_id).version, 5)

    def test_repeat_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = LocalEventLedger(str(Path(tmp) / "events.sqlite"))
            plan = plan_gmail_event(employer_reply(), [original_application()])
            self.assertEqual(ledger.record(plan, RECEIVED), "EVENT_RECORDED_PATCH_PENDING")
            self.assertEqual(ledger.record(plan, RECEIVED), "ALREADY_RECORDED")
            self.assertEqual(ledger.count(), 1)
            self.assertEqual(ledger.get_event(plan.event_id)["application_id"],
                             "SYNTHETIC-APP-001")
            self.assertIn('"Matched_ID": "SYNTHETIC-APP-001"', audit_json(plan, RECEIVED))

    def test_event_identity_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = LocalEventLedger(str(Path(tmp) / "events.sqlite"))
            plan = plan_gmail_event(employer_reply(), [original_application()])
            ledger.record(plan, RECEIVED)
            with self.assertRaisesRegex(ValueError, "EVENT_ID_CONFLICT_REQUIRES_REVIEW"):
                ledger.record(
                    replace(plan, application_id="SYNTHETIC-APP-OTHER"), RECEIVED
                )
            self.assertEqual(ledger.count(), 1)

    def test_already_reconciled_tracker_requires_no_second_status_change(self):
        app = replace(
            original_application(),
            status="EMAIL_SENT_VERIFIED / EMPLOYER_ACK_VERIFIED / NOT_COUNTED",
        )
        plan = plan_gmail_event(employer_reply(), [app])
        self.assertEqual(plan.proposed_status, app.status)

    def test_spoofed_or_unverified_sender_is_not_reconciled(self):
        for change in (
            {"sender_email": "jobs@evil-example.org"},
            {"dkim_pass": False},
            {"dmarc_pass": False},
        ):
            plan = plan_gmail_event(replace(employer_reply(), **change), [original_application()])
            self.assertEqual(plan.decision, "REVIEW_REQUIRED")

    def test_two_possible_applications_require_review(self):
        first = original_application()
        second = replace(first, application_id="SYNTHETIC-APP-002")
        plan = plan_gmail_event(employer_reply(), [first, second])
        self.assertEqual(plan.reason, "AMBIGUOUS_MATCH")
        self.assertIsNone(plan.application_id)

    def test_new_gmail_thread_can_match_employer_domain_and_role(self):
        message = replace(employer_reply(), in_reply_to=None)
        self.assertEqual(
            plan_gmail_event(message, [original_application()]).decision, "PATCH_PROPOSED"
        )

    def test_event_ids_are_scoped_to_mailbox(self):
        one = plan_gmail_event(employer_reply(), [original_application()])
        two = plan_gmail_event(
            replace(employer_reply(), mailbox_key="different-private-mailbox"),
            [original_application()],
        )
        self.assertNotEqual(one.event_id, two.event_id)

    def test_marketing_emails_are_not_application_receipts(self):
        message = replace(employer_reply(), subject="Daily job alerts and recommended jobs")
        self.assertEqual(plan_gmail_event(message, [original_application()]).decision, "IGNORED")

    def test_interview_invite_is_review_only_not_counted(self):
        message = replace(
            employer_reply(),
            subject="Interview invitation – Business Development Manager",
            body="We invite you to interview.",
        )
        plan = plan_gmail_event(message, [original_application()])
        self.assertEqual(plan.event_type, "INTERVIEW_INVITE")
        self.assertEqual(plan.decision, "REVIEW_REQUIRED")
        self.assertEqual(plan.proposed_status, original_application().status)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = LocalEventLedger(str(Path(tmp) / "events.sqlite"))
            self.assertEqual(ledger.record(plan, RECEIVED), "NOT_RECORDED")

    def test_reply_before_original_send_does_not_match(self):
        message = replace(
            employer_reply(), received_at=datetime(2026, 11, 14, tzinfo=timezone.utc)
        )
        self.assertEqual(
            plan_gmail_event(message, [original_application()]).reason, "NO_EXACT_MATCH"
        )

    def test_missing_timezone_rejected(self):
        message = replace(employer_reply(), received_at=datetime(2026, 11, 17))
        with self.assertRaises(ValueError):
            plan_gmail_event(message, [original_application()])

    def test_classification_precedence(self):
        self.assertEqual(classify("We are not moving forward", "Thank you for applying"), "REJECTION")


def good_job():
    return {
        "official_live_jd_verified": True,
        "mandatory_experience_fit": True,
        "dedupe_45d_clear": True,
        "lane": "remote",
        "hiring_countries": ["IN"],
        "requires_timezone_alignment": False,
        "unsupported_required_declaration": False,
    }


def good_email_route():
    return {
        "channel": "employer_email",
        "access": "available",
        "vacancy_specific_recipient_verified": True,
    }


class PreflightTests(unittest.TestCase):
    def test_verified_route_proceeds_only_to_policy_review(self):
        result = validate_route(good_job(), good_email_route())
        self.assertEqual(result.decision, "READY_FOR_POLICY_REVIEW")
        self.assertEqual(result.blockers, ())

    def test_missing_mandatory_ai_experience_holds(self):
        job = {**good_job(), "mandatory_experience_fit": False}
        self.assertIn("MANDATORY_EXPERIENCE_UNKNOWN_OR_UNMET",
                      validate_route(job, good_email_route()).blockers)

    def test_us_only_hiring_and_unverified_timezone_hold(self):
        job = {**good_job(), "hiring_countries": ["US"],
               "requires_timezone_alignment": True, "timezone_compatible": None}
        issues = validate_route(job, good_email_route()).blockers
        self.assertIn("INDIA_HIRING_UNVERIFIED", issues)
        self.assertIn("TIMEZONE_REQUIREMENT_UNVERIFIED_OR_UNMET", issues)

    def test_protected_ats_route_holds(self):
        route = {"channel": "ats", "access": "blocked", "authenticated_session": False}
        self.assertEqual(validate_route(good_job(), route).decision, "HOLD")
        self.assertIn("ROUTE_BLOCKED", validate_route(good_job(), route).blockers)

    def test_missing_recruitment_email_holds(self):
        route = {**good_email_route(), "vacancy_specific_recipient_verified": None}
        self.assertIn("RECIPIENT_UNVERIFIED", validate_route(good_job(), route).blockers)

    def test_unsupported_form_declaration_holds(self):
        job = {**good_job(), "unsupported_required_declaration": True}
        self.assertIn("UNSUPPORTED_DECLARATION",
                      validate_route(job, good_email_route()).blockers)

    def test_unknown_dedupe_state_holds(self):
        job = {**good_job(), "dedupe_45d_clear": None}
        self.assertIn("DUPLICATE_CHECK_NOT_CLEAR",
                      validate_route(job, good_email_route()).blockers)


if __name__ == "__main__":
    unittest.main()
