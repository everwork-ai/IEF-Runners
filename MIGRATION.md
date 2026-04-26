# Migration Notice

This repository has been absorbed into the IEF (Intelligent Employee Framework) repository family.

## New home

- **IEF-Workers repository**: https://github.com/brantzh6/IEF-Workers
- **Claude Code implementation**: `IEF-Workers/claudecode/`

## What changed

`claude-worker` is no longer the top-level execution layer. It is now the **first worker implementation** inside the broader IEF-Workers architecture.

| Before | After |
|---|---|
| `claude-worker` as standalone execution repo | `IEF-Workers/claudecode/` as one worker among many |
| Top-level worker logic | Worker logic implements `core/WorkerInterface` |

## Migration status

- [x] IEF-Workers repository created
- [x] `claudecode/` directory initialized
- [ ] Code files copied (pending — use `git` to migrate)
- [ ] Old repo to be archived after migration stabilizes

## How to migrate code

```bash
# Clone the new repo
git clone https://github.com/brantzh6/IEF-Workers.git
cd IEF-Workers

# Add claude-worker as remote and fetch
git remote add claude-worker https://github.com/brantzh6/claude-worker.git
git fetch claude-worker

# Checkout claude-worker files into claudecode/
git checkout claude-worker/main -- .
# Then move files into claudecode/ directory

# Commit and push
git add claudecode/
git commit -m "migrate claude-worker into IEF-Workers/claudecode"
git push origin main
```

## Why not rename directly?

Per IEF Design Pack v0.2 section 19.4, the old repository represents **one implementation**, while the new repository represents **an architectural layer**. That is a boundary change, not a pure rename.

See [IEF-Workers](https://github.com/brantzh6/IEF-Workers) for the canonical worker layer.
