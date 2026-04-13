# Research: Jira UI Test Coverage

## Decision 1: Use Existing Runtime Test Boundaries

**Decision**: Extend the existing Create page, Jira browser router, Jira browser service, and runtime config test files instead of creating a separate Phase 9-only test harness.

**Rationale**: The existing tests already exercise the runtime surfaces that Phase 9 must protect: Create page state transitions, mocked browser fetches, preset and step import behavior, backend router error shaping, Jira service normalization, and dashboard runtime config exposure. Extending those files keeps coverage close to the behavior under test and avoids a parallel model.

**Alternatives considered**:

- Add a new end-to-end browser suite. Rejected because the required coverage is primarily contract and state-transition regression coverage, and a full browser suite would add cost without improving hermeticity.
- Add only backend tests. Rejected because many Phase 9 requirements are user-facing Create page behaviors.

## Decision 2: Keep Required Jira Tests Hermetic

**Decision**: Required validation uses fixtures, mocked fetch responses, and stubbed Jira service clients. Live Jira provider verification remains outside the required suite.

**Rationale**: MoonMind test taxonomy separates required unit and hermetic integration checks from credentialed provider verification. Phase 9 is about keeping the Create page robust and safe without making local development or CI depend on Jira credentials.

**Alternatives considered**:

- Use live Jira credentials for issue and board browsing tests. Rejected because that would make an optional integration mandatory for required validation.
- Mock only the frontend. Rejected because backend normalization, policy, and redaction are explicit requirements.

## Decision 3: Treat Runtime Code Changes as Test-Driven Fixes

**Decision**: The implementation phase should first add or strengthen failing tests for each Phase 9 requirement, then change production runtime code only where tests expose a behavior gap.

**Rationale**: Earlier Jira phases already added most runtime surfaces. Phase 9's purpose is robustness. Test-first fixes avoid unnecessary churn while preserving the requirement that runtime code changes are included when behavior is incomplete.

**Alternatives considered**:

- Make production code changes preemptively. Rejected because the risk is higher than targeted test-driven correction.
- Produce docs-only validation plans. Rejected because runtime mode requires production runtime changes where needed plus executable validation.

## Decision 4: Preserve Submission Contract as a Regression Target

**Decision**: Tests must explicitly assert that Jira provenance, browser selections, and failure details are not required fields in task submission and do not create a separate task type or endpoint.

**Rationale**: The Create page submit flow is already complex. Jira import must feed existing authored fields only, so unchanged submission shape is one of the safest ways to catch accidental coupling.

**Alternatives considered**:

- Persist Jira provenance in the first Phase 9 work. Rejected because the spec states provenance persistence is not required and would broaden downstream contracts.

## Decision 5: Validate DOC-REQ Traceability as a Planning Gate

**Decision**: Generate an explicit requirements traceability contract mapping every `DOC-REQ-*` to functional requirements, implementation surfaces, and validation strategy.

**Rationale**: This feature is document-backed by `docs/UI/CreatePage.md`. Traceability prevents the plan from silently dropping source requirements such as failure isolation, policy boundaries, and unchanged submission behavior.

**Alternatives considered**:

- Rely on the source requirement table in `spec.md` only. Rejected because implementation planning needs concrete validation surfaces, not only high-level mappings.
