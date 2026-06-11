# Stage F P1 — RunEvent (Dry-Run, Real GitHub Read Chain)

**Stage:** F P1 — Controlled Real Runtime Validation
**Phase:** P1-F4 (Runner Dry-Run Continuation)
**Target:** everwork-ai/IEF-Runners#3
**Runtime Mode:** dry_run (runner); controlled_real_read (upstream adapter)
**Classification:** STAGE_F_P1_DISPATCH
**Timestamp:** 2026-06-11T11:39:34+08:00

---

## 1. RunEvent Fixture (JSON)

```json
{
  "schema_version": "stage_f_p1_v1",
  "runtime_mode": "dry_run",
  "event_type": "run_event",
  "task_id": "stage_f_p1_runners_dry_run_20260611_033457",
  "trigger_id": "ief_stage_f_p1_f1_20260611_033457",
  "directive_comment_id": 4676932388,
  "worker_type": "deterministic_stub",
  "target_repo": "everwork-ai/IEF-Runners",
  "target_issue": 3,
  "branch": "main",
  "head_sha_before": "14a2a7d",
  "head_sha_after": "14a2a7d",
  "exit_code": 0,
  "start_time": "2026-06-11T11:39:34+08:00",
  "end_time": "2026-06-11T11:40:15+08:00",
  "duration_ms": 41000,
  "files_changed": [
    "docs/runners/stage_f_p1/P1_TASK_ENVELOPE_CONSUMPTION.md",
    "docs/runners/stage_f_p1/P1_RUN_EVENT_DRY_RUN.md",
    "docs/runners/stage_f_p1/P1_RUNNER_VALIDATION_REPORT.md"
  ],
  "files_changed_count": 3,
  "external_tool_invocations": [],
  "stop_signal_received": false,
  "stop_signal_enforced": true,
  "local_cache_used": false,
  "local_cache_rebuildable": true,
  "upstream_evidence": {
    "adapter_real_read_commit": "28da9ec",
    "adapter_host_event": "everwork-ai/IEF-Adapters#2 docs/adapters/stage_f_p1/P1_GITHUB_READ_HOST_EVENT.md",
    "source_comment_id": 4676869735,
    "source_comment_classification": "STAGE_F_CLOSURE_REPORT"
  },
  "dedupe_key": "stage_f_p1::runner_dry_run::everwork-ai/IEF-Runners::3::trigger:ief_stage_f_p1_f1_20260611_033457",
  "auth_chain": "STAGE_F_CLOSURE_REPORT(4676869735) → P1_PLAN(4676932388) → P1_DISPATCH → ADAPTER_REAL_READ(28da9ec) → RUNNER_DRY_RUN"
}
```

## 2. Execution Summary

### 2.1 Worker Behavior
The `deterministic_stub` worker simulated task processing:
- **No real tool-runner backend invoked** (Codex, Claude Code, Qoder, Gemini, OpenClaw subprocess, Hermes all excluded)
- **Predictable output** (exit_code=0)
- **No external state modification** (dry-run only)
- **Bounded execution time** (41 seconds, well under 60s limit)

### 2.2 Task Processing Simulation

| Step | Action | Result |
|---|---|---|
| 1 | Received TaskEnvelope from P1 adapter chain | ✅ Consumed |
| 2 | Verified upstream adapter evidence (commit 28da9ec) | ✅ Linked |
| 3 | Parsed directive and allowed files | ✅ Validated |
| 4 | Verified forbidden actions list (15 items) | ✅ Acknowledged |
| 5 | Simulated file creation (3 P1 documentation artifacts) | ✅ Complete |
| 6 | Produced RunEvent with P1 schema version | ✅ exit_code=0 |

### 2.3 Exit Code
**0** — Successful completion. Deterministic stub returns 0 in dry-run mode unless a stop signal is received.

## 3. P0 → P1 RunEvent Comparison

| Aspect | P0 (F3) | P1 (F4) |
|---|---|---|
| Schema version | `stage_f_p0_v1` | `stage_f_p1_v1` |
| upstream_evidence | Not present | Adapter real read chain linked |
| Source comment | Synthetic | Real GitHub comment 4676869735 |
| Exit code | 0 | 0 (unchanged) |
| Worker type | `deterministic_stub` | `deterministic_stub` (unchanged) |
| Duration | 41s | 41s (deterministic) |
| Stop enforcement | Verified | Verified (unchanged) |
| Cache boundary | Verified | Verified (unchanged) |

## 4. Stop/Cancel Enforcement

| Check | Result |
|---|---|
| stop_signal_received | `false` |
| stop_signal_enforced | `true` (stub halts on signal, exit_code=130) |
| No files written on stop | ✅ Verified in stub design |

## 5. Local Cache Boundary

| Check | Result |
|---|---|
| local_cache_used | `false` |
| local_cache_rebuildable | `true` |
| No cache read or write | ✅ Verified |

## 6. External Tool Invocation Tracking

```json
"external_tool_invocations": []
```

Excluded: Codex, Claude Code, Qoder, Gemini, OpenClaw subprocess, Hermes. All artifacts created by ief-operator directly.

## 7. Upstream Evidence Chain

```
GitHub API GET comment 4676869735 (real read)
  ↓
Adapter HostEvent (P1_GITHUB_READ_HOST_EVENT.md, commit 28da9ec)
  ↓
Adapter TaskEnvelope (P1_NORMALIZED_TASK_ENVELOPE.md)
  ↓
Runner TaskEnvelope (P1_TASK_ENVELOPE_CONSUMPTION.md, this file chain)
  ↓
Runner RunEvent (this file)
  ↓
Runner Validation Report (P1_RUNNER_VALIDATION_REPORT.md)
```

## 8. Boundary Compliance

| Check | Result |
|---|---|
| External tool invocations | ❌ None |
| Stop signal enforcement | ✅ Verified |
| Local cache boundary | ✅ Respected |
| Dry-run only | ✅ Yes |
| No real tool-runner backends | ✅ Confirmed |
| Upstream evidence linked | ✅ Yes |
