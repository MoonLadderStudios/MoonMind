# Research: Temporal Task Draft Reconstruction

## Decision 1: Keep `/tasks/new` as the shared review surface

**Decision**: Use the existing shared task submit page for create, edit, and rerun review modes.

**Rationale**: The product goal is one familiar task form. Reusing the existing surface avoids divergent create/edit/rerun behavior and keeps later submit phases focused on update semantics rather than rebuilding form fields.

**Alternatives considered**:

- Create a separate edit-only page: rejected because it duplicates the shared form and increases drift risk.
- Route edit/rerun through legacy queue pages: rejected because the feature explicitly forbids queue-era fallback.

## Decision 2: Resolve mode from canonical route identifiers with rerun precedence

**Decision**: Resolve mode in this order: rerun execution identifier, edit execution identifier, create mode.

**Rationale**: Rerun is an explicit terminal-execution action. Giving it precedence avoids accidentally treating a rerun request as an in-place edit when both query parameters are present.

**Alternatives considered**:

- Reject URLs containing both identifiers: rejected because the source plan defines rerun precedence and this behavior is straightforward to test.
- Let edit win by parameter order: rejected because URL ordering is fragile and conflicts with the requested contract.

## Decision 3: Use existing execution detail and artifact read surfaces

**Decision**: Load Temporal execution detail through the existing execution read path and read immutable input artifact content only when inline task instructions are unavailable.

**Rationale**: Phase 0/1 already aligned execution detail fields and capability flags. Reusing the existing artifact download surface preserves auditability and avoids adding a new backend endpoint for a frontend reconstruction slice.

**Alternatives considered**:

- Add a backend-only draft reconstruction endpoint: deferred because Phase 2 can validate the contract using existing read surfaces, and later submit phases may refine backend payload helpers.
- Always read input artifacts: rejected because inline instructions should not require extra network work.

## Decision 4: Capability flags are mandatory display gates

**Decision**: Edit mode requires update capability and rerun mode requires rerun capability before the draft is considered usable.

**Rationale**: The backend remains authoritative for lifecycle and workflow capability. The frontend should not infer editability from state names alone.

**Alternatives considered**:

- Infer capability from active vs terminal state: rejected because lifecycle state alone is insufficient and may drift from backend policy.
- Show disabled controls with partial data: rejected because Phase 2 must avoid misleading partial state.

## Decision 5: Fail closed for incomplete reconstruction

**Decision**: Unsupported workflow type, missing requested capability, unreadable artifact, malformed artifact, or missing instructions produce explicit operator-readable errors.

**Rationale**: The task editing design prioritizes trustworthy reconstruction. A partial form could cause operators to submit unintended task data in later phases.

**Alternatives considered**:

- Show best-effort partial fields: rejected because the source plan requires refusing misleading partial state.
- Fall back to queue edit data: rejected because queue fallback is explicitly forbidden.

## Decision 6: Submit behavior remains intentionally blocked for edit/rerun in Phase 2

**Decision**: Phase 2 updates titles, CTAs, and prefill behavior, but does not submit `UpdateInputs` or `RequestRerun`.

**Rationale**: The milestone is navigable but non-submitting. Preventing edit/rerun from using create semantics avoids accidental new-run creation or queue fallback before Phase 3/4 payload semantics are implemented.

**Alternatives considered**:

- Submit edit/rerun through the create endpoint temporarily: rejected because it would violate Temporal-native semantics.
- Add placeholder update calls: rejected because update payload preparation and artifact-safe submit semantics belong to later phases.
