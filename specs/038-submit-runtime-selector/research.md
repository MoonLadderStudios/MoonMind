# Research: Submit Runtime Selector

## Decision 1: Source runtime options from dashboard config, then append Orchestrator
- **Decision**: Use the serialized runtime config (`system.supportedTaskRuntimes` + `defaultTaskRuntime`) that already ships inside `task_dashboard_config`, normalize each entry, and inject a UI-only `orchestrator` option when the list omits it.
- **Rationale**: Keeps worker options fully config-driven and honors any future runtime ordering without recompiling the dashboard. Appending Orchestrator only in the UI satisfies the Phase 1 contract (no backend changes) while still exposing the new submit target everywhere the shared form renders.
- **Alternatives**:
  - Hard-code all four options in JavaScript. Rejected because operators would need a redeploy for every runtime change and the dashboard already exposes config for this purpose.
  - Extend the server view model now. Rejected for Phase 1 to minimize backend churn; config plumbing will be tackled in Phase 4 per the roadmap.

## Decision 2: Keep drafts in-memory via `createSubmitDraftController`
- **Decision**: Introduce a small controller that clones worker/orchestrator draft objects on save/load, and wire runtime change events to persist the current section before loading the target runtime’s draft.
- **Rationale**: Solves the acceptance criteria (“switching runtimes does not lose work”) without taking a dependency on `localStorage` yet. Cloning prevents DOM bindings from mutating stored drafts, and the helper can later be extended to read/write storage when Phase 2 lands.
- **Alternatives**:
  - Store drafts directly on DOM elements. Rejected because state would leak when nodes are re-rendered, and orchestrator/worker shapes differ.
  - Go straight to `localStorage`. Deferred because Phase 1 only mandates in-memory handling and we want to scope release risk.

## Decision 3: Bind instruction textarea to the active runtime, mirror queue primary step
- **Decision**: Treat the shared “Instructions / Objective” field as the canonical input for both runtimes. When the user is in worker mode, keep it synchronized with the primary step instructions (step editor remains the source-of-truth for queue payloads); in orchestrator mode, the textarea is independent.
- **Rationale**: Operators expect a single place to describe work, but worker payloads still require the primary step. Mirroring prevents double-entry while guaranteeing queue validation (primary step instructions required) still fires.
- **Alternatives**:
  - Hide the shared textarea when a worker runtime is selected. Rejected because switching to Orchestrator would drop context, violating FR-005.
  - Make the primary step optional. Rejected because backend contract still requires at least one step with instructions.

## Decision 4: Runtime-aware submission routing helper
- **Decision**: Build `determineSubmitDestination(runtimeValue, endpoints)` that chooses between `sources.queue.create` and `sources.orchestrator.create`, returning both the endpoint URL and a `mode` flag for downstream branching.
- **Rationale**: Ensures we only have one place that knows which runtimes map to which endpoints, making validation/tests straightforward. Also centralizes the config fallback logic so the submit handler simply checks `mode`.
- **Alternatives**:
  - Inline endpoint comparisons in the submit handler. Rejected because we would duplicate the `if (runtime === 'orchestrator')` logic in multiple places (runtime select, submission, navigation).

## Decision 5: Minimal orchestrator validation helper
- **Decision**: Provide `validateOrchestratorSubmission({ instruction, targetService, priority, approvalToken })` that enforces instruction + target service, normalizes priority to `normal|high`, trims an optional approval token, and returns `{ ok, value|error }`.
- **Rationale**: Keeps orchestrator-specific validation isolated and unit-testable. The submit handler can call it before performing any network work, giving consistent inline error messages.
- **Alternatives**:
  - Reuse queue validation logic. Rejected because queue validation expects repository/steps/publish fields that orchestrator runs do not expose.

## Decision 6: Dashboard unit tests for regression coverage
- **Decision**: Add `tests/task_dashboard/test_submit_runtime.js` using the existing VM harness to exercise the new helpers (draft controller, endpoint routing, validation).
- **Rationale**: Guarantees runtime switching, endpoint selection, and orchestrator validation stay reliable without spinning up a browser automation stack. Fits into the already-mandated `./tools/test_unit.sh` workflow.
- **Alternatives**:
  - Ship without automated tests and rely on manual QA. Rejected due to FR-011 and CI guardrails.
