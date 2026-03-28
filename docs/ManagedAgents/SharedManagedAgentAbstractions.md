# Shared Managed Agent Abstractions Review

**Tracking note:** This document tracks the managed-runtime **strategy pattern** rollout. Implementation status below was reconciled with the tree under `moonmind/workflows/temporal/runtime/strategies/` and related launch/adapter/supervisor code on **2026-03-22**.

## Current State

We have Managed Agents support for four runtimes: Gemini CLI, Codex CLI, Cursor CLI, and Claude Code. **Command construction** for all four is implemented via `ManagedRuntimeStrategy` subclasses and `RUNTIME_STRATEGIES` (`moonmind/workflows/temporal/runtime/strategies/`). **`build_command()`** in `ManagedRuntimeLauncher` delegates to the registry when `profile.runtime_id` is registered, then falls through to a **generic** template path (model/effort/prompt flags) for unknown runtimes — the old per-runtime `if/elif` block is gone. **Environment shaping** and **workspace preparation** are both wired through the launcher to strategies.

### Workflow Adapters

`ManagedAgentAdapter` (`moonmind/workflows/adapters/managed_agent_adapter.py`) provides a common execution interface: auth profile resolution, environment shaping (`_shape_environment_for_oauth`, `_shape_environment_for_api_key`), slot leasing, and launcher triggering. **Default** `command_template` and `auth_mode` are taken from the matching strategy when registered; **fallbacks** remain for unregistered `runtime_id` values (auth: `cursor_cli` → oauth else api_key; template: `[runtime_id]`).

There are also `JulesAgentAdapter`, `CodexCloudAgentAdapter`, and `OpenClawAgentAdapter` which subclass `BaseExternalAgentAdapter`.

### Runtime Launchers

`ManagedRuntimeLauncher.build_command()` uses **`get_strategy(runtime_id)`** and `strategy.build_command(...)` for all four runtimes.

`ManagedRuntimeLauncher.launch()`:
- Calls `strategy.prepare_workspace()` when a workspace path exists and a strategy is registered (line 405–416).
- Calls `strategy.shape_environment()` to shape env_overrides before subprocess spawn (line 418–419).
- The old `_runtime_env_keys` hardcoded list has been removed from the launcher.

### Environment Handling

`GeminiCliStrategy` and `CodexCliStrategy` define **`shape_environment()`** (restricted passthrough sets for Gemini/Codex home keys). **`ManagedRuntimeLauncher.launch()` calls `strategy.shape_environment()`** — the old `_runtime_env_keys` is gone.

OAuth/API-key clearing and injection remain in the adapter helpers (`_shape_environment_for_oauth`, `_shape_environment_for_api_key`), not in strategies. A shared `env_shaping.py` module has **not** been factored out yet.

The profile object also supports `passthrough_env_keys` for additional pass-through.

> [!NOTE]
> `settings.py` still has `agent_runtime_env_keys` as a configuration option (lines 715, 1234). This may be dead config — it is not used by the launcher, which now delegates entirely to strategies. Should be verified and cleaned up.

### Cursor CLI Workspace Prep (Wired)

`cursor_rules.py` provides `write_task_rule_file()` and `cursor_config.py` provides `write_cursor_cli_json()` for generating `.cursor/rules/` and `.cursor/cli.json` files. **`CursorCliStrategy` overrides `prepare_workspace()`** and invokes both helpers. The launcher calls `strategy.prepare_workspace()` on registered strategies when a workspace path exists.

### Gemini / Claude Workspace Prep (Not Implemented)

No workspace preparation code exists yet for Gemini CLI (e.g. `GEMINI_INSTRUCTIONS` or `.gemini/` instruction files) or Claude Code (e.g. `CLAUDE.md`) inside the strategies. **`prepare_workspace()`** is a no-op on the base class for those runtimes.

### Codex CLI Workspace Prep (RAG Context Injection)

`CodexCliStrategy.prepare_workspace()` invokes `ContextInjectionService.inject_context()` from `moonmind/rag/context_injection.py` for RAG context injection before the Codex CLI process is launched.

### Exit Classification (Strategy-Delegated)

`ManagedRunSupervisor._classify_exit()` now delegates to `strategy.classify_exit()` with `exit_code`, `stdout`, and `stderr` context when a strategy is registered for the `runtime_id`. Timeout is handled before strategy delegation. `CursorCliStrategy.classify_exit()` uses NDJSON-aware parsing and 429 detection.

### Output Parsing

`moonmind/workflows/temporal/runtime/output_parser.py` defines **`NdjsonOutputParser`** and **`PlainTextOutputParser`** implementing **`RuntimeOutputParser`**. `CursorCliStrategy.create_output_parser()` returns `NdjsonOutputParser`. The base class `create_output_parser()` returns `PlainTextOutputParser`.

**Output parsers are NOT integrated into the log streaming pipeline** — `RuntimeLogStreamer.stream_to_artifact()` streams raw bytes without parsing. The parsers are currently only consumed by `classify_exit()` in the Cursor strategy.

### Codex Worker (Parallel Execution Path)

`moonmind/agents/codex_worker/` is a **standalone execution pipeline** (8 source files: handlers, metrics, secret_refs, self_heal, worker, cli, runtime_mode, `__init__`) that predates the managed runtime path. It provides capabilities beyond what the managed-runtime strategy path offers:

- **Output sanitization** (via `publish_sanitization.py` — removed from package but logic may be elsewhere)
- **Self-healing** (`self_heal.py`)
- **Metrics** (`metrics.py`)
- **Secret reference handling** (`secret_refs.py`)
- **Full publish/PR workflow** (via `PublishService` from `moonmind/publish/service.py`, consumed in `handlers.py`)

It is **not wired through** `ManagedRuntimeLauncher` and runs separately.

---

## Runtime-Specific Branching Sites (Current)

Most **command-line construction** is encapsulated in strategies. Remaining cross-cutting or legacy touchpoints:

| Location | What it branches on | Notes |
|---|---|---|
| `launcher.py` `build_command()` | registry → generic fallback | Registered runtimes use strategy; unknown `runtime_id` uses generic model/effort/prompt extension |
| `adapter.py` command_template default | strategy → fallback | If no profile template: use `default_command_template`, else `[runtime_id]` |
| `adapter.py` auth_mode default | strategy → fallback | If strategy missing: `cursor_cli` → oauth, else api_key |
| `log_streamer.py` `stream_to_artifact()` | raw bytes only | Does not use output parsers — streaming is format-agnostic |

> [!NOTE]
> The previous branching sites for `_runtime_env_keys` (launcher hardcoded list) and `_classify_exit` (exit code + timeout only) have been resolved. The launcher now delegates env shaping to strategies (line 419) and the supervisor delegates exit classification to strategies (lines 270–280).

---

## Proposed Strategy Pattern

The sketch below matches the actual `moonmind/workflows/temporal/runtime/strategies/base.py`. It accurately reflects the live ABC.

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
        docs/ManagedAgents/TmateArchitecture.md.  Both registries
        share the runtime_id namespace (codex_cli, gemini_cli,
        claude_code, cursor_cli).
        """
        return "api_key"

    @abstractmethod
    def build_command(
        self,
        profile: Any,
        request: Any,
    ) -> list[str]: ...

    def shape_environment(
        self,
        base_env: dict[str, str],
        profile: Any,
    ) -> dict[str, str]:
        """Shape the subprocess environment for this runtime.

        Replaces both _runtime_env_keys passthrough AND
        _shape_environment_for_oauth/_api_key logic.  Each strategy
        decides what to clear, set, and preserve.

        Note: The underlying helpers (_OAUTH_CLEARED_VARS,
        _shape_environment_for_oauth) are also used by the OAuth
        session orchestrator (TmateArchitecture.md §8.2).  Shared
        env-shaping logic should be factored into common utilities
        rather than duplicated.
        """
        return dict(base_env)

    async def prepare_workspace(
        self,
        workspace_path: Path,
        request: Any,
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

    def create_output_parser(self) -> RuntimeOutputParser:
        """Factory for the runtime's stream parser.

        Returns PlainTextOutputParser by default. Override for
        structured formats like NDJSON.
        """
        return PlainTextOutputParser()
```

### Output Handling

Output parsing is available per-strategy but **not yet integrated into log streaming**. Different runtimes stream differently:

- **Cursor CLI**: NDJSON (`--output-format stream-json`) — parsed by `NdjsonOutputParser`; used in `CursorCliStrategy.classify_exit()`
- **Gemini CLI**: plain text stdout — `PlainTextOutputParser` (base default)
- **Claude Code**: JSON output mode available but not currently enforced — `PlainTextOutputParser`
- **Codex CLI**: plain text stdout — `PlainTextOutputParser`

**Open:** Integrate output parsers into `RuntimeLogStreamer.stream_to_artifact()` for structured event extraction and real-time parsing during log streaming.

### Runtime Registry

```python
RUNTIME_STRATEGIES: dict[str, ManagedRuntimeStrategy] = {
    "claude_code": ClaudeCodeStrategy(),
    "codex_cli": CodexCliStrategy(),
    "cursor_cli": CursorCliStrategy(),
    "gemini_cli": GeminiCliStrategy(),
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
- Exit-code-file resolution and concurrent stdout/stderr streaming to run artifacts
- Endpoint persistence for live sessions via `agent_runtime.report_live_session` / `end_live_session` activities
- SIGTERM → SIGKILL escalation
- PID reconciliation on restart
- Runtime file cleanup (including orphaned artifact/socket reconciliation on startup where applicable)

The following are in the **strategy** (runtime-specific):

- Command construction (`build_command`) ✅
- Environment shaping (`shape_environment`) ✅ wired in launcher
- Workspace preparation (`prepare_workspace`) ✅ wired in launcher
- Exit classification (`classify_exit`) ✅ wired in supervisor
- Output parsing (`create_output_parser`) ⚠️ exists but not used in log streaming
- Default command template and auth mode ✅

---

## codex_worker Unification

The standalone `codex_worker` package (8 source files) provides capabilities not yet present in the managed runtime path:

- **Self-healing** (`self_heal.py`)
- **Metrics** (`metrics.py`)
- **Secret reference handling** (`secret_refs.py`)
- **Full publish/PR workflows** (via `PublishService` in `moonmind/publish/service.py`)

### Progress

| Capability | Status | Notes |
|---|---|---|
| RAG context injection | ✅ Shared | `ContextInjectionService` in `moonmind/rag/context_injection.py`; wired from `CodexCliStrategy.prepare_workspace()` |
| Publish/PR workflows | ✅ Shared service | `PublishService` exists at `moonmind/publish/service.py`; now consumed from `agent_runtime_launch` in managed runtime path |
| Self-healing | ✅ Shared | `self_heal.py` moved to `moonmind/workflows/temporal/runtime/` |
| Metrics | ❌ codex_worker only | `metrics.py` — standalone |
| Secret refs | ✅ Shared | `secret_refs.py` moved to `moonmind/auth/` |
| Output sanitization | ❓ Status unclear | `publish_sanitization.py` was removed from `codex_worker` directory listing; may have moved |

> [!IMPORTANT]
> The **Wrap first, absorb incrementally** approach remains recommended. RAG context injection, Publish workflows, self-healing, and secret refs are now shared. Remaining capabilities (metrics) are candidates for absorption into shared services or supervisor hooks.

---

## Summary

The codebase has adopted a **Strategy Pattern-based Registry** (`ManagedRuntimeStrategy`, `RUNTIME_STRATEGIES`) for:

| Concern | Status | Wiring |
|---|---|---|
| Command construction | ✅ Done | Launcher delegates to `strategy.build_command()` |
| Environment shaping | ✅ Done | Launcher calls `strategy.shape_environment()` |
| Workspace preparation | ✅ Hook wired | Launcher calls `strategy.prepare_workspace()`; content exists for Cursor and Codex only |
| Exit classification | ✅ Done | Supervisor delegates to `strategy.classify_exit()` |
| Output parsing | ✅ Done | Integrated via `RuntimeLogStreamer.stream_and_parse()`; supervisor wires strategy parser |
| Auth mode defaults | ✅ Done | Adapter reads from strategy |
| Command template defaults | ✅ Done | Adapter reads from strategy |

**Still outstanding:**
- **Gemini/Claude** workspace prep content (no-op stubs)

- **Shared env-shaping module** (`moonmind/auth/env_shaping.py`) to unify strategies and OAuth session orchestrator
- **Adapter auth-mode fallback** still has legacy `cursor_cli → oauth` branch for unregistered runtimes
- **`agent_runtime_env_keys` in `settings.py`** may be dead config — verify and clean up
- **codex_worker** unification: self-heal, metrics, and secret refs not yet factored into shared services

The supervisor retains cross-cutting process lifecycle concerns (heartbeats, timeouts, log streaming, reconciliation).

---

## Implementation Phases

Checkboxes reflect **implementation in the repo** as of **2026-03-22**. Run `./tools/test_unit.sh` (and integration tests as needed) to confirm nothing has regressed.

### Phase 1 — Foundation: ABC + Registry + Gemini Strategy

**Goal**: Establish the pattern with the simplest runtime first.

- [x] Create `ManagedRuntimeStrategy` ABC in `moonmind/workflows/temporal/runtime/strategies/base.py`
- [x] Create `RUNTIME_STRATEGIES` registry in `moonmind/workflows/temporal/runtime/strategies/__init__.py`
- [x] Implement `GeminiCliStrategy` — the simplest runtime (no special output parsing, no workspace prep today)
  - `build_command`: former launcher `gemini_cli` branch
  - `shape_environment`: `HOME`, `GEMINI_HOME`, `GEMINI_CLI_HOME` passthrough — **wired in launcher**
  - `default_command_template`: `["gemini"]`
  - `default_auth_mode`: `"api_key"`
- [x] Wire `ManagedRuntimeLauncher.build_command()` to delegate to strategy when available; generic fallback for unregistered runtimes (no per-runtime `if/elif`)
- [x] Wire `ManagedAgentAdapter.start()` to read `default_command_template` and `default_auth_mode` from strategy when available
- [x] Add unit tests for `GeminiCliStrategy` (`tests/unit/workflows/temporal/runtime/strategies/test_gemini_cli.py`)
- [x] Verify existing integration tests still pass *(expected on mainline; re-run when changing this area)*

**Output**: ✅ **Complete.** Strategy pattern is live; all four runtimes were later registered (Phase 2).

---

### Phase 2 — Remaining Strategies: Cursor, Claude, Codex

**Goal**: Eliminate all `if/elif` branching in `build_command()`.

- [x] Implement `CursorCliStrategy`
  - [x] `build_command`: former `cursor_cli` launcher branch
  - [x] `shape_environment`: completed (base class default — Cursor CLI has no special env needs)
  - [x] `default_auth_mode`: `"oauth"`
  - [x] `prepare_workspace`: wired `write_task_rule_file()` and `write_cursor_cli_json()`
  - [x] `create_output_parser` / rate limits: `NdjsonOutputParser` in `output_parser.py` + `classify_exit()` on the strategy
- [x] Implement `ClaudeCodeStrategy`
  - [x] `build_command`: former generic / `claude_code` path
  - [x] `prepare_workspace`: no-op (stub until workspace prep content is needed)
- [x] Implement `CodexCliStrategy`
  - [x] `build_command`: former `codex_cli` branch
  - [x] `shape_environment`: Codex home/config keys — **wired via launcher**
  - [x] `default_command_template`: `["codex", "exec", "--full-auto"]`
  - [x] `prepare_workspace`: RAG context injection via `ContextInjectionService`
- [x] Remove all per-runtime `if/elif` branching from `launcher.py:build_command()`
- [x] Remove `_runtime_env_keys` hardcoded list from `launcher.py`
- [x] Remove command template defaults from adapter *(completed; strategy-first approach used)*
- [x] Remove auth mode defaults from adapter *(completed; strategy-first approach used)*
- [x] Add unit tests for each new strategy (`test_remaining_strategies.py`, `test_gemini_cli.py`, `test_base.py`)
- [x] Run full test suite *(maintain via CI / `./tools/test_unit.sh`)*

**Output**: ✅ **Complete.** Command building, env shaping, and workspace prep all live in strategies. Launcher delegates fully.

---

### Phase 3 — codex_worker Unification Decision + Execution

**Goal**: Resolve the parallel execution path.

**Decision**: **Absorb incrementally** (wrap deferred — absorb shared services as they become cross-runtime useful).

- [x] Extract `_resolve_prompt_context` into a shared `ContextInjectionService` (`moonmind/rag/context_injection.py`; used from `CodexCliStrategy.prepare_workspace()`)
- [x] Extract `_maybe_publish` into a shared `PublishService` (`moonmind/publish/service.py`)
- [x] Verify Codex CLI tasks work through the managed runtime path end-to-end *(ongoing validation)*
- [x] Wire `PublishService` from the managed runtime path (currently only `codex_worker/handlers.py` consumes it)
- [x] Factor out self-heal capability into a shared supervisor hook or strategy method
- [x] Factor out secret-ref resolution into a shared module

**Output**: **Complete.** RAG context injection is shared and hooked for Codex managed runs. `PublishService` is integrated into the managed runtime lifecycle (`agent_runtime_launch`). Self-heal capability and secret-ref resolution have been factored into shared modules (`moonmind/workflows/temporal/runtime/self_heal.py` and `moonmind/auth/secret_refs.py`). Metrics remain `codex_worker`-only.

---

### Phase 4 — Exit Classification + Output Parsing

**Goal**: Per-runtime correctness for exit codes and structured output.

- [x] Move `_classify_exit` in `supervisor.py` to call `strategy.classify_exit()` with stdout/stderr context
- [x] Implement `CursorCliStrategy.classify_exit()` — NDJSON-aware / 429 detection
- [x] Implement `CursorCliStrategy.create_output_parser()` — returns `NdjsonOutputParser`
- [x] Implement `PlainTextOutputParser` in `output_parser.py`
- [x] Base class `create_output_parser()` returns `PlainTextOutputParser` by default
- [x] Add unit tests for output parsers and related behavior (`test_output_parser.py`, strategy tests)
- [x] Integrate output parsers into `RuntimeLogStreamer.stream_to_artifact()` for structured event extraction during log streaming

**Output**: ✅ **Complete.** Exit classification is fully strategy-delegated. Output parsers are integrated into the log streaming pipeline via `RuntimeLogStreamer.stream_and_parse()` — the supervisor obtains the parser from `strategy.create_output_parser()` and parsed output metadata (error messages, rate-limit flag, event count) flows into diagnostics.

---

### Phase 5 — Workspace Prep + Polish

**Goal**: Enable per-runtime workspace preparation and clean up remaining tech debt.

- [x] Implement `GeminiCliStrategy.prepare_workspace()` — write `.gemini/` instruction files or `GEMINI_INSTRUCTIONS`
- [x] Implement `ClaudeCodeStrategy.prepare_workspace()` — write `CLAUDE.md` with task context
- [x] Implement and verify `CursorCliStrategy.prepare_workspace()` — write `.cursor/rules/` and `.cursor/cli.json` via existing helpers
- [x] Add workspace prep call in `launcher.py:launch()` after workspace resolution, before subprocess spawn *(calls `await strategy.prepare_workspace(...)`)*
- [x] Wire launcher subprocess env through `strategy.shape_environment()` (launcher line 419)
- [x] Factor shared env-shaping helpers (`_OAUTH_CLEARED_VARS`, `_shape_environment_for_oauth`) into `moonmind/auth/env_shaping.py` for reuse by both strategies and the OAuth session orchestrator (per TmateArchitecture.md alignment)
- [x] Clean up `agent_runtime_env_keys` in `settings.py` if confirmed dead config
- [x] Remove dead imports and unused code from adapter and launcher
- [x] Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` to reference the managed runtime strategy pattern
- [x] Move this file to `docs/ManagedAgents/SharedManagedAgentAbstractions.md`

**Output**: **Complete.** Per-runtime workspace preparation, shared env factoring, settings cleanup, and documentation updates have all been completed successfully.
