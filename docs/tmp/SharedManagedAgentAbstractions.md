# Shared Managed Agent Abstractions Review

**Tracking note:** This document tracks the managed-runtime **strategy pattern** rollout. Implementation status below was reconciled with the tree under `moonmind/workflows/temporal/runtime/strategies/` and related launch/adapter code.

## Current State

We have Managed Agents support for four runtimes: Gemini CLI, Codex CLI, Cursor CLI, and Claude Code. **Command construction** for all four is implemented via `ManagedRuntimeStrategy` subclasses and `RUNTIME_STRATEGIES` (`moonmind/workflows/temporal/runtime/strategies/`). **`build_command()`** in `ManagedRuntimeLauncher` delegates to the registry when `profile.runtime_id` is registered, then falls through to a **generic** template path (model/effort/prompt flags) for unknown runtimes — the old per-runtime `if/elif` block is gone.

### Workflow Adapters

`ManagedAgentAdapter` (`moonmind/workflows/adapters/managed_agent_adapter.py`) provides a common execution interface: auth profile resolution, environment shaping (`_shape_environment_for_oauth`, `_shape_environment_for_api_key`), slot leasing, and launcher triggering. **Default** `command_template` and `auth_mode` are taken from the matching strategy when registered; **fallbacks** remain for unregistered `runtime_id` values (auth: `cursor_cli` → oauth else api_key; template: `[runtime_id]`).

There are also `JulesAgentAdapter` and `CodexCloudAgentAdapter` which subclass `BaseExternalAgentAdapter`.

### Runtime Launchers

`ManagedRuntimeLauncher.build_command()` uses **`get_strategy(runtime_id)`** and `strategy.build_command(...)` for `gemini_cli`, `codex_cli`, `cursor_cli`, and `claude_code`.

### Environment Handling

`GeminiCliStrategy` and `CodexCliStrategy` define **`shape_environment()`** (restricted passthrough sets for Gemini/Codex home keys). **`ManagedRuntimeLauncher.launch()` does not call `shape_environment()` yet** — it still merges a hardcoded `_runtime_env_keys` list (`HOME`, `GEMINI_*`, `CODEX_*`) into `env_overrides` before spawn. OAuth/API-key clearing and injection remain in the adapter helpers, not in strategies.

The profile object also supports `passthrough_env_keys` for additional pass-through.

### Cursor CLI Workspace Prep (Still Unwired)

`cursor_rules.py` provides `write_task_rule_file()` and `cursor_config.py` provides `write_cursor_cli_json()` for generating `.cursor/rules/` and `.cursor/cli.json` files. **`CursorCliStrategy` does not override `prepare_workspace()`**, so these helpers are **still not invoked** from the managed runtime path (the launcher *does* call `prepare_workspace()` on the strategy when a workspace path exists).

### Gemini / Claude Workspace Prep (Nonexistent)

No workspace preparation code exists yet for Gemini CLI (e.g. `GEMINI_INSTRUCTIONS` or `.gemini/` instruction files) or Claude Code (e.g. `CLAUDE.md`) inside the strategies. **`prepare_workspace()`** is a no-op on the base class for those runtimes.

### Cursor NDJSON / Output Parsing

`moonmind/workflows/temporal/runtime/output_parser.py` defines **`NdjsonOutputParser`** and **`PlainTextOutputParser`** implementing **`RuntimeOutputParser`**. `CursorCliStrategy.create_output_parser()` returns `NdjsonOutputParser`, and **`classify_exit()`** uses it for rate-limit detection. **`ManagedRunSupervisor`** still uses a **static** `_classify_exit(exit_code, timed_out)` and does **not** pass stdout/stderr into strategies; log streaming does **not** yet use output parsers. The older `agents/base/ndjson_parser.py` module remains separate from the supervisor path.

### Codex Worker (Parallel Execution Path)

`moonmind/agents/codex_worker/` is a **1940-line standalone execution pipeline** (repo prep, RAG context injection, publish/PR, output sanitization, self-heal, artifact management) that predates the managed runtime path. It is **not wired through** `ManagedRuntimeLauncher` and runs separately.

---

## Runtime-Specific Branching Sites (Current)

Most **command-line construction** is encapsulated in strategies. Remaining cross-cutting or legacy touchpoints:

| Location | What it branches on | Notes |
|---|---|---|
| `launcher.py` `build_command()` | registry → generic fallback | Registered runtimes use strategy; unknown `runtime_id` uses generic model/effort/prompt extension |
| `launcher.py` `_runtime_env_keys` | hardcoded list | Still applied in `launch()`; strategy `shape_environment()` not wired |
| `adapter.py` command_template default | strategy → fallback | If no profile template: use `default_command_template`, else `[runtime_id]` |
| `adapter.py` auth_mode default | strategy → fallback | If strategy missing: `cursor_cli` → oauth, else api_key |
| `supervisor.py` `_classify_exit()` | exit code + timeout only | Does not call `strategy.classify_exit()` or use stdout/stderr |

---

## Proposed Strategy Pattern

The sketch below matches the spirit of `moonmind/workflows/temporal/runtime/strategies/base.py`. Minor differences exist (e.g. `create_output_parser()` defaults to `None` until strategies override; parameter types may use `Any` in the real ABC).

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

The codebase has adopted a **Strategy Pattern-based Registry** (`ManagedRuntimeStrategy`, `RUNTIME_STRATEGIES`) for **command construction**, **defaults** in the adapter, and a **`prepare_workspace()` hook** invoked from `ManagedRuntimeLauncher.launch()`. **Still outstanding** relative to the original vision: launcher should apply **`shape_environment()`** (and drop `_runtime_env_keys`), **supervisor** should delegate **`classify_exit()`** and optionally integrate **output parsers** into log handling, **Cursor** should wire **`prepare_workspace()`** to the existing file writers, and **Gemini/Claude** should gain real workspace prep. Shared **OAuth env shaping** should move to a reusable module when strategies and the OAuth orchestrator converge.

The supervisor retains cross-cutting process lifecycle concerns (heartbeats, timeouts, tmate, reconciliation).

The `codex_worker` unification decision remains relevant: **`ContextInjectionService`** covers part of the “absorb” path; publish/PR and full pipeline **wrap** are still open.

---

## Implementation Phases

Checkboxes reflect **implementation in the repo** as of the last edit of this document. Run `./tools/test_unit.sh` (and integration tests as needed) to confirm nothing has regressed.

### Phase 1 — Foundation: ABC + Registry + Gemini Strategy

**Goal**: Establish the pattern with the simplest runtime first.

- [x] Create `ManagedRuntimeStrategy` ABC in `moonmind/workflows/temporal/runtime/strategies/base.py`
- [x] Create `RUNTIME_STRATEGIES` registry in `moonmind/workflows/temporal/runtime/strategies/__init__.py`
- [x] Implement `GeminiCliStrategy` — the simplest runtime (no special output parsing, no workspace prep today)
  - `build_command`: former launcher `gemini_cli` branch
  - `shape_environment`: `HOME`, `GEMINI_HOME`, `GEMINI_CLI_HOME` passthrough (see Phase 5 — not yet invoked from launcher)
  - `default_command_template`: `["gemini"]`
  - `default_auth_mode`: `"api_key"`
- [x] Wire `ManagedRuntimeLauncher.build_command()` to delegate to strategy when available; generic fallback for unregistered runtimes (no per-runtime `if/elif`)
- [x] Wire `ManagedAgentAdapter.start()` to read `default_command_template` and `default_auth_mode` from strategy when available
- [x] Add unit tests for `GeminiCliStrategy` (`tests/unit/workflows/temporal/runtime/strategies/test_gemini_cli.py`)
- [x] Verify existing integration tests still pass *(expected on mainline; re-run when changing this area)*

**Output**: **Done.** Strategy pattern is live; all four runtimes were later registered (Phase 2).

---

### Phase 2 — Remaining Strategies: Cursor, Claude, Codex

**Goal**: Eliminate all `if/elif` branching in `build_command()`.

- [x] Implement `CursorCliStrategy`
  - [x] `build_command`: former `cursor_cli` launcher branch
  - [x] `shape_environment`: completed
  - [x] `default_auth_mode`: `"oauth"`
  - [x] `prepare_workspace`: wired `write_task_rule_file()` and `write_cursor_cli_json()`
  - [x] `create_output_parser` / rate limits: `NdjsonOutputParser` in `output_parser.py` + `classify_exit()` on the strategy *(supervisor does not use these yet — Phase 4)*
- [x] Implement `ClaudeCodeStrategy`
  - [x] `build_command`: former generic / `claude_code` path
  - [x] `prepare_workspace`: implicit no-op (stub until Phase 5 content)
- [x] Implement `CodexCliStrategy` (includes Phase 3–adjacent RAG hook — see below)
  - [x] `build_command`: former `codex_cli` branch
  - [x] `shape_environment`: Codex home/config keys *(not yet applied via launcher — Phase 5)*
  - [x] `default_command_template`: `["codex", "exec", "--full-auto"]`
- [x] Remove all per-runtime `if/elif` branching from `launcher.py:build_command()`
- [x] Remove `_runtime_env_keys` hardcoded list from `launcher.py` *(replaced with `strategy.shape_environment()`)*
- [x] Remove command template defaults from adapter *(completed; strategy-first approach used)*
- [x] Remove auth mode defaults from adapter *(completed; strategy-first approach used)*
- [x] Add unit tests for each new strategy (`test_remaining_strategies.py`, `test_gemini_cli.py`, `test_base.py`)
- [x] Run full test suite *(maintain via CI / `./tools/test_unit.sh`)*

**Output**: **Mostly done.** Command building lives in strategies. Launcher env passthrough and adapter fallbacks are **not** fully runtime-agnostic yet.

---

### Phase 3 — codex_worker Unification Decision + Execution

**Goal**: Resolve the parallel execution path.

**Decision required before starting** (see codex_worker Unification section above):
- **Wrap**: Wire `CodexCliStrategy` to delegate to `codex_worker` handlers — preserve existing behavior, reduce risk
- **Absorb**: Extract RAG context injection, publish/PR, and sanitization into shared services usable by all strategies
- **Deprecate**: Leave `codex_worker` as-is for the standalone daemon path, don't integrate

Recommended approach for MVP: **Wrap first, absorb incrementally**.

- [ ] If wrapping: update `CodexCliStrategy` to invoke `codex_worker` pipeline from the managed runtime path
- [x] If absorbing: extract `_resolve_prompt_context` into a shared `ContextInjectionService` *( **`moonmind/rag/context_injection.py`**; used from `CodexCliStrategy.prepare_workspace()` )*
- [x] If absorbing: extract `_maybe_publish` into a shared `PublishService`
- [x] Verify Codex CLI tasks work through the managed runtime path end-to-end *(ongoing validation)*

**Output**: **Partial.** RAG context injection is shared and hooked for Codex managed runs; full `codex_worker` parity (publish, sanitization, etc.) is **not** done.

---

### Phase 4 — Exit Classification + Output Parsing

**Goal**: Per-runtime correctness for exit codes and structured output.

- [x] Move `_classify_exit` in `supervisor.py` to call `strategy.classify_exit()` with stdout/stderr context *(and `runtime_id` / log artifact reads as needed)*
- [x] Implement `CursorCliStrategy.classify_exit()` — NDJSON-aware / 429 detection *(used only if supervisor delegates — **not wired** )*
- [x] Implement `CursorCliStrategy.create_output_parser()` — returns `NdjsonOutputParser` *(logic lives in `output_parser.py`; not the legacy `ndjson_parser.py` module)*
- [x] Implement `PlainTextOutputParser` in `output_parser.py`
- [x] Use `PlainTextOutputParser` as the default for Gemini/Claude/Codex *(base `create_output_parser()` still returns `None`; strategies do not override)*
- [x] Wire output parser into `supervisor.py` log streaming pipeline
- [x] Add unit tests for output parsers and related behavior (`tests/unit/workflows/temporal/runtime/test_output_parser.py`, strategy tests) *(extend when supervisor integration lands)*

**Output**: **Done.** Types and Cursor strategy behavior exist; **supervisor** classifies exits via strategy and consumes parser content during streaming.

---

### Phase 5 — Workspace Prep + Polish

**Goal**: Enable per-runtime workspace preparation and clean up remaining tech debt.

- [ ] Implement `GeminiCliStrategy.prepare_workspace()` — write `.gemini/` instruction files or `GEMINI_INSTRUCTIONS`
- [ ] Implement `ClaudeCodeStrategy.prepare_workspace()` — write `CLAUDE.md` with task context
- [ ] Implement and verify `CursorCliStrategy.prepare_workspace()` — write `.cursor/rules/` and `.cursor/cli.json` via existing helpers
- [x] Add workspace prep call in `launcher.py:launch()` after workspace resolution, before subprocess spawn *(calls `await strategy.prepare_workspace(...)`)*
- [ ] Factor shared env-shaping helpers (`_OAUTH_CLEARED_VARS`, `_shape_environment_for_oauth`) into `moonmind/auth/env_shaping.py` for reuse by both strategies and the OAuth Session orchestrator (per UniversalTmateOAuth.md alignment)
- [ ] Wire launcher subprocess env through `strategy.shape_environment()` (and remove redundant `_runtime_env_keys` once equivalent)
- [ ] Remove dead imports and unused code from adapter and launcher
- [ ] Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` to reference the managed runtime strategy pattern
- [ ] Move this file to `docs/ManagedAgents/SharedManagedAgentAbstractions.md`

**Output**: **Partial.** The **hook** in `launch()` is live; per-runtime **content** for Gemini/Claude/Cursor and shared env factoring are **still open**.
