import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from claude_worker.worker import (
    ClaudeWorkerRuntime,
    WorkerPacket,
    _default_claude_worker_root,
    build_parser,
    _resolve_claude_binary,
    normalize_coding_result,
    normalize_review_result,
    SUPERVISOR_FILENAME,
    PROMPT_FILENAME,
    VALID_EXECUTION_MODES,
    DEFAULT_EXECUTION_MODE,
    ProviderConfig,
    ProviderRegistry,
    CredentialStore,
    check_prerequisites,
    verify_provider_endpoint,
    _DEFAULT_PROVIDERS,
)


class FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None
        self._next_returncode = returncode
        self.terminated = False
        self.killed = False
        self.communicate_calls = 0

    def communicate(self, timeout: int | None = None):
        self.communicate_calls += 1
        self.returncode = self._next_returncode
        return self.stdout, self.stderr

    def wait(self, timeout: int | None = None):
        self.returncode = self._next_returncode
        return self._next_returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class ClaudeWorkerRuntimeTest(unittest.TestCase):
    def test_default_run_root_is_externalized(self) -> None:
        root = _default_claude_worker_root()
        expected = Path(os.environ["CLAUDE_WORKER_HOME"]).resolve() if os.environ.get("CLAUDE_WORKER_HOME") else (Path.home() / ".claude-worker").resolve()
        self.assertEqual(root, expected)

    def _run_cli(self, args: list[str]) -> dict[str, object]:
        command = [sys.executable, "-m", "claude_worker", *args]
        proc = subprocess.run(
            command,
            cwd=str(API_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(proc.stdout.strip())

    def test_start_wait_and_fetch_writes_durable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launched = {}

            def launcher(command, **kwargs):
                launched["command"] = command
                launched["kwargs"] = kwargs
                return FakeProcess(
                    json.dumps(
                        {
                            "summary": "Added the new worker runtime files.",
                            "files_changed": [
                                "services/api/claude_worker/worker.py",
                                "services/api/tests/test_claude_worker.py",
                            ],
                            "validation_run": "python -m unittest tests.test_claude_worker",
                            "known_risks": ["CLI schema may need tightening"],
                            "recommendation": "accept_with_changes",
                            "patch_diff": "--- a/foo\n+++ b/foo\n",
                        }
                    )
                )

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Add the worker runtime.",
                    cwd=tmpdir,
                    model="qwen3-coder-next",
                    task_id="task-123",
                    title="Toy coding packet",
                    lane="coding",
                    reasoning_mode="high",
                    sandbox_identity="claude-worker-coding",
                    sandbox_kind="claude_worker_run_root",
                    capability_profile="coding_high_reasoning",
                    write_scope=["services/api/claude_worker/worker.py", "services/api/tests/test_claude_worker.py"],
                    network_policy="restricted",
                    workspace_root=tmpdir,
                    runtime_root=str(Path(tmpdir) / "runs"),
                    environment_mode="isolated_workspace",
                )
            )

            self.assertTrue((record.run_dir / "meta.json").exists())
            self.assertTrue((record.run_dir / "events.ndjson").exists())
            self.assertTrue((record.run_dir / "summary.md").exists())
            self.assertTrue((record.run_dir / "patch.diff").exists())

            final_payload = runtime.wait(record.run_id)

            self.assertEqual(final_payload["kind"], "coding")
            self.assertEqual(final_payload["recommendation"], "accept_with_changes")
            self.assertEqual(final_payload["files_changed"][0], "services/api/claude_worker/worker.py")
            self.assertEqual(final_payload["validation_run"], "python -m unittest tests.test_claude_worker")
            self.assertIn("Added the new worker runtime files.", final_payload["summary"])
            self.assertEqual(final_payload["lane"], "coding")
            self.assertEqual(final_payload["reasoning_mode"], "high")
            self.assertEqual(final_payload["sandbox_identity"], "claude-worker-coding")
            self.assertEqual(final_payload["sandbox_kind"], "claude_worker_run_root")
            self.assertEqual(final_payload["capability_profile"], "coding_high_reasoning")
            self.assertEqual(final_payload["write_scope"], ["services/api/claude_worker/worker.py", "services/api/tests/test_claude_worker.py"])
            self.assertEqual(final_payload["network_policy"], "restricted")

            fetched = runtime.fetch(record.run_id)
            self.assertEqual(fetched["final"]["recommendation"], "accept_with_changes")
            self.assertIn("meta", fetched)
            self.assertIn("events", fetched)
            self.assertIn("summary", fetched)

            self.assertTrue(Path(launched["command"][0]).name.startswith("claude"))
            self.assertIn("--model", launched["command"])
            self.assertIn("qwen3-coder-next", launched["command"])
            self.assertEqual(launched["kwargs"]["cwd"], tmpdir)

    def test_review_result_is_normalized(self) -> None:
        raw = {
            "summary": "Found a scope leak in the worker packet.",
            "findings": [
                {"title": "Scope leak", "body": "The packet touches too many files.", "file": "docs/plan.md"}
            ],
            "validation_gaps": ["No real CLI proof yet"],
            "recommendation": "accept_with_changes",
        }
        normalized = normalize_review_result(raw)
        self.assertEqual(normalized["kind"], "review")
        self.assertEqual(normalized["recommendation"], "accept_with_changes")
        self.assertEqual(normalized["findings"][0]["title"], "Scope leak")
        self.assertEqual(normalized["validation_gaps"][0], "No real CLI proof yet")

    def test_coding_result_is_normalized(self) -> None:
        raw = {
            "summary": "Worker is ready.",
            "files_changed": ["services/api/claude_worker/worker.py"],
            "validation_run": "python -m unittest tests.test_claude_worker",
            "known_risks": ["Need a real toy-run proof"],
            "recommendation": "accept_with_changes",
        }
        normalized = normalize_coding_result(raw)
        self.assertEqual(normalized["kind"], "coding")
        self.assertEqual(normalized["files_changed"], ["services/api/claude_worker/worker.py"])
        self.assertEqual(normalized["validation_run"], "python -m unittest tests.test_claude_worker")

    def test_wait_reads_structured_output_from_artifact_files_when_wrapper_stdout_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            def launcher(command, **kwargs):
                stdout_path = Path(kwargs["stdout_path"])
                stderr_path = Path(kwargs["stderr_path"])
                exitcode_path = Path(kwargs["exitcode_path"])
                stdout_path.write_text(
                    json.dumps(
                        {
                            "summary": "Artifact-backed result",
                            "files_changed": ["services/api/conversation_runtime/p0.py"],
                            "validation_run": "python -m unittest tests.test_conversation_runtime_route",
                            "known_risks": ["Read-only validation packet."],
                            "recommendation": "accept_with_changes",
                        }
                    ),
                    encoding="utf-8",
                )
                stderr_path.write_text("", encoding="utf-8")
                exitcode_path.write_text("0", encoding="utf-8")
                return FakeProcess("", "", 0)

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Validate artifact-backed wait finalization.",
                    cwd=tmpdir,
                    task_id="artifact-backed-wait",
                    title="Artifact-backed wait finalization",
                )
            )

            result = runtime.wait(record.run_id)

            self.assertEqual(result["summary"], "Artifact-backed result")
            self.assertEqual(result["files_changed"], ["services/api/conversation_runtime/p0.py"])
            self.assertEqual(
                result["validation_run"],
                "python -m unittest tests.test_conversation_runtime_route",
            )
            self.assertEqual(result["recommendation"], "accept_with_changes")

    def test_abort_marks_run_aborted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            class HangingProcess(FakeProcess):
                def communicate(self, timeout: int | None = None):
                    self.communicate_calls += 1
                    self.returncode = -15
                    return "", ""

            def launcher(command, **kwargs):
                return HangingProcess("", "")

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(
                    kind="review",
                    prompt="Review the patch.",
                    cwd=tmpdir,
                )
            )
            result = runtime.abort(record.run_id)
            self.assertEqual(result["status"], "aborted")
            self.assertTrue((record.run_dir / "final.json").exists())

    def test_resolve_claude_binary_prefers_cmd_on_windows(self) -> None:
        with patch.object(os, "name", "nt"):
            with patch.object(shutil, "which") as mock_which:
                mock_which.side_effect = lambda name: f"C:\\path\\{name}" if name == "claude.cmd" else None
                resolved = _resolve_claude_binary("claude")
                self.assertEqual(resolved, "C:\\path\\claude.cmd")

    def test_timeout_path_returns_running_without_killing(self) -> None:
        class TimeoutThenFailProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False
                self.killed = False
                self.calls = 0

            def communicate(self, timeout: int | None = None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
                raise RuntimeError("post-timeout drain failed")

            def wait(self, timeout: int | None = None):
                self.returncode = 124
                return 124

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=lambda *args, **kwargs: TimeoutThenFailProcess())
            record = runtime.start(WorkerPacket(kind="coding", prompt="Hang test", cwd=tmpdir))
            result = runtime.wait(record.run_id)
            # New behavior: wait timeout returns running, does NOT terminate
            self.assertEqual(result["status"], "running")
            self.assertTrue(result["lifecycle"]["timeout"])
            self.assertFalse(result["lifecycle"]["terminate_requested"])
            self.assertFalse(result["lifecycle"]["kill_requested"])
            self.assertFalse(record.process.terminated)
            self.assertFalse(record.process.killed)
            # No final.json — process is still alive
            self.assertFalse((record.run_dir / "final.json").exists())
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "wait_timeout"', events)
            self.assertNotIn('"event": "terminate_requested"', events)

    def test_timeout_then_abort_terminates_and_finalizes(self) -> None:
        """wait() timeout returns running; subsequent abort() terminates and finalizes."""
        class TimeoutThenCompleteProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False
                self.killed = False
                self._calls = 0

            def communicate(self, timeout: int | None = None):
                self._calls += 1
                if self._calls == 1:
                    raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
                return "", ""

            def wait(self, timeout: int | None = None):
                self.returncode = 0
                return 0

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 143

            def kill(self):
                self.killed = True
                self.returncode = 137

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=lambda *args, **kwargs: TimeoutThenCompleteProcess())
            record = runtime.start(WorkerPacket(kind="coding", prompt="Timeout then abort", cwd=tmpdir))
            # First wait: timeout → running
            result = runtime.wait(record.run_id)
            self.assertEqual(result["status"], "running")
            self.assertFalse(record.process.terminated)
            # Now abort
            aborted = runtime.abort(record.run_id)
            self.assertEqual(aborted["status"], "aborted")
            self.assertTrue(record.process.terminated)
            self.assertTrue((record.run_dir / "final.json").exists())

    def test_live_hanging_subprocess_times_out_and_returns_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            def launcher(*args, **kwargs):
                return subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=kwargs.get("cwd"),
                )

            runtime = ClaudeWorkerRuntime(
                run_root=Path(tmpdir) / "runs",
                wait_timeout_seconds=0.1,
                launcher=launcher,
            )
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Real hang test",
                    cwd=tmpdir,
                )
            )
            result = runtime.wait(record.run_id)
            # New behavior: timeout returns running, child is NOT terminated
            self.assertEqual(result["status"], "running")
            self.assertTrue(result["lifecycle"]["timeout"])
            self.assertFalse(result["lifecycle"]["terminate_requested"])
            # No final.json — process is still alive
            self.assertFalse((record.run_dir / "final.json").exists())
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "wait_timeout"', events)
            self.assertNotIn('"event": "terminate_requested"', events)
            # Clean up: abort the still-running process
            aborted = runtime.abort(record.run_id)
            self.assertEqual(aborted["status"], "aborted")

    def test_detached_wait_returns_running_snapshot_and_abort_requires_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "runs"
            if os.name == "nt":
                detached_binary = Path(tmpdir) / "claude.cmd"
                detached_binary.write_text(
                    "@echo off\r\n"
                    ":loop\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"
                    "goto loop\r\n",
                    encoding="utf-8",
                )
            else:
                detached_binary = Path(tmpdir) / "claude"
                detached_binary.write_text(
                    "#!/bin/sh\n"
                    "while true; do\n"
                    "  sleep 1\n"
                    "done\n",
                    encoding="utf-8",
                )
                detached_binary.chmod(0o755)
            owner_runtime = ClaudeWorkerRuntime(run_root=run_root, claude_binary=str(detached_binary))
            record = owner_runtime.start(WorkerPacket(kind="coding", prompt="Detached", cwd=tmpdir, task_id="detached-1"))

            detached_runtime = ClaudeWorkerRuntime(
                run_root=run_root,
                detached_wait_timeout_seconds=0.05,
                detached_poll_interval_seconds=0.01,
            )
            snapshot = detached_runtime.wait(record.run_id)
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(snapshot["detached_wait"]["state"], "timed_out")
            self.assertEqual(snapshot["detached_wait"]["strategy"]["wait_mode"], "poll-final-json")
            self.assertIn("owner_pid", snapshot["detached_wait"])

            aborted = detached_runtime.abort(record.run_id)
            self.assertEqual(aborted["status"], "aborted")
            self.assertTrue(aborted["lifecycle"]["detached_abort"])
            owner_process = owner_runtime._records[record.run_id].process
            if owner_process is not None and hasattr(owner_process, "communicate"):
                try:
                    owner_process.communicate(timeout=1)
                except Exception:
                    pass

    def test_reload_rejects_corrupted_meta_and_invalid_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            bad_run = run_root / "bad-run"
            bad_run.mkdir()

            (bad_run / "meta.json").write_text("{not-json", encoding="utf-8")
            runtime = ClaudeWorkerRuntime(run_root=run_root)
            with self.assertRaises(ValueError):
                runtime.fetch("bad-run")

            kind_run = run_root / "kind-run"
            kind_run.mkdir()
            (kind_run / "meta.json").write_text(
                json.dumps(
                    {
                        "kind": "coding",
                        "packet": {
                            "kind": "bogus",
                            "prompt": "x",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                runtime.fetch("kind-run")

            missing_run = run_root / "missing-run"
            missing_run.mkdir()
            (missing_run / "meta.json").write_text(
                json.dumps(
                    {
                        "kind": "coding",
                        "packet": {
                            "kind": "coding",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                runtime.fetch("missing-run")

    def test_unknown_run_id_raises_key_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(run_root=tmpdir)
            with self.assertRaises(KeyError):
                runtime.fetch("missing-run")

    def test_malformed_json_finalizes_with_fallback_summary(self) -> None:
        class BadJsonProcess(FakeProcess):
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: BadJsonProcess("not-json-output"),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Bad JSON", cwd=tmpdir))
            result = runtime.wait(record.run_id)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["summary"], "not-json-output")
            self.assertEqual(result["files_changed"], [])

    def test_finalize_writes_harness_result_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_root = Path(tmpdir) / "results"
            runtime = ClaudeWorkerRuntime(
                run_root=Path(tmpdir) / "runs",
                result_root=result_root,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "summary": "Harness projection test",
                            "files_changed": ["services/api/claude_worker/worker.py"],
                            "validation_run": "python -m unittest tests.test_claude_worker",
                            "known_risks": ["none"],
                            "recommendation": "accept",
                        }
                    )
                ),
            )
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Projection",
                    cwd=tmpdir,
                    task_id="task-harness",
                    lane="coding",
                    reasoning_mode="high",
                    sandbox_identity="claude-worker-coding",
                    sandbox_kind="claude_worker_run_root",
                    capability_profile="coding_high_reasoning",
                    write_scope=["services/api/claude_worker/worker.py"],
                    network_policy="restricted",
                )
            )
            runtime.wait(record.run_id)
            result_path = result_root / "task-harness.json"
            self.assertTrue(result_path.exists())
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["protocol"], "delegation_result.v1")
            self.assertEqual(payload["task_id"], "task-harness")
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["lane"], "coding")
            self.assertEqual(payload["reasoning_mode"], "high")
            self.assertEqual(payload["sandbox_identity"], "claude-worker-coding")
            self.assertEqual(payload["sandbox_kind"], "claude_worker_run_root")
            self.assertEqual(payload["capability_profile"], "coding_high_reasoning")
            self.assertEqual(payload["write_scope"], ["services/api/claude_worker/worker.py"])
            self.assertEqual(payload["network_policy"], "restricted")
            self.assertIn("artifacts", payload)
            self.assertEqual(payload["files_changed"], ["services/api/claude_worker/worker.py"])

    def test_fetch_finalizes_detached_run_when_exitcode_and_stdout_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "runs"
            runtime = ClaudeWorkerRuntime(
                run_root=run_root,
                detached_wait_timeout_seconds=0.05,
                detached_poll_interval_seconds=0.01,
            )
            run_dir = run_root / "detached-finished"
            run_dir.mkdir(parents=True)
            meta = {
                "run_id": "detached-finished",
                "kind": "coding",
                "task_id": "detached-finished",
                "status": "running",
                "child_pid": 999999,
                "packet": {
                    "kind": "coding",
                    "prompt": "x",
                    "task_id": "detached-finished",
                    "model": "qwen3.6-plus",
                    "permission_mode": "bypassPermissions",
                },
            }
            (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (run_dir / "events.ndjson").write_text("", encoding="utf-8")
            (run_dir / "summary.md").write_text("started\n", encoding="utf-8")
            (run_dir / "patch.diff").write_text("", encoding="utf-8")
            (run_dir / "stdout.txt").write_text(
                json.dumps(
                    {
                        "summary": "Detached finalized",
                        "files_changed": ["services/api/claude_worker/worker.py"],
                        "validation_run": "python -m unittest tests.test_claude_worker",
                        "known_risks": [],
                        "recommendation": "accept_with_changes",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "stderr.txt").write_text("", encoding="utf-8")
            (run_dir / "exitcode.txt").write_text("0", encoding="utf-8")

            fetched = runtime.fetch("detached-finished")
            self.assertIsNotNone(fetched["final"])
            self.assertEqual(fetched["final"]["status"], "succeeded")
            self.assertTrue((run_dir / "final.json").exists())

    def test_wait_finalizes_detached_run_after_child_exit_without_stale_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "runs"
            runtime = ClaudeWorkerRuntime(
                run_root=run_root,
                detached_wait_timeout_seconds=0.05,
                detached_poll_interval_seconds=0.01,
            )
            run_dir = run_root / "detached-exited"
            run_dir.mkdir(parents=True)
            meta = {
                "run_id": "detached-exited",
                "kind": "coding",
                "task_id": "detached-exited",
                "status": "running",
                "child_pid": 999998,
                "packet": {
                    "kind": "coding",
                    "prompt": "x",
                    "task_id": "detached-exited",
                    "model": "qwen3.6-plus",
                    "permission_mode": "bypassPermissions",
                },
            }
            (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (run_dir / "events.ndjson").write_text("", encoding="utf-8")
            (run_dir / "summary.md").write_text("started\n", encoding="utf-8")
            (run_dir / "patch.diff").write_text("", encoding="utf-8")
            (run_dir / "stdout.txt").write_text(
                json.dumps(
                    {
                        "summary": "Detached wait finalized",
                        "files_changed": [],
                        "validation_run": "",
                        "known_risks": [],
                        "recommendation": "accept_with_changes",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "stderr.txt").write_text("", encoding="utf-8")
            (run_dir / "exitcode.txt").write_text("0", encoding="utf-8")

            result = runtime.wait("detached-exited")
            self.assertEqual(result["status"], "succeeded")
            self.assertTrue(result["lifecycle"]["detached_finalize"])

    def test_invalid_payload_shape_sets_normalization_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "structured_output": {
                                "summary": "bad shape",
                                "findings": {"title": "not-a-list"},
                                "validation_gaps": [],
                                "recommendation": "reject",
                            }
                        }
                    )
                ),
            )
            record = runtime.start(WorkerPacket(kind="review", prompt="Bad shape", cwd=tmpdir))
            result = runtime.wait(record.run_id)
            self.assertEqual(result["status"], "failed")
            self.assertIn("normalization_error", result)
            self.assertEqual(result["recommendation"], "reject")

    def test_cli_parser_accepts_start_wait_abort(self) -> None:
        parser = build_parser()
        start = parser.parse_args(["start", "--kind", "coding", "--prompt", "hello"])
        self.assertEqual(start.command, "start")
        self.assertEqual(start.kind, "coding")
        self.assertEqual(start.wait_timeout_seconds, 600)
        parsed = parser.parse_args(["--wait-timeout-seconds", "7", "start", "--kind", "coding", "--prompt", "hello"])
        self.assertEqual(parsed.wait_timeout_seconds, 7.0)
        parsed = parser.parse_args(
            [
                "--detached-wait-timeout-seconds",
                "2.5",
                "--detached-poll-interval-seconds",
                "0.5",
                "--result-root",
                "D:\\tmp\\results",
                "start",
                "--kind",
                "coding",
                "--prompt",
                "hello",
                "--lane",
                "review",
                "--reasoning-mode",
                "high",
                "--sandbox-identity",
                "claude-review",
                "--sandbox-kind",
                "claude_worker_run_root",
                "--capability-profile",
                "review_high_reasoning",
                "--write-scope",
                "services/api/routers/example.py",
                "--network-policy",
                "disabled",
                "--workspace-root",
                "D:\\tmp\\workspace",
                "--runtime-root",
                "D:\\tmp\\runtime",
                "--environment-mode",
                "isolated_workspace",
            ]
        )
        self.assertEqual(parsed.detached_wait_timeout_seconds, 2.5)
        self.assertEqual(parsed.detached_poll_interval_seconds, 0.5)
        self.assertEqual(parsed.result_root, "D:\\tmp\\results")
        self.assertEqual(parsed.lane, "review")
        self.assertEqual(parsed.reasoning_mode, "high")
        self.assertEqual(parsed.sandbox_identity, "claude-review")
        self.assertEqual(parsed.sandbox_kind, "claude_worker_run_root")
        self.assertEqual(parsed.capability_profile, "review_high_reasoning")
        self.assertEqual(parsed.write_scope, ["services/api/routers/example.py"])
        self.assertEqual(parsed.network_policy, "disabled")
        self.assertEqual(parsed.workspace_root, "D:\\tmp\\workspace")
        self.assertEqual(parsed.runtime_root, "D:\\tmp\\runtime")
        self.assertEqual(parsed.environment_mode, "isolated_workspace")
        wait = parser.parse_args(["wait", "--run-id", "abc"])
        self.assertEqual(wait.command, "wait")
        abort = parser.parse_args(["abort", "--run-id", "abc"])
        self.assertEqual(abort.command, "abort")

    def test_cli_end_to_end_start_fetch_abort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            run_root = tmp_path / "runs"
            result_root = tmp_path / "results"
            if os.name == "nt":
                cli_binary = tmp_path / "claude.cmd"
                cli_binary.write_text(
                    "@echo off\r\n"
                    ":loop\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"
                    "goto loop\r\n",
                    encoding="utf-8",
                )
            else:
                cli_binary = tmp_path / "claude"
                cli_binary.write_text(
                    "#!/bin/sh\n"
                    "while true; do\n"
                    "  sleep 1\n"
                    "done\n",
                    encoding="utf-8",
                )
                cli_binary.chmod(0o755)

            base_args = [
                "--run-root",
                str(run_root),
                "--result-root",
                str(result_root),
                "--claude-binary",
                str(cli_binary),
                "--wait-timeout-seconds",
                "0.1",
                "--detached-wait-timeout-seconds",
                "0.1",
                "--detached-poll-interval-seconds",
                "0.01",
            ]
            started = self._run_cli(base_args + ["start", "--kind", "coding", "--prompt", "e2e", "--cwd", tmpdir, "--task-id", "cli-e2e"])
            run_id = str(started["run_id"])

            fetched_before = self._run_cli(base_args + ["fetch", "--run-id", run_id])
            self.assertEqual(fetched_before["meta"]["task_id"], "cli-e2e")
            self.assertIsNone(fetched_before["final"])

            aborted = self._run_cli(base_args + ["abort", "--run-id", run_id])
            self.assertEqual(aborted["status"], "aborted")

            fetched_after = self._run_cli(base_args + ["fetch", "--run-id", run_id])
            self.assertEqual(fetched_after["final"]["status"], "aborted")
            self.assertTrue((result_root / "cli-e2e.json").exists())


    def test_live_hanging_subprocess_records_wait_communicate_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            def launcher(*args, **kwargs):
                return subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=kwargs.get("cwd"),
                )

            runtime = ClaudeWorkerRuntime(
                run_root=Path(tmpdir) / "runs",
                wait_timeout_seconds=0.1,
                kill_grace_period_seconds=0.1,
                launcher=launcher,
            )
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Real hang audit test",
                    cwd=tmpdir,
                )
            )
            result = runtime.wait(record.run_id)
            # New behavior: timeout returns running, not failed
            self.assertEqual(result["status"], "running")
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "wait_communicate_start"', events)
            self.assertIn('"event": "wait_timeout"', events)
            # No terminate_requested — process is left alive
            self.assertNotIn('"event": "terminate_requested"', events)
            self.assertTrue(result["lifecycle"]["timeout"])
            self.assertFalse(result["lifecycle"]["terminate_requested"])
            # Clean up
            runtime.abort(record.run_id)

    def test_kill_grace_period_is_respected_on_abort(self) -> None:
        class TimeoutOnlyProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False
                self.killed = False
                self._communicate_count = 0

            def communicate(self, timeout: int | None = None):
                self._communicate_count += 1
                if self._communicate_count <= 2:
                    raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
                return "", ""

            def wait(self, timeout: int | None = None):
                self.returncode = 124
                return 124

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                kill_grace_period_seconds=0.01,
                launcher=lambda *args, **kwargs: TimeoutOnlyProcess(),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Grace period test", cwd=tmpdir))
            # First wait() returns running on timeout — does NOT terminate
            result = runtime.wait(record.run_id)
            self.assertEqual(result["status"], "running")
            self.assertFalse(record.process.terminated)
            self.assertFalse(record.process.killed)
            # Then abort() terminates and kills
            aborted = runtime.abort(record.run_id)
            self.assertEqual(aborted["status"], "aborted")
            self.assertTrue(record.process.terminated)
            self.assertTrue(record.process.killed)

    def test_start_writes_supervisor_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: FakeProcess('{"summary":"s","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}'),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Supervisor test", cwd=tmpdir))
            supervisor_path = record.run_dir / SUPERVISOR_FILENAME
            self.assertTrue(supervisor_path.exists())
            supervisor = json.loads(supervisor_path.read_text(encoding="utf-8"))
            self.assertEqual(supervisor["run_id"], record.run_id)
            self.assertEqual(supervisor["ownership_mode"], "single-process")
            self.assertEqual(supervisor["strategy"], "poll-final-json")
            self.assertIn("fetch", supervisor["resume_via"])
            self.assertIn("limitation", supervisor)
            self.assertIn(os.getpid(), [supervisor["owner_pid"]])

    def test_detached_wait_contract_in_meta_includes_limitation_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: FakeProcess('{"summary":"s","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}'),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Contract test", cwd=tmpdir))
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            contract = meta["detached_wait_contract"]
            self.assertIn("limitation", contract)
            self.assertIn("resume_contract", contract)
            self.assertIn("fetch", contract["resume_contract"])
            self.assertIn("wait", contract["resume_contract"])
            self.assertIn("abort", contract["resume_contract"])

    def test_coding_result_normalizes_why_this_solution(self) -> None:
        raw = {
            "summary": "Implemented the feature.",
            "files_changed": ["services/api/claude_worker/worker.py"],
            "why_this_solution": "This approach minimizes scope while preserving backward compatibility.",
            "validation_run": "python -m unittest tests.test_claude_worker",
            "known_risks": ["Edge case in normalization"],
            "recommendation": "accept",
        }
        normalized = normalize_coding_result(raw)
        self.assertEqual(normalized["why_this_solution"], "This approach minimizes scope while preserving backward compatibility.")

    def test_coding_result_defaults_why_this_solution_to_empty(self) -> None:
        raw = {
            "summary": "Simple fix.",
            "files_changed": [],
            "validation_run": "",
            "known_risks": [],
            "recommendation": "accept_with_changes",
        }
        normalized = normalize_coding_result(raw)
        self.assertEqual(normalized["why_this_solution"], "")

    def test_harness_result_includes_why_this_solution_and_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_root = Path(tmpdir) / "results"
            runtime = ClaudeWorkerRuntime(
                run_root=Path(tmpdir) / "runs",
                result_root=result_root,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "summary": "Harness why_this_solution test",
                            "files_changed": ["services/api/claude_worker/worker.py"],
                            "why_this_solution": "Minimal change to align result protocol.",
                            "validation_run": "python -m unittest tests.test_claude_worker",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                ),
            )
            record = runtime.start(
                WorkerPacket(
                    kind="coding",
                    prompt="Projection",
                    cwd=tmpdir,
                    task_id="task-why-this",
                    lane="coding",
                )
            )
            runtime.wait(record.run_id)
            result_path = result_root / "task-why-this.json"
            self.assertTrue(result_path.exists())
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["why_this_solution"], "Minimal change to align result protocol.")
            self.assertIn("supervisor", payload["artifacts"])

    def test_wait_communicate_done_event_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "summary": "Quick success",
                            "files_changed": [],
                            "validation_run": "",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                ),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Event test", cwd=tmpdir))
            runtime.wait(record.run_id)
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "wait_communicate_start"', events)
            self.assertIn('"event": "wait_communicate_done"', events)

    def test_cli_e2e_start_wait_fetch_with_fast_exit_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            run_root = tmp_path / "runs"
            result_root = tmp_path / "results"
            if os.name == "nt":
                cli_binary = tmp_path / "claude.cmd"
                cli_binary.write_text(
                    "@echo off\r\n"
                    "echo {\"summary\":\"cli-e2e-ok\",\"files_changed\":[],\"validation_run\":\"\",\"known_risks\":[],\"recommendation\":\"accept\"}\r\n",
                    encoding="utf-8",
                )
            else:
                cli_binary = tmp_path / "claude"
                cli_binary.write_text(
                    '#!/bin/sh\n'
                    'echo \'{"summary":"cli-e2e-ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}\'\n',
                    encoding="utf-8",
                )
                cli_binary.chmod(0o755)

            base_args = [
                "--run-root", str(run_root),
                "--result-root", str(result_root),
                "--claude-binary", str(cli_binary),
                "--wait-timeout-seconds", "10",
                "--detached-wait-timeout-seconds", "0.1",
                "--detached-poll-interval-seconds", "0.01",
            ]
            started = self._run_cli(base_args + ["start", "--kind", "coding", "--prompt", "e2e-wait", "--cwd", tmpdir, "--task-id", "cli-e2e-wait"])
            run_id = str(started["run_id"])

            waited = self._run_cli(base_args + ["wait", "--run-id", run_id])
            self.assertEqual(waited["status"], "succeeded")
            self.assertEqual(waited["recommendation"], "accept")

            fetched = self._run_cli(base_args + ["fetch", "--run-id", run_id])
            self.assertIsNotNone(fetched["final"])
            self.assertEqual(fetched["final"]["status"], "succeeded")
            self.assertIsNotNone(fetched["supervisor"])

            self.assertTrue((result_root / "cli-e2e-wait.json").exists())

    def test_cli_e2e_kill_grace_period_propagated(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            ["--kill-grace-period-seconds", "2.5", "start", "--kind", "coding", "--prompt", "hello"]
        )
        self.assertEqual(parsed.kill_grace_period_seconds, 2.5)

    def test_prompt_file_is_durable_truth_before_process_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_content = "This is a durable prompt that must survive owner crash."
            launched_order = []

            def launcher(command, **kwargs):
                launched_order.append("launched")
                return FakeProcess(
                    json.dumps(
                        {
                            "summary": "Prompt delivered.",
                            "files_changed": [],
                            "validation_run": "",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                )

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(kind="coding", prompt=prompt_content, cwd=tmpdir)
            )
            prompt_path = record.run_dir / PROMPT_FILENAME
            self.assertTrue(prompt_path.exists())
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), prompt_content)
            # Verify prompt_delivery metadata
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertIn("prompt_delivery", meta)
            self.assertEqual(meta["prompt_delivery"]["method"], "file_and_stdin")
            self.assertTrue(meta["prompt_delivery"]["prompt_file_verified_after_start"])
            # Verify event
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "prompt_delivery_verified"', events)

    def test_prompt_with_newlines_passes_verification_on_windows(self) -> None:
        """Prompt files with newlines must survive CRLF-free verification on Windows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Multi-line prompt with many \n characters
            prompt_content = "Line one\nLine two\nLine three\nLine four\n"
            launched_order = []

            def launcher(command, **kwargs):
                launched_order.append("launched")
                return FakeProcess(
                    json.dumps(
                        {
                            "summary": "Multi-line prompt delivered.",
                            "files_changed": [],
                            "validation_run": "",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                )

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(kind="coding", prompt=prompt_content, cwd=tmpdir)
            )
            # On Windows, write_text without newline="" would convert \n to \r\n,
            # causing st_size to exceed len(encode("utf-8")) and breaking verification.
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(
                meta["prompt_delivery"]["prompt_file_verified_after_start"],
                f"Prompt verification failed: disk_size={record.run_dir.joinpath(PROMPT_FILENAME).stat().st_size}, "
                f"expected_size={meta['prompt_delivery']['prompt_file_size']}",
            )
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"event": "prompt_delivery_verified"', events)

    def test_effort_flag_passed_to_cc_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launched = {}

            def launcher(command, **kwargs):
                launched["command"] = command
                return FakeProcess('{"summary":"ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}')

            # Default effort
            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(kind="coding", prompt="effort test", cwd=tmpdir, execution_mode="one_shot")
            )
            self.assertIn("--effort", launched["command"])
            idx = launched["command"].index("--effort")
            self.assertEqual(launched["command"][idx + 1], "high")
            # Verify effort in meta.json
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["effort"], "high")

            # Max effort
            launched.clear()
            record2 = runtime.start(
                WorkerPacket(kind="coding", prompt="max effort test", cwd=tmpdir, execution_mode="one_shot", effort="max")
            )
            self.assertIn("--effort", launched["command"])
            idx = launched["command"].index("--effort")
            self.assertEqual(launched["command"][idx + 1], "max")
            meta2 = json.loads((record2.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta2["effort"], "max")

    def test_execution_mode_one_shot_uses_p_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launched = {}

            def launcher(command, **kwargs):
                launched["command"] = command
                return FakeProcess('{"summary":"ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}')

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(kind="coding", prompt="One-shot test", cwd=tmpdir, execution_mode="one_shot")
            )
            # one_shot mode should include -p flag
            self.assertIn("-p", launched["command"])
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["execution_mode"], "one_shot")

    def test_execution_mode_interactive_omits_p_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launched = {}

            def launcher(command, **kwargs):
                launched["command"] = command
                return FakeProcess('{"summary":"ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}')

            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
            record = runtime.start(
                WorkerPacket(kind="coding", prompt="Interactive test", cwd=tmpdir, execution_mode="interactive")
            )
            # interactive mode should NOT include -p flag
            self.assertNotIn("-p", launched["command"])
            meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["execution_mode"], "interactive")
            self.assertEqual(meta["prompt_delivery"]["mode"], "interactive")

    def test_invalid_execution_mode_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=lambda *a, **kw: FakeProcess(""))
            with self.assertRaises(ValueError):
                runtime.start(
                    WorkerPacket(kind="coding", prompt="Bad mode", cwd=tmpdir, execution_mode="daemon")
                )

    def test_default_execution_mode_is_one_shot(self) -> None:
        packet = WorkerPacket(kind="coding", prompt="test")
        self.assertEqual(packet.execution_mode, "one_shot")

    def test_execution_mode_in_final_and_harness_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_root = Path(tmpdir) / "results"
            runtime = ClaudeWorkerRuntime(
                run_root=Path(tmpdir) / "runs",
                result_root=result_root,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "summary": "Mode test",
                            "files_changed": [],
                            "validation_run": "",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                ),
            )
            record = runtime.start(
                WorkerPacket(kind="coding", prompt="Mode result", cwd=tmpdir, execution_mode="interactive", task_id="task-mode")
            )
            result = runtime.wait(record.run_id)
            self.assertEqual(result["execution_mode"], "interactive")

            harness_path = result_root / "task-mode.json"
            self.assertTrue(harness_path.exists())
            harness = json.loads(harness_path.read_text(encoding="utf-8"))
            self.assertEqual(harness["execution_mode"], "interactive")

    def test_detached_finalize_includes_owner_alive_and_prompt_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "runs"
            runtime = ClaudeWorkerRuntime(
                run_root=run_root,
                detached_wait_timeout_seconds=0.05,
                detached_poll_interval_seconds=0.01,
            )
            run_dir = run_root / "detached-durable"
            run_dir.mkdir(parents=True)
            meta = {
                "run_id": "detached-durable",
                "kind": "coding",
                "task_id": "detached-durable",
                "status": "running",
                "owner_pid": 999997,
                "child_pid": 999996,
                "packet": {
                    "kind": "coding",
                    "prompt": "x",
                    "task_id": "detached-durable",
                    "model": "qwen3.6-plus",
                    "permission_mode": "bypassPermissions",
                    "execution_mode": "one_shot",
                },
            }
            (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            (run_dir / "events.ndjson").write_text("", encoding="utf-8")
            (run_dir / "summary.md").write_text("started\n", encoding="utf-8")
            (run_dir / "patch.diff").write_text("", encoding="utf-8")
            (run_dir / PROMPT_FILENAME).write_text("x", encoding="utf-8")
            (run_dir / "stdout.txt").write_text(
                json.dumps(
                    {
                        "summary": "Detached durable finalized",
                        "files_changed": [],
                        "validation_run": "",
                        "known_risks": [],
                        "recommendation": "accept",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "stderr.txt").write_text("", encoding="utf-8")
            (run_dir / "exitcode.txt").write_text("0", encoding="utf-8")

            fetched = runtime.fetch("detached-durable")
            self.assertIsNotNone(fetched["final"])
            lifecycle = fetched["final"]["lifecycle"]
            self.assertTrue(lifecycle["detached_finalize"])
            self.assertIn("owner_alive", lifecycle)
            self.assertIn("child_alive", lifecycle)
            self.assertTrue(lifecycle["prompt_file_exists_at_finalize"])

    def test_prompt_delivery_evidence_in_final_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *args, **kwargs: FakeProcess(
                    json.dumps(
                        {
                            "summary": "Prompt evidence test",
                            "files_changed": [],
                            "validation_run": "",
                            "known_risks": [],
                            "recommendation": "accept",
                        }
                    )
                ),
            )
            record = runtime.start(WorkerPacket(kind="coding", prompt="Evidence test", cwd=tmpdir))
            result = runtime.wait(record.run_id)
            self.assertIn("prompt_delivery", result)
            self.assertTrue(result["prompt_delivery"]["prompt_file_exists"])
            final_path = record.run_dir / "final.json"
            self.assertTrue(final_path.exists())
            final_payload = json.loads(final_path.read_text(encoding="utf-8"))
            self.assertIn("prompt_delivery", final_payload)
            self.assertTrue(final_payload["prompt_delivery"]["prompt_file_exists"])

    def test_cli_execution_mode_argument(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["start", "--kind", "coding", "--prompt", "hello", "--execution-mode", "interactive"])
        self.assertEqual(parsed.execution_mode, "interactive")
        parsed_default = parser.parse_args(["start", "--kind", "coding", "--prompt", "hello"])
        self.assertEqual(parsed_default.execution_mode, "one_shot")

    def test_cli_e2e_execution_mode_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            run_root = tmp_path / "runs"
            result_root = tmp_path / "results"
            if os.name == "nt":
                cli_binary = tmp_path / "claude.cmd"
                cli_binary.write_text(
                    "@echo off\r\n"
                    "echo {\"summary\":\"mode-e2e\",\"files_changed\":[],\"validation_run\":\"\",\"known_risks\":[],\"recommendation\":\"accept\"}\r\n",
                    encoding="utf-8",
                )
            else:
                cli_binary = tmp_path / "claude"
                cli_binary.write_text(
                    '#!/bin/sh\n'
                    'echo \'{"summary":"mode-e2e","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}\'\n',
                    encoding="utf-8",
                )
                cli_binary.chmod(0o755)

            base_args = [
                "--run-root", str(run_root),
                "--result-root", str(result_root),
                "--claude-binary", str(cli_binary),
                "--wait-timeout-seconds", "10",
            ]
            started = self._run_cli(
                base_args + ["start", "--kind", "coding", "--prompt", "e2e-mode", "--cwd", tmpdir, "--task-id", "cli-mode", "--execution-mode", "one_shot"]
            )
            run_id = str(started["run_id"])
            waited = self._run_cli(base_args + ["wait", "--run-id", run_id])
            self.assertEqual(waited["execution_mode"], "one_shot")

    def test_started_event_includes_execution_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ClaudeWorkerRuntime(
                run_root=tmpdir,
                launcher=lambda *a, **kw: FakeProcess('{"summary":"s","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}'),
            )
            record = runtime.start(
                WorkerPacket(kind="coding", prompt="Event mode", cwd=tmpdir, execution_mode="interactive")
            )
            events = (record.run_dir / "events.ndjson").read_text(encoding="utf-8")
            self.assertIn('"execution_mode": "interactive"', events)

    def test_provider_registry_lists_builtins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "providers.json"
            # On first access, providers.json is auto-generated from defaults
            self.assertFalse(db_path.exists())
            registry = ProviderRegistry(db_path=db_path)
            self.assertTrue(db_path.exists())  # auto-created
            providers = registry.list_providers()
        names = [p.name for p in providers]
        self.assertIn("anthropic", names)
        self.assertIn("deepseek", names)
        self.assertIn("qwen-bailian", names)
        self.assertIn("qwen-bailian-coding", names)
        self.assertIn("openrouter", names)
        self.assertIn("z-ai", names)
        self.assertIn("kimi", names)
        self.assertIn("minimax", names)
        self.assertIn("siliconflow", names)

    def test_provider_registry_add_and_remove_custom(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "providers.json"
            registry = ProviderRegistry(db_path=db_path)
            custom = ProviderConfig(name="test-provider", api_key_env="TEST_KEY", base_url="https://api.test.com/v1", models=["test-model-1"])
            registry.add_provider(custom)
            retrieved = registry.get_provider("test-provider")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.base_url, "https://api.test.com/v1")
            # Remove custom provider
            self.assertTrue(registry.remove_provider("test-provider"))
            self.assertIsNone(registry.get_provider("test-provider"))
            # Can also remove any provider (all live in providers.json now)
            self.assertTrue(registry.remove_provider("anthropic"))

    def test_provider_registry_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "providers.json"
            registry = ProviderRegistry(db_path=db_path)
            # Remove a default provider
            registry.remove_provider("anthropic")
            self.assertIsNone(registry.get_provider("anthropic"))
            # Add a custom one
            registry.add_provider(ProviderConfig(name="my-custom", api_key_env="MY_KEY"))
            self.assertIsNotNone(registry.get_provider("my-custom"))
            # Reset — should delete file and re-seed
            registry.db_path.unlink()
            registry._providers.clear()
            registry._seed_from_defaults()
            self.assertIsNotNone(registry.get_provider("anthropic"))  # back
            self.assertIsNone(registry.get_provider("my-custom"))     # gone

    def test_provider_registry_resolves_model_to_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(db_path=Path(tmpdir) / "providers.json")
        # qwen3.6-plus exists in both qwen-bailian-coding (priority=0) and qwen-bailian (priority=5); bailian-coding wins
        provider = registry.resolve_provider_for_model("qwen3.6-plus")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "qwen-bailian-coding")
        # deepseek-chat resolves to deepseek
        ds = registry.resolve_provider_for_model("deepseek-chat")
        self.assertIsNotNone(ds)
        self.assertEqual(ds.name, "deepseek")
        # kimi-k2.5 exists in both qwen-bailian-coding (priority=0) and kimi (priority=10); bailian-coding wins
        kimi = registry.resolve_provider_for_model("kimi-k2.5")
        self.assertIsNotNone(kimi)
        self.assertEqual(kimi.name, "qwen-bailian-coding")
        # glm-4.7 exists in both qwen-bailian-coding (priority=0) and z-ai (priority=10); bailian-coding wins
        glm = registry.resolve_provider_for_model("glm-4.7")
        self.assertIsNotNone(glm)
        self.assertEqual(glm.name, "qwen-bailian-coding")
        # MiniMax-M2.5 exists in both qwen-bailian-coding (priority=0) and minimax (priority=10); bailian-coding wins
        mm = registry.resolve_provider_for_model("MiniMax-M2.5")
        self.assertIsNotNone(mm)
        self.assertEqual(mm.name, "qwen-bailian-coding")
        unknown = registry.resolve_provider_for_model("nonexistent-model-xyz")
        self.assertIsNone(unknown)

    def test_provider_switch_writes_claude_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_dir = Path(tmpdir) / ".claude"
            settings_dir.mkdir()
            settings_path = settings_dir / "settings.json"
            settings_path.write_text('{"model": "opus"}', encoding="utf-8")
            cred_db_path = Path(tmpdir) / "credentials.db"
            cred_store = CredentialStore(db_path=cred_db_path)
            with patch("claude_worker.worker._claude_settings_dir", return_value=settings_dir):
                with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key-123"}):
                    registry = ProviderRegistry(db_path=Path(tmpdir) / "providers.json", cred_store=cred_store)
                    result = registry.switch_active_provider("qwen-bailian")
                    self.assertEqual(result["provider"], "qwen-bailian")
                    self.assertEqual(result["credential_source"], "env_var")
                    self.assertIn("ANTHROPIC_API_KEY", result["env_vars_set"])
                    self.assertIn("ANTHROPIC_BASE_URL", result["env_vars_set"])
                    settings = json.loads(settings_path.read_text(encoding="utf-8"))
                    self.assertEqual(settings["env"]["ANTHROPIC_API_KEY"], "test-key-123")
                    self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://dashscope.aliyuncs.com/apps/anthropic")
                    self.assertEqual(settings["model"], "opus")  # preserved

    def test_check_prerequisites_returns_structure(self) -> None:
        result = check_prerequisites()
        self.assertIn("checks", result)
        self.assertIn("ready", result)
        self.assertIn("missing", result)
        self.assertIn("install_hints", result)
        self.assertIn("python", result["checks"])
        self.assertTrue(result["checks"]["python"]["ok"])

    def test_start_auto_switches_provider_for_known_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_dir = Path(tmpdir) / ".claude"
            settings_dir.mkdir()
            settings_path = settings_dir / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")
            launched = {}

            def launcher(command, **kwargs):
                launched["command"] = command
                return FakeProcess('{"summary":"ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}')

            with patch("claude_worker.worker._claude_settings_dir", return_value=settings_dir):
                with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fake-key"}):
                    runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
                    record = runtime.start(
                        WorkerPacket(kind="coding", prompt="Auto switch", cwd=tmpdir, model="qwen3.6-plus")
                    )
                    meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
                    self.assertEqual(meta["provider"], "qwen-bailian-coding")
                    self.assertIsNotNone(meta["provider_switch"])

    def test_start_with_explicit_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_dir = Path(tmpdir) / ".claude"
            settings_dir.mkdir()
            settings_path = settings_dir / "settings.json"
            settings_path.write_text("{}", encoding="utf-8")

            def launcher(command, **kwargs):
                return FakeProcess('{"summary":"ok","files_changed":[],"validation_run":"","known_risks":[],"recommendation":"accept"}')

            with patch("claude_worker.worker._claude_settings_dir", return_value=settings_dir):
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
                    runtime = ClaudeWorkerRuntime(run_root=tmpdir, launcher=launcher)
                    record = runtime.start(
                        WorkerPacket(kind="coding", prompt="Explicit provider", cwd=tmpdir, provider="deepseek")
                    )
                    meta = json.loads((record.run_dir / "meta.json").read_text(encoding="utf-8"))
                    self.assertEqual(meta["provider"], "deepseek")

    def test_cli_setup_command(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["setup"])
        self.assertEqual(parsed.command, "setup")

    def test_cli_provider_commands(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["provider", "list"])
        self.assertEqual(parsed.command, "provider")
        self.assertEqual(parsed.provider_command, "list")
        parsed = parser.parse_args(["provider", "switch", "deepseek"])
        self.assertEqual(parsed.provider_command, "switch")
        self.assertEqual(parsed.name, "deepseek")
        parsed = parser.parse_args(["provider", "add", "--name", "custom", "--base-url", "https://api.custom.com"])
        self.assertEqual(parsed.provider_command, "add")
        self.assertEqual(parsed.name, "custom")

    def test_cli_start_provider_argument(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["start", "--kind", "coding", "--prompt", "hello", "--provider", "deepseek"])
        self.assertEqual(parsed.provider, "deepseek")

    def test_provider_switch_fails_for_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(db_path=Path(tmpdir) / "providers.json")
            with self.assertRaises(ValueError):
                registry.switch_active_provider("nonexistent-provider")

    def test_verify_provider_no_api_key(self) -> None:
        """verify_provider_endpoint returns error when no API key is set."""
        provider = ProviderConfig(name="test-nokey", api_key_env="NONEXISTENT_KEY_12345", base_url="https://example.com/anthropic")
        with patch("claude_worker.worker._load_claude_env", return_value={}), \
             patch("claude_worker.worker._load_cc_switch_providers", return_value={}):
            result = verify_provider_endpoint(provider, timeout=5.0)
            self.assertFalse(result["ok"])
            self.assertIn("No API key found", result["error"])

    def test_verify_provider_connection_error(self) -> None:
        """verify_provider_endpoint handles connection errors gracefully."""
        provider = ProviderConfig(name="test-connfail", api_key_env="TEST_VERIFY_KEY", base_url="https://0.0.0.0:1/anthropic")
        with patch.dict(os.environ, {"TEST_VERIFY_KEY": "fake-key"}):
            result = verify_provider_endpoint(provider, timeout=2.0)
            self.assertFalse(result["ok"])
            self.assertIsNotNone(result["error"])

    def test_verify_provider_http_error(self) -> None:
        """verify_provider_endpoint handles HTTP errors (e.g. 401)."""
        provider = ProviderConfig(name="test-httperr", api_key_env="TEST_VERIFY_KEY", base_url="https://api.anthropic.com")
        err_body = json.dumps({"error": {"message": "invalid api key"}}).encode()
        exc = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        exc.read = lambda: err_body
        with patch.dict(os.environ, {"TEST_VERIFY_KEY": "fake-key"}):
            with patch("claude_worker.worker.urllib.request.urlopen", side_effect=exc):
                result = verify_provider_endpoint(provider, timeout=5.0)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status_code"], 401)

    def test_verify_provider_success(self) -> None:
        """verify_provider_endpoint returns ok=True on 200 response."""
        provider = ProviderConfig(name="test-ok", api_key_env="TEST_VERIFY_KEY", base_url="https://example.com/anthropic", models=["test-model"])
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"model": "test-model", "content": [{"type": "text", "text": "OK"}]}).encode()
        # Make the mock work as context manager: with urlopen(...) as resp
        mock_resp.__enter__ = unittest.mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        with patch.dict(os.environ, {"TEST_VERIFY_KEY": "fake-key"}):
            with patch("claude_worker.worker.urllib.request.urlopen", return_value=mock_resp):
                result = verify_provider_endpoint(provider, timeout=5.0)
                self.assertTrue(result["ok"])
                self.assertEqual(result["model_used"], "test-model")
                self.assertIsNotNone(result["latency_ms"])


if __name__ == "__main__":
    unittest.main()
