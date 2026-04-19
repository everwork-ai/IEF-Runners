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
│   └── worker.py              # Complete runtime (single file)
└── tests/
    └── test_claude_worker.py  # 54 tests
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
claude-worker provider set-key z-ai
# Prompts: Enter API key for z-ai (ZAI_API_KEY): <paste your key>

# Option B: Environment variable (no DB, must set every session)
export ZAI_API_KEY=your-key-here

# Then verify it works:
claude-worker provider verify z-ai
```

### Supported Providers

| Provider | Key Env Var | Base URL | Models | Auth Method |
|----------|------------|----------|--------|-------------|
| **z-ai** | `ZAI_API_KEY` | `https://api.z.ai/api/anthropic` | glm-4.7, glm-4.5-air | AUTH_TOKEN (key → ANTHROPIC_AUTH_TOKEN) |
| **qwen-bailian-coding** | `DASHSCOPE_CODING_API_KEY` | `https://coding.dashscope.aliyuncs.com/apps/anthropic` | qwen3.6-plus, qwen3-coder, qwen3-coder-plus, qwen-coder-plus-latest | API_KEY (key → ANTHROPIC_API_KEY) |
| **qwen-bailian** | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/apps/anthropic` | qwen3.6-plus, qwen3-max, qwen3-coder-plus, qwen3-coder-next, qwen-plus, qwen-turbo, qwen3.5-flash, qwen3-vl-plus | API_KEY |
| **deepseek** | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/anthropic` | deepseek-chat, deepseek-reasoner | AUTH_TOKEN |
| **openrouter** | `OPENROUTER_API_KEY` | `https://openrouter.ai/api` | anthropic/claude-opus-4.7, anthropic/claude-sonnet-4.6, openai/gpt-4o | AUTH_TOKEN |
| **kimi** | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/anthropic` | kimi-k2.5, kimi-k2-thinking, kimi-k2-turbo-preview | AUTH_TOKEN |
| **minimax** | `MINIMAX_API_KEY` | `https://api.minimax.io/anthropic` | MiniMax-M2.5, MiniMax-M2.5-highspeed, MiniMax-M2.1 | API_KEY |
| **siliconflow** | `SILICONFLOW_API_KEY` | `https://api.siliconflow.cn/` | deepseek-ai/DeepSeek-V3, Qwen/Qwen3-235B-A22B, Pro/deepseek-ai/DeepSeek-R1 | API_KEY |
| **anthropic** | `ANTHROPIC_API_KEY` | (official API) | (all Claude models) | API_KEY (or `claude login`) |

**Auth Method explained:**
- **API_KEY**: Your key is sent as `ANTHROPIC_API_KEY`. Standard Anthropic protocol.
- **AUTH_TOKEN**: Your key is sent as `ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_API_KEY` is cleared. Used by providers that follow the newer Anthropic auth pattern.

### Check What's Configured

```bash
# See all providers, key status, and which are ready:
claude-worker provider list

# Export full config (safe, no key values):
claude-worker provider export

# Test connectivity:
claude-worker provider verify z-ai

# Import from existing cc-switch installation:
claude-worker provider import-cc-switch
```

### Add a Custom Provider

```bash
claude-worker provider add my-provider \
  --base-url https://api.example.com/anthropic \
  --api-key-env MY_PROVIDER_API_KEY \
  --models model-a model-b

claude-worker provider set-key my-provider
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

