# Local Cache Boundary

**Classification:** `STAGE_E_LOCAL_CACHE_BOUNDARY`
**Status:** DELIVERED
**Resolves:** Review Condition RC4 — Local cache boundary
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## 1. Cache Purpose

| Field | Value |
|---|---|
| Purpose | Crash recovery and idempotent restart only |
| Authoritative? | **No** — Operations ledger is authoritative |
| Rebuildable? | **Yes** — cache can be fully rebuilt from Operations ledger |
| Subordinate? | **Yes** — cache entries subordinate to Operations ledger |
| Hidden task DB? | **No** — cache must not accumulate task state |

---

## 2. Cache Format

| Field | Value |
|---|---|
| Location | `~/.ief-runner/exec.log` |
| Format | Append-only log |
| Fields | `event_id | envelope_id | status | timestamp` |
| Example | `evt_abc123 | env_xyz789 | completed | 2026-06-09T06:15:00Z` |

---

## 3. Cache Invariants

1. Cache is append-only (no updates, no deletes).
2. Cache is never the source of truth.
3. If cache conflicts with Operations ledger, Operations wins.
4. Cache may be deleted and rebuilt at any time.
5. Cache entries expire when Operations ledger state changes.

---

## 4. Rebuild Procedure

If local cache is lost or corrupted:

1. Delete `~/.ief-runner/exec.log`.
2. Query Operations ledger for all RunEvents where `runner_id` matches this runner.
3. Rebuild cache from Operations ledger entries.
4. Verify dedupe keys match Operations ledger.

---

## 5. Cache Validation

| Check | Method | Action on failure |
|---|---|---|
| Cache file exists | Filesystem check | Create empty cache |
| Cache is append-only | Log integrity check | Rebuild from Operations |
| Cache entries match Operations | Cross-reference check | Rebuild from Operations |
| No duplicate dedupe keys | Uniqueness check | Rebuild from Operations |

---

## 6. Cross-Reference

- [OPERATIONS_DELIVERY_PATH.md](OPERATIONS_DELIVERY_PATH.md) — Where RunEvents are delivered
- [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md) — Dedupe key format
- [STAGE_E_RUNNER_CONTRACT.md §1.3](STAGE_E_RUNNER_CONTRACT.md#13-envelope-validation-rules) — Validation rule 2

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
