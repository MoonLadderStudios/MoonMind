# Research: Temporal Task Editing Entry Points

## Decision 1: Gate Detail Actions With Both Backend Capabilities and Runtime Flag

**Decision**: Use `actions.canUpdateInputs` and `actions.canRerun` as authoritative backend capability flags, and require the dashboard runtime flag `temporalTaskEditing` before rendering Edit or Rerun.

**Rationale**: Backend capability flags know workflow type and lifecycle state; the runtime flag lets operators hide the new flow during rollout. Requiring both prevents the UI from guessing lifecycle semantics or accidentally exposing work-in-progress behavior.

**Alternatives considered**:

- Frontend-only lifecycle checks: rejected because lifecycle state and workflow support belong to the execution read model.
- Backend-only flagging: rejected because operators need a rollout guard that can hide new entry points without changing execution state.
- Disabled buttons with explanatory text: rejected for Phase 1 because the feature request requires unsupported actions to be omitted.

## Decision 2: Extend the Existing Execution Detail Payload

**Decision**: Add the Phase 0 read fields to the existing Temporal execution detail payload instead of adding a new edit-specific read endpoint.

**Rationale**: The detail page already fetches execution identity, lifecycle, runtime, model, repository, and action state. Extending that contract keeps the canonical detail read path as the source of truth and avoids divergent views of editability.

**Alternatives considered**:

- Separate `/edit-draft` endpoint: rejected for Phase 0/1 because draft reconstruction is Phase 2, and a separate endpoint would duplicate detail-page gating data too early.
- Encode draft data only in frontend fixtures: rejected because later phases need backend/frontend contract alignment, not UI-only assumptions.

## Decision 3: Centralize Canonical Route Helpers

**Decision**: Introduce a frontend helper module that creates `/tasks/new`, `/tasks/new?editExecutionId=<workflowId>`, and `/tasks/new?rerunExecutionId=<workflowId>`.

**Rationale**: Route helpers make it easy to test that new entry points never emit `editJobId`, `/tasks/queue/new`, or queue resubmit targets. They also give Phase 2 one place to reuse mode-routing semantics.

**Alternatives considered**:

- Inline string concatenation in task detail: rejected because it is harder to audit for queue-era fallback leakage.
- A backend-provided route target: rejected for Phase 1 because frontend navigation is straightforward and already uses dashboard-side route construction.

## Decision 4: Limit Support to `MoonMind.Run`

**Decision**: Treat only `MoonMind.Run` executions as supported for Edit and Rerun in this slice.

**Rationale**: The canonical design and task plan call out `MoonMind.Run` as the initial supported workflow. Other workflow types may have different input shapes, lifecycle semantics, or rerun lineage and need separate contract work.

**Alternatives considered**:

- Allow any execution with matching lifecycle state: rejected because it could expose invalid actions on workflows without compatible input/update semantics.
- Add compatibility aliases for older workflow types: rejected by the compatibility policy and pre-release constitution.

## Decision 5: Validate With Hermetic Unit and Frontend Tests

**Decision**: Use mocked execution fixtures and unit/frontend tests for Phase 0/1 validation.

**Rationale**: This slice is contract and navigation scaffolding. It does not need provider credentials, external services, or full Temporal integration to prove visibility and route behavior. Hermetic tests are required for PR safety and can cover active, terminal, unsupported, and flag-disabled states.

**Alternatives considered**:

- Provider verification tests: rejected because no external provider behavior is involved.
- Browser-only end-to-end tests: rejected for this phase because route generation and visibility rules are testable faster and more deterministically at unit level.

## Decision 6: Runtime Mode Requires Production Code and Tests

**Decision**: Treat the feature as runtime implementation, not docs-only alignment.

**Rationale**: The user explicitly selected runtime mode and required production runtime code changes plus validation tests. Planning and tasks must therefore point at backend/frontend runtime files and test files, not only docs/spec artifacts.

**Alternatives considered**:

- Spec-only planning: rejected because it violates the runtime scope guard.
