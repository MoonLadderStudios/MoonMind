# Research: Temporal Rerun Submit

## Decision: Reuse the Temporal task editing payload builder for rerun requests

**Decision**: Use the same artifact-safe payload construction path for edit and rerun, while selecting `UpdateInputs` for active edit mode and `RequestRerun` for terminal rerun mode.

**Rationale**: Edit and rerun submit the same operator-reviewed task input shape, and both need identical artifact externalization behavior. Keeping one builder avoids drift while the update name preserves the lifecycle distinction required by the spec.

**Alternatives considered**:

- Separate rerun-only payload builder. Rejected because it would duplicate artifact and parameter patch logic without adding a meaningful contract difference.
- Route rerun through the normal create flow. Rejected because it loses source execution lineage and violates the no queue/create fallback requirement.

## Decision: Create replacement input artifacts for artifact-backed reruns

**Decision**: When a rerun source has an existing input artifact or when the edited input exceeds inline limits, create a new input artifact reference for the rerun request.

**Rationale**: Historical artifacts are audit records. Rerun requests may preserve, modify, or review those inputs, but any new submitted content must be represented by a new artifact reference so the source run remains inspectable.

**Alternatives considered**:

- Reuse the historical artifact reference when instructions are unchanged. Rejected for the first runtime slice because the form submits a reviewed input state and the artifact-safe rule is simpler and more auditable when a source artifact exists.
- Mutate the historical artifact. Rejected because it violates artifact immutability and destroys lineage.

## Decision: Return to Temporal execution detail after accepted rerun

**Decision**: After an accepted rerun request, navigate back to the Temporal execution context, using a returned workflow identifier when present and otherwise the source workflow identifier.

**Rationale**: The operator started from a Temporal detail flow and needs continuity after submission. This also supports same-logical-execution continue-as-new behavior and future latest-run detail responses without introducing queue/list fallback.

**Alternatives considered**:

- Redirect to the task list. Rejected because it hides whether the rerun was accepted and loses source context.
- Redirect to a queue route. Rejected because queue-era rerun paths are explicitly out of scope.

## Decision: Treat backend capability flags as advisory and revalidate on submit

**Decision**: Load-time checks require `canRerun`, but submit handling must still surface backend rejection messages without redirecting.

**Rationale**: Execution state can change between page load and submit. Explicit failure handling prevents stale capability flags from becoming silent success or queue fallback behavior.

**Alternatives considered**:

- Trust load-time capability flags only. Rejected because it fails stale-state scenarios.
- Disable the rerun button permanently for any uncertain state. Rejected because backend capability flags are the authoritative runtime contract and already distinguish supported terminal cases.

## Decision: Compare edit and rerun semantics in frontend regression tests

**Decision**: Add tests that verify edit submits `UpdateInputs`, rerun submits `RequestRerun`, artifact-backed reruns create replacement refs, accepted reruns redirect to Temporal detail, and backend rerun rejection does not redirect.

**Rationale**: The highest regression risk is accidental cross-mode fallback: rerun using create, edit using rerun, or artifact mutation/reuse. These behaviors are most visible at the shared form boundary.

**Alternatives considered**:

- Backend-only tests. Rejected because the current risk is shared form mode plumbing and request construction.
- Manual quickstart only. Rejected because the no-fallback requirement needs repeatable regression coverage.
