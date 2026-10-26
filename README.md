# CareerOS guarded execution gateway (separate from existing production v0.4.0)

This repository contains an **approval/receipt staging service**, not a restored copy of the deployed CareerOS v0.4.0 application. Its full production `src/main.py` was not available through the Vercel download API, which truncated the source. **Do not link or deploy this project over the existing CareerOS production service without a compatibility review.** No private candidate material, CVs, compensation details or API tokens belong in this public repository.

## Capabilities

- Canonical employer-job identity, eligibility policy and 45-day previous-application guardrails.
- An exact job + CV hash + form answer + terms digest approval packet.
- Independent, externally provisioned approval-verifier and receipt-verifier credentials.
- Atomic authorization of a **single** attempt per candidate + employer + provider job ID. Retrying unknown external submissions is blocked.
- Independent receipt attestation with actual submitted time, IST daily scorecard, and immutable audit log.
- No endpoint performs employer ATS submission or pretends a queued packet is a provider-confirmed application.
- Production PostgreSQL requires `DATABASE_URL`; local SQLite is **explicitly test-only**, not suitable for serverless production.

## Local installation and test

```bash
python -m pip install -r requirements.txt
python -m pip install pytest httpx
pytest -q
```

To use the **local test database only** (never expose this configuration publicly):

```bash
export CAREEROS_ALLOW_LOCAL_DB=1
export CAREEROS_TEST_DB_PATH=/tmp/careeros_approval_staging.sqlite3
export CAREEROS_API_KEY='replace-with-a-random-secret-of-at-least-32-characters'
export CAREEROS_APPROVAL_VERIFIER_KEY='another-independent-random-secret-at-least-32-chars'
export CAREEROS_RECEIPT_VERIFIER_KEY='third-independent-random-secret-at-least-32-chars'
uvicorn src.main:app --host 127.0.0.1 --port 8100
```

Private Neon `schema.sql` is separately testable on a temporary Neon branch. A real production adapter and authenticated proof-of-approval UI must be validated before activating the service. Do not expose an approval-verifier key to a client browser. Work/TinyFish submission is a separate, consented browser workflow; the HTTP authorization API cannot by itself verify an employer receipt.

## Workflow

`POST /v1/jobs` → externally confirmed job evidence → `POST /v1/approvals` with **two trusted backend credentials** → `POST /v1/attempts/authorize` → independent authorized browser/ATS work → `POST /v1/receipts/attest` with an independent trusted verifier → `GET /v1/scorecard/{candidate}` and `/v1/audit/{job_key}`.

If browser execution yields `UNKNOWN`, call `/v1/attempts/unknown` and reconcile employer Applied state; never retry blindly. The `APPROVED`/`AUTHORIZED` responses are **not provider receipts**. Generic strings or self-asserted flags do not establish independent human consent or evidence verification; the external approval/receipt attesters must check them before invoking the protected endpoints.

## Deliberate release gates

1. Run unit/integration tests against local staging; validate Neon schema on a temporary branch.
2. Implement and test a real Postgres adapter for this gateway; current default remains fail-closed when Neon backend not wired.
3. Integrate an identity-verified approval UI and ATS verifier; authorize only exact unchanged packets.
4. Authenticate TinyFish browser profiles through its separate user login flow. No CAPTCHA/OTP bypass and no unapproved form submission.
5. Prove one provider-confirmed employer application and durable readback. Only then consider production and 10+10 scaling.

## Credentials / PII

Only random keys in a private environment store, never in GitHub or client JS. All applicant data remains private in CareerOS; PathLattice carries public signals only. Keep `CAREEROS_ALLOW_LOCAL_DB` unset in Vercel production.