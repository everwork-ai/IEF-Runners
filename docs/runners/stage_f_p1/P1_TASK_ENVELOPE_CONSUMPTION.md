# Stage F P1 — TaskEnvelope Consumption (Real GitHub Read Chain)

**Stage:** F P1 — Controlled Real Runtime Validation
**Phase:** P1-F4 (Runner Dry-Run Continuation)
**Target:** everwork-ai/IEF-Runners#3
**Runtime Mode:** controlled_real_read (upstream); dry_run (runner)
**Classification:** STAGE_F_P1_DISPATCH
**Timestamp:** 2026-06-11T11:39:34+08:00

---

## 1. TaskEnvelope Consumed (JSON)

This TaskEnvelope represents the runner's view of the P1 task, derived from the adapter's real GitHub read chain.

```json
{
  "schema_version": "stage_f_p1_v1",
  "runtime_mode": "dry_run",
  "task_id": "stage_f_p1_runners_dry_run_20260611_033457",
  "trigger_id": "ief_stage_f_p1_f1_20260611_033457",
  "task_type": "runtime_validation_real_github_read",
  "source_ref": {
    "repo": "everwork-ai/IEF-Program",
    "issue_number": 11,
    "comment_id": 4676869735,
    "classification": "STAGE_F_CLOSURE_REPORT",
    "adapter_read_method": "real_github_api_read",
    "adapter_read_verified": true
  },
  "directive_comment_id": 4676932388,
  "directive_url": "https://github.com/everwork-ai/IEF-Program/issues/11#issuecomment-4676932388",
  "target_repo": "everwork-ai/IEF-Runners",
  "target_issue": 3,
  "branch": "main",
  "current_head_sha": "14a2a7d",
  "actor": "brantzh6",
  "worker_type": "deterministic_stub",
  "upstream_chain": {
    "adapter_host_event": "everwork-ai/IEF-Adapters#2 docs/adapters/stage_f_p1/P1_GITHUB_READ_HOST_EVENT.md",
    "adapter_task_envelope": "everwork-ai/IEF-Adapters#2 docs/adapters/stage_f_p1/P1_NORMALIZED_TASK_ENVELOPE.md",
    "adapter_report": "everwork-ai/IEF-Adapters#2 docs/adapters/stage_f_p1/P1_ADAPTER_REPORT.md",
    "adapter_commit": "28da9ec"
  },
  "allowed_files": [
    "docs/runners/stage_f_p1/P1_TASK_ENVELOPE_CONSUMPTION.md",
    "docs/runners/stage_f_p1/P1_RUN_EVENT_DRY_RUN.md",
    "docs/runners/stage_f_p1/P1_RUNNER_VALIDATION_REPORT.md"
  ],
  "forbidden_actions": [
    "invoke_codex",
    "invoke_claude_code",
    "invoke_qoder",
    "invoke_gemini",
    "invoke_openclaw_subprocess",
    "invoke_hermes",
    "cross_repo_writes",
    "close_issues",
    "merge_prs",
    "mutate_source_comment",
    "deploy_webhook",
    "start_polling_daemon",
    "promote_knowledge",
    "authorize_stage_g"
  ],
  "done_criteria": [
    "TaskEnvelope consumed from P1 adapter chain",
    "Deterministic stub executed (dry-run, no real backend)",
    "RunEvent produced with P1 schema version",
    "Upstream adapter evidence linked",
    "Validation report posted",
    "No real tool-runner backend invoked",
    "No cross-repo mutation"
  ],
  "dedupe_key": "stage_f_p1::runner_dry_run::everwork-ai/IEF-Runners::3::trigger:ief_stage_f_p1_f1_20260611_033457",
  "created_at": "2026-06-11T11:39:34+08:00",
  "created_by": "ief-operator",
  "auth_chain": "STAGE_F_CLOSURE_REPORT(4676869735) → P1_PLAN(4676932388) → P1_DISPATCH → ADAPTER_REAL_READ(28da9ec) → RUNNER_DRY_RUN"
}
```

## 2. Schema Validation

| Field | Required | Present | Value |
|---|---|---|---|
| `schema_version` | ✅ | ✅ | `stage_f_p1_v1` |
| `runtime_mode` | ✅ | ✅ | `dry_run` |
| `task_id` | ✅ | ✅ | `stage_f_p1_runners_dry_run_20260611_033457` |
| `trigger_id` | ✅ | ✅ | `ief_stage_f_p1_f1_20260611_033457` |
| `task_type` | ✅ | ✅ | `runtime_validation_real_github_read` |
| `source_ref` | ✅ | ✅ | comment 4676869735, adapter read verified |
| `target_repo` | ✅ | ✅ | `everwork-ai/IEF-Runners` |
| `target_issue` | ✅ | ✅ | `3` |
| `worker_type` | ✅ | ✅ | `deterministic_stub` |
| `upstream_chain` | ✅ | ✅ | 4 adapter artifact references |

**Result:** Schema valid. All required fields present.

## 3. P0 → P1 Upgrade

| Aspect | P0 (F3) | P1 (F4) |
|---|---|---|
| Schema version | `stage_f_p0_v1` | `stage_f_p1_v1` |
| source_ref | Not present | Real GitHub comment 4676869735 |
| upstream_chain | Not present | Links to adapter P1 artifacts |
| Runtime mode | `dry_run` | `dry_run` (runner unchanged) |
| Adapter source | Synthetic fixture | Real GitHub API read |
| Runner behavior | Deterministic stub | Deterministic stub (unchanged) |

**Key change:** The runner now receives a TaskEnvelope that traces back to a real GitHub read, not a synthetic fixture. The runner itself remains deterministic_stub — the upgrade is in the provenance chain, not the runner behavior.

## 4. Consumption Log

| Step | Action | Result |
|---|---|---|
| 1 | Parse TaskEnvelope | ✅ Valid schema |
| 2 | Verify upstream_chain references | ✅ All 4 adapter artifacts exist on GitHub |
| 3 | Verify source_ref comment | ✅ Comment 4676869735 exists, classification matches |
| 4 | Verify runtime_mode | ✅ `dry_run` — no real backend authorized |
| 5 | Verify forbidden_actions | ✅ 15 forbidden actions listed |
| 6 | Initialize deterministic stub | ✅ Stub ready, no backend calls |

## 5. Boundary Compliance

| Check | Result |
|---|---|
| Real tool-runner backend invoked | ❌ No |
| Cross-repo writes | ❌ No |
| Source comment mutated | ❌ No |
| Upstream adapter evidence linked | ✅ Yes |
| Runner stays deterministic_stub | ✅ Yes |
