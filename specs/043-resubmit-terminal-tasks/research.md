# Research: Resubmit Terminal Tasks

## Decision 1: Add a first-class resubmit endpoint instead of UI-only create replay

- **Decision**: Implement `POST /api/queue/jobs/{jobId}/resubmit` as a dedicated authenticated queue API.
- **Rationale**: Preserves server-side authorization checks, stable audit linkage, and future attachment-copy extensibility.
- **Alternatives considered**:
  - Client-only `POST /api/queue/jobs` replay: rejected because source/new lineage is not guaranteed at the server boundary.
  - Reusing `PUT /api/queue/jobs/{jobId}` for terminal jobs: rejected because it mutates immutable history and blurs lifecycle semantics.

## Decision 2: Keep eligibility split strict between edit and resubmit

- **Decision**: Keep edit eligibility at `type=task`, `status=queued`, `startedAt=null`; add resubmit eligibility at `type=task`, `status in {failed,cancelled}`.
- **Rationale**: Preserves existing update behavior and keeps terminal-job retry policy explicit.
- **Alternatives considered**:
  - Broad terminal eligibility including `dead_letter` in v1: rejected to keep blast radius small and policy deliberate.
  - Allowing queued/running resubmit: rejected because active jobs must use update/cancel controls.

## Decision 3: Reuse ownership semantics from queued update path

- **Decision**: Require source ownership (`created_by_user_id` or `requested_by_user_id`) with superuser bypass parity.
- **Rationale**: Prevents policy drift between queue mutation flows and keeps authorization predictable.
- **Alternatives considered**:
  - New resubmit-specific role: rejected as unnecessary scope and operational overhead.
  - UI-only ownership gating: rejected because authorization must remain server-enforced.

## Decision 4: Normalize resubmit payload using existing task normalization

- **Decision**: Run resubmit payload through the same canonical task normalization/runtime-gate path used by create/update.
- **Rationale**: Ensures runtime capability and payload validation stay consistent across submission modes.
- **Alternatives considered**:
  - Lightweight resubmit validation only: rejected because it can admit payloads create/update would reject.
  - Bypassing runtime gates for retries: rejected because retries must obey the same policy controls.

## Decision 5: Preserve source immutability and append lineage events

- **Decision**: Create a new queued job row for successful resubmits and emit linkage events on source and new jobs.
- **Rationale**: Maintains immutable source history while giving operators deterministic traceability.
- **Alternatives considered**:
  - Mutating source row into queued state: rejected because terminal history would be overwritten.
  - Linking only through UI query params: rejected because audit lineage must persist independent of UI state.

## Decision 6: Keep attachment copying out of v1 resubmit scope

- **Decision**: Resubmit does not copy source attachments; reject attachment mutation payload fields and show explicit UI guidance.
- **Rationale**: Avoids artifact-copy race/ownership complexity in the initial rollout while keeping behavior explicit.
- **Alternatives considered**:
  - Automatic attachment cloning in same feature: rejected due to extra storage and consistency complexity.
  - Silent attachment omission without UI messaging: rejected because it causes operator confusion.

## Decision 7: Reuse existing prefill route with mode inference

- **Decision**: Continue using `/tasks/queue/new?editJobId=<jobId>` and infer `edit` vs `resubmit` from source job status/type.
- **Rationale**: Minimizes routing surface and keeps one prefill path in the dashboard.
- **Alternatives considered**:
  - Introducing a separate `resubmitJobId` query param: rejected for duplicated parsing and route-state complexity.
  - Creating a standalone resubmit page: rejected as unnecessary UI duplication.

## Decision 8: Keep endpoint templates runtime-injected via dashboard config

- **Decision**: Ensure `sources.queue.resubmit` is part of runtime config and consume that template in dashboard submit routing.
- **Rationale**: Maintains thin-dashboard endpoint indirection and environment portability.
- **Alternatives considered**:
  - Hardcoding `/api/queue/jobs/{id}/resubmit` in UI only: rejected because it bypasses established runtime config pattern.

## Decision 9: Runtime-mode alignment is mandatory for this feature

- **Decision**: Treat this feature as runtime scope end-to-end and enforce implementation + validation deliverables.
- **Rationale**: `spec.md` explicitly sets `Implementation Intent: Runtime implementation`; docs-only output fails completion criteria.
- **Alternatives considered**:
  - Planning/docs-only completion for this phase: rejected by FR-018 and SC-006.
  - Partial ad-hoc validation: rejected because acceptance requires repository-standard `./tools/test_unit.sh`.
