# Claude Worker P1 Hardening Packet

## Task

- `task_id`: `CLAUDE-WORKER-P1-HARDENING-2026-04-08`
- `title`: `Claude worker hardening follow-up`
- `task_type`: `coding`
- `priority`: `P1`

## Why This Task Exists

The Claude worker runtime already passed the P0 integration bar, but the controller has identified four bounded hardening gaps that should be closed without widening scope or changing controller/runtime ownership.

## Goal

Harden the existing local Claude worker runtime along the four approved axes below, while preserving current semantics and durable artifact behavior.

## Scope

Only these four areas are in scope:

1. live hang-proof hardening
2. detached run / supervisor seed
3. CLI end-to-end integration tests
4. result protocol alignment with current harness

## Allowed To Change

- `services/api/claude_worker/worker.py`
- `services/api/tests/test_claude_worker.py`
- any minimal helper files required strictly for the above two paths

Do not expand the write set unless the current patch cannot be completed otherwise.

## Allowed To Read

- `services/api/claude_worker/worker.py`
- `services/api/tests/test_claude_worker.py`
- `docs/CLAUDE_CODE_RUNTIME_P0_INTEGRATION_PLAN.md`
- `docs/IKE_CLAUDE_WORKER_MCP_FEASIBILITY_2026-04-07.md`
- `docs/IKE_RUNTIME_V0_R1-J_PHASE_JUDGMENT_2026-04-08.md`
- `docs/IKE_RUNTIME_V0_PACKET_R1-J1_CODING_BRIEF.md`

## Constraints

- Do not add controller behavior.
- Do not turn the worker into a daemon or a general orchestrator.
- Do not introduce new runtime truth semantics.
- Do not broaden into R1-J1 DB-backed runtime stabilization work.
- Keep the existing durable run artifact contract intact unless a minimal harness mapping change is required.
- Preserve truthful failure modes; do not fake hang-proof or fake durability.
- Keep patches minimal and additive.

## Detailed Objectives

### 1. Live Hang-Proof Hardening

- Improve coverage beyond the existing fake-process timeout path.
- Prefer a test or validation path that exercises a real hanging subprocess as close to actual Claude invocation as the local environment allows.
- Preserve auditability for `communicate(timeout=...)`, `terminate()`, `kill()`, and finalization.

### 2. Detached Run / Supervisor Seed

- Make the current limitation explicit: cross-process wait is not a complete solution yet.
- Introduce or document a bounded run ownership / resume / fetch contract if needed.
- Define a detached wait strategy without implementing a full daemon.

### 3. CLI End-to-End Integration Tests

- Add a black-box test path that invokes `python -m claude_worker start`, `fetch`, and `abort` end-to-end.
- The test should verify the actual CLI surface, not only parser construction.

### 4. Result Protocol Alignment

- Make coding/review outputs easier to map back into the current harness protocol.
- Prefer direct compatibility with `.runtime/delegation/results/*.json` style outputs and the existing controller audit chain.
- Keep the result schema truthful and small.

## Expected Output

Return exactly these fields:

1. `summary`
2. `files_changed`
3. `validation_run`
4. `remaining_gaps`
5. `recommendation`
6. whether `P0 hardened enough for routine coding/review lane` has been reached

## Validation

At minimum, run the narrow worker tests that cover the changed paths and record exact commands and results.

If a live hang-proof test requires a longer runtime or a platform-specific path, document that explicitly instead of widening scope.

## Stop Conditions

Stop and report if:

- the work needs controller-level redesign
- the work requires widening beyond the four approved categories
- a true end-to-end hang-proof or detached supervision proof is not possible in the current environment
- a change would break the durable artifact contract or fake runtime truth

## Return Contract

The final report must be controller-readable and auditable.

