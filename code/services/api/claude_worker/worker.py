from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence


DEFAULT_MODEL = "qwen3.6-plus"
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_REASONING_MODE = "high"
DEFAULT_SANDBOX_KIND = "claude_worker_run_root"
DEFAULT_WAIT_TIMEOUT_SECONDS = 600
DEFAULT_DETACHED_WAIT_TIMEOUT_SECONDS = 5
DEFAULT_DETACHED_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_KILL_GRACE_PERIOD_SECONDS = 5
SUPERVISOR_FILENAME = "supervisor.json"
PROMPT_FILENAME = "prompt.txt"
STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"
EXITCODE_FILENAME = "exitcode.txt"
VALID_EXECUTION_MODES = ("one_shot", "interactive")
DEFAULT_EXECUTION_MODE = "one_shot"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_claude_worker_root() -> Path:
    override = os.environ.get("IKE_CLAUDE_WORKER_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (_repo_root().parent / "_agent-runtimes" / "claude-worker").resolve()


def _resolve_claude_binary(preferred: str) -> str:
    if os.name != "nt":
        resolved = shutil.which(preferred)
        return resolved or preferred

    if Path(preferred).suffix.lower() in {".cmd", ".exe", ".bat"}:
        return shutil.which(preferred) or preferred

    for candidate in (f"{preferred}.cmd", f"{preferred}.exe", f"{preferred}.bat", preferred):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return preferred


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    _ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _append_event(path: Path, event: dict[str, Any]) -> None:
    _ensure_parent(path)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        raise ValueError("expected a list-like value, got mapping")
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _load_packet_from_meta(meta: Any, default_model: str, default_permission_mode: str) -> WorkerPacket:
    if not isinstance(meta, dict):
        raise ValueError("meta.json must be a JSON object")

    packet_meta = meta.get("packet")
    if not isinstance(packet_meta, dict):
        raise ValueError("meta.json packet must be a JSON object")

    kind = packet_meta.get("kind", meta.get("kind"))
    if kind not in {"coding", "review"}:
        raise ValueError(f"invalid worker kind in meta.json: {kind}")

    prompt = packet_meta.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("meta.json packet.prompt is required")

    return WorkerPacket(
        kind=kind,
        prompt=prompt,
        cwd=packet_meta.get("cwd", meta.get("cwd")),
        model=packet_meta.get("model", meta.get("model", default_model)),
        permission_mode=packet_meta.get("permission_mode", meta.get("permission_mode", default_permission_mode)),
        execution_mode=packet_meta.get("execution_mode", meta.get("execution_mode", DEFAULT_EXECUTION_MODE)),
        task_id=packet_meta.get("task_id", meta.get("task_id")),
        title=packet_meta.get("title", meta.get("title")),
        lane=packet_meta.get("lane", meta.get("lane")),
        reasoning_mode=packet_meta.get("reasoning_mode", meta.get("reasoning_mode")),
        sandbox_identity=packet_meta.get("sandbox_identity", meta.get("sandbox_identity")),
        sandbox_kind=packet_meta.get("sandbox_kind", meta.get("sandbox_kind")),
        capability_profile=packet_meta.get("capability_profile", meta.get("capability_profile")),
        write_scope=packet_meta.get("write_scope", meta.get("write_scope")),
        network_policy=packet_meta.get("network_policy", meta.get("network_policy")),
        workspace_root=packet_meta.get("workspace_root", meta.get("workspace_root")),
        runtime_root=packet_meta.get("runtime_root", meta.get("runtime_root")),
        environment_mode=packet_meta.get("environment_mode", meta.get("environment_mode")),
    )


def _default_capability_profile(kind: str, reasoning_mode: str | None) -> str | None:
    if reasoning_mode == "high":
        if kind == "coding":
            return "coding_high_reasoning"
        if kind == "review":
            return "review_high_reasoning"
    return None


def _default_network_policy(kind: str, capability_profile: str | None) -> str | None:
    if capability_profile == "review_high_reasoning":
        return "disabled"
    if capability_profile == "coding_high_reasoning":
        return "restricted"
    if kind == "review":
        return "disabled"
    if kind == "coding":
        return "restricted"
    return None


def _schema_for_kind(kind: str) -> dict[str, Any]:
    if kind == "review":
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "file": {"type": "string"},
                            "start": {"type": "integer"},
                            "end": {"type": "integer"},
                            "priority": {"type": "integer"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["title", "body", "file"],
                        "additionalProperties": True,
                    },
                },
                "validation_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "recommendation": {
                    "type": "string",
                    "enum": ["accept", "accept_with_changes", "reject"],
                },
                "patch_diff": {"type": "string"},
            },
            "required": ["summary", "findings", "validation_gaps", "recommendation"],
            "additionalProperties": True,
        }

    if kind != "coding":
        raise ValueError(f"unknown worker kind: {kind}")

    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "files_changed": {
                "type": "array",
                "items": {"type": "string"},
            },
            "why_this_solution": {"type": "string"},
            "validation_run": {"type": "string"},
            "known_risks": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommendation": {
                "type": "string",
                "enum": ["accept", "accept_with_changes", "reject"],
            },
            "patch_diff": {"type": "string"},
        },
        "required": ["summary", "files_changed", "validation_run", "known_risks", "recommendation"],
        "additionalProperties": True,
    }


def _normalize_base(raw: Any, kind: str) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {"summary": raw}
    if not isinstance(raw, dict):
        raw = {}

    payload = raw.get("structured_output") if isinstance(raw.get("structured_output"), dict) else raw
    if "result" in raw and payload is raw:
        result = raw["result"]
        if isinstance(result, str):
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                payload = {"summary": result}
        elif isinstance(result, dict):
            payload = result
        else:
            payload = {"summary": str(result)}

    recommendation = payload.get("recommendation")
    if recommendation not in {"accept", "accept_with_changes", "reject"}:
        recommendation = "accept_with_changes"

    normalized: dict[str, Any] = {
        "kind": kind,
        "summary": str(payload.get("summary", "")).strip(),
        "recommendation": recommendation,
    }
    normalized["patch_diff"] = str(payload.get("patch_diff", "")).strip()
    normalized["raw_output"] = payload
    normalized["raw_envelope"] = raw
    return normalized


def normalize_coding_result(raw: Any) -> dict[str, Any]:
    normalized = _normalize_base(raw, "coding")
    payload = normalized["raw_output"]
    normalized["files_changed"] = [str(item) for item in _as_list(payload.get("files_changed"))]
    normalized["why_this_solution"] = str(payload.get("why_this_solution", "")).strip()
    normalized["validation_run"] = str(payload.get("validation_run", "")).strip()
    normalized["known_risks"] = [str(item) for item in _as_list(payload.get("known_risks"))]
    return normalized


def normalize_review_result(raw: Any) -> dict[str, Any]:
    normalized = _normalize_base(raw, "review")
    payload = normalized["raw_output"]
    normalized["findings"] = _as_list(payload.get("findings"))
    normalized["validation_gaps"] = [str(item) for item in _as_list(payload.get("validation_gaps"))]
    return normalized


@dataclass(frozen=True)
class WorkerPacket:
    kind: Literal["coding", "review"]
    prompt: str
    cwd: str | None = None
    model: str = DEFAULT_MODEL
    permission_mode: str = DEFAULT_PERMISSION_MODE
    execution_mode: str = DEFAULT_EXECUTION_MODE
    task_id: str | None = None
    title: str | None = None
    lane: str | None = None
    reasoning_mode: str | None = DEFAULT_REASONING_MODE
    sandbox_identity: str | None = None
    sandbox_kind: str | None = DEFAULT_SANDBOX_KIND
    capability_profile: str | None = None
    write_scope: list[str] | None = None
    network_policy: str | None = None
    workspace_root: str | None = None
    runtime_root: str | None = None
    environment_mode: str | None = None


@dataclass
class RunRecord:
    run_id: str
    run_dir: Path
    command: list[str]
    packet: WorkerPacket
    process: Any = field(repr=False)


class ClaudeWorkerRuntime:
    def __init__(
        self,
        run_root: str | Path | None = None,
        result_root: str | Path | None = None,
        claude_binary: str = "claude",
        default_model: str = DEFAULT_MODEL,
        default_permission_mode: str = DEFAULT_PERMISSION_MODE,
        wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
        detached_wait_timeout_seconds: float = DEFAULT_DETACHED_WAIT_TIMEOUT_SECONDS,
        detached_poll_interval_seconds: float = DEFAULT_DETACHED_POLL_INTERVAL_SECONDS,
        kill_grace_period_seconds: float = DEFAULT_KILL_GRACE_PERIOD_SECONDS,
        launcher: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        default_root = _default_claude_worker_root()
        self.run_root = Path(run_root or default_root / "runs").resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.result_root = Path(result_root or _repo_root() / ".runtime" / "delegation" / "results").resolve()
        self.result_root.mkdir(parents=True, exist_ok=True)
        self.claude_binary = _resolve_claude_binary(claude_binary)
        self.default_model = default_model
        self.default_permission_mode = default_permission_mode
        self.wait_timeout_seconds = wait_timeout_seconds
        self.detached_wait_timeout_seconds = detached_wait_timeout_seconds
        self.detached_poll_interval_seconds = detached_poll_interval_seconds
        self.kill_grace_period_seconds = kill_grace_period_seconds
        self.launcher = launcher
        self._records: dict[str, RunRecord] = {}

    def start(self, packet: WorkerPacket) -> RunRecord:
        run_id = f"{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
        run_dir = self.run_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)

        execution_mode = packet.execution_mode or DEFAULT_EXECUTION_MODE
        if execution_mode not in VALID_EXECUTION_MODES:
            raise ValueError(f"invalid execution_mode: {execution_mode!r}; must be one of {VALID_EXECUTION_MODES}")

        model = packet.model or self.default_model
        permission_mode = packet.permission_mode or self.default_permission_mode
        reasoning_mode = packet.reasoning_mode or DEFAULT_REASONING_MODE
        sandbox_kind = packet.sandbox_kind or DEFAULT_SANDBOX_KIND
        capability_profile = packet.capability_profile or _default_capability_profile(packet.kind, reasoning_mode)
        network_policy = packet.network_policy or _default_network_policy(packet.kind, capability_profile)
        schema = _schema_for_kind(packet.kind)
        schema_json = json.dumps(schema, ensure_ascii=False)

        # Write prompt to durable file BEFORE building the command.
        # This is the single source of truth for prompt delivery.
        prompt_path = run_dir / PROMPT_FILENAME
        _write_text(prompt_path, packet.prompt)
        prompt_delivery = {
            "method": "file_and_stdin",
            "prompt_file": str(prompt_path),
            "prompt_file_size": len(packet.prompt.encode("utf-8")),
            "prompt_file_written_at": _utc_now().isoformat(),
            "mode": execution_mode,
        }

        # Build command based on execution mode:
        # one_shot: uses -p flag with prompt text (print mode, Claude exits after responding)
        # interactive: no -p flag (for future PTY/tmux support; currently same-dir with stdin pipe)
        if execution_mode == "one_shot":
            command = [
                self.claude_binary,
                "-p",
                "--output-format",
                "json",
                "--model",
                model,
                "--permission-mode",
                permission_mode,
                "--json-schema",
                schema_json,
                packet.prompt,
            ]
        else:
            # interactive mode: no -p flag; prompt delivered via stdin pipe from file
            command = [
                self.claude_binary,
                "--output-format",
                "json",
                "--model",
                model,
                "--permission-mode",
                permission_mode,
                "--json-schema",
                schema_json,
            ]

        meta = {
            "run_id": run_id,
            "kind": packet.kind,
            "task_id": packet.task_id,
            "title": packet.title,
            "lane": packet.lane,
            "execution_mode": execution_mode,
            "reasoning_mode": reasoning_mode,
            "sandbox_identity": packet.sandbox_identity,
            "sandbox_kind": sandbox_kind,
            "capability_profile": capability_profile,
            "write_scope": packet.write_scope or [],
            "network_policy": network_policy,
            "workspace_root": packet.workspace_root,
            "runtime_root": packet.runtime_root,
            "environment_mode": packet.environment_mode,
            "model": model,
            "permission_mode": permission_mode,
            "claude_binary": self.claude_binary,
            "cwd": packet.cwd,
            "command": command,
            "owner_pid": os.getpid(),
            "ownership_mode": "single-process",
            "prompt_delivery": prompt_delivery,
            "detached_wait_contract": {
                "wait_mode": "poll-final-json",
                "abort_requires_owner": True,
                "resume_via": ["fetch", "wait"],
                "limitation": "cross-process wait is not a complete solution; owner process must stay alive or a detached poll/fetch must be used",
                "resume_contract": {
                    "fetch": "Reads durable artifacts (final.json, exitcode.txt) to reconstruct run state without owner process",
                    "wait": "Polls for final.json with configurable timeout; returns running snapshot if not yet finalized",
                    "abort": "Attempts to terminate child process by PID; requires owner process alive or OS-level process access",
                },
            },
            "created_at": _utc_now().isoformat(),
            "status": "running",
            "packet": {
                "kind": packet.kind,
                "prompt": packet.prompt,
                "cwd": packet.cwd,
                "model": model,
                "permission_mode": permission_mode,
                "execution_mode": execution_mode,
                "task_id": packet.task_id,
                "title": packet.title,
                "lane": packet.lane,
                "reasoning_mode": reasoning_mode,
                "sandbox_identity": packet.sandbox_identity,
                "sandbox_kind": sandbox_kind,
                "capability_profile": capability_profile,
                "write_scope": packet.write_scope or [],
                "network_policy": network_policy,
                "workspace_root": packet.workspace_root,
                "runtime_root": packet.runtime_root,
                "environment_mode": packet.environment_mode,
            },
        }
        _write_json(run_dir / "meta.json", meta)
        _append_event(run_dir / "events.ndjson", {"event": "started", "run_id": run_id, "execution_mode": execution_mode, "created_at": meta["created_at"]})
        _write_text(run_dir / "summary.md", f"Run {run_id} started for {packet.kind} (mode={execution_mode}).\n")
        _write_text(run_dir / "patch.diff", "")
        supervisor = {
            "run_id": run_id,
            "owner_pid": os.getpid(),
            "ownership_mode": "single-process",
            "execution_mode": execution_mode,
            "strategy": "poll-final-json",
            "resume_via": ["fetch", "wait"],
            "limitation": "cross-process wait is not complete; if owner exits unexpectedly, use fetch to reconstruct state from durable artifacts",
            "created_at": meta["created_at"],
        }
        _write_json(run_dir / SUPERVISOR_FILENAME, supervisor)
        process = self._start_process(run_dir, command, packet)
        meta["child_pid"] = getattr(process, "pid", None)
        # Verify prompt file persisted after process start (durable truth)
        prompt_verified = prompt_path.exists() and prompt_path.stat().st_size == prompt_delivery["prompt_file_size"]
        prompt_delivery["prompt_file_verified_after_start"] = prompt_verified
        meta["prompt_delivery"] = prompt_delivery
        _write_json(run_dir / "meta.json", meta)
        if prompt_verified:
            _append_event(run_dir / "events.ndjson", {"event": "prompt_delivery_verified", "run_id": run_id, "at": _utc_now().isoformat()})
        else:
            _append_event(run_dir / "events.ndjson", {"event": "prompt_delivery_verification_failed", "run_id": run_id, "at": _utc_now().isoformat()})
        record = RunRecord(run_id=run_id, run_dir=run_dir, command=command, packet=packet, process=process)
        self._records[run_id] = record
        return record

    def wait(self, run_id: str) -> dict[str, Any]:
        record = self._get_record(run_id)
        final_path = record.run_dir / "final.json"
        if record.process is None:
            if final_path.exists():
                return _read_json(final_path)
            return self._detached_wait(record)
        _append_event(
            record.run_dir / "events.ndjson",
            {"event": "wait_communicate_start", "run_id": run_id, "timeout_seconds": self.wait_timeout_seconds, "at": _utc_now().isoformat()},
        )
        try:
            stdout, stderr = record.process.communicate(timeout=self.wait_timeout_seconds)
        except subprocess.TimeoutExpired:
            terminated = False
            killed = False
            _append_event(
                record.run_dir / "events.ndjson",
                {
                    "event": "wait_timeout",
                    "run_id": run_id,
                    "timeout_seconds": self.wait_timeout_seconds,
                    "at": _utc_now().isoformat(),
                },
            )
            if hasattr(record.process, "terminate"):
                record.process.terminate()
                terminated = True
                _append_event(
                    record.run_dir / "events.ndjson",
                    {"event": "terminate_requested", "run_id": run_id, "grace_period_seconds": self.kill_grace_period_seconds, "at": _utc_now().isoformat()},
                )
            try:
                stdout, stderr = record.process.communicate(timeout=self.kill_grace_period_seconds)
            except Exception:
                if hasattr(record.process, "kill"):
                    record.process.kill()
                    killed = True
                    _append_event(
                        record.run_dir / "events.ndjson",
                        {"event": "kill_requested", "run_id": run_id, "at": _utc_now().isoformat()},
                    )
                try:
                    stdout, stderr = record.process.communicate(timeout=self.kill_grace_period_seconds)
                except Exception:
                    if hasattr(record.process, "wait"):
                        try:
                            record.process.wait(timeout=1)
                        except Exception:
                            pass
                    stdout, stderr = "", ""
            stdout = stdout or ""
            stderr = (stderr or "") + f"\nTimed out after {self.wait_timeout_seconds} seconds"
            return self._finalize(
                record,
                stdout,
                stderr,
                status="failed",
                returncode=124,
                lifecycle={
                    "timeout": True,
                    "terminate_requested": terminated,
                    "kill_requested": killed,
                    "kill_grace_period_seconds": self.kill_grace_period_seconds,
                },
            )
        _append_event(
            record.run_dir / "events.ndjson",
            {"event": "wait_communicate_done", "run_id": run_id, "at": _utc_now().isoformat()},
        )
        returncode = self._process_returncode(record.process)
        status = "succeeded" if returncode == 0 else "failed"
        return self._finalize(record, stdout or "", stderr or "", status=status, returncode=returncode)

    def abort(self, run_id: str) -> dict[str, Any]:
        record = self._get_record(run_id)
        final_path = record.run_dir / "final.json"
        if record.process is None:
            if final_path.exists():
                return _read_json(final_path)
            meta = _read_json(record.run_dir / "meta.json")
            owner_pid = meta.get("owner_pid")
            child_pid = meta.get("child_pid")
            terminated, detail = self._terminate_detached_child(child_pid)
            _append_event(
                record.run_dir / "events.ndjson",
                {
                    "event": "detached_abort_requested",
                    "run_id": run_id,
                    "owner_pid": owner_pid,
                    "child_pid": child_pid,
                    "terminated": terminated,
                    "at": _utc_now().isoformat(),
                },
            )
            if not terminated:
                raise RuntimeError(
                    f"Run {run_id} is detached from this process (owner_pid={owner_pid}); detached abort failed: {detail}"
                )
            detached_raw = self._detached_abort_envelope(record.packet.kind, owner_pid=owner_pid, child_pid=child_pid)
            return self._finalize(
                record,
                json.dumps(detached_raw, ensure_ascii=False),
                detail,
                status="aborted",
                returncode=143,
                lifecycle={
                    "detached_abort": True,
                    "owner_pid": owner_pid,
                    "child_pid": child_pid,
                },
            )
        process = record.process
        if hasattr(process, "poll") and process.poll() is None:
            if hasattr(process, "terminate"):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except TypeError:
                stdout, stderr = process.communicate()
            except Exception:
                if hasattr(process, "kill"):
                    process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    if hasattr(process, "wait"):
                        try:
                            process.wait(timeout=1)
                        except Exception:
                            pass
                    stdout, stderr = "", ""
        else:
            stdout, stderr = process.communicate()
        return self._finalize(record, stdout or "", stderr or "", status="aborted", returncode=self._process_returncode(process))

    def fetch(self, run_id: str) -> dict[str, Any]:
        record = self._get_record(run_id)
        self._maybe_finalize_detached(record)
        meta_path = record.run_dir / "meta.json"
        final_path = record.run_dir / "final.json"
        summary_path = record.run_dir / "summary.md"
        patch_path = record.run_dir / "patch.diff"
        events_path = record.run_dir / "events.ndjson"
        supervisor_path = record.run_dir / SUPERVISOR_FILENAME
        return {
            "run_id": run_id,
            "run_dir": str(record.run_dir),
            "meta": _read_json(meta_path) if meta_path.exists() else None,
            "final": _read_json(final_path) if final_path.exists() else None,
            "summary": summary_path.read_text(encoding="utf-8") if summary_path.exists() else None,
            "patch_diff": patch_path.read_text(encoding="utf-8") if patch_path.exists() else None,
            "events": events_path.read_text(encoding="utf-8") if events_path.exists() else None,
            "supervisor": _read_json(supervisor_path) if supervisor_path.exists() else None,
        }

    def _start_process(self, run_dir: Path, command: list[str], packet: WorkerPacket) -> Any:
        if self.launcher is not subprocess.Popen:
            return self.launcher(
                command,
                cwd=packet.cwd or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        prompt_path = run_dir / PROMPT_FILENAME
        stdout_path = run_dir / STDOUT_FILENAME
        stderr_path = run_dir / STDERR_FILENAME
        exitcode_path = run_dir / EXITCODE_FILENAME
        # prompt.txt is already written by start() before calling this method.
        # The wrapper script reads it from disk, ensuring durable prompt delivery
        # even if the owner process dies after spawning the child.
        launcher_code = (
            "import json, pathlib, subprocess, sys\n"
            "command = json.loads(sys.argv[1])\n"
            "cwd = None if sys.argv[2] == '-' else sys.argv[2]\n"
            "prompt_path = pathlib.Path(sys.argv[3])\n"
            "stdout_path = pathlib.Path(sys.argv[4])\n"
            "stderr_path = pathlib.Path(sys.argv[5])\n"
            "exitcode_path = pathlib.Path(sys.argv[6])\n"
            "prompt = prompt_path.read_text(encoding='utf-8')\n"
            "returncode = 1\n"
            "try:\n"
            "    with stdout_path.open('w', encoding='utf-8', newline='\\n') as out, stderr_path.open('w', encoding='utf-8', newline='\\n') as err:\n"
            "        proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True)\n"
            "        if proc.stdin is not None:\n"
            "            proc.stdin.write(prompt)\n"
            "            proc.stdin.close()\n"
            "        returncode = proc.wait()\n"
            "except Exception as exc:\n"
            "    stderr_path.write_text(str(exc), encoding='utf-8')\n"
            "finally:\n"
            "    exitcode_path.write_text(str(returncode), encoding='utf-8')\n"
            "sys.exit(returncode)\n"
        )
        wrapper_command = [
            sys.executable,
            "-c",
            launcher_code,
            json.dumps(command, ensure_ascii=False),
            packet.cwd or "-",
            str(prompt_path),
            str(stdout_path),
            str(stderr_path),
            str(exitcode_path),
        ]
        return self.launcher(
            wrapper_command,
            cwd=packet.cwd or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def _detached_artifact_paths(self, run_dir: Path) -> dict[str, Path]:
        return {
            "stdout": run_dir / STDOUT_FILENAME,
            "stderr": run_dir / STDERR_FILENAME,
            "exitcode": run_dir / EXITCODE_FILENAME,
        }

    def _read_exitcode(self, path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (TypeError, ValueError):
            return None

    def _process_exists(self, pid: Any) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                output = (result.stdout or "") + "\n" + (result.stderr or "")
                return str(pid) in output
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _maybe_finalize_detached(self, record: RunRecord) -> dict[str, Any] | None:
        if record.process is not None:
            return None
        final_path = record.run_dir / "final.json"
        if final_path.exists():
            return _read_json(final_path)
        meta_path = record.run_dir / "meta.json"
        if not meta_path.exists():
            return None
        meta = _read_json(meta_path)
        artifact_paths = self._detached_artifact_paths(record.run_dir)
        exitcode = self._read_exitcode(artifact_paths["exitcode"])
        child_pid = meta.get("child_pid")
        owner_pid = meta.get("owner_pid")
        child_alive = self._process_exists(child_pid)
        owner_alive = self._process_exists(owner_pid)

        if exitcode is None and child_alive:
            return None

        # Verify prompt file durability at finalization time
        prompt_path = record.run_dir / PROMPT_FILENAME
        prompt_file_exists = prompt_path.exists()

        stdout = _read_text_if_exists(artifact_paths["stdout"])
        stderr = _read_text_if_exists(artifact_paths["stderr"])
        if exitcode is None:
            exitcode = 1
            if not stderr.strip():
                stderr = "Detached run exited without a durable exitcode record."
        status = "succeeded" if exitcode == 0 else "failed"
        lifecycle = {
            "detached_finalize": True,
            "child_pid": child_pid,
            "owner_pid": owner_pid,
            "owner_alive": owner_alive,
            "child_alive": child_alive,
            "prompt_file_exists_at_finalize": prompt_file_exists,
        }
        return self._finalize(record, stdout, stderr, status=status, returncode=exitcode, lifecycle=lifecycle)

    def _detached_wait(self, record: RunRecord) -> dict[str, Any]:
        final_path = record.run_dir / "final.json"
        deadline = _utc_now().timestamp() + max(0.0, self.detached_wait_timeout_seconds)
        while _utc_now().timestamp() < deadline:
            finalized = self._maybe_finalize_detached(record)
            if finalized is not None:
                return finalized
            if final_path.exists():
                return _read_json(final_path)
            sleep_seconds = max(0.01, self.detached_poll_interval_seconds)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

        meta_path = record.run_dir / "meta.json"
        meta = _read_json(meta_path) if meta_path.exists() else {}
        return {
            "run_id": record.run_id,
            "kind": record.packet.kind,
            "task_id": record.packet.task_id,
            "status": "running",
            "recommendation": "accept_with_changes",
            "detached_wait": {
                "state": "timed_out",
                "owner_pid": meta.get("owner_pid"),
                "child_pid": meta.get("child_pid"),
                "wait_timeout_seconds": self.detached_wait_timeout_seconds,
                "poll_interval_seconds": self.detached_poll_interval_seconds,
                "strategy": meta.get("detached_wait_contract", {}),
            },
            "message": "Run is still owned by another process; retry wait/fetch or query owner process.",
        }

    def _detached_abort_envelope(self, kind: Literal["coding", "review"], owner_pid: Any, child_pid: Any) -> dict[str, Any]:
        summary = f"Detached abort requested for child_pid={child_pid} (owner_pid={owner_pid})."
        if kind == "review":
            return {
                "summary": summary,
                "findings": [],
                "validation_gaps": [],
                "recommendation": "accept_with_changes",
            }
        return {
            "summary": summary,
            "files_changed": [],
            "why_this_solution": "",
            "validation_run": "",
            "known_risks": ["Detached abort finalization does not include child stdout/stderr replay."],
            "recommendation": "accept_with_changes",
        }

    def _terminate_detached_child(self, child_pid: Any) -> tuple[bool, str]:
        if not isinstance(child_pid, int) or child_pid <= 0:
            return False, "invalid child_pid"
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()
                    return False, detail or f"taskkill failed with code {result.returncode}"
                return True, (result.stdout or "taskkill succeeded").strip()
            os.kill(child_pid, signal.SIGTERM)
            return True, f"SIGTERM sent to pid {child_pid}"
        except Exception as exc:
            return False, str(exc)

    def _get_record(self, run_id: str) -> RunRecord:
        record = self._records.get(run_id)
        if record is not None:
            return record
        meta_path = self.run_root / run_id / "meta.json"
        if not meta_path.exists():
            raise KeyError(f"Unknown run_id: {run_id}")
        try:
            meta = _read_json(meta_path)
            packet = _load_packet_from_meta(meta, self.default_model, self.default_permission_mode)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid persisted run metadata for {run_id}: {exc}") from exc
        record = RunRecord(
            run_id=run_id,
            run_dir=self.run_root / run_id,
            command=list(meta.get("command", [])),
            packet=packet,
            process=None,
        )
        self._records[run_id] = record
        return record

    def _process_returncode(self, process: Any) -> int:
        returncode = getattr(process, "returncode", None)
        if returncode is None and hasattr(process, "wait"):
            returncode = process.wait()
        return int(returncode or 0)

    def _finalize(
        self,
        record: RunRecord,
        stdout: str,
        stderr: str,
        *,
        status: str,
        returncode: int,
        lifecycle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_payload: Any
        try:
            raw_payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            raw_payload = {"summary": stdout.strip(), "raw_stdout": stdout}

        try:
            if record.packet.kind == "review":
                normalized = normalize_review_result(raw_payload)
            else:
                normalized = normalize_coding_result(raw_payload)
            normalization_error = None
        except (TypeError, ValueError) as exc:
            normalization_error = str(exc)
            normalized = {
                "kind": record.packet.kind,
                "summary": f"Invalid structured output: {exc}",
                "recommendation": "reject",
                "patch_diff": "",
                "raw_output": raw_payload,
                "raw_envelope": raw_payload,
            }
            if record.packet.kind == "review":
                normalized["findings"] = []
                normalized["validation_gaps"] = [str(exc)]
            else:
                normalized["files_changed"] = []
                normalized["why_this_solution"] = ""
                normalized["validation_run"] = ""
                normalized["known_risks"] = [str(exc)]

        final_payload: dict[str, Any] = {
            "run_id": record.run_id,
            "kind": record.packet.kind,
            "task_id": record.packet.task_id,
            "title": record.packet.title,
            "lane": record.packet.lane,
            "execution_mode": record.packet.execution_mode or DEFAULT_EXECUTION_MODE,
            "reasoning_mode": record.packet.reasoning_mode or DEFAULT_REASONING_MODE,
            "sandbox_identity": record.packet.sandbox_identity,
            "sandbox_kind": record.packet.sandbox_kind or DEFAULT_SANDBOX_KIND,
            "capability_profile": record.packet.capability_profile or _default_capability_profile(record.packet.kind, record.packet.reasoning_mode or DEFAULT_REASONING_MODE),
            "write_scope": record.packet.write_scope or [],
            "network_policy": record.packet.network_policy or _default_network_policy(
                record.packet.kind,
                record.packet.capability_profile or _default_capability_profile(record.packet.kind, record.packet.reasoning_mode or DEFAULT_REASONING_MODE),
            ),
            "workspace_root": record.packet.workspace_root,
            "runtime_root": record.packet.runtime_root,
            "environment_mode": record.packet.environment_mode,
            "model": record.packet.model,
            "permission_mode": record.packet.permission_mode,
            "status": "failed" if normalization_error is not None else status,
            "returncode": returncode,
            "summary": normalized.get("summary", ""),
            "recommendation": normalized.get("recommendation", "accept_with_changes"),
            "patch_diff": normalized.get("patch_diff", ""),
            "raw_output": normalized.get("raw_output", {}),
            "stdout": stdout,
            "stderr": stderr,
        }
        if lifecycle:
            final_payload["lifecycle"] = lifecycle
        if normalization_error is not None:
            final_payload["normalization_error"] = normalization_error
            if final_payload["returncode"] == 0:
                final_payload["returncode"] = 1
        if record.packet.kind == "review":
            final_payload["findings"] = normalized.get("findings", [])
            final_payload["validation_gaps"] = normalized.get("validation_gaps", [])
        else:
            final_payload["files_changed"] = normalized.get("files_changed", [])
            final_payload["why_this_solution"] = normalized.get("why_this_solution", "")
            final_payload["validation_run"] = normalized.get("validation_run", "")
            final_payload["known_risks"] = normalized.get("known_risks", [])

        prompt_path = record.run_dir / PROMPT_FILENAME
        if prompt_path.exists():
            final_payload["prompt_delivery"] = {
                "method": "file_and_stdin",
                "prompt_file": str(prompt_path),
                "prompt_file_exists": True,
                "prompt_file_size": prompt_path.stat().st_size,
            }

        meta_path = record.run_dir / "meta.json"
        meta = _read_json(meta_path) if meta_path.exists() else {}
        final_status = final_payload["status"]
        final_returncode = final_payload["returncode"]
        meta["status"] = final_status
        meta["returncode"] = final_returncode
        meta["finished_at"] = _utc_now().isoformat()
        _write_json(meta_path, meta)
        _append_event(
            record.run_dir / "events.ndjson",
            {
                "event": "finished" if status != "aborted" else "aborted",
                "run_id": record.run_id,
                "status": final_status,
                "returncode": final_returncode,
                "finished_at": meta["finished_at"],
            },
        )
        _write_json(record.run_dir / "final.json", final_payload)
        self._write_harness_result(record, final_payload)
        summary_lines = [
            f"# Run {record.run_id}",
            "",
            f"- kind: {record.packet.kind}",
            f"- status: {final_status}",
            f"- returncode: {final_returncode}",
            f"- model: {record.packet.model}",
            "",
            normalized.get("summary", "").strip(),
        ]
        _write_text(record.run_dir / "summary.md", "\n".join(summary_lines).strip() + "\n")
        _write_text(record.run_dir / "patch.diff", normalized.get("patch_diff", ""))
        return final_payload

    def _write_harness_result(self, record: RunRecord, final_payload: dict[str, Any]) -> None:
        result_payload: dict[str, Any] = {
            "protocol": "delegation_result.v1",
            "worker": "claude-worker",
            "run_id": record.run_id,
            "task_id": record.packet.task_id,
            "lane": record.packet.lane,
            "execution_mode": record.packet.execution_mode or DEFAULT_EXECUTION_MODE,
            "reasoning_mode": record.packet.reasoning_mode or DEFAULT_REASONING_MODE,
            "sandbox_identity": record.packet.sandbox_identity,
            "sandbox_kind": record.packet.sandbox_kind or DEFAULT_SANDBOX_KIND,
            "capability_profile": record.packet.capability_profile or _default_capability_profile(record.packet.kind, record.packet.reasoning_mode or DEFAULT_REASONING_MODE),
            "write_scope": record.packet.write_scope or [],
            "network_policy": record.packet.network_policy or _default_network_policy(
                record.packet.kind,
                record.packet.capability_profile or _default_capability_profile(record.packet.kind, record.packet.reasoning_mode or DEFAULT_REASONING_MODE),
            ),
            "kind": final_payload.get("kind"),
            "status": final_payload.get("status"),
            "recommendation": final_payload.get("recommendation"),
            "summary": final_payload.get("summary", ""),
            "run_dir": str(record.run_dir),
            "artifacts": {
                "meta": str(record.run_dir / "meta.json"),
                "events": str(record.run_dir / "events.ndjson"),
                "final": str(record.run_dir / "final.json"),
                "summary": str(record.run_dir / "summary.md"),
                "patch_diff": str(record.run_dir / "patch.diff"),
                "supervisor": str(record.run_dir / SUPERVISOR_FILENAME),
            },
        }
        if record.packet.kind == "review":
            result_payload["findings"] = final_payload.get("findings", [])
            result_payload["validation_gaps"] = final_payload.get("validation_gaps", [])
        else:
            result_payload["files_changed"] = final_payload.get("files_changed", [])
            result_payload["why_this_solution"] = final_payload.get("why_this_solution", "")
            result_payload["validation_run"] = final_payload.get("validation_run", "")
            result_payload["known_risks"] = final_payload.get("known_risks", [])

        if "prompt_delivery" in final_payload:
            result_payload["prompt_delivery"] = final_payload["prompt_delivery"]

        result_key = (record.packet.task_id or record.run_id).replace("\\", "_").replace("/", "_")
        _write_json(self.result_root / f"{result_key}.json", result_payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-worker")
    parser.add_argument("--run-root", default=None)
    parser.add_argument("--claude-binary", default="claude")
    parser.add_argument("--default-model", default=DEFAULT_MODEL)
    parser.add_argument("--default-permission-mode", default=DEFAULT_PERMISSION_MODE)
    parser.add_argument("--wait-timeout-seconds", type=float, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--detached-wait-timeout-seconds", type=float, default=DEFAULT_DETACHED_WAIT_TIMEOUT_SECONDS)
    parser.add_argument("--detached-poll-interval-seconds", type=float, default=DEFAULT_DETACHED_POLL_INTERVAL_SECONDS)
    parser.add_argument("--kill-grace-period-seconds", type=float, default=DEFAULT_KILL_GRACE_PERIOD_SECONDS)
    parser.add_argument("--result-root", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--kind", choices=["coding", "review"], required=True)
    start.add_argument("--prompt", required=True)
    start.add_argument("--cwd", default=None)
    start.add_argument("--model", default=None)
    start.add_argument("--permission-mode", default=None)
    start.add_argument("--execution-mode", choices=list(VALID_EXECUTION_MODES), default=DEFAULT_EXECUTION_MODE)
    start.add_argument("--task-id", default=None)
    start.add_argument("--title", default=None)
    start.add_argument("--lane", default=None)
    start.add_argument("--reasoning-mode", default=DEFAULT_REASONING_MODE)
    start.add_argument("--sandbox-identity", default=None)
    start.add_argument("--sandbox-kind", default=DEFAULT_SANDBOX_KIND)
    start.add_argument("--capability-profile", default=None)
    start.add_argument("--write-scope", action="append", default=[])
    start.add_argument("--network-policy", default=None)
    start.add_argument("--workspace-root", default=None)
    start.add_argument("--runtime-root", default=None)
    start.add_argument("--environment-mode", default=None)

    for name in ("wait", "fetch", "abort"):
        cmd = subparsers.add_parser(name)
        cmd.add_argument("--run-id", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime = ClaudeWorkerRuntime(
        run_root=args.run_root,
        result_root=args.result_root,
        claude_binary=args.claude_binary,
        default_model=args.default_model,
        default_permission_mode=args.default_permission_mode,
        wait_timeout_seconds=args.wait_timeout_seconds,
        detached_wait_timeout_seconds=args.detached_wait_timeout_seconds,
        detached_poll_interval_seconds=args.detached_poll_interval_seconds,
        kill_grace_period_seconds=args.kill_grace_period_seconds,
    )

    if args.command == "start":
        packet = WorkerPacket(
            kind=args.kind,
            prompt=args.prompt,
            cwd=args.cwd,
            model=args.model or args.default_model,
            permission_mode=args.permission_mode or args.default_permission_mode,
            execution_mode=args.execution_mode,
            task_id=args.task_id,
            title=args.title,
            lane=args.lane,
            reasoning_mode=args.reasoning_mode,
            sandbox_identity=args.sandbox_identity,
            sandbox_kind=args.sandbox_kind,
            capability_profile=args.capability_profile,
            write_scope=args.write_scope or [],
            network_policy=args.network_policy,
            workspace_root=args.workspace_root,
            runtime_root=args.runtime_root,
            environment_mode=args.environment_mode,
        )
        record = runtime.start(packet)
        print(json.dumps({"run_id": record.run_id, "run_dir": str(record.run_dir)}, ensure_ascii=False))
        return 0

    if args.command == "wait":
        print(json.dumps(runtime.wait(args.run_id), ensure_ascii=False))
        return 0

    if args.command == "fetch":
        print(json.dumps(runtime.fetch(args.run_id), ensure_ascii=False))
        return 0

    if args.command == "abort":
        print(json.dumps(runtime.abort(args.run_id), ensure_ascii=False))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
