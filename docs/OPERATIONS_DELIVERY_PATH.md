# Operations Delivery Path

**Classification:** `STAGE_E_OPERATIONS_DELIVERY_PATH`
**Status:** DELIVERED
**Resolves:** Review Condition RC2 — Operations delivery path
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)

---

## 1. Authoritative Destination

| Field | Value |
|---|---|
| Authoritative destination | Operations ledger (`everwork-ai/IEF-Operations`) |
| Delivery mechanism | GitHub-visible comment on target issue/PR |
| Posted by | ief-operator after runner completion |
| Latency requirement | 30 seconds for standard events, 120 seconds for heartbeats |

---

## 2. Evidence Flow

```
Runner completes execution
    ↓
Runner emits RunEvent (completed/failed/stopped/etc.)
    ↓
ief-operator collects RunEvent
    ↓
ief-operator posts RunEvent as GitHub comment on target issue/PR
    ↓
Operations ledger ingests comment as RunEvent entry
    ↓
Operations ledger becomes authoritative truth source
```

---

## 3. Runner-Local Logs

| Field | Value |
|---|---|
| Purpose | Crash recovery and idempotent restart only |
| Location | `~/.ief-runner/exec.log` |
| Authoritative? | **No** — non-authoritative |
| Rebuildable? | **Yes** — can be fully rebuilt from Operations ledger |
| Subordinate? | **Yes** — subordinate to Operations ledger state |
| Format | Append-only log: `event_id | envelope_id | status | timestamp` |

### 3.1 Example Local Log Entry

```
evt_abc123 | env_xyz789 | completed | 2026-06-09T06:15:00Z
```

### 3.2 Local Log Rules

1. Local logs are append-only.
2. Local logs are never the source of truth.
3. If local log conflicts with Operations ledger, Operations wins.
4. Local logs may be deleted and rebuilt at any time.

---

## 4. Delivery Guarantees

1. Every RunEvent produced by a runner is delivered to Operations.
2. Delivery is via GitHub-visible comment (no hidden channels).
3. Delivery latency: 30 seconds standard, 120 seconds heartbeat.
4. Failed delivery is logged as `STAGE_E_FAILURE_REPORT`.

---

## 5. Cross-Reference

- [IEF-Operations repository](https://github.com/everwork-ai/IEF-Operations)
- [STAGE_E_RUNNER_CONTRACT.md §3](STAGE_E_RUNNER_CONTRACT.md#3-evidence-reporting-format) — RunEvent schema
- [LOCAL_CACHE_BOUNDARY.md](LOCAL_CACHE_BOUNDARY.md) — Cache boundary rules

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
