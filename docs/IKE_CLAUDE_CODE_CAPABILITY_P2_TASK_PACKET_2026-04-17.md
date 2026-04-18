# IKE Claude Code Capability P2 Task Packet

Date: 2026-04-17
Status: controller task packet

## task_id

`ike-claude-code-capability-p2-001`

## title

Improve the Claude Code execution capability into a clearer bounded substrate

## task_type

`implementation`

## priority

`high`

## why_this_task_exists

Claude Code is already useful as a bounded execution lane, but the current
state still has a real-run gap:

- prompt delivery has been brittle on the detached worker path
- durable finalization after owner exit still needs stronger closure
- the current lane works, but it is not yet the cleanest reusable substrate for
  other bounded tasks

Hermes also suggests a useful operating split that IKE should absorb more
explicitly:

- one-shot `print` style lane for bounded tasks
- interactive `PTY` / `tmux` style lane for long-running iterative tasks

This task exists to move Claude Code from "useful but narrow" toward "cleanly
bounded and reusable" without turning it into controller logic.

## goal

Close one narrow capability step for Claude Code support:

1. make prompt delivery more trustworthy for real runs
2. make finalization more durable after owner exit
3. make the execution mode split more explicit:
   - bounded one-shot lane
   - interactive long-run lane
4. preserve controller ownership of architecture and acceptance

## allowed_to_change

- `services/api/claude_worker/worker.py`
- `services/api/tests/test_claude_worker.py`
- Claude Code task packet / handoff docs under `docs/`
- progress / changelog entries that record the narrowed capability step

## allowed_to_read

- [D:\code\MyAttention\docs\IKE_CLAUDE_WORKER_REAL_RUN_GAP_2026-04-11.md](/D:/code/MyAttention/docs/IKE_CLAUDE_WORKER_REAL_RUN_GAP_2026-04-11.md)
- [D:\code\MyAttention\docs\IKE_CLAUDE_WORKER_P1_RESULT_2026-04-11.md](/D:/code/MyAttention/docs/IKE_CLAUDE_WORKER_P1_RESULT_2026-04-11.md)
- [D:\code\MyAttention\docs\CLAUDE_CODE_RUNTIME_P0_INTEGRATION_PLAN.md](/D:/code/MyAttention/docs/CLAUDE_CODE_RUNTIME_P0_INTEGRATION_PLAN.md)
- [D:\code\MyAttention\docs\CLAUDE_CODE_PROVIDER_SWITCHING_NOTE_2026-04-10.md](/D:/code/MyAttention/docs/CLAUDE_CODE_PROVIDER_SWITCHING_NOTE_2026-04-10.md)
- [D:\code\MyAttention\docs\IKE_AI_DOMAIN_DISCOVERY_LOOP_P1_RESULT_2026-04-12.md](/D:/code/MyAttention/docs/IKE_AI_DOMAIN_DISCOVERY_LOOP_P1_RESULT_2026-04-12.md)
- [D:\code\MyAttention\docs\IKE_VISION_DESIGN_ARCHITECTURE_PATH_ALIGNMENT_2026-04-17.md](/D:/code/MyAttention/docs/IKE_VISION_DESIGN_ARCHITECTURE_PATH_ALIGNMENT_2026-04-17.md)
- [D:\code\MyAttention\docs\CURRENT_AGENT_HARNESS_INDEX_2026-04-10.md](/D:/code/MyAttention/docs/CURRENT_AGENT_HARNESS_INDEX_2026-04-10.md)

## constraints

1. Do not make Claude Code the controller.
2. Do not broaden into a general multi-agent framework rewrite.
3. Do not add OpenClaw transport dependence.
4. Do not claim production-grade sandbox enforcement.
5. Do not alter project vision or active mainline.
6. Keep the task bounded to the Claude Code capability itself.
7. Preserve durable artifacts and controller-readable results.
8. If you need a mode split, prefer explicit one-shot vs interactive behavior
   instead of hidden heuristics.

## expected_output

Return a controller-readable result with:

1. summary
2. files_changed
3. why_this_solution
4. validation_run
5. known_risks
6. recommendation

The result should also state:

- what changed in the Claude Code capability
- whether prompt delivery is now trustworthy enough for real bounded packets
- whether finalization now closes truthfully after owner exit
- whether the one-shot vs interactive split is explicit enough to reuse

## validation

Minimum validation:

1. focused unit tests for the worker
2. one toy coding run
3. one real bounded coding packet
4. one failure-path proof for finalization or prompt delivery

If the implementation changes harness semantics, also validate that the
controller-facing result schema remains readable.

## stop_conditions

Stop and report immediately if:

1. the task starts requiring controller-level architecture changes
2. the task turns into a generic harness redesign
3. the task depends on missing local Claude CLI / auth capability
4. durable artifact claims are not supported by actual files on disk
5. the worker still cannot distinguish bounded one-shot and interactive modes

## required_review_gates

- no self-acceptance
- review required after implementation
- reject if scope drifts into broader orchestration or memory redesign
- accept only if evidence is durable and bounded

