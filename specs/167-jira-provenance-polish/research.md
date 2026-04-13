# Research: Jira Provenance Polish

## Decision: Track Jira provenance in local Create page state

**Rationale**: The feature is operator clarity for draft authoring, not durable task metadata. Local state can represent the imported issue key, board id, import mode, and target type without changing the Create payload or downstream workflow contracts.

**Alternatives considered**:

- Persist provenance in the submitted task payload. Rejected because the spec explicitly preserves the task submission contract and there is no downstream consumer yet.
- Encode provenance into the imported instruction text. Rejected because it would alter operator-authored instructions and could affect task objective/title derivation.

## Decision: Scope provenance to preset or individual step targets

**Rationale**: The Jira browser already has a single active import target. Provenance should mirror that target so importing into a step does not mark the preset area or unrelated steps.

**Alternatives considered**:

- Store one global last-import marker for the page. Rejected because it would not tell operators which field was changed by Jira.
- Store provenance on every step after any Jira import. Rejected because it creates false origin signals.

## Decision: Clear stale provenance on manual edits

**Rationale**: A provenance chip means the current field content was last populated by a Jira import. Once an operator manually changes that field, retaining the chip risks misleading them about the text's current origin.

**Alternatives considered**:

- Keep provenance until another Jira import occurs. Rejected because it can become stale immediately after manual edits.
- Require an explicit clear action. Rejected because it adds friction for a state that can be inferred from the edit event.

## Decision: Use browser session storage for last Jira project and board

**Rationale**: Session storage matches the requested session-only memory. It survives refresh within the same browser session but avoids durable preference storage.

**Alternatives considered**:

- Local storage. Rejected because it persists beyond the browser session.
- Backend user preferences. Rejected because this phase intentionally avoids backend persistence and task payload changes.
- In-memory React state only. Rejected because it would not restore after refresh.

## Decision: Treat session storage as best effort

**Rationale**: Browsers can block or throw on storage access. Jira is additive, so storage failures must fall back to normal project/board selection without blocking manual task creation.

**Alternatives considered**:

- Surface a global error when storage fails. Rejected because this would distract from task authoring and does not materially help the operator complete the draft.
- Disable Jira browsing when storage fails. Rejected because storage memory is optional and unrelated to Jira browser availability.

## Decision: Validate through focused Create page tests

**Rationale**: The risk is client-side state transition correctness: chip visibility, target scoping, stale-state clearing, session-memory restoration, and payload non-persistence. Existing Create page tests already mock runtime config, Jira browser data, import actions, and task submission.

**Alternatives considered**:

- Add backend tests. Rejected because this phase does not change backend routers, runtime config generation, storage, or task execution.
- Rely on manual smoke testing only. Rejected because the targeted state transitions are easy to regress and straightforward to automate.
