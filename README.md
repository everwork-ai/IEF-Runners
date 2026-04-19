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

```bash
# List providers
python -m claude_worker.worker provider list

# Add a custom provider
python -m claude_worker.worker provider add my-provider \
  --base-url https://api.example.com/api/anthropic \
  --auth-token-env ANTHROPIC_AUTH_TOKEN

# Import from cc-switch
python -m claude_worker.worker provider import-cc-switch

# Verify connectivity
python -m claude_worker.worker provider verify my-provider
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

