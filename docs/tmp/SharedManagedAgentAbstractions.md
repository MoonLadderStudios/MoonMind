# Shared Managed Agent Abstractions Review

## Current State

We have Managed Agents support for four runtimes: Gemini CLI, Codex CLI, Cursor CLI, and Claude Code, in various stages of implementation.

### Workflow Adapters

`ManagedAgentAdapter` (`moonmind/workflows/adapters/managed_agent_adapter.py`) provides a common execution interface: auth profile resolution, environment shaping (`_shape_environment_for_oauth`, `_shape_environment_for_api_key`), slot leasing, and launcher triggering.

There are also `JulesAgentAdapter` and `CodexCloudAgentAdapter` which subclass `BaseExternalAgentAdapter`.

### Runtime Launchers

`ManagedRuntimeLauncher` (`moonmind/workflows/temporal/runtime/launcher.py`) contains a `build_command` method with a large `if/elif` block checking `profile.runtime_id` (`"codex_cli"`, `"gemini_cli"`, `"cursor_cli"`, generic path for `"claude_code"`) to construct CLI arguments.

### Environment Handling

In `ManagedRuntimeLauncher.launch()`, a hardcoded `_runtime_env_keys` list (`"HOME"`, `"GEMINI_HOME"`, `"GEMINI_CLI_HOME"`, `"CODEX_HOME"`, `"CODEX_CONFIG_HOME"`, `"CODEX_CONFIG_PATH"`) is explicitly passed through to the subprocess. The profile object also supports `passthrough_env_keys` for additional pass-through.

### Cursor CLI Workspace Prep (Dead Code)

`cursor_rules.py` provides `write_task_rule_file()` and `cursor_config.py` provides `write_cursor_cli_json()` for generating `.cursor/rules/` and `.cursor/cli.json` files. However, **neither is called from any code in the `workflows/` tree** — these are currently dead code from the managed runtime launcher's perspective.

### Gemini / Claude Workspace Prep (Nonexistent)

No workspace preparation code exists for Gemini CLI (e.g. writing `GEMINI_INSTRUCTIONS` or `.gemini/` instruction files) or Claude Code (e.g. `CLAUDE.md`). The `prepare_workspace()` hook in the Strategy would be **new capability** for these runtimes, not just cleanup.

### Cursor NDJSON Parser (Standalone)

`agents/base/ndjson_parser.py` provides Cursor-specific `--output-format stream-json` parsing and 429 rate-limit detection. It exists as standalone code not connected to the managed runtime path — a natural fit for a `CursorCliStrategy` output handler.

### Codex Worker (Parallel Execution Path)

`moonmind/agents/codex_worker/` is a **1940-line standalone execution pipeline** (repo prep, RAG context injection, publish/PR, output sanitization, self-heal, artifact management) that predates the managed runtime path. It is **not wired through** `ManagedRuntimeLauncher` and runs separately.

---

## All Runtime-Specific Branching Sites

The current runtime-specific branching violates the Open/Closed Principle. Adding a new runtime requires modifying multiple core classes. Here are **all** identified branching sites:

| Location | What it branches on | Runtimes handled |
|---|---|---|
| `launcher.py` `build_command()` | `profile.runtime_id` | codex_cli, gemini_cli, cursor_cli, generic/claude_code |
| `launcher.py` `_runtime_env_keys` | hardcoded list | GEMINI_HOME, CODEX_HOME (global, not per-runtime) |
| `adapter.py:286-293` command_template defaults | `runtime_id_for_profile` | gemini_cli, claude_code, codex_cli, fallback |
| `adapter.py:236-238` auth_mode defaults | `runtime_for_profile` | cursor_cli → oauth, all others → api_key |
| `supervisor.py` `_classify_exit()` | exit code (generic) | All runtimes treated identically |

---

## Proposed Strategy Pattern

### `ManagedRuntimeStrategy` Interface

```python
class ManagedRuntimeStrategy(ABC):
    """Per-runtime strategy for managed agent lifecycle."""

    @property
    @abstractmethod
    def runtime_id(self) -> str: ...

    @property
    @abstractmethod
    def default_command_template(self) -> list[str]:
        """e.g. ['gemini'] or ['codex', 'exec', '--full-auto']"""

    @property
    def default_auth_mode(self) -> str:
        """Override for runtimes using oauth (e.g. cursor_cli).

        Must align with the OAuthProviderSpec entries defined in
        docs/ManagedAgents/UniversalTmateOAuth.md.  Both registries
        share the runtime_id namespace (codex_cli, gemini_cli,
        claude_code, cursor_cli).
        """
        return "api_key"

    @abstractmethod
    def build_command(
        self,
        profile: ManagedRuntimeProfile,
        request: AgentExecutionRequest,
    ) -> list[str]: ...

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: ManagedRuntimeProfile,
    ) -> dict[str, str]:
        """Shape the subprocess environment for this runtime.

        Replaces both _runtime_env_keys passthrough AND
        _shape_environment_for_oauth/_api_key logic.  Each strategy
        decides what to clear, set, and preserve.

        Note: The underlying helpers (_OAUTH_CLEARED_VARS,
        _shape_environment_for_oauth) are also used by the OAuth
        Session orchestrator (UniversalTmateOAuth.md §9.2).  Shared
        env-shaping logic should be factored into common utilities
        rather than duplicated.
        """
        return dict(base_env)

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: AgentExecutionRequest,
    ) -> None:
        """Pre-launch workspace setup.

        Examples:
         - Cursor: write .cursor/rules/moonmind-task.mdc, .cursor/cli.json
         - Gemini: write GEMINI_INSTRUCTIONS or .gemini/ instruction files
         - Claude: write CLAUDE.md
        """

    def classify_exit(
        self,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> tuple[str, str | None]:
        """Classify process exit into (status, failure_class|None).

        Default treats 0 as completed, non-zero as failed.
        Override for runtimes with non-standard exit semantics.
        """
        if exit_code == 0:
            return "completed", None
        return "failed", "execution_error"
```

### Output Handling

Output parsing should be **streaming-aware** rather than a post-hoc `parse_output(stdout, stderr)`. Different runtimes stream differently:

- **Cursor CLI**: NDJSON (`--output-format stream-json`) — already parsed by `ndjson_parser.py`
- **Gemini CLI**: plain text stdout
- **Claude Code**: JSON output mode available but not currently enforced
- **Codex CLI**: plain text stdout

A separate `OutputParser` class or async iterator pattern is more appropriate:

```python
class ManagedRuntimeStrategy(ABC):
    # ...
    def create_output_parser(self) -> RuntimeOutputParser:
        """Factory for the runtime's stream parser.

        Returns a default line-based parser; override for
        structured formats like NDJSON.
        """
        return PlainTextOutputParser()
```

### Runtime Registry

```python
RUNTIME_STRATEGIES: dict[str, ManagedRuntimeStrategy] = {
    "gemini_cli": GeminiCliStrategy(),
    "codex_cli": CodexCliStrategy(),
    "cursor_cli": CursorCliStrategy(),
    "claude_code": ClaudeCodeStrategy(),
}
```

The launcher and adapter look up the strategy:

```python
strategy = RUNTIME_STRATEGIES.get(profile.runtime_id)
if strategy is None:
    raise ValueError(f"Unsupported runtime: '{profile.runtime_id}'")
cmd = strategy.build_command(profile, request)
```

---

## Supervisor vs Strategy Boundary

The following concerns stay in the **supervisor** (cross-cutting, not runtime-specific):

- Process heartbeats and timeout
- tmate session management and exit-code-file resolution
- SIGTERM → SIGKILL escalation
- PID reconciliation on restart
- Runtime file cleanup

The following move to the **strategy**:

- Command construction (`build_command`)
- Environment shaping (`shape_environment`)
- Workspace preparation (`prepare_workspace`)
- Exit classification (`classify_exit`)
- Output parsing (`create_output_parser`)
- Default command template and auth mode

---

## codex_worker Unification

The standalone `codex_worker` package (11 files, ~3000 lines) provides capabilities not yet present in the managed runtime path:

- **RAG context injection** (`_resolve_prompt_context`)
- **Publish/PR workflows** (`_maybe_publish`, branch/PR creation)
- **Output sanitization** (`publish_sanitization.py`)
- **Self-healing** (`self_heal.py`)
- **Metrics** (`metrics.py`)
- **Secret reference handling** (`secret_refs.py`)

### Options

| Option | Approach | Effort | Trade-off |
|---|---|---|---|
| **Wrap** | Keep codex_worker as-is, invoke from `CodexCliStrategy` | Low | Two execution paths remain |
| **Absorb** | Migrate capabilities into strategy + shared services | High | Cleanest architecture, single path |
| **Deprecate** | Mark as legacy, sunset independently | Minimal | Doesn't actually unify |

> [!IMPORTANT]
> This is a key architectural decision that should be made before implementing the Strategy refactor. The RAG context injection and publish workflows are capabilities that would benefit **all** runtimes if extracted into shared services.

---

## Summary

To reduce bespoke implementation per Managed Agent, we should transition from branching logic in the core adapters and launchers to a **Strategy Pattern-based Registry**. This encapsulates:

- Command building
- Environment shaping (clearing, setting, filtering — not just key lists)
- Workspace preparation (new capability for Gemini/Claude, existing dead code for Cursor)
- Exit classification (per-runtime semantics)
- Output parsing (streaming-aware)
- Default command templates and auth modes

into isolated, testable strategy classes. The supervisor retains cross-cutting process lifecycle concerns (heartbeats, timeouts, tmate, reconciliation).

The `codex_worker` unification decision should be resolved early since it determines whether `CodexCliStrategy` is a thin wrapper or a full migration.

---

## Implementation Phases

### Phase 1 — Foundation: ABC + Registry + Gemini Strategy

**Goal**: Establish the pattern with the simplest runtime first.

- [ ] Create `ManagedRuntimeStrategy` ABC in `moonmind/workflows/temporal/runtime/strategies/base.py`
- [ ] Create `RUNTIME_STRATEGIES` registry in `moonmind/workflows/temporal/runtime/strategies/__init__.py`
- [ ] Implement `GeminiCliStrategy` — the simplest runtime (no special output parsing, no workspace prep today)
  - `build_command`: extract from `launcher.py:342-351`
  - `shape_environment`: extract `GEMINI_HOME`, `GEMINI_CLI_HOME` passthrough
  - `default_command_template`: `["gemini"]`
  - `default_auth_mode`: `"api_key"`
- [ ] Wire `ManagedRuntimeLauncher.build_command()` to delegate to strategy when available, fall through to existing `if/elif` for unregistered runtimes
- [ ] Wire `ManagedAgentAdapter.start()` to read `default_command_template` and `default_auth_mode` from strategy when available
- [ ] Add unit tests for `GeminiCliStrategy`
- [ ] Verify existing integration tests still pass

**Output**: Strategy pattern is live for one runtime; remaining runtimes still use legacy branching.

---

### Phase 2 — Remaining Strategies: Cursor, Claude, Codex

**Goal**: Eliminate all `if/elif` branching.

- [ ] Implement `CursorCliStrategy`
  - `build_command`: extract from `launcher.py:352-365`
  - `shape_environment`: extract OAuth-related clearing, set `cursor_cli` env keys
  - `default_auth_mode`: `"oauth"`
  - `prepare_workspace`: wire in `write_task_rule_file()` and `write_cursor_cli_json()` from the existing dead code
  - `create_output_parser`: wrap existing `ndjson_parser.py`
- [ ] Implement `ClaudeCodeStrategy`
  - `build_command`: extract from `launcher.py:367-376` (generic path)
  - `prepare_workspace`: stub for future `CLAUDE.md` injection
- [ ] Implement `CodexCliStrategy` (thin version — see Phase 3 for unification decision)
  - `build_command`: extract from `launcher.py:335-341`
  - `shape_environment`: extract `CODEX_HOME`, `CODEX_CONFIG_HOME`, `CODEX_CONFIG_PATH` passthrough
  - `default_command_template`: `["codex", "exec", "--full-auto"]`
- [ ] Remove all `if/elif` branching from `launcher.py:build_command()`
- [ ] Remove `_runtime_env_keys` hardcoded list from `launcher.py`
- [ ] Remove command template defaults from `adapter.py:286-293`
- [ ] Remove auth mode defaults from `adapter.py:236-238`
- [ ] Add unit tests for each new strategy
- [ ] Run full test suite

**Output**: All runtime-specific logic lives in strategy classes. Core launcher and adapter are runtime-agnostic.

---

### Phase 3 — codex_worker Unification Decision + Execution

**Goal**: Resolve the parallel execution path.

**Decision required before starting** (see codex_worker Unification section above):
- **Wrap**: Wire `CodexCliStrategy` to delegate to `codex_worker` handlers — preserve existing behavior, reduce risk
- **Absorb**: Extract RAG context injection, publish/PR, and sanitization into shared services usable by all strategies
- **Deprecate**: Leave `codex_worker` as-is for the standalone daemon path, don't integrate

Recommended approach for MVP: **Wrap first, absorb incrementally**.

- [ ] If wrapping: update `CodexCliStrategy` to invoke `codex_worker` pipeline from the managed runtime path
- [ ] If absorbing: extract `_resolve_prompt_context` into a shared `ContextInjectionService`
- [ ] If absorbing: extract `_maybe_publish` into a shared `PublishService`
- [ ] Verify Codex CLI tasks work through the managed runtime path end-to-end

**Output**: Single execution path for Codex (or clear boundary between managed runtime and standalone daemon).

---

### Phase 4 — Exit Classification + Output Parsing

**Goal**: Per-runtime correctness for exit codes and structured output.

- [ ] Move `_classify_exit` from `supervisor.py` to call `strategy.classify_exit()` with stdout/stderr context
- [ ] Implement `CursorCliStrategy.classify_exit()` — parse NDJSON for richer failure classification
- [ ] Implement `CursorCliStrategy.create_output_parser()` — wrap `ndjson_parser.py` into the `RuntimeOutputParser` interface
- [ ] Implement `PlainTextOutputParser` as the default for Gemini/Claude/Codex
- [ ] Wire output parser into `supervisor.py` log streaming pipeline
- [ ] Add unit tests for exit classification and output parser per runtime

**Output**: Supervisor delegates exit classification and output parsing to strategies. Runtime-specific failure semantics (e.g. Cursor rate-limit detection via NDJSON) are handled correctly.

---

### Phase 5 — Workspace Prep + Polish

**Goal**: Enable per-runtime workspace preparation and clean up remaining tech debt.

- [ ] Implement `GeminiCliStrategy.prepare_workspace()` — write `.gemini/` instruction files or `GEMINI_INSTRUCTIONS`
- [ ] Implement `ClaudeCodeStrategy.prepare_workspace()` — write `CLAUDE.md` with task context
- [ ] Verify `CursorCliStrategy.prepare_workspace()` correctly writes `.cursor/rules/` and `.cursor/cli.json`
- [ ] Add workspace prep call in `launcher.py:launch()` after workspace resolution, before subprocess spawn
- [ ] Factor shared env-shaping helpers (`_OAUTH_CLEARED_VARS`, `_shape_environment_for_oauth`) into `moonmind/auth/env_shaping.py` for reuse by both strategies and the OAuth Session orchestrator (per UniversalTmateOAuth.md alignment)
- [ ] Remove dead imports and unused code from adapter and launcher
- [ ] Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` to reference strategy pattern
- [ ] Move `docs/tmp/SharedManagedAgentAbstractions.md` to `docs/ManagedAgents/SharedManagedAgentAbstractions.md`

**Output**: All strategy hooks are live. Shared env-shaping logic is factored for OAuth session reuse. Documentation is up to date.
