# Stage C Open Questions — Resolved

**Classification:** `STAGE_C_OPEN_QUESTIONS_RESOLVED`
**Status:** DELIVERED
**Source:** [IEF-Runners#3 — Stage E Contract Plan (comment 4646520609)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4646520609)
**Implementation Plan:** [comment 4656179869](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## Q1: Protocol Schema Source

**Question:** Where do runners get Protocol schemas for validation?

**Answer:** Runners get Protocol schemas from `everwork-ai/IEF-Protocol` repository, `schemas/` directory. The exact version is pinned to the commit SHA referenced in the Operations dispatch comment. If no SHA is referenced, runners use Protocol `main` HEAD at time of dispatch.

**Authoritative source:** [PROTOCOL_SCHEMA_REFERENCE.md](PROTOCOL_SCHEMA_REFERENCE.md)

**Resolved by:** Review Condition RC1

---

## Q2: Operations Delivery Path

**Question:** Where do runners deliver RunEvent evidence?

**Answer:** Runners deliver RunEvent evidence via GitHub-visible comment on the target issue/PR, posted by ief-operator. The authoritative destination is the Operations ledger (`everwork-ai/IEF-Operations`). Runner-local logs at `~/.ief-runner/exec.log` are non-authoritative, rebuildable, and subordinate to Operations.

**Authoritative source:** [OPERATIONS_DELIVERY_PATH.md](OPERATIONS_DELIVERY_PATH.md)

**Resolved by:** Review Condition RC2

---

## Q3: Dedupe and Event Identity

**Question:** How do runners handle dedupe and event identity?

**Answer:** Event ID is UUID v4, assigned by Operations, treated as opaque by runners. Dedupe key format: `stage_e::<classification>::<repo>::<issue_or_pr>::<target>::<canonical_comment_or_commit>`. Runners check dedupe key against local cache before execution. Duplicate RunEvent reports are safe — Operations deduplicates by `event_id`. Consumed envelopes are never re-executed.

**Authoritative source:** [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md)

**Resolved by:** Review Condition RC3

---

## Q4: Local Cache Boundary

**Question:** What is the boundary for runner-local execution cache?

**Answer:** Local cache purpose is crash recovery and idempotent restart only. Cache is non-authoritative (Operations ledger is truth), rebuildable (can be fully rebuilt from Operations), subordinate (cache entries subordinate to Operations), and must not become a hidden task database. Cache format: append-only log at `~/.ief-runner/exec.log` with fields `event_id | envelope_id | status | timestamp`.

**Authoritative source:** [LOCAL_CACHE_BOUNDARY.md](LOCAL_CACHE_BOUNDARY.md)

**Resolved by:** Review Condition RC4

---

## Q5: Stop/Cancel Enforcement

**Question:** How are stop/cancel/blocked states enforced as hard stops?

**Answer:** Six test scenarios verify hard stop points: T-STOP-1 (stop at dispatched), T-STOP-2 (cancel at dispatched), T-STOP-3 (blocked at dispatched), T-STOP-4 (stop during executing with partial artifacts), T-STOP-5 (timeout during executing), T-STOP-6 (schema validation failure at envelope received). Each test produces a deterministic pass/fail result.

**Authoritative source:** [STOP_CANCEL_ENFORCEMENT_TESTS.md](STOP_CANCEL_ENFORCEMENT_TESTS.md)

**Resolved by:** Review Condition RC5

---

## Q6: Downstream Unblock

**Question:** Can runner completion reports unblock downstream targets?

**Answer:** No. Runner completion reports are evidence only. Runners do not close tasks, approve work, merge PRs, or unblock downstream targets. Only the Coordinator's `STAGE_E_EXECUTION_VERIFY_RESULT` with `PASSED` can promote evidence to acceptance and unblock downstream targets. Evidence ≠ acceptance.

**Authoritative source:** [BOUNDARY_ENFORCEMENT_RULES.md](BOUNDARY_ENFORCEMENT_RULES.md) §5

**Resolved by:** Review Condition RC6

---

## Summary

All 6 Stage C open questions are resolved with explicit, traceable answers. Each answer references an authoritative document delivered in this Stage E execution cycle.

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
