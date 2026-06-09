# Stage E Runner Contract

**Classification:** `STAGE_E_RUNNER_CONTRACT`
**Status:** DELIVERED
**Source:** [IEF-Runners#3 — Stage E Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)
**Contract Plan:** [comment 4646520609](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4646520609)
**Implementation Review:** [comment 4656665045](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656665045) — PASSED
**Boundary:** Contract documentation only — no code changes, no implementation, no self-approval, no cross-repo mutations.

---

## 1. Control-Plane Event Consumption

### 1.1 Event Sources

Runners consume events from **exactly one authoritative path**:

```
Program Controller / Coordinator
    → IEF-Operations ledger (TaskEnvelope creation)
        → IEF-Runners (dispatch consumption)
```

Runners MUST NOT:
- Read task state from chat history, local memory, or prior model context.
- Accept TaskEnvelopes from Adapters or Knowledge directly.
- Self-generate TaskEnvelopes or invent dispatch signals.

### 1.2 Event Types Consumed

| Event Source | Event Type | Runner Action |
|---|---|---|
| Operations ledger | `TaskEnvelope` (dispatched) | Validate, check dedupe, execute or report blocked |
| Operations ledger | `TaskEnvelope` (stop_requested) | **STOP POINT** — halt, report stopped |
| Operations ledger | `TaskEnvelope` (cancelled) | **STOP POINT** — abort, report cancelled |
| Operations ledger | `TaskEnvelope` (blocked) | **STOP POINT** — do not execute, acknowledge |
| Program/Coordinator | `STAGE_E_EXECUTION_DISPATCH` comment | Begin execution within named scope only |
| Program/Coordinator | `STAGE_E_RETRY_AUTHORIZATION` comment | Retry the named target with fresh evidence |
| Program/Coordinator | `STAGE_E_DEDUP_DECISION` comment | Mark item as consumed, do not re-execute |

### 1.3 Envelope Validation Rules

Before any execution, a Runner MUST validate:

1. **Schema conformance:** Envelope matches the current Protocol schema version (see [PROTOCOL_SCHEMA_REFERENCE.md](PROTOCOL_SCHEMA_REFERENCE.md)).
2. **Dedupe check:** Envelope dedupe key not present in local execution cache (see [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md)).
3. **Stage authorization:** A matching `STAGE_E_EXECUTION_DISPATCH` or `STAGE_E_RETRY_AUTHORIZATION` exists as a GitHub-visible comment on the target issue. No GitHub-visible authorization → no execution.
4. **Write-path check:** The dispatch comment explicitly names this repo and the allowed files. If not named, report `blocked` with reason `write_path_not_authorized`.

If any validation fails, emit a `failed` or `blocked` RunEvent with the appropriate reason code. Do not execute.

### 1.4 Attribution Preservation

Every TaskEnvelope carries an `actor` field. Runners preserve this in all downstream RunEvent emissions. The `actor` is the entity that authorized the dispatch (e.g., `ief-pm`, `ief-coordinator`, a human GitHub username). Runners do not claim authorship of the task — they report execution under the dispatching actor's attribution.

---

## 2. Runner Execution Lifecycle

### 2.1 Stage-Gated Lifecycle

Stage E introduces a **gated lifecycle** that extends the Stage D deterministic stub:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    RUNNER EXECUTION LIFECYCLE                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [1] ENVELOPE RECEIVED                                                   │
│       │                                                                  │
│       ├─ Validate schema ──FAIL──→ RunEvent(failed)                      │
│       ├─ Check dedupe ─────DUP───→ RunEvent(dedupe_hit)                  │
│       ├─ Check stage auth ──NO───→ RunEvent(blocked)                     │
│       │                                                                  │
│  [2] DISPATCHED                                                          │
│       │                                                                  │
│       ├─ Envelope state = stop_requested? ──YES──→ STOP                  │
│       ├─ Envelope state = cancelled? ───────YES──→ STOP                  │
│       ├─ Envelope state = blocked? ─────────YES──→ STOP                  │
│       │                                                                  │
│       │  RunEvent(started)                                               │
│       │                                                                  │
│  [3] EXECUTING                                                           │
│       │                                                                  │
│       ├─ Heartbeat RunEvents (if elapsed > heartbeat interval)           │
│       ├─ Timeout? ──YES──→ RunEvent(timed_out) ──→ STOP                 │
│       │                                                                  │
│  [4] COMPLETED                                                           │
│       │                                                                  │
│       │  RunEvent(completed) with artifact_refs + evidence               │
│       │                                                                  │
│  [5] REPORTED                                                            │
│       │                                                                  │
│       └─ Wait for Coordinator verification or next dispatch              │
│                                                                          │
│  STOP states:                                                            │
│    stopped   → RunEvent(stopped, reason, partial_artifacts)              │
│    cancelled → RunEvent(cancelled, reason)                               │
│    blocked   → RunEvent(blocked, reason_code)                            │
│    failed    → RunEvent(failed, error_code, error_message)               │
│    timed_out → RunEvent(timed_out, elapsed_ms, timeout_ms)               │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Lifecycle State Transitions (Runner-Side View)

| Runner Internal State | Trigger | Outbound RunEvent | Next State |
|---|---|---|---|
| `idle` | Envelope received, all validations pass | — | `dispatched` |
| `dispatched` | Envelope state = `dispatched` | `started` | `executing` |
| `dispatched` | Envelope state = `stop_requested` | `stopped` | `stopped` (terminal) |
| `dispatched` | Envelope state = `cancelled` | `cancelled` | `cancelled` (terminal) |
| `dispatched` | Envelope state = `blocked` | `blocked` | `blocked` (terminal) |
| `dispatched` | Validation failure | `failed` | `failed` (terminal) |
| `executing` | Execution succeeds | `completed` | `reported` |
| `executing` | Execution fails | `failed` | `failed` (terminal) |
| `executing` | Timeout exceeded | `timed_out` | `timed_out` (terminal) |
| `executing` | Stop signal received | `stopped` | `stopped` (terminal) |
| `reported` | Coordinator: `PASSED` | — | `idle` (task complete) |
| `reported` | Coordinator: `NEEDS_REWORK` | — | `dispatched` (retry with same envelope + fresh evidence) |

### 2.3 Stage E Execution Modes

| Mode | Authorized by | Scope |
|---|---|---|
| **Deterministic stub** | Stage D P0 (already delivered) | No external calls, controlled lifecycle only |
| **Gated real-runner** | `STAGE_E_EXECUTION_DISPATCH` with named tool-runner | One tool-runner, one envelope, one cycle |
| **Multi-runner orchestration** | NOT AUTHORIZED in Stage E | Excluded — see boundary rules |

### 2.4 Heartbeat Rules

- First heartbeat: emitted when execution exceeds 60 seconds (configurable per runner).
- Subsequent heartbeats: every 120 seconds.
- Heartbeat RunEvent includes: `envelope_id`, `runner_id`, `heartbeat_seq` (monotonic), `elapsed_ms`, `status_detail`.
- If heartbeats stop, Operations may infer runner death and escalate.

---

## 3. Evidence Reporting Format

### 3.1 RunEvent Schema (Stage E)

```
RunEvent {
  event_id:        <UUID>                    // Unique event identifier for dedupe at Operations
  envelope_id:     <TaskEnvelope.envelope_id> // Reference to the consumed envelope
  runner_id:       <string>                   // e.g., deterministic-stub, claude-code-runner
  tool_runner_type: <string>                  // e.g., deterministic_stub, claude_code
  status:          <enum>                     // started | completed | failed | stopped |
                                              // cancelled | blocked | timed_out | running
  actor:           <string>                   // Preserved from TaskEnvelope.actor
  started_at:      <ISO-8601 UTC | null>
  finished_at:     <ISO-8601 UTC | null>
  exit_code:       <number | null>
  error_code:      <string | null>            // e.g., schema_validation_failed
  error_message:   <string | null>
  artifact_refs:   <ArtifactRef[]>
  evidence_summary: <string | null>           // Human-readable: what was done, produced, observed
  heartbeat_seq:   <number | null>            // Monotonic for heartbeat events
  elapsed_ms:      <number | null>
  timeout_ms:      <number | null>
  incomplete:      <boolean>                  // true if stopped/cancelled mid-execution
  commit_evidence: <CommitEvidence | null>     // If commits were made
}
```

### 3.2 ArtifactRef Schema

```
ArtifactRef {
  artifact_id:   <string>            // Unique artifact identifier
  artifact_type: <enum>              // file | comment | commit | log | report
  location:      <string>            // URL, path, or reference
  content_hash:  <string | null>     // SHA-256 of content where applicable
  description:   <string>
  created_by:    <string>            // Preserved from TaskEnvelope.actor
}
```

### 3.3 Evidence Reporting Guarantees

1. **Every execution produces at least one RunEvent** — even failures, stops, and blocks.
2. **RunEvents are append-only** — never overwritten or deleted.
3. **Evidence ≠ acceptance** — a `completed` RunEvent is evidence of execution, not acceptance of work.
4. **Attribution preserved** — `actor` from TaskEnvelope is preserved in all RunEvents.
5. **Incomplete markers** — stopped/cancelled runs include `incomplete: true` and `partial_artifacts`.

### 3.4 Coordinator Verification Interface

After `RunEvent(completed)`, the Coordinator may:
- Inspect `artifact_refs` and `evidence_summary`
- Verify changed files against allowed files
- Run checks (lint, test, build) if applicable
- Post `STAGE_E_EXECUTION_VERIFY_RESULT`

The Runner waits in `reported` state until verification arrives.

---

## 4. Evidence ≠ Acceptance

**Critical invariant:** A `RunEvent(status=completed)` is evidence that execution occurred. It is **NOT**:
- Acceptance of the work
- Approval of the output
- Task closure
- Downstream unblock authorization

Only the Coordinator's `STAGE_E_EXECUTION_VERIFY_RESULT` with `PASSED` can promote evidence to acceptance. Runners do not self-approve, self-merge, or self-unblock.

---

## 5. Related Documents

- [PROTOCOL_SCHEMA_REFERENCE.md](PROTOCOL_SCHEMA_REFERENCE.md) — Exact Protocol schema version pinning
- [OPERATIONS_DELIVERY_PATH.md](OPERATIONS_DELIVERY_PATH.md) — Where RunEvent evidence is delivered
- [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md) — Event identity and dedupe behavior
- [LOCAL_CACHE_BOUNDARY.md](LOCAL_CACHE_BOUNDARY.md) — Non-authoritative cache boundary
- [STOP_CANCEL_ENFORCEMENT_TESTS.md](STOP_CANCEL_ENFORCEMENT_TESTS.md) — Hard stop point tests
- [BOUNDARY_ENFORCEMENT_RULES.md](BOUNDARY_ENFORCEMENT_RULES.md) — May/May-not rules

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations, no self-approvals.*
