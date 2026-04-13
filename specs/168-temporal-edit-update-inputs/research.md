# Research: Temporal Edit UpdateInputs

## Decision 1: Use the Existing Execution Update Endpoint

**Decision**: Active edit submission will use the existing execution update endpoint for the target workflow and send the canonical `UpdateInputs` update name.

**Rationale**: The backend already exposes an execution update request model with `updateName`, `inputArtifactRef`, and `parametersPatch`, and the Temporal service already routes `UpdateInputs` to the run workflow update path. Reusing this path keeps Temporal as the source of truth and avoids a separate edit API.

**Alternatives considered**:

- Create a dedicated edit endpoint. Rejected because it duplicates existing update semantics and increases contract surface.
- Submit through create mode with a hidden edit identifier. Rejected because it risks queue-era or create-era fallback semantics.

## Decision 2: Use the Shared Create-Form Payload as the Parameters Patch

**Decision**: The edit flow will reuse the shared task form's normalized task payload construction and submit that input state as `parametersPatch`.

**Rationale**: Phase 2 reconstructs edit mode into the same draft/form shape as create mode. Reusing the normalized payload preserves supported fields consistently and avoids a forked edit-only form.

**Alternatives considered**:

- Build a separate sparse field patch. Rejected because the current edit UX presents a reviewable replacement input state, and sparse patches could omit fields that the operator expects to preserve.
- Send only instructions. Rejected because the feature requires supported task fields beyond instructions, including runtime, model, repository, branches, publish mode, skill, and template state.

## Decision 3: Create New Artifacts for Artifact-Backed Edits

**Decision**: When an execution was reconstructed from a historical input artifact, saving edits creates a new input artifact reference for the edited input content. Oversized edited input content also follows the existing artifact externalization policy.

**Rationale**: Historical artifacts are audit records. Reusing or mutating them would make it unclear which input state was used by the original execution and which state was introduced by the edit.

**Alternatives considered**:

- Reuse the historical artifact ref when edited content remains small. Rejected because it would incorrectly associate the update with immutable historical content.
- Mutate the historical artifact content in place. Rejected because it violates auditability and artifact immutability.

## Decision 4: Treat Backend Outcome as the Submit-Time Authority

**Decision**: The UI will continue to use feature flags and capability flags as load-time gates, but the backend update response determines whether submit succeeded, was deferred, or was rejected.

**Rationale**: Execution state can change between draft load and submit. The edit page must handle stale capability and terminal-state transitions without pretending the earlier read model is still authoritative.

**Alternatives considered**:

- Trust the initial capability flags and assume submit success. Rejected because active workflows can become terminal or otherwise non-editable after page load.
- Hide backend outcome details behind generic success/failure. Rejected because operators need to understand whether a change applied immediately or was scheduled.

## Decision 5: Keep Rerun and Queue Paths Out of Scope

**Decision**: This phase enables active edit submission only. Rerun submission remains blocked until its own phase, and queue-era routes/parameters remain forbidden.

**Rationale**: Edit and rerun have different lifecycle semantics. Keeping Phase 3 narrow reduces the risk of accidentally reviving queue resubmit behavior.

**Alternatives considered**:

- Implement rerun submission while touching shared submit code. Rejected because Phase 4 owns terminal rerun semantics and lineage behavior.
- Route failed edit submits through legacy queue resubmit. Rejected by the feature requirements and Temporal-native architecture.
