# IKE Claude Worker MCP Feasibility 2026-04-07

## Summary

Using local Claude Code as a coding assistant behind a small MCP worker gateway
is feasible and worth pursuing.

It is a good fit for the current project because:

- it keeps Codex as controller
- it keeps the coding lane delegated
- it reduces dependence on local OpenClaw transport correctness
- it gives a stronger implementation worker for bounded coding tasks

But it is not a drop-in replacement for the current delegation stack yet.

## Direct Findings

### 1. The supplied package is directionally correct

Artifact inspected:

- `C:\Users\jiuyou\Downloads\cc-worker-mcp-complete-package.zip`

The package is not a leaked-source fork plan.
It is a wrapper around the standard Claude CLI / future Agent SDK.

Its intended control model matches current project method:

- Codex = controller
- Claude = worker
- small high-level tool surface
- durable run artifacts on disk

This is strongly aligned with our current harness principles.

### 2. The package shape is usable as a worker gateway

Observed tool surface:

- `cc_delegate_coding_task`
- `cc_wait_for_run`
- `cc_continue_run`
- `cc_abort_run`
- `cc_fetch_artifacts`

Observed artifact model:

- `runs/<run-id>/meta.json`
- `runs/<run-id>/events.ndjson`
- `runs/<run-id>/final.json`
- `runs/<run-id>/summary.md`
- `runs/<run-id>/patch.diff`

This is compatible with the project need for:

- durable result recovery
- review before acceptance
- auditable run history

### 3. The current local machine is not ready yet

Direct local checks:

- `node` exists
- `npm` exists
- `claude` is not currently on `PATH`

So the main blocker is not architecture.
It is environment readiness.

Current status:

- package feasibility: `yes`
- local execution readiness: `no`

### 4. This improves stability, but does not make the lane fully offline

Important correction:

This path removes dependence on:

- OpenClaw alias drift
- OpenClaw ACP transport instability
- local reviewer/coder route confusion

But it still depends on:

- Claude CLI availability
- Claude authentication
- Anthropic runtime availability

So this is more stable than the current OpenClaw lane for coding work, but it
is not a no-network local model lane.

## Why This Is Valuable

This path is valuable for two reasons:

1. stronger coding worker quality
2. simpler controller-to-worker contract

The current OpenClaw stack has shown recurrent operational fragility in:

- alias semantics
- provider routing
- auth profile drift
- ACP session recovery

A narrow Claude worker MCP wrapper avoids much of that complexity by reducing
the lane to:

- start job
- wait
- fetch artifacts
- review

That is closer to the project's preferred packetized execution model.

## Gaps Between This Package and Current Project Needs

The package is promising but still V0.

### Gap 1. Local Claude runtime is missing

Current machine does not have `claude` on `PATH`.

Without this, the worker cannot run.

### Gap 2. Result schema does not yet match project harness contract

Current worker final schema:

- `root_cause`
- `changed_files`
- `summary`
- `risks`
- `next_steps`

Current project coding result expectation is closer to:

- `summary`
- `files_changed`
- `why_this_solution`
- `validation_run`
- `known_risks`
- `recommendation`

So an adapter layer is required before this can become a first-class coding
lane inside the current harness.

### Gap 3. Continue/resume is not finished

`cc_continue_run` is scaffolded but not actually implemented.

That is acceptable for a first coding lane proof, but not for long-running
repair loops yet.

### Gap 4. Worktree isolation is not real yet

The package documents:

- `worktree` mode falls back to `same-dir`

For our project, first cut can tolerate same-dir execution if the worker is
used only for tightly bounded patches.

But later hardening should add true isolated worktrees.

### Gap 5. Validation and recommendation are too weak for direct acceptance

The package currently optimizes for run completion and artifact capture.

Our harness also needs:

- explicit validation commands
- explicit recommendation
- clearer stop/blocker reporting

So the worker should remain a coding lane only.
Controller review remains mandatory.

Current stability note:

- the timeout path has moved from "no coverage" to real subprocess hang-path coverage, with fake-process kill fallback still covered
- this is usable and worth continuing to harden
- it still does **not** replace runtime truth core because it is not yet a live Claude end-to-end hung-process proof

## Recommended Integration Model

Do not replace the whole current delegation stack immediately.

Use this as a new bounded coding lane:

- `codex-controller`
  - owns planning, packeting, review, acceptance
- `claude-worker-mcp`
  - owns bounded coding execution
- existing review/evolution lanes
  - stay separate for now

Recommended first role:

- primary coding lane for bounded backend/runtime packets

Not recommended yet:

- architecture lane
- review lane
- evolution lane
- long-running autonomous program manager

## Recommended Phases

### Phase A. Local feasibility proof

Goal:

- prove the package can start, run, and return a structured result on this
  machine

Required prerequisites:

- install Claude CLI
- verify auth works
- build the package
- run against a toy repo

Success criteria:

- one run completes end-to-end
- artifacts are written durably
- Codex can poll and fetch the result

### Phase B. Harness adapter

Goal:

- make Claude worker results compatible with current project contract

Required work:

- map worker schema to project coding-result schema
- define acceptance/review rules
- define task-packet bridge

Success criteria:

- one real project packet runs through the Claude worker lane
- controller can review it using existing project standards

### Phase C. Runtime lane adoption

Goal:

- use Claude worker as an additional stable coding lane for real project work

Suggested first real target:

- one narrow `IKE Runtime v0` hardening packet

Not recommended first target:

- broad UI feature work
- multi-packet orchestration
- review/evolution responsibilities

## Controller Judgment

The correct judgment is:

- `feasible`
- `worth pursuing`
- `not ready as a full replacement`

Most accurate short conclusion:

This should be added as a new local coding lane, not treated as a full
substitute for all current OpenClaw roles.

## Next Best Milestone

Create a dedicated proof track:

- `Claude Worker MCP P0`

with only these tasks:

1. install/verify local Claude CLI
2. build the worker package
3. run one toy coding task
4. adapt final result schema to current project harness expectations
5. decide whether to adopt it as:
   - `claude-worker-coder`
   - or keep it as experimental

## Follow-Up Packet

- [D:\code\MyAttention\docs\IKE_CLAUDE_WORKER_P1_HARDENING_PACKET_2026-04-08.md](/D:/code/MyAttention/docs/IKE_CLAUDE_WORKER_P1_HARDENING_PACKET_2026-04-08.md)
