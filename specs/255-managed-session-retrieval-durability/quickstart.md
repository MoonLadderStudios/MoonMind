# Quickstart: Managed-Session Retrieval Durability Boundaries

## Goal

Verify MM-507 as a runtime durability story: retrieval truth remains authoritative in artifact/ref-backed MoonMind surfaces, large retrieved bodies stay out of durable workflow payloads, reset or session-epoch changes preserve retrieval evidence, and the next step can recover by rerunning retrieval or reattaching the latest durable context reference.

## Preconditions

- Work from the active feature directory `specs/255-managed-session-retrieval-durability`.
- Set `MOONMIND_FORCE_LOCAL_TESTS=1` for local unit verification in this managed runtime.
- Start with verification tests before changing production code because core retrieval publication already exists.
- Use the existing managed-session runtime and retrieval test suites instead of inventing an ad hoc harness.

## Unit Test Strategy

Run the focused retrieval and managed-session boundary suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/rag/test_context_pack.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py \
  tests/unit/services/temporal/runtime/test_launcher.py \
  tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py
```

Expected use:
- Add failing tests first for durable retrieval authority, compact metadata, reset preservation, and latest-context recovery behavior.
- Preserve existing startup retrieval tests unless MM-507 exposes a justified contract gap.

## Integration Test Strategy

Preferred hermetic path when compatible with the final implementation:

```bash
./tools/test_integration.sh
```

If MM-507 requires focused workflow/runtime-boundary proof that is intentionally outside `integration_ci`, run targeted Temporal integration tests such as:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short
```

Expected use:
- Prove durable retrieval publication remains compact and reusable after reset or new session epochs.
- Prove the next step can recover by rerunning retrieval or reattaching the latest durable context reference.
- Prove shared durability semantics stay consistent across managed runtimes rather than becoming runtime-specific.

## End-to-End Verification Flow

1. Run the focused unit suite.
2. Add failing verification tests for any MM-507 requirement that remains partial or unverified.
3. Implement only the production changes required to satisfy those failing tests.
4. Re-run the focused unit suite.
5. Run the integration path required by the implemented scope.
6. Confirm `spec.md`, `plan.md`, `research.md`, `data-model.md`, this quickstart, and the contract artifact preserve `MM-507`.

## Requirement-to-Test Guidance

- FR-001 / DESIGN-REQ-005 / DESIGN-REQ-012: verify durable artifact/ref-backed retrieval truth remains authoritative after continuity changes.
- FR-002 / DESIGN-REQ-011: verify large retrieved bodies remain behind artifacts/refs and compact metadata stays bounded.
- FR-003 / DESIGN-REQ-013: verify reset and session-epoch replacement preserve durable retrieval evidence.
- FR-004 / DESIGN-REQ-017: verify the next step recovers by rerunning retrieval or reattaching the latest durable context ref.
- FR-005 / DESIGN-REQ-023: verify durability semantics remain runtime-neutral across managed runtimes.
- FR-006: verify `MM-507` traceability remains present in planning and final verification artifacts.
