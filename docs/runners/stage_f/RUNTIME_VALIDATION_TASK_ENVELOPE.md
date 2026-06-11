# Runtime Validation — Synthetic TaskEnvelope Fixture

**Stage:** F3 — Runtime Validation Execution
**Target:** everwork-ai/IEF-Runners#3
**Runtime Mode:** dry_run
**Classification:** STAGE_F_P0_DISPATCH
**Timestamp:** 2026-06-11T10:51:43+08:00

---

## 1. TaskEnvelope Fixture (JSON)

```json
{
  "schema_version": "stage_f_p0_v1",
  "runtime_mode": "dry_run",
  "trigger_id": "ief_stage_f_f3_runners_20260611_023000",
  "directive_comment_id": 4672992418,
  "directive_url": "https://github.com/everwork-ai/IEF-Program/issues/11#issuecomment-4672992418",
  "target_repo": "everwork-ai/IEF-Runners",
  "target_issue": 3,
  "branch": "main",
  "current_head_sha": "41bd4c8",
  "actor": "brantzh6",
  "worker_type": "deterministic_stub",
  "task_type": "DRY_RUN_STUB_VALIDATION",
  "allowed_files": [
    "docs/runners/stage_f/RUNTIME_VALIDATION_TASK_ENVELOPE.md",
    "docs/runners/stage_f/RUNTIME_VALIDATION_RUN_EVENT.md",
    "docs/runners/stage_f/RUNTIME_VALIDATION_EVIDENCE.md"
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
    "merge_prs"
  ],
  "done_criteria": [
    "TaskEnvelope fixture created",
    "Schema validated (all required fields present)",
    "Deterministic stub executed (no real tool-runner backend)",
    "RunEvent produced",
    "Evidence schema completeness verified",
    "No external tool invocations",
    "Stop/cancel enforcement verified",
    "Local cache boundary verified"
  ],
  "dedupe_key": "stage_f::runner_stub::everwork-ai/IEF-Runners::3::trigger:ief_stage_f_f3_runners_20260611_023000",
  "created_at": "2026-06-11T10:51:43+08:00",
  "created_by": "ief-operator",
  "auth_chain": "STAGE_F_RUNTIME_VALIDATION_PLAN(4672992418) -> F1_SUPPLEMENT(4675876434) -> F2_PASSED(4675923879) -> F3_EXECUTION"
}
```

## 2. Schema Validation

### 2.1 Required Fields Check

| Field | Required | Present | Value |
|---|---|---|---|
| `schema_version` | ✅ | ✅ | `stage_f_p0_v1` |
| `runtime_mode` | ✅ | ✅ | `dry_run` |
| `trigger_id` | ✅ | ✅ | `ief_stage_f_f3_runners_20260611_023000` |
| `directive_comment_id` | ✅ | ✅ | `4672992418` |
| `target_repo` | ✅ | ✅ | `everwork-ai/IEF-Runners` |
| `target_issue` | ✅ | ✅ | `3` |
| `worker_type` | ✅ | ✅ | `deterministic_stub` |

**Result:** All required fields present. Schema valid.

### 2.2 Optional Fields Present

| Field | Value |
|---|---|
| `directive_url` | `https://github.com/everwork-ai/IEF-Program/issues/11#issuecomment-4672992418` |
| `branch` | `main` |
| `current_head_sha` | `41bd4c8` |
| `actor` | `brantzh6` |
| `task_type` | `DRY_RUN_STUB_VALIDATION` |
| `allowed_files` | 3 entries |
| `forbidden_actions` | 9 entries |
| `done_criteria` | 8 entries |
| `dedupe_key` | computed |
| `auth_chain` | full chain |

### 2.3 Type Validation

| Field | Expected Type | Actual Type | Valid |
|---|---|---|---|
| `schema_version` | string | string | ✅ |
| `runtime_mode` | enum(dry_run) | string("dry_run") | ✅ |
| `trigger_id` | string | string | ✅ |
| `directive_comment_id` | integer | integer | ✅ |
| `target_repo` | string | string | ✅ |
| `target_issue` | integer | integer | ✅ |
| `worker_type` | string | string | ✅ |
| `allowed_files` | array[string] | array[string] | ✅ |
| `forbidden_actions` | array[string] | array[string] | ✅ |
| `done_criteria` | array[string] | array[string] | ✅ |

**Overall Schema Validation: PASS**

## 3. Design Notes

### 3.1 Worker Type
`deterministic_stub` — a synthetic worker that simulates task processing without invoking any real tool-runner backend. It produces predictable output (exit_code=0) and does not modify external state.

### 3.2 No Real Tool-Runner Backends
The following backends are explicitly excluded from this validation:
- ❌ Codex
- ❌ Claude Code
- ❌ Qoder
- ❌ Gemini
- ❌ OpenClaw subprocess worker
- ❌ Hermes

### 3.3 Dedupe Key
```
stage_f::runner_stub::everwork-ai/IEF-Runners::3::trigger:ief_stage_f_f3_runners_20260611_023000
```
Deterministic key derived from stage, repo, issue, and trigger ID. Prevents duplicate processing of the same validation cycle.

## 4. Boundary Compliance

| Check | Result |
|---|---|
| Real tool-runner backend invoked | ❌ No |
| Cross-repo writes | ❌ No |
| Issue close / PR merge | ❌ No |
| Schema validated | ✅ Yes |
| Dry-run only | ✅ Yes |
