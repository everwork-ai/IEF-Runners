# Protocol Schema Reference

**Classification:** `STAGE_E_PROTOCOL_SCHEMA_REFERENCE`
**Status:** DELIVERED
**Resolves:** Review Condition RC1 — Protocol schema version reference
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## 1. Schema Source

| Field | Value |
|---|---|
| Source repository | `everwork-ai/IEF-Protocol` |
| Schema directory | `schemas/` |
| Version pinning rule | Commit SHA referenced in the Operations dispatch comment. If no SHA referenced, Protocol `main` HEAD at time of dispatch. |
| No floating references | All schema validation must use the pinned commit SHA. |

---

## 2. Referenced Schemas

### 2.1 TaskEnvelope

```json
{
  "envelope_id": "<uuid-v4>",
  "created_at": "<ISO-8601 UTC>",
  "updated_at": "<ISO-8601 UTC>",
  "state": "<dispatched | stop_requested | cancelled | blocked>",
  "task_type": "<string>",
  "target_repo": "<owner/repo>",
  "target_issue": "<number>",
  "target_pr": "<number | null>",
  "branch": "<string>",
  "allowed_files": ["<path>"],
  "forbidden_actions": ["<action>"],
  "actor": "<github-login | system-id>",
  "priority": "<normal | urgent | blocker>",
  "timeout_ms": "<number | null>",
  "heartbeat_interval_ms": "<number | null>",
  "context": {
    "directive_comment_id": "<string | null>",
    "directive_url": "<string | null>",
    "ledger_refs": ["<ledger-entry-id>"]
  }
}
```

**Validation rules:**
- `envelope_id`: UUID v4, required
- `state`: enum, required
- `target_repo`: required, must match dispatch target
- `allowed_files`: required, explicit list
- `actor`: required, preserved in RunEvents

### 2.2 RunEvent

See [STAGE_E_RUNNER_CONTRACT.md §3.1](STAGE_E_RUNNER_CONTRACT.md#31-runevent-schema-stage-e) for full schema.

**Validation rules:**
- `event_id`: UUID v4, required, assigned by Operations
- `envelope_id`: required, references TaskEnvelope
- `status`: enum, required
- `actor`: required, preserved from TaskEnvelope
- `artifact_refs`: array, may be empty

### 2.3 ArtifactRef

See [STAGE_E_RUNNER_CONTRACT.md §3.2](STAGE_E_RUNNER_CONTRACT.md#32-artifactref-schema) for full schema.

**Validation rules:**
- `artifact_id`: required, unique within RunEvent
- `artifact_type`: enum, required
- `location`: required
- `created_by`: required, preserved from TaskEnvelope.actor

---

## 3. Schema Version Pinning

### 3.1 Pinning Procedure

1. When Operations dispatches a TaskEnvelope, the dispatch comment includes a reference to the Protocol commit SHA.
2. Runners extract this SHA from the dispatch comment.
3. Runners validate the TaskEnvelope schema against the schemas at that exact commit.
4. If no SHA is referenced, runners use Protocol `main` HEAD at time of dispatch.

### 3.2 Example Dispatch Comment

```
## STAGE_E_EXECUTION_DISPATCH

- Target: everwork-ai/IEF-Runners#3
- Envelope: envelope_abc123
- Protocol schema: everwork-ai/IEF-Protocol@abc123def456
- Allowed files: docs/**, runners/deterministic/**
- Forbidden actions: code changes, cross-repo mutations
```

### 3.3 Schema Validation Failure

If schema validation fails:
- Runner emits `RunEvent(status=failed, error_code=schema_validation_failed)`
- Runner does not proceed to execution
- Runner logs validation error details

---

## 4. Schema Evolution

- Protocol schemas are versioned by commit SHA.
- Runners must not assume schema stability.
- Each dispatch specifies the exact schema version.
- Runners validate per-dispatch, not globally.

---

## 5. Cross-Reference

- [IEF-Protocol repository](https://github.com/everwork-ai/IEF-Protocol)
- [DEDUPE_EVENT_IDENTITY.md](DEDUPE_EVENT_IDENTITY.md) — How runners dedupe envelopes
- [STAGE_E_RUNNER_CONTRACT.md §1.3](STAGE_E_RUNNER_CONTRACT.md#13-envelope-validation-rules) — Validation procedure

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
