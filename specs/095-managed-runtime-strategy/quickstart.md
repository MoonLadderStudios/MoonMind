# Quickstart: 095-managed-runtime-strategy

## Validate Changes

```bash
# Run all unit tests
./tools/test_unit.sh

# Run only the strategy-specific tests (via test_unit.sh filter)
./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/
```

## Verify Strategy Registration

```python
from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

# Should contain "gemini_cli"
assert "gemini_cli" in RUNTIME_STRATEGIES

strategy = RUNTIME_STRATEGIES["gemini_cli"]
print(f"runtime_id: {strategy.runtime_id}")
print(f"default_command_template: {strategy.default_command_template}")
print(f"default_auth_mode: {strategy.default_auth_mode}")
```

## Verify Launcher Delegation

```python
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

store = ManagedRunStore("/tmp/test_store")
launcher = ManagedRuntimeLauncher(store)

profile = ManagedRuntimeProfile(
    profile_id="test",
    runtime_id="gemini_cli",
    auth_mode="api_key",
    command_template=["gemini"],
)
request = AgentExecutionRequest(
    agent_kind="managed",
    agent_id="gemini_cli",
    execution_profile_ref="auto",
    instruction_ref="Fix the bug",
)

cmd = launcher.build_command(profile, request)
print(f"Command: {cmd}")
# Expected: ['gemini', '--model', ..., '--yolo', '--prompt', 'Fix the bug']
```
