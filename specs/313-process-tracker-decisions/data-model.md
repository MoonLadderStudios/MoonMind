# Data Model: Process Verified Tracker Decisions

## Proposal Delivery

Represents a task proposal delivered to an external review surface.

Key fields:
- `id`: Stable proposal identifier.
- `status`: Current proposal lifecycle state. Existing values include `open`, `accepted`, `promoted`, and `dismissed`; this story may require distinct non-executing states or metadata for deferred, reprioritized, and revision-requested outcomes.
- `provider`: Review provider such as GitHub or Jira.
- `externalKey`: Provider issue key or number.
- `externalUrl`: Provider issue URL.
- `taskSnapshotRef`: Reference to stored executable proposal evidence.
- `taskCreateRequest`: Stored proposal snapshot used for promotion.
- `providerMetadata.delivery`: Delivery status, marker, labels, duplicate source, and stored-snapshot notice.
- `providerMetadata.providerDecisions`: Ordered provider decision audit rows.
- `promotedAt`, `promotedByUserId`, `decidedByUserId`, `decisionNote`: Existing local decision fields.

Validation rules:
- Provider and external identity must match the incoming decision event before state changes.
- Stored executable task content must come from `taskCreateRequest`, not issue body or comments.
- Secret-like provider metadata keys must be redacted or rejected before persistence.
- Duplicate provider event IDs must return the previous decision result.

## Provider Decision Event

Represents a trusted or rejected event received from an external tracker.

Fields:
- `provider`: Provider name.
- `externalKey`: Provider issue key or number.
- `providerEventId`: Stable provider event identity used for idempotency.
- `actor`: Provider actor identity.
- `action`: Normalized action when available from trusted provider state.
- `body` / `note`: Review artifact text used only to parse bounded controls.
- `observedAt`: Provider event observation time.
- `authenticity`: Verification result for signature or shared secret.
- `authorization`: Actor and destination policy result.

Validation rules:
- Authenticity must pass before parsing or applying a decision.
- Actor authorization must pass before any state mutation or run creation.
- Missing or blank event IDs must fail before side effects.
- Action values must normalize to promote, dismiss, defer, reprioritize, or request revision.

## Provider Decision Result

Represents the bounded decision MoonMind accepts or rejects.

Fields:
- `accepted`: Whether the decision may affect proposal state.
- `decision`: One of promote, dismiss, defer, reprioritize, request revision, or null for rejected events.
- `reason`: Rejection or no-op reason.
- `actor`: Verified external actor.
- `providerEventId`: Idempotency key.
- `note`: Optional scrubbed reviewer note.
- `priority`: Optional normalized priority for reprioritize.
- `deferUntil`: Optional defer target.
- `runtimeMode`: Optional bounded runtime override for promotion.
- `resultingExternalState`: External issue state after handling or desired state update.
- `promotedExecutionId`: Created run identifier for successful promotion.

Validation rules:
- Runtime override must be validated before run creation.
- Rejected decision results must not expose secrets.
- Duplicate provider events must not append duplicate audit rows.

## Promotion Controls

Bounded fields that may affect a promoted run without replacing the stored snapshot.

Fields:
- `priority`
- `maxAttempts`
- `note`
- `runtimeMode`

Validation rules:
- Full task payload replacement is forbidden.
- Unknown controls are ignored only if policy says they are non-side-effecting; otherwise reject fail-fast.
- Runtime controls must use the same validation as manual promotion.

## MoonMind.Run

Created execution for accepted promotion decisions.

Fields:
- `workflowType`: `MoonMind.Run`
- `owner`: Authorized actor mapping or service owner determined by provider policy.
- `initialParameters`: Stored proposal snapshot payload with validated bounded controls.
- `idempotencyKey`: Stable key derived from proposal and provider decision identity.
- `repository`: Repository from stored proposal snapshot.
- `summary`: Proposal summary.

Validation rules:
- One accepted provider approval can create at most one run.
- Duplicate provider approval replay must return the already recorded outcome.
- Failed external issue updates after run creation must leave recoverable decision metadata.

## State Transitions

```text
open
├── verified promote -> promoted + promotedExecutionId
├── verified dismiss -> dismissed
├── verified defer -> deferred metadata, no run
├── verified reprioritize -> open with updated priority metadata, no run
├── verified request revision -> revision-requested metadata, no run
├── rejected/unauthorized/unverified -> open with rejected decision audit
└── duplicate event -> prior outcome reused, no duplicate side effects
```

If the implementation keeps existing enum values unchanged, distinct deferred/revision-requested outcomes must be represented in provider decision metadata and serialized review state without ambiguity.
