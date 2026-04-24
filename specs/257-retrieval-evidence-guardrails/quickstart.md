# Quickstart: Retrieval Evidence And Trust Guardrails

## Goal

Verify MM-509 as a runtime Workflow RAG story: every retrieval path publishes durable evidence, retrieved text remains inside an explicit untrusted-reference boundary, secret-bearing retrieval data stays out of durable surfaces, session-issued retrieval remains policy-bounded, disabled and degraded states remain explicit, and the same contract holds across runtimes.

## Preconditions

- Work from the active feature directory `specs/257-retrieval-evidence-guardrails`.
- Set `MOONMIND_FORCE_LOCAL_TESTS=1` for local unit verification in this managed runtime.
- Start with verification tests before changing production code because substantial retrieval runtime behavior already exists.
- Keep unit and integration verification separate so runtime-boundary gaps are visible.

## Unit Test Strategy

Run the focused retrieval, guardrail, and gateway suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/rag/test_context_pack.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/rag/test_service.py \
  tests/unit/rag/test_guardrails.py \
  tests/unit/rag/test_telemetry.py \
  tests/unit/api/routers/test_retrieval_gateway.py
```

Expected use:
- add failing tests first for missing durable-evidence fields, trust-framing assertions, secret exclusion, policy envelope enforcement, and explicit degraded-state visibility
- preserve current retrieval code unless those tests expose a real MM-509 gap

## Integration Test Strategy

Preferred hermetic path when compatible with the final implementation:

```bash
./tools/test_integration.sh
```

Targeted runtime-boundary verification for this story:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short
```

Expected use:
- prove runtime-visible retrieval evidence for semantic and degraded paths
- prove artifact/ref-backed durability survives managed-session boundaries
- prove another managed runtime path preserves the same trust and evidence contract

## End-to-End Verification Flow

1. Run the focused unit suite.
2. Add failing verification tests for any MM-509 requirement that remains partial or unverified.
3. Implement only the production changes required to satisfy those failing tests.
4. Re-run the focused unit suite.
5. Run the targeted integration tests required by the implemented scope.
6. Use `./tools/test_integration.sh` if the final change set touches broader hermetic integration behavior.
7. Confirm `spec.md`, `plan.md`, `research.md`, `data-model.md`, this quickstart, and the contract artifact preserve `MM-509`.

## Requirement-to-Test Guidance

- FR-001 / DESIGN-REQ-016: verify semantic and degraded retrieval publish one stable durable evidence envelope.
- FR-002 / DESIGN-REQ-018: verify injected retrieval context always carries the untrusted-reference and current-workspace-preference safety framing.
- FR-003 / DESIGN-REQ-020: verify provider keys, OAuth tokens, and secret-bearing config bodies do not appear in durable retrieval artifacts or metadata.
- FR-004 / DESIGN-REQ-021 / DESIGN-REQ-025: verify session-issued retrieval remains bounded by scope, filters, budgets, transport policy, provider/secret policy, and audit metadata.
- FR-005 / DESIGN-REQ-022: verify disabled and degraded retrieval states remain explicit and do not masquerade as normal semantic retrieval.
- FR-006 / DESIGN-REQ-023: verify Codex and at least one additional managed runtime share the same retrieval evidence and trust contract.
- FR-007: verify MM-509 traceability remains present in planning and final verification artifacts.
