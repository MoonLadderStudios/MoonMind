# Research: Task Editing System

## Decision 1: Update queued task jobs in place (preserve job ID)

- **Decision**: Use one update mutation on the existing job row instead of cancel-and-recreate.
- **Rationale**: Preserves queue correlation/job identity and aligns with required in-place edit behavior.
- **Alternatives considered**:
  - Cancel + create replacement job: rejected because it breaks ID continuity and audit traceability.
  - Shadow-copy record swap: rejected as unnecessary complexity for v1.

## Decision 2: Lock-first mutation with strict editability invariants

- **Decision**: Load job using row lock, then enforce `type=task`, `status=queued`, and `started_at is null` before applying edits.
- **Rationale**: Prevents unsafe updates during worker claim transitions and keeps eligibility deterministic.
- **Alternatives considered**:
  - Optimistic-only update without row lock: rejected because claim/update races can pass stale state checks.
  - UI-only eligibility enforcement: rejected because server-side invariants are required for safety.

## Decision 3: Keep update request contract aligned with create envelope

- **Decision**: Mirror create fields (`type`, `priority`, `maxAttempts`, `affinityKey`, `payload`) and extend with optional `expectedUpdatedAt` and `note`.
- **Rationale**: Reuse of serializer/builders in dashboard code reduces drift and implementation duplication.
- **Alternatives considered**:
  - Introduce a partial PATCH contract: rejected because it adds translation complexity and diverges from current submit model.
  - Separate update-only payload shape: rejected due to higher UI/backend mapping overhead.

## Decision 4: Enforce optional optimistic concurrency with `expectedUpdatedAt`

- **Decision**: When provided, `expectedUpdatedAt` must exactly match current `updated_at`; mismatch returns conflict.
- **Rationale**: Prevents silent overwrite in multi-tab/edit-lag scenarios without forcing all clients to send a token.
- **Alternatives considered**:
  - Always require concurrency token: rejected for backward compatibility with non-edit clients.
  - Ignore token on mismatch: rejected because it defeats stale-write protection.

## Decision 5: Reuse task payload normalization and runtime gate validation from create flow

- **Decision**: Run queued update payloads through the same task normalization path used by create.
- **Rationale**: Keeps runtime config/rule enforcement consistent and avoids divergent validation behavior.
- **Alternatives considered**:
  - Lightweight update-time validation only: rejected because it can permit payloads create would reject.
  - Skip runtime gate checks on update: rejected because runtime constraints must stay consistent.

## Decision 6: Normalize router errors to documented queue semantics

- **Decision**: Map update exceptions to `job_not_found` (404), `job_not_authorized` (403), `job_state_conflict` (409), `invalid_queue_payload` (422), and runtime gate failures (`claude_runtime_disabled`, 400).
- **Rationale**: Predictable client behavior depends on stable semantic error codes.
- **Alternatives considered**:
  - Return raw internal exception strings: rejected for unstable client contracts.
  - Collapse conflicts into generic 400/500: rejected because operators need explicit retry vs refresh guidance.

## Decision 7: Reuse `/tasks/queue/new` for edit mode via `editJobId`

- **Decision**: Keep one form route and toggle behavior by query param, prefilled from queue detail API.
- **Rationale**: Avoids split UX and duplicated form logic while preserving a clear edit entry point.
- **Alternatives considered**:
  - Separate edit page implementation: rejected as unnecessary duplicate UI surface.
  - Inline editing on list only: rejected because detail-driven context is required for v1 clarity.

## Decision 8: Keep attachment edits out of v1 update scope

- **Decision**: Treat attachments as immutable during queued task edits in this release.
- **Rationale**: Attachment mutation introduces additional claim-race and transactional ordering complexity.
- **Alternatives considered**:
  - Add attachment-edit support in same endpoint: rejected due to higher race/consistency risk.
  - Allow delayed attachment uploads without lock semantics: rejected for worker-claim safety concerns.

## Decision 9: Runtime-vs-docs orchestration mode must remain runtime-aligned

- **Decision**: Plan and execution for this feature must include production runtime code changes plus validation tests.
- **Rationale**: Spec objective explicitly rejects docs/spec-only completion.
- **Alternatives considered**:
  - Treat this step as documentation-only completion: rejected by runtime scope guard (`DOC-REQ-018`, FR-015, FR-016).
  - Run only ad-hoc targeted tests: rejected as acceptance gate; canonical validation remains `./tools/test_unit.sh`.
