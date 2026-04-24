# Verification Notes: MM-506

## Red-First Evidence

Before production changes, the focused unit lane failed on the intended MM-506 gaps:
- `tests/unit/api/routers/test_retrieval_gateway.py::test_context_rejects_unsupported_budget_keys_for_authorized_request`
- `tests/unit/agents/codex_worker/test_handlers.py::test_handler_appends_retrieval_capability_note_when_rag_available`
- `tests/unit/agents/codex_worker/test_handlers.py::test_handler_appends_retrieval_unavailable_reason_when_rag_disabled`
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py::test_agent_runtime_prepare_turn_instructions_adds_retrieval_capability_hint`
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py::test_agent_runtime_prepare_turn_instructions_reports_disabled_retrieval_reason`

Those failures proved the missing behavior before implementation:
- retrieval gateway accepted unsupported budget keys
- direct Codex worker prompts lacked the managed retrieval capability signal
- Temporal managed-runtime turn preparation lacked the managed retrieval capability signal

## Implemented Behavior

- Added strict retrieval budget-key validation in `api_service/api/routers/retrieval_gateway.py`.
- Added a shared managed retrieval capability note in `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`.
- Applied that note on the direct Codex worker path in `moonmind/agents/codex_worker/handlers.py`.
- Preserved runtime-neutral wording and explicit disabled reasons through the shared note builder.

## Passing Evidence

### Unit

Command:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_retrieval_gateway.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py
```

Result:
- Python lane: `199 passed`
- UI lane triggered by the repo runner: `14 passed` test files, `418 passed` tests

### Integration / Workflow Boundary

Command:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short
```

Result:
- `2 passed`

## Traceability

Traceability validation command:

```bash
rg -n "MM-506" specs/254-managed-session-followup-retrieval
```

Result:
- `MM-506` remains present in the active spec, plan, research, data model, quickstart, contract, tasks, and this verification note.

## Remaining Work

- `/moonspec-verify` has not been run in this turn.
- Some optional task-list items remain unchecked because the current implementation did not require those exact follow-on artifact refreshes or extra coverage expansions.
