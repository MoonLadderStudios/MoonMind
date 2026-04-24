# Quickstart: Initial Managed-Session Retrieval Context

## Goal

Verify MM-505 as a runtime story: MoonMind resolves initial managed-session retrieval context before runtime execution, publishes durable startup evidence, injects the retrieved context through the adapter input surface with untrusted-text framing, and preserves MM-505 traceability.

## Preconditions

- Work from the active feature directory `specs/253-initial-managed-session-retrieval-context`.
- Set `MOONMIND_FORCE_LOCAL_TESTS=1` for local unit verification in this managed runtime.
- Use the existing retrieval/runtime unit suites before any production changes.
- If workflow-boundary integration proof is needed, use the existing Temporal integration test infrastructure rather than inventing an ad hoc harness.

## Unit Test Strategy

Run the focused unit and workflow-boundary suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/rag/test_context_pack.py \
  tests/unit/rag/test_service.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_launcher.py
```

Expected use:
- Verify existing behavior for retrieval packaging, artifact publication, startup ordering, and runtime instruction framing.
- Add failing tests first for any uncovered MM-505 requirement before changing production code.

## Integration Test Strategy

Preferred hermetic path when compatible with the final implementation:

```bash
./tools/test_integration.sh
```

If MM-505 requires a focused workflow-boundary scenario that is intentionally outside `integration_ci`, run a targeted Temporal integration test such as:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short
```

Expected use:
- Prove durable retrieval publication and compact runtime-boundary behavior when unit tests are not sufficient.
- Prove shared runtime contract behavior if Codex-only unit evidence is not enough.
- MM-505 currently uses `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` as the focused boundary check for Claude startup reusing the shared context-injection contract before `CLAUDE.md` is written.

## End-to-End Verification Flow

1. Run the focused unit suite.
2. If any MM-505 requirement remains only partially proven, add verification-first tests for that boundary.
3. Apply production changes only where those tests expose a real gap.
4. Run the targeted unit suite again.
5. Run the integration path required by the implemented scope.
6. Confirm `spec.md`, `plan.md`, `research.md`, this quickstart, and final verification artifacts preserve MM-505.

## Requirement-to-Test Guidance

- FR-001 / DESIGN-REQ-001: verify prepare-workspace ordering before command launch.
- FR-002 / DESIGN-REQ-005: verify embedding-backed retrieval and deterministic ContextPack assembly without a generative retrieval hop.
- FR-003 / DESIGN-REQ-002 / DESIGN-REQ-008 / DESIGN-REQ-011: verify durable artifact-backed publication and compact startup-state handling.
- FR-004 / DESIGN-REQ-006: verify adapter instruction framing treats retrieved text as untrusted reference data.
- FR-005 / DESIGN-REQ-017 / DESIGN-REQ-025: verify reusable runtime-boundary behavior and transport/policy neutrality.
- FR-006: verify MM-505 traceability remains present in MoonSpec artifacts and final evidence.
