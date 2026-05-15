# Quickstart: Runtime Command Rendering After Context Preparation

Use Jira issue `MM-686` and the original preset brief preserved in `spec.md` as the source of truth.

## Red-First Unit Tests

1. Add strategy/renderer tests before production changes:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py
```

Expected before implementation: new tests fail because no final runtime command renderer exists and command builders append the mutated instruction text directly.

2. Cover these unit cases:
- Codex CLI prompt-prefix render starts with `/review` before body and prepared context.
- Claude Code prompt-prefix render starts with `/review` before body and prepared context.
- Unknown valid `/future-command` remains opaque pass-through and is not materialized.
- Escaped `\/review` renders as literal non-command text.
- Renderer failure returns `runtime_command_render_failed` or an approved fallback event before launch.
- Render diagnostics are redacted and command args/body are treated as untrusted text.

## Red-First Integration Tests

1. Add launcher boundary coverage before production changes:

```bash
./tools/test_integration.sh
```

For focused local iteration, use the relevant pytest path first, then rerun the integration runner:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short
```

Expected before implementation: new integration tests fail because retrieval context, skill summaries, or managed runtime notes can occupy the first prompt position instead of the slash command.

2. Cover these integration cases:
- Codex CLI launch with `/review`, retrieval context, skill activation summary, and managed runtime notes captures a prompt beginning with `/review`.
- Claude Code launch with `/review` and retrieval context captures a prompt beginning with `/review`.
- The same captured prompt places prepared context after the instruction body.
- A hard render failure prevents subprocess launch.

## Implementation Validation

After production changes:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/tasks/test_task_contract.py
./tools/test_integration.sh
```

Final verification should confirm:
- `MM-686` remains in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-command-rendering.md`, `quickstart.md`, later tasks, commit text, and pull request metadata.
- Prompt-prefix commands remain first after all MoonMind-prepared context.
- Unknown commands remain pass-through for slash-capable runtimes.
- Escaped literal commands do not execute.
- Render failures are typed or explicitly represented as approved fallback events.
