# Shared Managed Agent Abstractions Review

## Current State

We currently have Managed Agents support for four runtimes: Gemini CLI, Codex CLI, Cursor CLI, and Claude Code. They are in various stages of implementation.

Based on an analysis of the codebase, here's how they are currently integrated:

1.  **Workflow Adapters:**
    *   There is a `ManagedAgentAdapter` (`moonmind/workflows/adapters/managed_agent_adapter.py`) which provides a common execution interface. It handles auth profile resolution, environment shaping (`_shape_environment_for_oauth`, `_shape_environment_for_api_key`), slot leasing, and launcher triggering.
    *   There are also `JulesAgentAdapter` and `CodexCloudAgentAdapter` which subclass `BaseExternalAgentAdapter`.
2.  **Runtime Launchers:**
    *   The `ManagedRuntimeLauncher` (`moonmind/workflows/temporal/runtime/launcher.py`) contains a `build_command` method that has a large `if/elif` block checking the `profile.runtime_id` (e.g., `"codex_cli"`, `"gemini_cli"`, `"cursor_cli"`, generic path for `"claude_code"`) to construct the specific CLI arguments.
3.  **Environment Handling:**
    *   In `ManagedRuntimeLauncher.launch()`, there is a hardcoded list of `_runtime_env_keys` (`"HOME"`, `"GEMINI_HOME"`, `"GEMINI_CLI_HOME"`, `"CODEX_HOME"`, `"CODEX_CONFIG_HOME"`, `"CODEX_CONFIG_PATH"`) that are explicitly passed through to the subprocess if present in the worker's environment.
4.  **CLI specific logic (Codex):**
    *   There is a whole package for `codex_worker` (`moonmind/agents/codex_worker/`) which seems to handle Codex specific logic (metrics, self-healing, sanitization, secret refs, handlers). This suggests Codex might be doing things differently or have a more robust legacy implementation compared to the simple launcher approach.
5.  **CLI specific logic (Cursor):**
    *   `moonmind/agents/base/cursor_rules.py` and `moonmind/agents/base/cursor_config.py` deal with generating `.cursor/rules/` and `.cursor/cli.json` files.

## Identified Opportunities for Abstraction

The current implementation in `ManagedRuntimeLauncher` and `ManagedAgentAdapter` is branching on `runtime_id`. This violates the Open/Closed Principle as adding a new runtime requires modifying these core classes.

We can introduce a `ManagedRuntimeStrategy` interface (or abstract base class) to encapsulate the CLI-specific logic.

### 1. Command Building Strategy

Instead of the `if/elif` block in `ManagedRuntimeLauncher.build_command`:

```python
class ManagedRuntimeStrategy(ABC):
    @abstractmethod
    def build_command(
        self,
        profile: ManagedRuntimeProfile,
        request: AgentExecutionRequest,
    ) -> list[str]:
        pass
```

We would implement concrete classes like `GeminiCliStrategy`, `CodexCliStrategy`, `CursorCliStrategy`, and `ClaudeCodeStrategy`.

### 2. Environment Variable Management

The hardcoded list of runtime env keys in `launcher.py` and the `_shape_environment_for_oauth` logic in `managed_agent_adapter.py` can be pushed into the strategy or a configuration registry.

```python
class ManagedRuntimeStrategy(ABC):
    # ...
    @property
    @abstractmethod
    def required_env_keys(self) -> tuple[str, ...]:
        """e.g., return ('GEMINI_HOME', 'GEMINI_CLI_HOME')"""
        pass
```

### 3. Output/Stream Parsing

Right now, different CLIs output differently (e.g., Cursor CLI is currently invoked with `--output-format stream-json`; Claude Code supports `--output-format json` (not currently enforced by the launcher unless included in the runtime profile's `command_template`)). The interpretation of success/failure (Exit Code Mapping) and the parsing of streams to MoonMind logs should be standardized. A strategy could define how to parse the CLI's standard output/error into canonical MoonMind events.

```python
class ManagedRuntimeStrategy(ABC):
    # ...
    @abstractmethod
    def parse_output(self, stdout: str, stderr: str, exit_code: int) -> tuple[FailureClass | None, str]:
        """Returns FailureClass (None if success) and a summary string."""
        pass
```

### 4. Pre-launch Workspace Setup

Cursor needs `.cursor/cli.json` and `.cursor/rules/moonmind-task.mdc` setup before launch. Codex might need its own setup. The strategy could have a hook for this.

```python
class ManagedRuntimeStrategy(ABC):
    # ...
    async def prepare_workspace(self, workspace_path: Path, request: AgentExecutionRequest) -> None:
        """Hook to write .cursor/rules, .codex_config, etc."""
        pass
```

### 5. Runtime Registry

Instead of hardcoding `if runtime_id == ...`, we should have a registry where strategies are registered.

```python
RUNTIME_STRATEGIES: dict[str, ManagedRuntimeStrategy] = {
    "gemini_cli": GeminiCliStrategy(),
    "codex_cli": CodexCliStrategy(),
    "cursor_cli": CursorCliStrategy(),
    "claude_code": ClaudeCodeStrategy(),
}
```

The launcher would simply look up the strategy:

```python
strategy = RUNTIME_STRATEGIES.get(profile.runtime_id)
if strategy is None:
    raise ValueError(f"Unsupported runtime: '{profile.runtime_id}'")
cmd = strategy.build_command(profile, request)
```

## Summary

To reduce bespoke implementation per Managed Agent, we should transition from branching logic in the core adapters and launchers to a Strategy Pattern-based Registry. This will encapsulate command building, environment requirements, workspace preparation, and output parsing for each CLI into isolated, testable strategy classes.
