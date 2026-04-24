# Quickstart: Managed-Session Follow-Up Retrieval

## Goal

Verify MM-506 as a runtime story: a managed session receives an explicit capability signal for follow-up retrieval, requests more context through a MoonMind-owned surface, receives both machine-readable and text retrieval output, and gets deterministic fail-fast denials when retrieval is disabled or the request is outside policy.

## Preconditions

- Work from the active feature directory `specs/254-managed-session-followup-retrieval`.
- Set `MOONMIND_FORCE_LOCAL_TESTS=1` for local unit verification in this managed runtime.
- Start with verification tests before changing production code because parts of the retrieval stack already exist.
- Use the existing retrieval and runtime-boundary test suites rather than inventing an ad hoc harness.

## Unit Test Strategy

Run the focused unit and runtime-boundary suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/api/routers/test_retrieval_gateway.py \
  tests/unit/rag/test_service.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/agents/codex_worker/test_handlers.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_launcher.py
```

Expected use:
- Add failing tests first for capability signalling, managed-session retrieval routing, response shape verification, and disabled/invalid request denials.
- Preserve existing retrieval service and context-pack tests unless the managed-session contract requires a justified change.

## Integration Test Strategy

Preferred hermetic path when compatible with the final implementation:

```bash
./tools/test_integration.sh
```

If MM-506 requires focused runtime-boundary proof that is intentionally outside `integration_ci`, run a targeted Temporal test such as:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short
```

Expected use:
- Prove the capability signal reaches the managed runtime boundary.
- Prove follow-up retrieval uses a MoonMind-owned surface instead of an unmanaged bypass.
- Prove disabled retrieval and invalid contract cases fail fast with stable reasons.
- Prove direct and gateway transports preserve the same external response semantics.

## End-to-End Verification Flow

1. Run the focused unit suite.
2. Add failing verification tests for any MM-506 requirement that is still only partially evidenced.
3. Implement only the production changes required to satisfy those failing tests.
4. Re-run the focused unit suite.
5. Run the integration path required by the implemented scope.
6. Confirm `spec.md`, `plan.md`, `research.md`, `data-model.md`, this quickstart, and the contract artifact preserve `MM-506`.

## Requirement-to-Test Guidance

- FR-001 / DESIGN-REQ-019: verify the runtime-facing capability signal includes enablement, request path, reference-data notice, and policy bounds.
- FR-002 / DESIGN-REQ-003 / DESIGN-REQ-007: verify follow-up retrieval is invoked only through MoonMind-owned routing.
- FR-003 / DESIGN-REQ-015: verify accepted and rejected request contract fields, including bounded budget overrides.
- FR-004 / DESIGN-REQ-025: verify successful retrieval returns both `ContextPack` metadata and `context_text`.
- FR-005 / DESIGN-REQ-020: verify disabled or invalid follow-up retrieval requests fail fast with deterministic reasons.
- FR-006 / DESIGN-REQ-023: verify the externally visible contract remains runtime-neutral rather than Codex-specific.
- FR-007: verify `MM-506` traceability remains present in planning and final verification artifacts.
