# Boundary Enforcement Rules

**Classification:** `STAGE_E_BOUNDARY_ENFORCEMENT_RULES`
**Status:** DELIVERED
**Resolves:** Review Condition RC6 — No downstream unblock
**Source:** [IEF-Runners#3 — Implementation Plan (comment 4656179869)](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4656179869)
**Contract Plan:** [comment 4646520609](https://github.com/everwork-ai/IEF-Runners/issues/3#issuecomment-4646520609)

---

## 1. MAY Permissions (11)

| # | Permission | Description |
|---|---|---|
| M1 | Consume envelopes | Accept TaskEnvelopes from Operations ledger |
| M2 | Validate envelopes | Check schema conformance, dedupe, authorization |
| M3 | Execute tasks | Run authorized work within named scope |
| M4 | Emit RunEvents | Report execution status to Operations |
| M5 | Emit ArtifactRefs | Reference produced artifacts |
| M6 | Emit heartbeats | Report progress during long executions |
| M7 | Stop on signal | Halt execution when stop/cancel/blocked received |
| M8 | Stop on timeout | Halt execution when timeout exceeded |
| M9 | Preserve attribution | Carry `actor` from envelope to RunEvent |
| M10 | Use local cache | Maintain non-authoritative execution cache |
| M11 | Wait for verification | Pause after completion until Coordinator verifies |

---

## 2. MUST NOT Prohibitions (14)

| # | Prohibition | Rationale |
|---|---|---|
| P1 | Self-generate envelopes | Only Operations creates TaskEnvelopes |
| P2 | Accept from Adapters | Only Operations is authoritative |
| P3 | Accept from Knowledge | Only Operations is authoritative |
| P4 | Skip validation | All envelopes must pass 4 validation rules |
| P5 | Execute without authorization | GitHub-visible dispatch required |
| P6 | Execute outside allowed files | Only files named in dispatch comment |
| P7 | Modify Protocol schemas | Protocol is authoritative, immutable to runners |
| P8 | Modify Operations ledger | Operations is authoritative, immutable to runners |
| P9 | Self-approve | Evidence ≠ acceptance |
| P10 | Self-merge | Only Coordinator/Human can merge |
| P11 | Self-unblock | Only Coordinator can unblock downstream |
| P12 | Close tasks | Only Coordinator can close |
| P13 | Approve work | Only Coordinator can approve |
| P14 | Unblock downstream targets | Only `STAGE_E_EXECUTION_VERIFY_RESULT` can unblock |

---

## 3. Cross-System Relationships (6)

| # | Relationship | Description |
|---|---|---|
| C1 | Operations → Runners | Operations dispatches TaskEnvelopes |
| C2 | Runners → Operations | Runners emit RunEvents to Operations |
| C3 | Program → Runners | Program authorizes execution via dispatch comments |
| C4 | Coordinator → Runners | Coordinator verifies execution results |
| C5 | Runners → Protocol | Runners validate against Protocol schemas |
| C6 | Runners ≠ Adapters | No direct communication; Adapters feed Operations, Operations feeds Runners |

---

## 4. Machine-Checkable Rules

Each rule is verifiable:

```
{rule_id, category, condition, violation_action}
```

| Rule ID | Category | Condition | Violation Action |
|---|---|---|---|
| M1–M11 | Permission | Runner action is in MAY list | Allow |
| P1–P14 | Prohibition | Runner action is in MUST NOT list | Stop, report STAGE_E_FAILURE_REPORT |
| C1–C6 | Relationship | Cross-system interaction matches table | Stop, report violation |

---

## 5. Downstream Unblock Prohibition (RC6)

**Critical invariant:** Runner completion reports are **evidence only**.

| Action | Runner authorized? |
|---|---|
| Emit `RunEvent(completed)` | Yes |
| Close target issue | **No** |
| Merge target PR | **No** |
| Approve work | **No** |
| Unblock downstream targets | **No** |
| Promote evidence to acceptance | **No** |

Only the Coordinator's `STAGE_E_EXECUTION_VERIFY_RESULT` with `PASSED` can:
- Promote evidence to acceptance
- Unblock downstream targets
- Authorize next stage

---

## 6. Self-Test Checklist

- [ ] All 11 MAY permissions documented
- [ ] All 14 MUST NOT prohibitions documented
- [ ] All 6 cross-system relationships documented
- [ ] Downstream unblock prohibition explicit
- [ ] Evidence ≠ acceptance stated
- [ ] Each rule is machine-checkable (yes/no)

---

## 7. Cross-Reference

- [STAGE_E_RUNNER_CONTRACT.md](STAGE_E_RUNNER_CONTRACT.md) — Core contract
- [STOP_CANCEL_ENFORCEMENT_TESTS.md](STOP_CANCEL_ENFORCEMENT_TESTS.md) — Stop point tests
- [STAGE_C_OPEN_QUESTIONS_RESOLVED.md](STAGE_C_OPEN_QUESTIONS_RESOLVED.md) — Open questions

---

*Delivered by ief-operator executing one bounded Stage E execution cycle. No code changes, no cross-repo mutations.*
