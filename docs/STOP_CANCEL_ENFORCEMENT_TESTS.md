# Stop/Cancel Enforcement Tests

**Classification:** `STAGE_E_STOP_CANCEL_ENFORCEMENT_TESTS`
**Status:** DELIVERED
**Resolves:** Review Condition RC5 — Stop/cancel enforcement
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## 1. Test Overview

All tests verify that runners treat `stop_requested`, `cancelled`, `blocked`, and failure states as **hard stop points** — no execution proceeds after these signals.

Each test is a dry-run scenario executable against the deterministic stub.

---

## 2. Test Scenarios

### T-STOP-1: Stop at Dispatched Stage

| Field | Value |
|---|---|
| Preconditions | Runner in `idle` state, receives TaskEnvelope |
| Trigger | Envelope `state = stop_requested` |
| Expected RunEvent | `RunEvent(status=stopped, reason=stop_requested)` |
| Expected behavior | Runner does not enter `executing` state |
| Pass criteria | RunEvent emitted, no execution occurred |

### T-STOP-2: Cancel at Dispatched Stage

| Field | Value |
|---|---|
| Preconditions | Runner in `idle` state, receives TaskEnvelope |
| Trigger | Envelope `state = cancelled` |
| Expected RunEvent | `RunEvent(status=cancelled, reason=cancelled)` |
| Expected behavior | Runner does not enter `executing` state |
| Pass criteria | RunEvent emitted, no execution occurred |

### T-STOP-3: Blocked at Dispatched Stage

| Field | Value |
|---|---|
| Preconditions | Runner in `idle` state, receives TaskEnvelope |
| Trigger | Envelope `state = blocked` |
| Expected RunEvent | `RunEvent(status=blocked, reason_code=blocked)` |
| Expected behavior | Runner does not enter `executing` state |
| Pass criteria | RunEvent emitted, no execution occurred |

### T-STOP-4: Stop Signal During Execution

| Field | Value |
|---|---|
| Preconditions | Runner in `executing` state |
| Trigger | Stop signal received (envelope state changed to `stop_requested`) |
| Expected RunEvent | `RunEvent(status=stopped, partial_artifacts=[...])` |
| Expected behavior | Runner halts immediately, emits partial artifacts |
| Pass criteria | RunEvent emitted with `incomplete: true`, execution halted |

### T-STOP-5: Timeout During Execution

| Field | Value |
|---|---|
| Preconditions | Runner in `executing` state, `timeout_ms` set |
| Trigger | Elapsed time exceeds `timeout_ms` |
| Expected RunEvent | `RunEvent(status=timed_out, elapsed_ms=X, timeout_ms=Y)` |
| Expected behavior | Runner halts immediately |
| Pass criteria | RunEvent emitted, execution halted |

### T-STOP-6: Schema Validation Failure

| Field | Value |
|---|---|
| Preconditions | Runner in `idle` state, receives malformed TaskEnvelope |
| Trigger | Schema validation fails |
| Expected RunEvent | `RunEvent(status=failed, error_code=schema_validation_failed)` |
| Expected behavior | Runner does not proceed to `dispatched` state |
| Pass criteria | RunEvent emitted, no execution occurred |

---

## 3. Dry-Run Procedure

For each test:

1. Prepare TaskEnvelope matching the trigger condition.
2. Feed envelope to deterministic stub via operator.
3. Observe RunEvent output.
4. Verify RunEvent matches expected status and fields.
5. Verify no execution occurred (no file changes, no commits).
6. Record pass/fail with evidence.

---

## 4. Evidence Format

Each test produces:
- RunEvent JSON (actual output)
- Pass/fail determination
- Execution evidence (or lack thereof)
- Timestamp

---

## 5. Cross-Reference

- [STAGE_E_RUNNER_CONTRACT.md §2](STAGE_E_RUNNER_CONTRACT.md#2-runner-execution-lifecycle) — Lifecycle states
- [BOUNDARY_ENFORCEMENT_RULES.md](BOUNDARY_ENFORCEMENT_RULES.md) — Boundary rules
- [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md) — Event identity

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
