# Claude Worker

A CLI-driven Claude Code executor that wraps the `claude` CLI into a structured, programmable interface.

## Three Invocation Patterns

```
──────────────────────────────────────────────────────────────────
 1. Task Mode  (one_shot / detached)
──────────────────────────────────────────────────────────────────
    start → wait → fetch

    Single task, single result.  CC process runs once, writes
    durable artifacts, then exits.  "detached" allows the caller
    to detach and poll/fetch later.

    CLI:   start --execution-mode one_shot|detached → wait → fetch

──────────────────────────────────────────────────────────────────
 2. Session Chain Mode  (continue)
──────────────────────────────────────────────────────────────────
    start → wait → fetch → continue → wait → fetch → ...

    Each turn is a separate CC invocation linked via --resume so
    the model retains context.  Crash-resilient: any completed
    run can be continued from a fresh process.

    CLI:   start → wait → fetch
           continue --run-id <id> --prompt "..."

──────────────────────────────────────────────────────────────────
 3. Live Session Mode  (LongRunSession)
──────────────────────────────────────────────────────────────────
    session-start → session-send / session-capture → session-stop

    CC process stays alive.  Inject follow-up prompts at any time
    via bidirectional streaming (--input-format stream-json).
    Auto-approves tool permissions in bypassPermissions mode.

    CLI:   session-start --prompt "..."
           session-send    --session-id <id> --prompt "..."
           session-capture --session-id <id>
           session-stop    --session-id <id>

    Python API:
           session = LongRunSession(packet)
           session.start()
           session.send("follow-up prompt")
           output = session.capture()
           session.stop()

──────────────────────────────────────────────────────────────────
 Quick comparison
──────────────────────────────────────────────────────────────────
                    Task      Session Chain    Live Session
  ─────────────────────────────────────────────────────────
  CC process        exits     exits per turn   stays alive
  Context kept?     no        yes (--resume)   yes (in-process)
  Crash-resilient?  yes       yes              no
  Latency per turn  cold      warm (cache)     hot (live)
  Inject mid-run?   no        no               yes
  Cross-process?    yes       yes              no (in-memory)
```

## Project Structure

```
code/services/api/
├── claude_worker/
│   └── worker.py              # Complete runtime (single file, zero dependencies)
└── tests/
    └── test_claude_worker.py  # 55 tests
```

## Runtime Directory

All config and credentials live in one directory:

```
~/.claude-worker/                          # Default (no setup needed)
├── config/
│   └── providers.json                     # Provider definitions (auto-generated on first run)
└── credentials.db                         # Encrypted API key storage

# Or override:
CLAUDE_WORKER_HOME=/custom/path            # All files go here instead
```

**No environment variable needed for normal use.** The default `~/.claude-worker/` works out of the box.

To override (optional):

```powershell
# PowerShell — current session only
$env:CLAUDE_WORKER_HOME = "D:\code\_agent-runtimes\claude-worker-dev"

# PowerShell — permanent (user-level)
[Environment]::SetEnvironmentVariable("CLAUDE_WORKER_HOME", "D:\custom\path", "User")

# Linux/Mac — current session
export CLAUDE_WORKER_HOME=/custom/path

# Linux/Mac — permanent (add to ~/.bashrc or ~/.zshrc)
echo 'export CLAUDE_WORKER_HOME=/custom/path' >> ~/.bashrc
```

## Quick Start

```bash
# Task Mode — run a single coding task
python -m claude_worker.worker start \
  --kind coding \
  --prompt "Add error handling to all API calls" \
  --provider z-ai

# Session Chain — continue a completed run
python -m claude_worker.worker continue \
  --run-id <run-id> \
  --prompt "Now add unit tests for the error handling"

# Live Session — interactive multi-turn
python -m claude_worker.worker session-start \
  --prompt "Refactor the auth module" \
  --provider z-ai
# (returns session-id, then use session-send/capture/stop)
```

## Key Features

- **Provider switching**: Auto-resolves model → provider, switches CC settings and env vars
- **Credential store**: Encrypted per-provider credentials (compatible with cc-switch import)
- **Safety bounds**: `--max-turns`, `--allowed-tools`, `--permission-mode`
- **Durable artifacts**: `final.json`, `stdout.txt`, `exitcode.txt`, `events.ndjson`
- **Detached execution**: Fire-and-forget with poll/fetch/abort lifecycle
- **CC native capabilities**: `--resume`, `--continue`, `--fork-session`, `--bare`, `--output-format stream-json`
- **Reasoning control**: `--effort` flag controls extended thinking (low/medium/high/max), budget-based depth scaling

## Provider Management

### Credential Resolution Order

When a task runs, credentials are resolved in this order (first non-empty wins):

```
1. CredentialStore (~/.claude-worker/credentials.db)  ← set via: provider set-key
2. Environment variables (e.g. ZAI_API_KEY=xxx)       ← set via: export / .env
3. cc-switch DB (legacy fallback)                      ← imported via: provider import-cc-switch
4. ~/.claude/settings.json (last resort)               ← existing CC config
```

**You only need ONE of these.** If you store a key via `provider set-key`, you do NOT need to set environment variables.

### How to Set Up a Provider

```bash
# Option A: Store key in encrypted DB (recommended — persists across sessions)
python -m claude_worker.worker provider set-key z-ai
# Prompts: Enter API key for z-ai (ZAI_API_KEY): <paste your key>

# Option B: Environment variable (no DB, must set every session)
export ZAI_API_KEY=your-key-here

# Then verify it works:
python -m claude_worker.worker provider verify z-ai
```

### Supported Providers

| Provider | Key Env Var | Base URL | Models | Auth Method | Thinking |
|----------|------------|----------|--------|-------------|----------|
| **z-ai** | `ZAI_API_KEY` | `https://api.z.ai/api/anthropic` | glm-5.1, glm-5, glm-4.7, glm-4.5-air | AUTH_TOKEN | ✅ budget-controlled |
| **qwen-bailian-coding** | `DASHSCOPE_CODING_API_KEY` | `https://coding.dashscope.aliyuncs.com/apps/anthropic` | qwen3.6-plus, qwen3-coder-plus, qwen3.5-plus, qwen3-coder-next, glm-5, glm-4.7, MiniMax-M2.5, kimi-k2.5 | API_KEY | ✅ default-on |
| **qwen-bailian** | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/apps/anthropic` | qwen3.6-plus, qwen3-max, qwen3-coder-plus, qwen3-coder-next, qwen-plus, qwen-turbo, qwen3.5-flash, qwen3-vl-plus | API_KEY | ✅ default-on |
| **deepseek** | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/anthropic` | deepseek-chat, deepseek-reasoner | AUTH_TOKEN | ⚠️ unverified |
| **openrouter** | `OPENROUTER_API_KEY` | `https://openrouter.ai/api` | anthropic/claude-opus-4.7, anthropic/claude-sonnet-4.6, openai/gpt-4o | AUTH_TOKEN | ⚠️ unverified |
| **kimi** | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/anthropic` | kimi-k2.5, kimi-k2-thinking, kimi-k2-turbo-preview | AUTH_TOKEN | ⚠️ unverified |
| **minimax** | `MINIMAX_API_KEY` | `https://api.minimax.io/anthropic` | MiniMax-M2.5, MiniMax-M2.5-highspeed, MiniMax-M2.1 | API_KEY | ⚠️ unverified |
| **siliconflow** | `SILICONFLOW_API_KEY` | `https://api.siliconflow.cn/` | deepseek-ai/DeepSeek-V3, Qwen/Qwen3-235B-A22B, Pro/deepseek-ai/DeepSeek-R1 | API_KEY | ⚠️ unverified |
| **anthropic** | `ANTHROPIC_API_KEY` | (official API) | (all Claude models) | API_KEY (or `claude login`) | ✅ native |

**Auth Method explained:**
- **API_KEY**: Your key is sent as `ANTHROPIC_API_KEY`. Standard Anthropic protocol.
- **AUTH_TOKEN**: Your key is sent as `ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_API_KEY` is cleared. Used by providers that follow the newer Anthropic auth pattern.

### Check What's Configured

```bash
# See all providers, key status, and which are ready:
python -m claude_worker.worker provider list

# Export full config (safe, no key values):
python -m claude_worker.worker provider export

# Test connectivity:
python -m claude_worker.worker provider verify z-ai

# Import from existing cc-switch installation:
python -m claude_worker.worker provider import-cc-switch

# Reset providers.json to factory defaults:
python -m claude_worker.worker provider reset
```

### Add a Custom Provider

```bash
python -m claude_worker.worker provider add my-provider \
  --base-url https://api.example.com/anthropic \
  --api-key-env MY_PROVIDER_API_KEY \
  --models model-a model-b

python -m claude_worker.worker provider set-key my-provider
```

### Modify an Existing Provider

Providers are stored in `~/.claude-worker/config/providers.json`. You can edit it directly:

```bash
# Change a provider's base URL or model list without touching code:
vim ~/.claude-worker/config/providers.json

# Or remove a provider you don't need:
python -m claude_worker.worker provider remove anthropic

# Undo all changes and restore defaults:
python -m claude_worker.worker provider reset
```

## Reasoning Control

The `--effort` flag controls extended thinking (reasoning) depth. No separate toggle needed — `--effort` is both the switch and the depth control.

```
--effort low      → No extended thinking, direct response
--effort medium   → Light thinking
--effort high     → Deep thinking (default)
--effort max      → Maximum thinking budget
```

**Verified behavior (2026-04-19):**

| Provider | Without thinking param | With thinking param | Budget scaling |
|----------|----------------------|--------------------|----|
| **z-ai** | No thinking block, direct answer | ✅ Thinking block returned | budget 10K→1K chars, 32K→2K chars |
| **qwen-bailian-coding** | Thinking block by default | ✅ Thinking block returned | budget controls depth |

CC CLI maps `--effort` → `thinking.budget_tokens` in the Anthropic API request. Providers that don't support the `thinking` parameter gracefully degrade to non-reasoning mode.

```bash
# Use max reasoning for complex tasks on flagship models:
python -m claude_worker.worker start \
  --kind coding \
  --prompt "Design a fault-tolerant caching strategy" \
  --provider z-ai \
  --effort max

# Quick answer without reasoning:
python -m claude_worker.worker start \
  --kind coding \
  --prompt "Fix the typo in README" \
  --provider qwen-bailian-coding \
  --effort low
```

## Running Tests

```bash
cd code/services/api
python -m pytest tests/test_claude_worker.py -v
```

## Docs

- `docs/IKE_CLAUDE_WORKER_MCP_FEASIBILITY_2026-04-07.md` — Original feasibility analysis
- `docs/IKE_CLAUDE_WORKER_P1_HARDENING_PACKET_2026-04-08.md` — P1 hardening specification

## Requirements

- Python 3.10+
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- At least one configured provider (run `python -m claude_worker.worker setup` to check)

