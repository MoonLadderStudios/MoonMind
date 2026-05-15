# Implementation Notes: Runtime Command Rendering After Context Preparation

Jira issue: MM-686

This implementation preserves the original Jira preset brief in `spec.md` and
adds the managed-runtime render boundary described by the MoonSpec artifacts.

Original Jira preset brief summary: MM-686 requires managed runtime adapters to
render runtime slash commands after MoonMind prepares retrieval context, skill
activation summaries, and managed runtime notes, while keeping supported
commands first in the runtime-visible input.

## TDD Evidence

- Red-first unit command:
  `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/tasks/test_task_contract.py`
- Red-first unit result:
  failed before production changes because `RuntimeCommandInvocation` and
  `RuntimeCommandRenderResult` were not available.
- Red-first integration command:
  `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short`
- Red-first integration result:
  failed before production changes because runtime command render metadata was
  not available to the launcher path.

## Passing Evidence

- Targeted unit command:
  `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py`
- Targeted unit result:
  `281 passed, 2 subtests passed`; frontend runner also passed with
  `21 passed` test files and `351 passed | 229 skipped` tests.
- Targeted integration command:
  `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short`
- Targeted integration result:
  `8 passed`.
- Full unit command:
  `./tools/test_unit.sh`
- Full unit result:
  Python `5087 passed, 1 xpassed, 120 warnings, 16 subtests passed`;
  frontend `21 passed` test files and `351 passed | 229 skipped` tests.

## Integration Blocker

- Full hermetic integration command:
  `./tools/test_integration.sh`
- Result:
  blocked by the local Docker environment. The runner attempted to use Docker
  Compose and the daemon returned `403 Forbidden` with the message
  `Request forbidden by administrative rules`.

## Scope Notes

- The story remains limited to final managed-runtime command rendering after
  context preparation.
- Unknown opaque slash commands are passed through for slash-capable runtimes
  and are not materialized.
- Escaped slash literals render through a non-command literal wrapper.
- Unsupported runtimes receive an audited literal fallback event.
- Render diagnostics are redacted before being stored in runtime metadata.
