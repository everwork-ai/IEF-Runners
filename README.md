# Claude Worker Project

Project root: `D:\code\claude-worker`
Initialized from the Claude worker hardening session on 2026-04-18.

This repository is now the independent home for the Claude worker capability
line. It contains the key docs and code artifacts needed to review, reproduce,
or continue the work without reading the live repo first.

## Included Docs

- `docs/IKE_CLAUDE_WORKER_P1_HARDENING_PACKET_2026-04-08.md`
- `docs/IKE_CLAUDE_WORKER_MCP_FEASIBILITY_2026-04-07.md`

## Included Code

- `code/services/api/claude_worker/worker.py`
- `code/services/api/tests/test_claude_worker.py`

## What This Project Covers

- live hang-proof hardening
- detached run / supervisor seed
- CLI end-to-end integration tests
- result protocol alignment with the current harness

## Validation Recorded by the Delegate

- `python -m unittest tests.test_claude_worker`
- `python -m compileall claude_worker`

Delegate-reported results:

- unittest: `Ran 15 tests ... OK`
- compileall: passed

## Controller Review Result

- Recommendation: `accept_with_changes`
- Review findings:
  - result projection keyed by `task_id` can overwrite prior artifacts
  - detached abort does not prove child exit after signal delivery

## Remaining Gaps

- result-file uniqueness for repeated task ids
- detached abort exit confirmation / escalation
- live hang-proof still not system-level Claude binary fault injection

## Notes

This project is separate from `MyAttention`. The live repo remains the source
of the broader IKE mainline, while this repo now hosts the Claude worker
capability line as its own bounded workspace.
