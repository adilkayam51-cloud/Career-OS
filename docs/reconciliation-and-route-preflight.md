# Evidence and route preflight — staged implementation

**Status: local tests only.** These modules belong to the separately guarded
Career-OS staging gateway. They are **not** a deployed version of the existing
CareerOS production v0.4.0 service and do not authorize ATS submission.

## Data flow

1. A trusted Gmail connector supplies message ID, mailbox scope, sender address,
   verified sender-domain authentication, received timestamp, subject and body.
   The message body is never accepted as proof of sender authentication.
2. `plan_gmail_event` classifies the message and attempts a unique match against
   verified application references, requiring a registered employer domain,
   original send before incoming mail, and exact role text or matching RFC reply
   header. Independent Gmail conversations can still match.
3. A matching ordinary acknowledgment produces a **proposed** in-place status
   patch, preserving the other status markers including `NOT_COUNTED`. It does
   not infer a new submission, an interview, or ATS provider confirmation.
4. `LocalEventLedger` records a UUIDv5 mailbox-scoped Gmail event ID in
   local-test SQLite. Replayed IDs produce no duplicate. Conflicting event-ID
   reuse raises an error. The committed event is read back exactly.
5. The canonical Excel/Postgres writer remains a separate, **unimplemented**
   integration. It must use the latest Library version, optimistic concurrency,
   and a post-write exact readback. No workbook writer is hidden in these modules.

## Classification and exceptions

| Input | Outcome |
| --- | --- |
| Verified official acknowledgment with one exact application | Proposed ack-status patch; no counted application |
| Replay of the same event ID | Already recorded |
| DKIM/DMARC failure, unmatched role/domain or ambiguous applications | Review required; no patch |
| Interview invitation or rejection text | Review required; no automatic status mutation |
| Job alerts or marketing | Ignored |
| Missing timezone or malformed event identity | Fail closed |

`validate_route` independently checks live-JD evidence, mandatory skill match,
45-day deduplication, hiring geography, timezone, truthful declarations, and
route accessibility. A successful preflight yields
`READY_FOR_POLICY_REVIEW`, **not** permission to send or submit.

## Required integration and release gates

- [ ] Resolve the Master V6 versus legacy workbook V5 authorization precedence
      and preserve permanent exclusions. Do not silently replace legacy rules.
- [ ] Provide a trusted Gmail adapter that preserves authentication results and
      actual mailbox-scoped IDs; use read-only access initially.
- [ ] Implement a private transactional Postgres event adapter and confirmed
      canonical workbook writer. The existing staging event ID column is UUID.
- [ ] Test concurrent scheduler replays, version conflicts, out-of-order
      acknowledgments, and independent email threads on synthetic staging data.
- [ ] Verify the protected production API route and exact Neon target without
      assuming archived branch metadata means database failure.
- [ ] Keep provider receipt attestation and application-counting separate.
- [ ] Never store Gmail tokens, applicant CV, compensation or personal records
      in this public repository or export them to PathLattice.
- [ ] Run a compatibility review before any Vercel production deployment.

## Repeatable local verification

```bash
python3 -m unittest discover -s tests -v
```

Fixtures are synthetic, and these tests do not access Gmail, Neon, Vercel,
browser sessions or the Library.
