"""Claude Worker — a CLI-driven Claude Code executor.

Three invocation patterns, each suited to a different caller model:

─────────────────────────────────────────────────────────────────
 1. Task Mode  (one_shot / detached)
─────────────────────────────────────────────────────────────────
    start → wait → fetch

    The caller submits a single task and gets back a result.  The CC
    process runs once, writes durable artifacts (final.json, stdout.txt,
    exitcode.txt), then exits.  "detached" is the same workflow except
    the caller can detach after start and poll/fetch later.

    CLI:   start --execution-mode one_shot|detached → wait → fetch

    When to use:
      • Controller-driven delegation (fire-and-forget tasks)
      • CI/CD pipelines, batch jobs
      • Any scenario where you want a clean request/response boundary

─────────────────────────────────────────────────────────────────
 2. Session Chain Mode  (continue)
─────────────────────────────────────────────────────────────────
    start → wait → fetch → continue → wait → fetch → ...

    Each turn is a *separate* CC invocation, but linked via CC's native
    --resume flag so the model retains full conversation context across
    turns.  The caller can inspect results between turns and decide what
    to do next.  Between turns the CC process is NOT alive — only its
    server-side session state persists.

    CLI:   start → wait → fetch   (first turn)
           continue --run-id <id> --prompt "..."   (subsequent turns)

    When to use:
      • Iterative coding workflows (code → review → fix → test)
      • Human-in-the-loop decisions between turns
      • When you need to inspect results before deciding the next step
      • When the caller process may restart between turns

    Key property: each "continue" is a brand-new subprocess.  If the
    caller crashes, it can resume by running "continue" with the
    previous run-id — no in-memory state is lost.

─────────────────────────────────────────────────────────────────
 3. Live Session Mode  (LongRunSession)
─────────────────────────────────────────────────────────────────
    session-start → session-send / session-capture → session-stop

    A single CC process stays alive for the entire session.  The caller
    can inject follow-up prompts at any time via CC's bidirectional
    streaming protocol (--input-format stream-json).  The process
    reads prompts from stdin and writes JSON events to stdout in real
    time.  It only exits when the caller closes stdin or calls
    session-stop.

    CLI:   session-start --prompt "..."
           session-send    --session-id <id> --prompt "..."
           session-capture --session-id <id>
           session-stop    --session-id <id>

    Programmatic API:
           session = LongRunSession(packet)
           session.start()       # starts CC process, sends initial prompt
           session.send("...")   # injects follow-up prompt (process is alive)
           session.capture()     # returns buffered output
           session.stop()        # gracefully terminates CC process

    When to use:
      • Agent-to-agent collaboration where one agent drives CC
        interactively, sending prompts while CC works
      • Real-time monitoring + control (capture output, then send
        course-correction prompts without restarting)
      • Long exploratory sessions where context must never be lost
        and restart latency is unacceptable

    Key property: the CC process is alive between prompts.  If the
    caller process dies, the session is lost (no durable artifacts
    between prompts).  For crash-resilient multi-turn, use Session
    Chain Mode instead.

─────────────────────────────────────────────────────────────────
 Quick comparison
─────────────────────────────────────────────────────────────────
                    Task      Session Chain    Live Session
  ─────────────────────────────────────────────────────────
  CC process        exits     exits per turn   stays alive
  Context kept?     no        yes (--resume)   yes (in-process)
  Crash-resilient?  yes       yes              no
  Latency per turn  cold      warm (cache)     hot (live)
  Inject mid-run?   no        no               yes
  Cross-process?    yes       yes              no (in-memory)

"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence


DEFAULT_MODEL = "qwen3.6-plus"
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_REASONING_MODE = "high"
VALID_EFFORT_LEVELS = ("low", "medium", "high", "max")
DEFAULT_EFFORT = "high"
DEFAULT_SANDBOX_KIND = "claude_worker_run_root"
DEFAULT_WAIT_TIMEOUT_SECONDS = 600
DEFAULT_DETACHED_WAIT_TIMEOUT_SECONDS = 5
DEFAULT_DETACHED_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_KILL_GRACE_PERIOD_SECONDS = 5
DEFAULT_SESSION_POLL_INTERVAL = 0.5
SUPERVISOR_FILENAME = "supervisor.json"
PROMPT_FILENAME = "prompt.txt"
STDOUT_FILENAME = "stdout.txt"
STDERR_FILENAME = "stderr.txt"
EXITCODE_FILENAME = "exitcode.txt"
SESSIONS_DIR_NAME = "sessions"
VALID_EXECUTION_MODES = ("one_shot", "interactive", "detached")
DEFAULT_EXECUTION_MODE = "one_shot"
PROVIDERS_FILENAME = "providers.json"
CONFIG_DIR_NAME = "config"
CREDENTIALS_DB_FILENAME = "credentials.db"
RUNTIME_DIR_NAME = ".claude-worker"
CLAUDE_SETTINGS_DIR_NAME = ".claude"
CLAUDE_SETTINGS_FILENAME = "settings.json"
RUNTIME_ROOT_ENV = "CLAUDE_WORKER_HOME"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_claude_worker_root() -> Path:
    override = os.environ.get(RUNTIME_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / RUNTIME_DIR_NAME


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
        provider=packet_meta.get("provider", meta.get("provider")),
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
        effort=packet_meta.get("effort", meta.get("effort", DEFAULT_EFFORT)),
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


def _claude_settings_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return base / CLAUDE_SETTINGS_DIR_NAME


def _claude_settings_path() -> Path:
    return _claude_settings_dir() / CLAUDE_SETTINGS_FILENAME


def _providers_db_path() -> Path:
    return _default_claude_worker_root() / CONFIG_DIR_NAME / PROVIDERS_FILENAME


def _default_credentials_path() -> Path:
    return _default_claude_worker_root() / CREDENTIALS_DB_FILENAME


class CredentialStore:
    """Encrypted credential store for provider API keys.

    Storage: SQLite DB at <runtime-root>/credentials.db
    Schema:  credentials(provider_name TEXT PK, key_type TEXT, encrypted_value TEXT, salt TEXT)
    Encryption: XOR with key derived from SHA-256(username + hostname + salt), then base64-encoded.
    This is obfuscation (not cryptographic-grade), but prevents plaintext reading of the DB file.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_credentials_path()
        self._migrate_from_legacy_path()
        self._ensure_db()

    def _migrate_from_legacy_path(self) -> None:
        """One-time migration: if new path doesn't exist but legacy _agent-runtimes/ path does, copy it."""
        if self.db_path.exists():
            return
        # Legacy path from when runtime-root was _agent-runtimes/claude-worker/
        legacy = _repo_root().parent / "_agent-runtimes" / "claude-worker" / CREDENTIALS_DB_FILENAME
        if legacy.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(legacy), str(self.db_path))

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS credentials "
            "(provider_name TEXT NOT NULL, key_type TEXT NOT NULL, "
            " encrypted_value TEXT NOT NULL, salt TEXT NOT NULL, "
            " PRIMARY KEY (provider_name, key_type))"
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _derive_key(salt: str) -> bytes:
        machine_id = f"{os.environ.get('USERNAME', os.environ.get('USER', 'unknown'))}@{os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'localhost'))}"
        raw = hashlib.sha256(f"{machine_id}:{salt}".encode("utf-8")).digest()
        return raw

    @staticmethod
    def _encrypt(plaintext: str, salt: str) -> str:
        key = CredentialStore._derive_key(salt)
        data = plaintext.encode("utf-8")
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return base64.b64encode(xored).decode("ascii")

    @staticmethod
    def _decrypt(ciphertext: str, salt: str) -> str:
        key = CredentialStore._derive_key(salt)
        data = base64.b64decode(ciphertext)
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return xored.decode("utf-8")

    def set_credential(self, provider_name: str, key_type: str, value: str) -> None:
        salt = base64.b64encode(os.urandom(16)).decode("ascii")
        encrypted = self._encrypt(value, salt)
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO credentials (provider_name, key_type, encrypted_value, salt) VALUES (?, ?, ?, ?)",
            (provider_name, key_type, encrypted, salt),
        )
        conn.commit()
        conn.close()

    def get_credential(self, provider_name: str, key_type: str) -> str | None:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT encrypted_value, salt FROM credentials WHERE provider_name = ? AND key_type = ?",
            (provider_name, key_type),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        try:
            return self._decrypt(row[0], row[1])
        except Exception:
            return None

    def delete_credential(self, provider_name: str, key_type: str) -> bool:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute(
            "DELETE FROM credentials WHERE provider_name = ? AND key_type = ?",
            (provider_name, key_type),
        )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    def list_stored_providers(self) -> list[dict[str, str]]:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute("SELECT provider_name, key_type FROM credentials ORDER BY provider_name, key_type").fetchall()
        conn.close()
        return [{"provider_name": r[0], "key_type": r[1]} for r in rows]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None
    models: list[str] | None = None
    auth_token_env: str | None = None
    notes: str | None = None
    priority: int = 10  # Lower = higher priority for model resolution. Bailian Coding (0) > Bailian General (5) > Dedicated (10).

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "api_key_env": self.api_key_env}
        if self.base_url is not None:
            d["base_url"] = self.base_url
        if self.models is not None:
            d["models"] = self.models
        if self.auth_token_env is not None:
            d["auth_token_env"] = self.auth_token_env
        if self.notes is not None:
            d["notes"] = self.notes
        if self.priority != 10:  # 10 is default; only write when explicitly set to something else
            d["priority"] = self.priority
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderConfig:
        return cls(
            name=data["name"],
            api_key_env=data.get("api_key_env", "ANTHROPIC_API_KEY"),
            base_url=data.get("base_url"),
            models=data.get("models"),
            auth_token_env=data.get("auth_token_env"),
            notes=data.get("notes"),
            priority=data.get("priority", 0),
        )


_DEFAULT_PROVIDERS: list[ProviderConfig] = [
    ProviderConfig(
        name="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        notes="Anthropic official API. Uses claude login or ANTHROPIC_API_KEY env var.",
    ),
    ProviderConfig(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/anthropic",
        models=["deepseek-chat", "deepseek-reasoner"],
        auth_token_env="DEEPSEEK_API_KEY",
        notes="DeepSeek Anthropic-compatible endpoint. Set DEEPSEEK_API_KEY; ANTHROPIC_AUTH_TOKEN is set to same value; ANTHROPIC_API_KEY is cleared.",
    ),
    ProviderConfig(
        name="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api",
        models=["anthropic/claude-opus-4.7", "anthropic/claude-sonnet-4.6", "openai/gpt-4o"],
        auth_token_env="OPENROUTER_API_KEY",
        notes="OpenRouter aggregator (Anthropic Skin). Set OPENROUTER_API_KEY; ANTHROPIC_AUTH_TOKEN is set to same value; ANTHROPIC_API_KEY is cleared.",
    ),
    ProviderConfig(
        name="qwen-bailian",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/apps/anthropic",
        models=["qwen3.6-plus", "qwen3-max", "qwen3-coder-plus", "qwen3-coder-next", "qwen-plus", "qwen-turbo", "qwen3.5-flash", "qwen3-vl-plus"],
        notes="Alibaba Cloud Bailian pay-per-use (Anthropic-compatible). Set DASHSCOPE_API_KEY. Also supports third-party models: kimi-k2.5, glm-4.7, MiniMax-M2.5.",
        priority=5,  # Aggregator general plan: lower priority than Coding Plan
    ),
    ProviderConfig(
        name="qwen-bailian-coding",
        api_key_env="DASHSCOPE_CODING_API_KEY",
        base_url="https://coding.dashscope.aliyuncs.com/apps/anthropic",
        models=["qwen3.6-plus", "qwen3-coder-plus", "qwen3.5-plus", "qwen3-coder-next", "glm-5", "glm-4.7", "MiniMax-M2.5", "kimi-k2.5"],
        notes="Alibaba Cloud Bailian Coding Plan (Anthropic-compatible). Use Coding Plan API Key (sk-sp- prefix). Fixed monthly fee. Supports Qwen, GLM, MiniMax, and Kimi flagship models.",
        priority=0,  # Primary multi-vendor endpoint: highest resolution priority
    ),
    ProviderConfig(
        name="z-ai",
        api_key_env="ZAI_API_KEY",
        base_url="https://api.z.ai/api/anthropic",
        models=["glm-5.1", "glm-5", "glm-4.7", "glm-4.5-air"],
        auth_token_env="ZAI_API_KEY",
        notes="Z.AI (Zhipu/GLM) Coding Plan (Anthropic-compatible). Set ZAI_API_KEY; ANTHROPIC_AUTH_TOKEN is set to same value; ANTHROPIC_API_KEY is cleared.",
    ),
    ProviderConfig(
        name="kimi",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.cn/anthropic",
        models=["kimi-k2.5", "kimi-k2-thinking", "kimi-k2-turbo-preview"],
        auth_token_env="MOONSHOT_API_KEY",
        notes="Moonshot Kimi (Anthropic-compatible). Set MOONSHOT_API_KEY; ANTHROPIC_AUTH_TOKEN is set to same value; ANTHROPIC_API_KEY is cleared.",
    ),
    ProviderConfig(
        name="minimax",
        api_key_env="MINIMAX_API_KEY",
        base_url="https://api.minimax.io/anthropic",
        models=["MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.1"],
        notes="MiniMax (Anthropic-compatible). Set MINIMAX_API_KEY.",
    ),
    ProviderConfig(
        name="siliconflow",
        api_key_env="SILICONFLOW_API_KEY",
        base_url="https://api.siliconflow.cn/",
        models=["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-235B-A22B", "Pro/deepseek-ai/DeepSeek-R1"],
        notes="SiliconFlow (Anthropic-compatible endpoint). Set SILICONFLOW_API_KEY.",
    ),
]


class ProviderRegistry:
    def __init__(self, db_path: str | Path | None = None, cred_store: CredentialStore | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _providers_db_path()
        self.cred_store = cred_store or CredentialStore()
        self._providers: dict[str, ProviderConfig] = {}
        self._migrate_from_legacy_path()
        self._load()

    def _migrate_from_legacy_path(self) -> None:
        """One-time migration: if new config/ path doesn't exist but old _agent-runtimes/ one does, move it."""
        if self.db_path.exists():
            return
        # Legacy: _agent-runtimes/claude-worker/providers.json (before config/ subdirectory)
        legacy = _repo_root().parent / "_agent-runtimes" / "claude-worker" / PROVIDERS_FILENAME
        if legacy.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(self.db_path))

    def _load(self) -> None:
        # providers.json is the single source of truth.
        # If it doesn't exist yet, auto-generate from _DEFAULT_PROVIDERS.
        if not self.db_path.exists():
            self._seed_from_defaults()
        try:
            data = _read_json(self.db_path)
            for entry in _as_list(data.get("providers", [])):
                if isinstance(entry, dict) and "name" in entry:
                    pc = ProviderConfig.from_dict(entry)
                    self._providers[pc.name] = pc
        except (json.JSONDecodeError, ValueError, TypeError):
            # Corrupted file — fall back to defaults
            self._seed_from_defaults()

    def _seed_from_defaults(self) -> None:
        """Auto-generate providers.json from embedded defaults on first run."""
        for p in _DEFAULT_PROVIDERS:
            self._providers[p.name] = p
        self._save()

    def _save(self) -> None:
        _ensure_parent(self.db_path)
        entries = [self._providers[k].to_dict() for k in sorted(self._providers)]
        _write_json(self.db_path, {"providers": entries})

    def list_providers(self) -> list[ProviderConfig]:
        return list(self._providers.values())

    def get_provider(self, name: str) -> ProviderConfig | None:
        return self._providers.get(name)

    def add_provider(self, config: ProviderConfig) -> None:
        self._providers[config.name] = config
        self._save()

    def remove_provider(self, name: str) -> bool:
        if name in self._providers:
            del self._providers[name]
            self._save()
            return True
        return False

    def resolve_provider_for_model(self, model: str) -> ProviderConfig | None:
        """Find the best provider for a model. Bailian Coding (priority=0) > Bailian General (5) > Dedicated (10)."""
        candidates = [
            p for p in self._providers.values()
            if p.models and model in p.models
        ]
        if not candidates:
            return None
        # Lower priority number = higher precedence (dedicated before aggregator)
        candidates.sort(key=lambda p: p.priority)
        return candidates[0]

    def apply_provider(self, provider: ProviderConfig) -> dict[str, str]:
        """Resolve credentials for a provider and produce env vars to write to settings.json.

        Credential resolution order (first non-empty wins):
          1. CredentialStore (<runtime-root>/credentials.db) — our own encrypted DB
          2. os.environ (explicit environment variables, e.g. ANTHROPIC_API_KEY, ZAI_API_KEY)
          3. cc-switch SQLite DB (legacy fallback, matched by name mapping)
          4. ~/.claude/settings.json (current active env — last resort)
        """
        env_vars: dict[str, str] = {}

        # --- Step 1: CredentialStore (our own encrypted DB) ---
        stored_api_key = self.cred_store.get_credential(provider.name, "api_key")
        stored_auth_token = self.cred_store.get_credential(provider.name, "auth_token")

        # --- Step 2: os.environ ---
        env_api_key = os.environ.get(provider.api_key_env, "")
        env_auth_token = os.environ.get(provider.auth_token_env, "") if provider.auth_token_env else ""

        # --- Step 3: cc-switch DB (legacy fallback) ---
        cc_env = _resolve_provider_env(provider)
        cc_api_key = cc_env.get(provider.api_key_env, "") or cc_env.get("ANTHROPIC_API_KEY", "")
        cc_auth_token = ""
        if provider.auth_token_env:
            cc_auth_token = cc_env.get(provider.auth_token_env, "") or cc_env.get("ANTHROPIC_AUTH_TOKEN", "")
        if not cc_auth_token:
            cc_auth_token = cc_env.get("ANTHROPIC_AUTH_TOKEN", "")

        # --- Step 4: settings.json ---
        claude_env = _load_claude_env()
        settings_api_key = claude_env.get("ANTHROPIC_API_KEY", "")
        settings_auth_token = claude_env.get("ANTHROPIC_AUTH_TOKEN", "")

        # --- Merge: first non-empty wins ---
        api_key = stored_api_key or env_api_key or cc_api_key or settings_api_key
        if provider.auth_token_env:
            # Provider uses ANTHROPIC_AUTH_TOKEN (deepseek, openrouter, z-ai, kimi)
            auth_token = stored_auth_token or env_auth_token or cc_auth_token or settings_auth_token
        else:
            # Provider uses ANTHROPIC_API_KEY only — resolve auth_token only from own store
            auth_token = stored_auth_token or ""

        # --- Build output env vars ---
        if api_key:
            env_vars["ANTHROPIC_API_KEY"] = api_key
        if auth_token:
            env_vars["ANTHROPIC_AUTH_TOKEN"] = auth_token
            # For providers using ANTHROPIC_AUTH_TOKEN, clear ANTHROPIC_API_KEY to prevent conflicts
            if provider.name in ("openrouter", "deepseek", "z-ai", "kimi"):
                env_vars["ANTHROPIC_API_KEY"] = ""
        elif not provider.auth_token_env:
            # Provider doesn't use ANTHROPIC_AUTH_TOKEN — clear any leftover from previous switch
            env_vars["ANTHROPIC_AUTH_TOKEN"] = ""
        if provider.base_url:
            env_vars["ANTHROPIC_BASE_URL"] = provider.base_url
        # Also apply model overrides if present in cc-switch env or CredentialStore
        model_val = self.cred_store.get_credential(provider.name, "model")
        if model_val:
            env_vars["ANTHROPIC_MODEL"] = model_val
        for model_key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                          "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
            if model_key in cc_env and model_key not in env_vars:
                env_vars[model_key] = cc_env[model_key]
        return env_vars

    def switch_active_provider(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        if provider is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        env_vars = self.apply_provider(provider)
        settings_path = _claude_settings_path()
        settings: dict[str, Any] = {}
        if settings_path.exists():
            try:
                settings = _read_json(settings_path)
            except (json.JSONDecodeError, ValueError, TypeError):
                settings = {}
        if env_vars:
            if "env" not in settings:
                settings["env"] = {}
            settings["env"].update(env_vars)
        _ensure_parent(settings_path)
        _write_json(settings_path, settings)
        # Determine which source resolved the key
        has_stored = bool(self.cred_store.get_credential(provider.name, "api_key") or self.cred_store.get_credential(provider.name, "auth_token"))
        has_env = bool(os.environ.get(provider.api_key_env, "") or (provider.auth_token_env and os.environ.get(provider.auth_token_env, "")))
        source = "credential_store" if has_stored else ("env_var" if has_env else "cc-switch_or_settings")
        result: dict[str, Any] = {
            "provider": provider_name,
            "settings_path": str(settings_path),
            "env_vars_set": list(env_vars.keys()),
            "credential_source": source,
        }
        if provider.base_url:
            result["base_url"] = provider.base_url
        return result


def _load_claude_env() -> dict[str, str]:
    """Load environment variables from ~/.claude/settings.json env section."""
    env_vars: dict[str, str] = {}
    try:
        settings_path = _claude_settings_dir() / CLAUDE_SETTINGS_FILENAME
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for k, v in data.get("env", {}).items():
                if isinstance(v, str):
                    env_vars[k] = v
    except Exception:
        pass
    return env_vars


def _load_cc_switch_providers() -> dict[str, dict[str, str]]:
    """Load all provider env configs from cc-switch SQLite database.

    Returns a dict mapping provider name -> env vars dict.
    """
    providers: dict[str, dict[str, str]] = {}
    db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db_path.exists():
        return providers
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, settings_config FROM providers WHERE app_type = 'claude'"
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                sc = json.loads(r["settings_config"])
                env = sc.get("env", {})
                if isinstance(env, dict) and env:
                    providers[r["name"]] = {k: v for k, v in env.items() if isinstance(v, str)}
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception:
        pass
    return providers


def _resolve_provider_env(provider: ProviderConfig) -> dict[str, str]:
    """Resolve env vars for a provider from: os.environ > ~/.claude/settings.json > cc-switch DB.

    Returns a merged dict of env vars relevant to this provider.
    """
    env: dict[str, str] = {}

    # 1. CC-switch DB (highest fidelity — stores per-provider credentials)
    cc_providers = _load_cc_switch_providers()
    # Try matching by base_url or by convention name mapping
    cc_name_map = {
        "bailian-cp": "qwen-bailian-coding",
        "zhipu-cp": "z-ai",
        "kimi-cp": "kimi",
        "minimax-cp": "minimax",
        "deepseek-cp": "deepseek",
        "openrouter-cp": "openrouter",
        "siliconflow-cp": "siliconflow",
    }
    for cc_name, cc_env in cc_providers.items():
        mapped = cc_name_map.get(cc_name, cc_name)
        # Match by mapped name, by cc_name, or by base_url
        if mapped == provider.name or cc_name == provider.name:
            env.update(cc_env)
            break
        cc_base_url = cc_env.get("ANTHROPIC_BASE_URL", "")
        if provider.base_url and cc_base_url.rstrip("/") == provider.base_url.rstrip("/"):
            env.update(cc_env)
            break

    # 2. ~/.claude/settings.json (current active config)
    claude_env = _load_claude_env()
    for k, v in claude_env.items():
        if k not in env:
            env[k] = v

    # 3. os.environ (explicit env vars)
    for var_name in (provider.api_key_env, provider.auth_token_env or "",
                     "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        val = os.environ.get(var_name, "")
        if val and var_name not in env:
            env[var_name] = val

    return env


def verify_provider_endpoint(provider: ProviderConfig, timeout: float = 15.0) -> dict[str, Any]:
    """Send a minimal Anthropic-protocol request to verify the endpoint is reachable and auth works.

    Returns a dict with keys: ok, provider, base_url, status_code, error, model_used, latency_ms.
    """
    result: dict[str, Any] = {
        "ok": False,
        "provider": provider.name,
        "base_url": provider.base_url or "(official Anthropic API)",
        "status_code": None,
        "error": None,
        "model_used": None,
        "latency_ms": None,
    }

    # Resolve auth credentials from all sources
    merged_env = _resolve_provider_env(provider)
    api_key = merged_env.get(provider.api_key_env, "") or merged_env.get("ANTHROPIC_API_KEY", "")
    auth_token = ""
    if provider.auth_token_env:
        auth_token = merged_env.get(provider.auth_token_env, "") or merged_env.get("ANTHROPIC_AUTH_TOKEN", "")
    # Also check generic tokens as fallback
    if not auth_token:
        auth_token = merged_env.get("ANTHROPIC_AUTH_TOKEN", "")

    if not api_key and not auth_token:
        result["error"] = f"No API key found (checked {provider.api_key_env}"
        if provider.auth_token_env:
            result["error"] += f", {provider.auth_token_env}"
        result["error"] += ", ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN in env/cc-switch/settings.json)"
        return result

    # Build the target URL
    if provider.base_url:
        base = provider.base_url.rstrip("/")
        # Most Anthropic-compatible endpoints expect /v1/messages
        if base.endswith("/anthropic") or base.endswith("/api/anthropic"):
            url = f"{base}/v1/messages"
        elif base.endswith("/api"):
            url = f"{base}/v1/messages"
        else:
            url = f"{base}/v1/messages"
    else:
        url = "https://api.anthropic.com/v1/messages"

    # Pick a model to test with
    test_model = provider.models[0] if provider.models else "claude-sonnet-4-20250514"

    # Build Anthropic-protocol request body
    body = json.dumps({
        "model": test_model,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Say OK"}],
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    # Auth: prefer ANTHROPIC_AUTH_TOKEN for providers that use it
    # MiniMax requires Authorization: Bearer, others use x-api-key
    if auth_token:
        headers["x-api-key"] = auth_token
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["x-api-key"] = api_key
        # MiniMax specifically requires Authorization header
        if provider.name == "minimax":
            headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    start_ts = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.monotonic() - start_ts) * 1000
            result["status_code"] = resp.status
            result["latency_ms"] = round(elapsed_ms, 0)
            if resp.status == 200:
                resp_data = json.loads(resp.read().decode("utf-8"))
                result["ok"] = True
                result["model_used"] = resp_data.get("model", test_model)
            else:
                result["error"] = f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - start_ts) * 1000
        result["status_code"] = exc.code
        result["latency_ms"] = round(elapsed_ms, 0)
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            result["error"] = err_body.get("error", {}).get("message", "") or str(exc)
        except Exception:
            result["error"] = f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        result["error"] = f"Connection failed: {exc.reason}"
    except Exception as exc:
        result["error"] = str(exc)

    return result


def check_prerequisites() -> dict[str, Any]:
    results: dict[str, Any] = {"checks": {}, "ready": True, "missing": []}
    # Check Python
    results["checks"]["python"] = {"ok": True, "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"}
    # Check node
    node_path = shutil.which("node")
    if node_path:
        results["checks"]["node"] = {"ok": True, "path": node_path}
    else:
        results["checks"]["node"] = {"ok": False, "message": "node not found on PATH"}
        results["ready"] = False
        results["missing"].append("node")
    # Check npm
    npm_path = shutil.which("npm")
    if npm_path:
        results["checks"]["npm"] = {"ok": True, "path": npm_path}
    else:
        results["checks"]["npm"] = {"ok": False, "message": "npm not found on PATH"}
        results["ready"] = False
        results["missing"].append("npm")
    # Check claude CLI
    claude_path = shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
    if claude_path:
        results["checks"]["claude_cli"] = {"ok": True, "path": claude_path}
    else:
        results["checks"]["claude_cli"] = {"ok": False, "message": "claude CLI not found on PATH; install with: npm install -g @anthropic-ai/claude-code"}
        results["ready"] = False
        results["missing"].append("claude_cli")
    # Check claude auth
    settings_path = _claude_settings_path()
    if settings_path.exists():
        results["checks"]["claude_settings"] = {"ok": True, "path": str(settings_path)}
    else:
        results["checks"]["claude_settings"] = {"ok": False, "path": str(settings_path), "message": "No Claude settings file found; run 'claude login' to authenticate"}
    # Check API key env vars
    api_keys_found = []
    for var in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_CODING_API_KEY", "ZAI_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY", "SILICONFLOW_API_KEY"):
        if os.environ.get(var):
            api_keys_found.append(var)
    results["checks"]["api_keys"] = {"ok": bool(api_keys_found), "found": api_keys_found, "message": "At least one provider API key should be set as environment variable" if not api_keys_found else None}
    # Installation hints
    results["install_hints"] = {
        "claude_cli": "npm install -g @anthropic-ai/claude-code",
        "claude_login": "claude login",
        "node": "Install Node.js from https://nodejs.org/ (LTS recommended)",
    }
    return results


def _sessions_root() -> Path:
    return _default_claude_worker_root() / SESSIONS_DIR_NAME


class LongRunSession:
    """Live Session Mode — a CC process that stays alive and accepts multiple prompts.

    See the module docstring for the full comparison of Task / Session Chain /
    Live Session modes.  This class implements Live Session Mode.

    Communication protocol (CC bidirectional streaming):
      - CC is started with: -p --input-format stream-json
        --output-format stream-json --verbose
      - Input:  {"type": "user", "message": {"role": "user", "content": "..."}}
      - Output: {"type": "system", ...}          — init event (session_id, tools)
                {"type": "assistant", "message": { "content": [...] }} — responses
                {"type": "result", ...}           — turn result
      - In bypassPermissions mode, tool permission requests are auto-approved:
                {"type": "control_request", "request": {"subtype": "can_use_tool", ...}}
                → auto-reply with {"type": "control_response", ...}
      - Process stays alive until stdin is closed or session.stop() is called.

    Lifecycle:
      start()  →  send() × N  →  capture() × N  →  stop()
    """

    def __init__(self, packet: WorkerPacket, runtime: ClaudeWorkerRuntime | None = None) -> None:
        self.packet = packet
        self.runtime = runtime
        self.session_id = f"{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
        self.session_dir = _sessions_root() / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._output_lines: list[str] = []
        self._output_lock = threading.Lock()
        self._prompt_count = 0
        self._started = False
        self._stopped = False
        self._session_cc_id: str | None = None  # CC's own session_id from output

    def start(self) -> dict[str, Any]:
        """Start the CC process in bidirectional streaming mode."""
        if self._started:
            raise RuntimeError(f"Session {self.session_id} already started")
        self._started = True

        # Switch provider if needed
        model = self.packet.model or DEFAULT_MODEL
        provider_switch_result = None
        registry = ProviderRegistry()
        resolved_provider = self.packet.provider
        if not resolved_provider:
            auto_provider = registry.resolve_provider_for_model(model)
            if auto_provider:
                resolved_provider = auto_provider.name
        if resolved_provider:
            provider_config = registry.get_provider(resolved_provider)
            if provider_config:
                provider_switch_result = registry.switch_active_provider(resolved_provider)

        # Build command: CC in bidirectional streaming mode
        # stream-json output requires --verbose per CC spec
        command = [
            _resolve_claude_binary(self.runtime.claude_binary if self.runtime else "claude"),
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--permission-mode", self.packet.permission_mode or DEFAULT_PERMISSION_MODE,
        ]
        if self.packet.max_turns:
            command.extend(["--max-turns", str(self.packet.max_turns)])
        if self.packet.allowed_tools:
            command.extend(["--allowedTools", ",".join(self.packet.allowed_tools)])
        if self.packet.bare_mode:
            command.append("--bare")
        if self.packet.resume_session:
            command.extend(["--resume", self.packet.resume_session])
        # --effort: reasoning/thinking budget
        effort = self.packet.effort or DEFAULT_EFFORT
        if effort in VALID_EFFORT_LEVELS:
            command.extend(["--effort", effort])

        # Write session metadata
        meta = {
            "session_id": self.session_id,
            "command": command,
            "model": model,
            "provider": resolved_provider,
            "provider_switch": provider_switch_result,
            "started_at": _utc_now().isoformat(),
            "status": "running",
            "prompt_count": 0,
        }
        _write_json(self.session_dir / "session.json", meta)

        # Build environment: inherit from current process + provider env vars
        env = os.environ.copy()
        if provider_switch_result and provider_switch_result.get("env_vars_set"):
            # Apply provider environment variables from credential store
            for key in provider_switch_result["env_vars_set"]:
                val = os.environ.get(key)
                if val:
                    env[key] = val

        # Start process with stdin/stdout pipes
        self.process = subprocess.Popen(
            command,
            cwd=self.packet.cwd or None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        # Start background reader thread (also handles permission auto-approve)
        auto_approve = self.packet.permission_mode == "bypassPermissions"
        self._reader_thread = threading.Thread(
            target=self._read_output, daemon=True, name=f"cc-session-{self.session_id[:8]}",
            kwargs={"auto_approve": auto_approve},
        )
        self._reader_thread.start()

        # Send initial prompt
        self._send_prompt(self.packet.prompt)

        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "pid": self.process.pid,
            "status": "running",
            "provider": resolved_provider,
        }

    def _send_prompt(self, prompt: str) -> None:
        """Send a user message to CC via stdin in stream-json format."""
        if self.process is None or self.process.stdin is None:
            return
        # CC bidirectional streaming expects this format:
        # {"type": "user", "message": {"role": "user", "content": "..."}}
        msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": prompt},
        }, ensure_ascii=False) + "\n"
        try:
            self.process.stdin.write(msg)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        self._prompt_count += 1
        # Update metadata
        meta_path = self.session_dir / "session.json"
        if meta_path.exists():
            meta = _read_json(meta_path)
            meta["prompt_count"] = self._prompt_count
            _write_json(meta_path, meta)
        # Also append to prompt log
        with (self.session_dir / "prompts.ndjson").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"n": self._prompt_count, "prompt": prompt, "at": _utc_now().isoformat()}, ensure_ascii=False) + "\n")

    def _read_output(self, auto_approve: bool = False) -> None:
        """Background thread: continuously read CC stdout and buffer lines.

        When auto_approve=True (bypassPermissions mode), automatically responds
        to CC's can_use_tool permission requests with approval.
        """
        if self.process is None or self.process.stdout is None:
            return
        try:
            for line in self.process.stdout:
                with self._output_lock:
                    self._output_lines.append(line)
                # Try to extract CC session_id from init or result events
                if not self._session_cc_id and '"session_id"' in line:
                    try:
                        data = json.loads(line)
                        if "session_id" in data:
                            self._session_cc_id = data["session_id"]
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Auto-approve permission requests in bypassPermissions mode
                if auto_approve and self.process.stdin and '"can_use_tool"' in line:
                    try:
                        data = json.loads(line)
                        if (data.get("type") == "control_request"
                                and data.get("request", {}).get("subtype") == "can_use_tool"):
                            request_id = data.get("request_id", "")
                            response = json.dumps({
                                "type": "control_response",
                                "response": {
                                    "subtype": "success",
                                    "request_id": request_id,
                                    "response": {"approved": True},
                                },
                            }, ensure_ascii=False) + "\n"
                            self.process.stdin.write(response)
                            self.process.stdin.flush()
                    except (json.JSONDecodeError, TypeError, BrokenPipeError, OSError):
                        pass
        except Exception:
            pass

    def send(self, prompt: str) -> dict[str, Any]:
        """Send a follow-up prompt to the running session."""
        if not self._started or self._stopped:
            raise RuntimeError(f"Session {self.session_id} is not running")
        if self.process is None or self.process.poll() is not None:
            return {"error": "Process has exited", "exit_code": self.process.poll() if self.process else None}
        self._send_prompt(prompt)
        return {
            "session_id": self.session_id,
            "prompt_number": self._prompt_count,
            "status": "sent",
        }

    def capture(self, last_n: int = 0) -> dict[str, Any]:
        """Capture output from the session. If last_n > 0, only return the last N lines."""
        with self._output_lock:
            lines = self._output_lines[-last_n:] if last_n else list(self._output_lines)
        # Parse streaming events into structured output
        events = []
        text_parts = []
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(data)
                msg_type = data.get("type")
                # Extract text from result events
                if msg_type == "result":
                    results.append(data.get("result", ""))
                    text_parts.append(data.get("result", ""))
                # Extract text from assistant messages (CC stream-json format)
                elif msg_type == "assistant":
                    message = data.get("message", {})
                    for content_block in message.get("content", []):
                        if content_block.get("type") == "text":
                            text_parts.append(content_block.get("text", ""))
                # Extract text from streaming assistant messages
                elif msg_type == "message" and data.get("message_type") == "assistant_message":
                    text_parts.append(data.get("content", ""))
                # Extract text from stream_event with text delta
                elif msg_type == "stream_event":
                    event = data.get("event", {})
                    if event.get("message_type") == "assistant_message":
                        text_parts.append(event.get("content", ""))
            except (json.JSONDecodeError, TypeError):
                text_parts.append(line)
        return {
            "session_id": self.session_id,
            "event_count": len(events),
            "results": results,
            "text": "\n".join(text_parts) if text_parts else "",
            "lines_total": len(self._output_lines),
            "process_alive": self.process is not None and self.process.poll() is None,
            "prompt_count": self._prompt_count,
            "cc_session_id": self._session_cc_id,
        }

    def status(self) -> dict[str, Any]:
        """Get current session status."""
        alive = self.process is not None and self.process.poll() is None
        meta_path = self.session_dir / "session.json"
        meta = _read_json(meta_path) if meta_path.exists() else {}
        return {
            "session_id": self.session_id,
            "pid": self.process.pid if self.process else None,
            "process_alive": alive,
            "prompt_count": self._prompt_count,
            "cc_session_id": self._session_cc_id,
            "output_lines": len(self._output_lines),
            "started_at": meta.get("started_at"),
            "status": "running" if alive else "stopped",
        }

    def stop(self) -> dict[str, Any]:
        """Stop the session: terminate the CC process."""
        if self._stopped:
            return {"session_id": self.session_id, "status": "already_stopped"}
        self._stopped = True
        exit_code = None
        if self.process:
            try:
                # Close stdin to signal CC to finish
                if self.process.stdin:
                    self.process.stdin.close()
                # Give it a moment to exit gracefully
                try:
                    exit_code = self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        exit_code = self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        exit_code = self.process.wait(timeout=2)
            except Exception:
                pass
        # Wait for reader thread
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)
        # Capture final output to file
        output_path = self.session_dir / "output.ndjson"
        with self._output_lock:
            output_path.write_text("".join(self._output_lines), encoding="utf-8")
        # Update metadata
        meta_path = self.session_dir / "session.json"
        if meta_path.exists():
            meta = _read_json(meta_path)
            meta["status"] = "stopped"
            meta["stopped_at"] = _utc_now().isoformat()
            meta["exit_code"] = exit_code
            meta["total_prompts"] = self._prompt_count
            meta["total_output_lines"] = len(self._output_lines)
            meta["cc_session_id"] = self._session_cc_id
            _write_json(meta_path, meta)
        return {
            "session_id": self.session_id,
            "status": "stopped",
            "exit_code": exit_code,
            "prompts_sent": self._prompt_count,
            "output_lines": len(self._output_lines),
            "cc_session_id": self._session_cc_id,
        }


@dataclass(frozen=True)
class WorkerPacket:
    """Task description submitted to the worker.

    execution_mode selects the invocation pattern (see module docstring):
      - "one_shot"  (default): Task Mode — single run, process exits after result
      - "detached":  Task Mode — same but caller can detach and poll/fetch later
      - "interactive": not recommended; prefer Live Session Mode (LongRunSession)
    """
    kind: Literal["coding", "review"]
    prompt: str
    cwd: str | None = None
    model: str = DEFAULT_MODEL
    permission_mode: str = DEFAULT_PERMISSION_MODE
    execution_mode: str = DEFAULT_EXECUTION_MODE
    provider: str | None = None
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
    # CC native capabilities (exposed for caller control across all modes)
    max_turns: int | None = None
    allowed_tools: list[str] | None = None
    resume_session: str | None = None  # --resume <session_id>
    continue_session: bool = False     # --continue (resume most recent)
    fork_session: bool = False         # --fork-session (new ID, keeps history)
    bare_mode: bool = False            # --bare (skip hooks/plugins/MCP)
    output_format: str = "json"        # json | stream-json | text
    input_files: list[str] | None = None  # files to pipe as stdin context
    effort: str = DEFAULT_EFFORT       # --effort (thinking budget: low/medium/high/max)


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

        # Auto-switch provider based on packet.provider or model name
        provider_switch_result: dict[str, Any] | None = None
        resolved_provider = packet.provider
        registry = ProviderRegistry()
        if not resolved_provider:
            auto_provider = registry.resolve_provider_for_model(model)
            if auto_provider:
                resolved_provider = auto_provider.name
        if resolved_provider:
            provider_config = registry.get_provider(resolved_provider)
            if provider_config:
                provider_switch_result = registry.switch_active_provider(resolved_provider)

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

        # Build command based on execution mode and CC native capabilities
        if execution_mode == "one_shot":
            command = [
                self.claude_binary,
                "-p",
                "--output-format",
                packet.output_format,
                "--model",
                model,
                "--permission-mode",
                permission_mode,
            ]
        else:
            # interactive/detached mode: no -p flag; prompt delivered via stdin pipe from file
            # interactive: process attached to caller (stdin pipe, live monitoring)
            # detached:    process runs independently; caller uses fetch/wait to check status
            # NOTE: for true live multi-prompt sessions, prefer Live Session Mode (LongRunSession)
            command = [
                self.claude_binary,
                "--output-format",
                packet.output_format,
                "--model",
                model,
                "--permission-mode",
                permission_mode,
            ]

        # --- CC native capability flags ---

        # --max-turns: limit agentic loop count (safety bound)
        if packet.max_turns:
            command.extend(["--max-turns", str(packet.max_turns)])

        # --allowedTools: restrict tool set for security
        if packet.allowed_tools:
            command.extend(["--allowedTools", ",".join(packet.allowed_tools)])

        # --resume <session_id>: continue a specific session
        if packet.resume_session:
            command.extend(["--resume", packet.resume_session])

        # --continue: resume most recent session in cwd
        if packet.continue_session:
            command.append("--continue")

        # --fork-session: new session ID, keeps history from resumed session
        if packet.fork_session:
            command.append("--fork-session")

        # --bare: skip hooks, plugins, MCP servers (clean CI mode)
        if packet.bare_mode:
            command.append("--bare")

        # --effort: reasoning/thinking budget (low/medium/high/max)
        effort = packet.effort or DEFAULT_EFFORT
        if effort in VALID_EFFORT_LEVELS:
            command.extend(["--effort", effort])

        # --json-schema: structured output (only for json output format)
        if packet.output_format == "json":
            command.extend(["--json-schema", schema_json])

        # Prompt argument (only for one_shot mode)
        if execution_mode == "one_shot":
            command.append(packet.prompt)

        meta = {
            "run_id": run_id,
            "kind": packet.kind,
            "task_id": packet.task_id,
            "title": packet.title,
            "lane": packet.lane,
            "execution_mode": execution_mode,
            "provider": resolved_provider,
            "reasoning_mode": reasoning_mode,
            "effort": effort,
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
            "provider_switch": provider_switch_result,
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
                "provider": resolved_provider,
                "task_id": packet.task_id,
                "title": packet.title,
                "lane": packet.lane,
                "reasoning_mode": reasoning_mode,
                "effort": effort,
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
        stdout, stderr = self._read_wait_artifacts(record, stdout or "", stderr or "")
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
        prompt_path = run_dir / PROMPT_FILENAME
        stdout_path = run_dir / STDOUT_FILENAME
        stderr_path = run_dir / STDERR_FILENAME
        exitcode_path = run_dir / EXITCODE_FILENAME
        if self.launcher is not subprocess.Popen:
            return self.launcher(
                command,
                cwd=packet.cwd or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                run_dir=run_dir,
                prompt_path=prompt_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                exitcode_path=exitcode_path,
            )
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
            "input_files = json.loads(sys.argv[7]) if sys.argv[7] != '[]' else []\n"
            "prompt = prompt_path.read_text(encoding='utf-8')\n"
            "# Prepend piped input files to stdin context\n"
            "stdin_parts = []\n"
            "for fp in input_files:\n"
            "    try:\n"
            "        stdin_parts.append(pathlib.Path(fp).read_text(encoding='utf-8'))\n"
            "    except Exception:\n"
            "        pass\n"
            "if stdin_parts:\n"
            "    stdin_content = '\\n'.join(stdin_parts) + '\\n'\n"
            "else:\n"
            "    stdin_content = ''\n"
            "returncode = 1\n"
            "try:\n"
            "    with stdout_path.open('w', encoding='utf-8', newline='\\n') as out, stderr_path.open('w', encoding='utf-8', newline='\\n') as err:\n"
            "        proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True)\n"
            "        if proc.stdin is not None:\n"
            "            proc.stdin.write(stdin_content)\n"
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
            json.dumps(packet.input_files or []),
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

    def _read_wait_artifacts(self, record: RunRecord, stdout: str, stderr: str) -> tuple[str, str]:
        artifact_paths = self._detached_artifact_paths(record.run_dir)
        if not stdout:
            stdout = _read_text_if_exists(artifact_paths["stdout"])
        if not stderr:
            stderr = _read_text_if_exists(artifact_paths["stderr"])
        return stdout, stderr

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
            "effort": record.packet.effort or DEFAULT_EFFORT,
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
            "effort": record.packet.effort or DEFAULT_EFFORT,
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

    start = subparsers.add_parser("start", help="Task Mode: submit a single task, get a result (start → wait → fetch)")
    start.add_argument("--kind", choices=["coding", "review"], required=True)
    start.add_argument("--prompt", required=True)
    start.add_argument("--cwd", default=None)
    start.add_argument("--model", default=None)
    start.add_argument("--permission-mode", default=None)
    start.add_argument("--execution-mode", choices=list(VALID_EXECUTION_MODES), default=DEFAULT_EXECUTION_MODE)
    start.add_argument("--provider", default=None, help="Provider name to switch to before running (e.g. anthropic, deepseek, qwen-bailian)")
    start.add_argument("--task-id", default=None)
    start.add_argument("--title", default=None)
    start.add_argument("--lane", default=None)
    start.add_argument("--reasoning-mode", default=DEFAULT_REASONING_MODE)
    start.add_argument("--effort", default=DEFAULT_EFFORT, choices=VALID_EFFORT_LEVELS,
                       help="CC thinking budget: low, medium, high, max (default: high)")
    start.add_argument("--sandbox-identity", default=None)
    start.add_argument("--sandbox-kind", default=DEFAULT_SANDBOX_KIND)
    start.add_argument("--capability-profile", default=None)
    start.add_argument("--write-scope", action="append", default=[])
    start.add_argument("--network-policy", default=None)
    start.add_argument("--workspace-root", default=None)
    start.add_argument("--runtime-root", default=None)
    start.add_argument("--environment-mode", default=None)
    # CC native capability flags
    start.add_argument("--max-turns", type=int, default=None, help="Limit agentic loop count (safety bound)")
    start.add_argument("--allowed-tools", action="append", default=None, help="Allowed CC tools (e.g. Read,Edit,Bash). Can be repeated.")
    start.add_argument("--resume-session", default=None, help="Resume a specific session by ID")
    start.add_argument("--continue-session", action="store_true", default=False, help="Resume most recent session in cwd")
    start.add_argument("--fork-session", action="store_true", default=False, help="Fork from resumed session (new ID, keeps history)")
    start.add_argument("--bare", action="store_true", default=False, help="Skip hooks, plugins, MCP (clean CI mode)")
    start.add_argument("--output-format", choices=["json", "stream-json", "text"], default="json", help="CC output format (default: json)")
    start.add_argument("--input-file", action="append", default=None, help="File to pipe as stdin context (can be repeated)")

    for name in ("wait", "fetch", "abort"):
        cmd = subparsers.add_parser(name)
        cmd.add_argument("--run-id", required=True)

    send_cmd = subparsers.add_parser("send", help="Send a follow-up prompt to a running interactive/detached session")
    send_cmd.add_argument("--run-id", required=True, help="Run ID of the active session")
    send_cmd.add_argument("--prompt", required=True, help="Follow-up prompt to inject")

    continue_cmd = subparsers.add_parser("continue", help="Session Chain Mode: resume a completed run with a new prompt (uses --resume, CC keeps context)")
    continue_cmd.add_argument("--run-id", required=True, help="Run ID of the completed session to continue")
    continue_cmd.add_argument("--prompt", required=True, help="New prompt for the continued session")
    continue_cmd.add_argument("--max-turns", type=int, default=None)
    continue_cmd.add_argument("--fork", action="store_true", default=False, help="Fork session instead of continuing (new ID, keeps history)")

    # Live Session Mode commands (LongRunSession)
    session_start = subparsers.add_parser("session-start", help="Live Session Mode: start a CC process that stays alive and accepts multiple prompts via bidirectional streaming")
    session_start.add_argument("--prompt", required=True, help="Initial prompt for the session")
    session_start.add_argument("--model", default=None)
    session_start.add_argument("--permission-mode", default=None)
    session_start.add_argument("--provider", default=None)
    session_start.add_argument("--cwd", default=None)
    session_start.add_argument("--max-turns", type=int, default=None)
    session_start.add_argument("--allowed-tools", action="append", default=None, help="Allowed CC tools (repeatable)")
    session_start.add_argument("--bare", action="store_true", default=False)
    session_start.add_argument("--resume-session", default=None, help="Resume a specific CC session by ID")

    session_send = subparsers.add_parser("session-send", help="Send a follow-up prompt to a running session")
    session_send.add_argument("--session-id", required=True, help="Session ID returned by session-start")
    session_send.add_argument("--prompt", required=True, help="Follow-up prompt")

    session_capture = subparsers.add_parser("session-capture", help="Capture output from a running session")
    session_capture.add_argument("--session-id", required=True, help="Session ID")
    session_capture.add_argument("--last-n", type=int, default=0, help="Only return last N output lines (0=all)")

    session_status = subparsers.add_parser("session-status", help="Get status of a running session")
    session_status.add_argument("--session-id", required=True, help="Session ID")

    session_stop = subparsers.add_parser("session-stop", help="Stop a running session")
    session_stop.add_argument("--session-id", required=True, help="Session ID")

    setup_cmd = subparsers.add_parser("setup", help="Check prerequisites and display installation guide")
    setup_cmd.add_argument("--json", action="store_true", default=False, help="Output as JSON")

    provider_cmd = subparsers.add_parser("provider", help="Manage model providers")
    provider_sub = provider_cmd.add_subparsers(dest="provider_command", required=True)

    provider_list = provider_sub.add_parser("list", help="List available providers")
    provider_list.add_argument("--json", action="store_true", default=False)

    provider_switch = provider_sub.add_parser("switch", help="Switch active provider in Claude CLI settings")
    provider_switch.add_argument("name", help="Provider name to activate")

    provider_add = provider_sub.add_parser("add", help="Add a custom provider")
    provider_add.add_argument("--name", required=True, help="Provider name")
    provider_add.add_argument("--api-key-env", default="ANTHROPIC_API_KEY", help="Environment variable name for API key")
    provider_add.add_argument("--base-url", default=None, help="API base URL")
    provider_add.add_argument("--models", nargs="*", default=None, help="Model IDs supported by this provider")
    provider_add.add_argument("--notes", default=None, help="Notes about this provider")

    provider_remove = provider_sub.add_parser("remove", help="Remove a provider from config")
    provider_remove.add_argument("name", help="Provider name to remove")

    provider_reset = provider_sub.add_parser("reset", help="Reset providers.json to factory defaults")

    provider_verify = provider_sub.add_parser("verify", help="Verify provider endpoint connectivity and auth")
    provider_verify.add_argument("name", nargs="?", default=None, help="Provider name to verify (omit to verify all)")
    provider_verify.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds (default: 15)")
    provider_verify.add_argument("--json", action="store_true", default=False, help="Output as JSON")

    provider_setkey = provider_sub.add_parser("set-key", help="Store API key for a provider (keychain-style)")
    provider_setkey.add_argument("name", help="Provider name")
    provider_setkey.add_argument("--api-key", default=None, help="API key value (will prompt if omitted)")
    provider_setkey.add_argument("--auth-token", default=None, help="Auth token value (for ANTHROPIC_AUTH_TOKEN providers)")

    provider_import = provider_sub.add_parser("import-cc-switch", help="Import credentials from cc-switch DB into worker config")
    provider_import.add_argument("--dry-run", action="store_true", default=False, help="Show what would be imported without making changes")

    provider_export = provider_sub.add_parser("export", help="Export full provider config as JSON (key status included, key values hidden)")
    provider_export.add_argument("--output", default=None, help="Write to file instead of stdout")

    return parser


# Live Session Mode registry (in-process only; sessions are lost if caller exits)
_active_sessions: dict[str, LongRunSession] = {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup":
        result = check_prerequisites()
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("=== Claude Worker Setup Check ===\n")
            for name, check in result["checks"].items():
                status = "OK" if check.get("ok") else "MISSING"
                print(f"  [{status}] {name}: {check.get('path', check.get('message', ''))}")
            if result["missing"]:
                print(f"\nMissing: {', '.join(result['missing'])}")
                print("\nInstallation hints:")
                for key, hint in result.get("install_hints", {}).items():
                    if key.replace("_", " ") in " ".join(result["missing"]):
                        print(f"  {key}: {hint}")
            else:
                print("\nAll prerequisites met.")
        return 0 if result["ready"] else 1

    if args.command == "provider":
        registry = ProviderRegistry()
        if args.provider_command == "list":
            providers = registry.list_providers()
            cred_store = registry.cred_store
            if getattr(args, "json", False):
                output = []
                for p in providers:
                    d = p.to_dict()
                    d["key_status"] = {
                        "api_key": "SET" if cred_store.get_credential(p.name, "api_key") or os.environ.get(p.api_key_env, "") else "NOT SET",
                        "auth_token": "SET" if (p.auth_token_env and (cred_store.get_credential(p.name, "auth_token") or os.environ.get(p.auth_token_env, ""))) else ("N/A" if not p.auth_token_env else "NOT SET"),
                    }
                    output.append(d)
                print(json.dumps(output, ensure_ascii=False, indent=2))
            else:
                print("Available providers:\n")
                for p in providers:
                    stored_key = cred_store.get_credential(p.name, "api_key")
                    env_key = os.environ.get(p.api_key_env, "")
                    has_key = bool(stored_key or env_key)
                    key_source = "CredentialStore" if stored_key else ("env var" if env_key else "NOT SET")
                    stored_token = cred_store.get_credential(p.name, "auth_token") if p.auth_token_env else None
                    env_token = os.environ.get(p.auth_token_env, "") if p.auth_token_env else ""
                    has_token = bool(stored_token or env_token)
                    models = ", ".join(p.models) if p.models else "(all Claude models)"
                    url = p.base_url or "(official API)"
                    print(f"  {p.name}")
                    print(f"    base_url:    {url}")
                    print(f"    models:      {models}")
                    print(f"    api_key:     {p.api_key_env} [{key_source}]")
                    if p.auth_token_env:
                        token_source = "CredentialStore" if stored_token else ("env var" if env_token else "NOT SET")
                        print(f"    auth_token:  {p.auth_token_env} [{token_source}]")
                    print(f"    ready:       {'YES' if (has_key or has_token) else 'NO - run: claude-worker provider set-key ' + p.name}")
                    if p.notes:
                        print(f"    notes:       {p.notes}")
                    print()
            return 0

        if args.provider_command == "switch":
            try:
                result = registry.switch_active_provider(args.name)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.provider_command == "add":
            config = ProviderConfig(
                name=args.name,
                api_key_env=args.api_key_env,
                base_url=args.base_url,
                models=args.models,
                notes=args.notes,
            )
            registry.add_provider(config)
            print(f"Provider '{args.name}' added.")
            return 0

        if args.provider_command == "remove":
            removed = registry.remove_provider(args.name)
            if removed:
                print(f"Provider '{args.name}' removed.")
                return 0
            print(f"Provider '{args.name}' not found.", file=sys.stderr)
            return 1

        if args.provider_command == "reset":
            # Delete providers.json and re-seed from defaults
            if registry.db_path.exists():
                registry.db_path.unlink()
            registry._providers.clear()
            registry._seed_from_defaults()
            print(f"Providers reset to defaults at {registry.db_path}")
            return 0

        if args.provider_command == "verify":
            if args.name:
                provider = registry.get_provider(args.name)
                if provider is None:
                    print(f"Unknown provider: {args.name}", file=sys.stderr)
                    return 1
                providers_to_check = [provider]
            else:
                providers_to_check = registry.list_providers()

            results = []
            for p in providers_to_check:
                vr = verify_provider_endpoint(p, timeout=args.timeout)
                results.append(vr)
                if not getattr(args, "json", False):
                    icon = "OK" if vr["ok"] else "FAIL"
                    print(f"  [{icon}] {p.name}")
                    print(f"         endpoint: {vr['base_url']}")
                    if vr["ok"]:
                        print(f"         model: {vr['model_used']}, latency: {vr['latency_ms']:.0f}ms")
                    else:
                        print(f"         error: {vr['error']}")
                    print()

            if getattr(args, "json", False):
                print(json.dumps(results, ensure_ascii=False, indent=2))

            all_ok = all(r["ok"] for r in results)
            return 0 if all_ok else 1

        if args.provider_command == "set-key":
            provider = registry.get_provider(args.name)
            if provider is None:
                print(f"Unknown provider: {args.name}", file=sys.stderr)
                return 1
            api_key_val = args.api_key
            auth_token_val = args.auth_token
            # Prompt if not provided via flag
            if not api_key_val and not auth_token_val:
                try:
                    api_key_val = input(f"Enter API key for {args.name} ({provider.api_key_env}): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("", file=sys.stderr)
                    return 1
            # Store in CredentialStore (encrypted SQLite DB)
            if api_key_val:
                registry.cred_store.set_credential(args.name, "api_key", api_key_val)
            if auth_token_val:
                registry.cred_store.set_credential(args.name, "auth_token", auth_token_val)
            # For providers that use auth_token, auto-set auth_token = api_key if not explicitly provided
            if provider.auth_token_env and api_key_val and not auth_token_val:
                registry.cred_store.set_credential(args.name, "auth_token", api_key_val)
            print(f"Key stored for provider '{args.name}' in {registry.cred_store.db_path}")
            print(f"  Credential resolution: CredentialStore → os.environ → cc-switch DB → settings.json")
            return 0

        if args.provider_command == "import-cc-switch":
            cc_providers = _load_cc_switch_providers()
            if not cc_providers:
                print("No cc-switch providers found at ~/.cc-switch/cc-switch.db")
                return 0
            cc_name_map = {
                "bailian-cp": "qwen-bailian-coding",
                "bailian": "qwen-bailian",
                "zhipu-cp": "z-ai",
                "kimi-cp": "kimi",
                "minimax-cp": "minimax",
                "deepseek-cp": "deepseek",
                "openrouter-cp": "openrouter",
                "siliconflow-cp": "siliconflow",
            }
            imported = []
            for cc_name, cc_env in cc_providers.items():
                mapped_name = cc_name_map.get(cc_name, cc_name)
                provider = registry.get_provider(mapped_name)
                if provider is None:
                    # Also try matching by base_url
                    cc_base_url = cc_env.get("ANTHROPIC_BASE_URL", "")
                    for p in registry.list_providers():
                        if p.base_url and cc_base_url.rstrip("/") == p.base_url.rstrip("/"):
                            provider = p
                            mapped_name = p.name
                            break
                if provider is None:
                    if not args.dry_run:
                        print(f"  SKIP {cc_name}: no matching worker provider")
                    continue
                auth_token_val = cc_env.get("ANTHROPIC_AUTH_TOKEN")
                api_key_val = cc_env.get("ANTHROPIC_API_KEY")
                model_val = cc_env.get("ANTHROPIC_MODEL")
                # Determine correct key_type based on ProviderConfig:
                # Providers with auth_token_env use ANTHROPIC_AUTH_TOKEN, others use ANTHROPIC_API_KEY
                # cc-switch stores everything as ANTHROPIC_AUTH_TOKEN regardless, so we remap
                if args.dry_run:
                    has_key = "yes" if (auth_token_val or api_key_val) else "no"
                    print(f"  WOULD IMPORT {cc_name} → {mapped_name} (has_key={has_key})")
                else:
                    if provider.auth_token_env:
                        # Provider uses ANTHROPIC_AUTH_TOKEN (deepseek, openrouter, z-ai, kimi)
                        if auth_token_val:
                            registry.cred_store.set_credential(mapped_name, "auth_token", auth_token_val)
                        if api_key_val:
                            registry.cred_store.set_credential(mapped_name, "api_key", api_key_val)
                    else:
                        # Provider uses ANTHROPIC_API_KEY (anthropic, bailian, minimax, siliconflow)
                        # cc-switch may have stored key as ANTHROPIC_AUTH_TOKEN, remap to api_key
                        effective_key = api_key_val or auth_token_val
                        if effective_key:
                            registry.cred_store.set_credential(mapped_name, "api_key", effective_key)
                    if model_val:
                        registry.cred_store.set_credential(mapped_name, "model", model_val)
                    imported.append(mapped_name)
                    print(f"  IMPORTED {cc_name} → {mapped_name}")
            if not args.dry_run and imported:
                print(f"\nImported {len(imported)} provider(s) into {registry.cred_store.db_path}")
            return 0

        if args.provider_command == "export":
            # Export full provider config with key status (no actual key values)
            providers = registry.list_providers()
            cred_store = registry.cred_store
            output = {
                "_comment": "Claude Worker Provider Configuration — safe to share (no key values)",
                "_setup_instructions": {
                    "1_set_api_key": "claude-worker provider set-key <name>",
                    "2_or_set_env_var": "export <api_key_env>=<your-key>",
                    "3_verify": "claude-worker provider verify <name>",
                    "4_run_task": "claude-worker start --kind coding --prompt '...' --provider <name>",
                },
                "providers": [],
            }
            for p in providers:
                stored_key = cred_store.get_credential(p.name, "api_key")
                env_key = os.environ.get(p.api_key_env, "")
                stored_token = cred_store.get_credential(p.name, "auth_token") if p.auth_token_env else None
                env_token = os.environ.get(p.auth_token_env, "") if p.auth_token_env else ""
                entry = p.to_dict()
                entry["key_status"] = {
                    "api_key": "SET" if (stored_key or env_key) else "NOT SET",
                    "auth_token": "SET" if (p.auth_token_env and (stored_token or env_token)) else ("N/A" if not p.auth_token_env else "NOT SET"),
                }
                output["providers"].append(entry)
            text = json.dumps(output, ensure_ascii=False, indent=2)
            if getattr(args, "output", None):
                out_path = Path(args.output)
                out_path.write_text(text, encoding="utf-8")
                print(f"Exported to {out_path}")
            else:
                print(text)
            return 0

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
            provider=args.provider,
            task_id=args.task_id,
            title=args.title,
            lane=args.lane,
            reasoning_mode=args.reasoning_mode,
            effort=args.effort,
            sandbox_identity=args.sandbox_identity,
            sandbox_kind=args.sandbox_kind,
            capability_profile=args.capability_profile,
            write_scope=args.write_scope or [],
            network_policy=args.network_policy,
            workspace_root=args.workspace_root,
            runtime_root=args.runtime_root,
            environment_mode=args.environment_mode,
            max_turns=args.max_turns,
            allowed_tools=args.allowed_tools,
            resume_session=args.resume_session,
            continue_session=args.continue_session,
            fork_session=args.fork_session,
            bare_mode=args.bare,
            output_format=args.output_format,
            input_files=args.input_file,
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

    if args.command == "send":
        # Send a follow-up prompt to a running interactive/detached session
        # This writes the prompt to the run directory and signals it via stdin
        run_id = args.run_id
        run_dir = runtime.run_root / run_id
        if not run_dir.exists():
            print(f"Unknown run: {run_id}", file=sys.stderr)
            return 1
        # Write follow-up prompt as a numbered message file
        followup_dir = run_dir / "followups"
        followup_dir.mkdir(exist_ok=True)
        existing = len(list(followup_dir.glob("msg-*.txt")))
        msg_path = followup_dir / f"msg-{existing + 1}.txt"
        msg_path.write_text(args.prompt, encoding="utf-8")
        # For interactive mode: write to stdin pipe if process is alive
        meta_path = run_dir / "meta.json"
        meta = _read_json(meta_path) if meta_path.exists() else {}
        child_pid = meta.get("child_pid")
        stdin_pipe_path = run_dir / "stdin-pipe.txt"
        # Append prompt to stdin pipe (the wrapper script reads this)
        with stdin_pipe_path.open("a", encoding="utf-8") as f:
            f.write(args.prompt + "\n")
        result = {
            "run_id": run_id,
            "followup_number": existing + 1,
            "prompt": args.prompt,
            "child_pid": child_pid,
            "method": "stdin-pipe",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "continue":
        # Continue a completed session by starting a new run with --resume
        run_id = args.run_id
        run_dir = runtime.run_root / run_id
        if not run_dir.exists():
            print(f"Unknown run: {run_id}", file=sys.stderr)
            return 1
        # Read session_id from the previous run's stdout
        final_path = run_dir / "final.json"
        session_id = None
        if final_path.exists():
            try:
                final = _read_json(final_path)
                stdout_text = final.get("stdout", "")
                if stdout_text:
                    stdout_data = json.loads(stdout_text)
                    session_id = stdout_data.get("session_id")
            except (json.JSONDecodeError, TypeError):
                pass
        if not session_id:
            print(f"Cannot find session_id for run {run_id}. The run may not have completed successfully.", file=sys.stderr)
            return 1
        # Start a new run with --resume pointing to the previous session
        prev_meta = _read_json(run_dir / "meta.json") if (run_dir / "meta.json").exists() else {}
        prev_packet = prev_meta.get("packet", {})
        new_packet = WorkerPacket(
            kind=prev_packet.get("kind", "coding"),
            prompt=args.prompt,
            cwd=prev_packet.get("cwd"),
            model=prev_packet.get("model", DEFAULT_MODEL),
            permission_mode=prev_packet.get("permission_mode", DEFAULT_PERMISSION_MODE),
            execution_mode="one_shot",
            provider=prev_packet.get("provider"),
            max_turns=args.max_turns,
            resume_session=session_id,
            fork_session=args.fork,
        )
        record = runtime.start(new_packet)
        print(json.dumps({
            "continued_from": run_id,
            "new_run_id": record.run_id,
            "session_id": session_id,
            "forked": args.fork,
            "new_run_dir": str(record.run_dir),
        }, ensure_ascii=False))
        return 0

    # --- Live Session Mode commands ---

    if args.command == "session-start":
        packet = WorkerPacket(
            kind="coding",
            prompt=args.prompt,
            cwd=args.cwd,
            model=args.model or DEFAULT_MODEL,
            permission_mode=args.permission_mode or DEFAULT_PERMISSION_MODE,
            provider=args.provider,
            max_turns=args.max_turns,
            allowed_tools=args.allowed_tools,
            bare_mode=args.bare,
            resume_session=args.resume_session,
            output_format="stream-json",
        )
        session = LongRunSession(packet, runtime=runtime)
        result = session.start()
        # Store session in global registry
        _active_sessions[result["session_id"]] = session
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "session-send":
        session = _active_sessions.get(args.session_id)
        if session is None:
            print(f"Unknown session: {args.session_id}", file=sys.stderr)
            return 1
        result = session.send(args.prompt)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "session-capture":
        session = _active_sessions.get(args.session_id)
        if session is None:
            print(f"Unknown session: {args.session_id}", file=sys.stderr)
            return 1
        result = session.capture(last_n=args.last_n)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "session-status":
        session = _active_sessions.get(args.session_id)
        if session is None:
            print(f"Unknown session: {args.session_id}", file=sys.stderr)
            return 1
        result = session.status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "session-stop":
        session = _active_sessions.get(args.session_id)
        if session is None:
            print(f"Unknown session: {args.session_id}", file=sys.stderr)
            return 1
        result = session.stop()
        # Remove from active registry
        _active_sessions.pop(args.session_id, None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
