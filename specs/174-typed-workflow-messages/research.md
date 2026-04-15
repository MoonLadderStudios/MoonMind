# Research: Typed Workflow Messages

## Decision 1: Scope the runtime change to `MoonMind.AgentSession`

- **Decision**: Implement this story on the Codex task-scoped managed-session workflow.
- **Rationale**: The source document identifies managed sessions as the highest-value Temporal message surface, and existing code already concentrates epoch safety, idempotency, continuation, and lifecycle controls in `agent_session.py`.
- **Alternatives considered**: Retype every workflow in the repository. Rejected because the supplied story metadata is managed-session-specific and a whole-repo Temporal migration would exceed one independently testable story.

## Decision 2: Keep `control_action` as a replay shim

- **Decision**: Preserve the existing `control_action` signal as a legacy compatibility shim while keeping canonical controls as explicit updates and typed signals.
- **Rationale**: Temporal history may already contain this signal. Removing it in the same runtime change would risk replay failure and violate the workflow compatibility guidance.
- **Alternatives considered**: Delete the shim immediately. Rejected because in-flight Temporal histories require an explicit cutover plan before removal.

## Decision 3: Validate Continue-As-New with the workflow input model

- **Decision**: Build continuation state through `CodexManagedSessionWorkflowInput.model_validate(...)` and pass the typed model to `workflow.continue_as_new`.
- **Rationale**: This proves the continuation payload is the public workflow input contract, not an opaque scratch dictionary.
- **Alternatives considered**: Continue returning a JSON dict from the helper. Rejected because it leaves the highest-risk handoff without typed validation at the construction point.

## Decision 4: Use one named request model per control operation

- **Decision**: Add explicit clear, cancel, and terminate update request models, plus a typed attach-runtime-handles signal model.
- **Rationale**: The source document requires one operation, one request model for managed-session controls and named contracts for Signals and Updates.
- **Alternatives considered**: Keep a shared generic workflow control request. Rejected because it makes unrelated operations share a canonical bag-shaped contract.
