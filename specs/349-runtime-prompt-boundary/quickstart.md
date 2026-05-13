# Quickstart: Runtime Prompt Boundary

## Scope

Validate MM-650 against `specs/349-runtime-prompt-boundary/spec.md`, preserving `DESIGN-REQ-026` from `docs/Tasks/TaskArchitecture.md` section 10.

## Unit Test Strategy

Run focused unit tests first while developing:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/agents/codex_worker/test_worker.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py
```

Add or update unit tests to cover:
- multimodal/external adapter raw artifact refs from prepared context
- adapter rejection or prevention of non-canonical target broadening
- selected-runtime diagnostics when required generated context or raw refs are missing
- preservation of objective and current-step target binding

Before finalizing implementation, run the full unit suite:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

Run focused workflow-boundary tests during iteration:

```bash
pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q
```

Add or update hermetic `integration_ci` workflow-boundary coverage to prove:
- text-first runtime preparation receives generated context via `INPUT ATTACHMENTS`
- multimodal/external runtime preparation receives raw artifact refs without canonical task contract changes
- the same canonical task payload preserves objective and step target binding across runtime selection
- invalid target mappings produce explicit diagnostics

Before finalizing implementation, run required hermetic integration tests:

```bash
./tools/test_integration.sh
```

## End-to-End Verification

1. Build a canonical task payload with one objective image attachment and one current-step image attachment.
2. Prepare the payload for a text-first runtime and verify `INPUT ATTACHMENTS` includes generated context for objective and current-step attachments only.
3. Prepare the same payload for a multimodal/external runtime and verify raw artifact refs are selected without changing the canonical task payload.
4. Attempt to introduce a non-canonical target kind or sibling step target through an adapter boundary and verify it is rejected or excluded.
5. Confirm `MM-650`, the original Jira preset brief, and `DESIGN-REQ-026` remain preserved in spec, plan, research, data model, contract, quickstart, tasks, implementation notes, and final verification.
