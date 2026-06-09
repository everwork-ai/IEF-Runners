# Dedupe and Event Identity

**Classification:** `STAGE_E_DEDUPE_EVENT_IDENTITY`
**Status:** DELIVERED
**Resolves:** Review Condition RC3 — Dedupe and event identity
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## 1. Event ID

| Field | Value |
|---|---|
| Format | UUID v4 |
| Assigned by | Operations (at RunEvent creation) |
| Treated by runners as | Opaque identifier |
| Uniqueness | Globally unique across all RunEvents |

---

## 2. Dedupe Key Format

```
stage_e::<classification>::<repo>::<issue_or_pr>::<target>::<canonical_comment_or_commit>
```

### 2.1 Component Definitions

| Component | Description | Example |
|---|---|---|
| `classification` | Stage E event classification | `STAGE_E_EXECUTION_DISPATCH` |
| `repo` | Target repository | `everwork-ai/IEF-Runners` |
| `issue_or_pr` | Issue or PR number | `3` |
| `target` | Named target (runner, file, etc.) | `deterministic-stub` |
| `canonical_comment_or_commit` | GitHub comment ID or commit SHA | `comment_4646349295` |

### 2.2 Example Dedupe Keys

| Event Type | Dedupe Key |
|---|---|
| Execution dispatch | `stage_e::STAGE_E_EXECUTION_DISPATCH::everwork-ai/IEF-Runners::3::deterministic-stub::comment_4646349295` |
| Retry authorization | `stage_e::STAGE_E_RETRY_AUTHORIZATION::everwork-ai/IEF-Runners::3::deterministic-stub::comment_4650000000` |
| Dedup decision | `stage_e::STAGE_E_DEDUP_DECISION::everwork-ai/IEF-Runners::3::deterministic-stub::comment_4650000001` |

---

## 3. Dedupe Check Procedure

1. Before execution, runner extracts dedupe key from TaskEnvelope or dispatch comment.
2. Runner checks dedupe key against local execution cache (`~/.ief-runner/exec.log`).
3. If key is present:
   - Runner returns cached result (idempotent).
   - Runner emits `RunEvent(dedupe_hit)` with reference to original execution.
   - Runner does not re-execute.
4. If key is not present:
   - Runner proceeds with execution.
   - Runner records dedupe key in local cache upon completion.

---

## 4. At-Least-Once Delivery Safety

| Scenario | Behavior |
|---|---|
| Duplicate RunEvent reports | Safe — Operations deduplicates by `event_id` before ledger insertion |
| Duplicate TaskEnvelope dispatch | Runner checks dedupe key, returns cached result |
| Network retry | Safe — Operations treats duplicate `event_id` as idempotent |

---

## 5. No Duplicate Execution

**Critical invariant:** Consumed envelopes are never re-executed.

1. Runner checks dedupe key before execution.
2. If key exists in cache, runner does not execute.
3. Runner emits `RunEvent(dedupe_hit)` to acknowledge receipt without re-execution.
4. Operations logs dedupe hit for audit.

---

## 6. Cross-Reference

- [STAGE_E_RUNNER_CONTRACT.md §1.3](STAGE_E_RUNNER_CONTRACT.md#13-envelope-validation-rules) — Validation rule 2
- [LOCAL_CACHE_BOUNDARY.md](LOCAL_CACHE_BOUNDARY.md) — Cache behavior
- [PROTOCOL_SCHEMA_REFERENCE.md](PROTOCOL_SCHEMA_REFERENCE.md) — Schema version pinning

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
