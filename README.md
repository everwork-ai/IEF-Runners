# IEF-Runners

The **Hands** layer of the [Intelligent Employee Foundry (IEF)](https://github.com/everwork-ai/IEF-Program).

## What is IEF-Runners?

IEF-Runners is the execution layer of IEF. It hosts concrete worker implementations that receive tasks from [IEF-Operations](https://github.com/everwork-ai/IEF-Operations) and produce artifacts.

Each runner in this repository implements the **Runner Interface**:

```
prepare(context_pack)   → Load context, validate inputs
execute(task_slice)     → Run the task, produce artifacts
report(status, artifacts, errors) → Return structured results
resume(run_ref)         → Continue from a previous run
cancel(run_ref)         → Abort an in-progress run
```

## Repository Structure

```
IEF-Runners/
├── README.md              ← You are here (layer-level documentation)
└── runners/
    └── claudecode/        ← First runner: Claude Code based implementation
        ├── README.md      ← Runner-specific documentation
        ├── claudecode.skill.json
        ├── code/          ← Implementation code
        └── docs/          ← Design documents and specifications
```

## Runner Registry

| Runner | Codename | Status | Description |
|---|---|---|---|
| Claude Code | `claudecode` | Active | CLI-driven Claude Code executor with provider switching |

## Relationship to Other IEF Layers

- **IEF-Operations** (Dispatch) sends Task objects to runners
- **IEF-Protocol** (Relay) defines the artifact contracts and handoff format
- **IEF-Knowledge** (Library) provides context packs and skill definitions
- **IEF-Governance** (Charter) defines review gates and trust boundaries

## Contributing a New Runner

1. Create `runners/<your-runner>/` directory
2. Implement the Runner Interface
3. Provide `<runner>.skill.json` manifest
4. Open an issue in IEF-Program for registry approval

## License

See individual runner directories for license information.
