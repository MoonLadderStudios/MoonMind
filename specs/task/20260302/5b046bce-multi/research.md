# Research: Manifest Queue Alignment and Hardening

## Decision 1: Enforce manifest run actions at request schema boundary
- **Decision**: Validate `ManifestRunRequest.action` to allow only `plan` and `run`, while normalizing case/whitespace.
- **Rationale**: Aligns with MoonMind fail-fast strategy for unsupported runtime values and prevents invalid requests from reaching queue submission.
- **Alternatives considered**:
  - Keep validation only in `manifest_contract` via queue service path. Rejected because errors occur later than necessary and request-level contract remains ambiguous.
  - Add router-only manual validation. Rejected because schema-level validation is more reusable and testable.

## Decision 2: Treat specs/028 as a live alignment artifact, not a greenfield implementation plan
- **Decision**: Rewrite `specs/028-manifest-queue` to reflect already-implemented runtime baseline and focus tasks on remaining delta work.
- **Rationale**: Current codebase already includes manifest job type handling, contract normalization, registry endpoints, and test coverage; stale tasks misrepresent project state.
- **Alternatives considered**:
  - Leave historical tasks unchanged and only mark complete. Rejected because stale paths/contract details continue to cause drift.
  - Create a brand-new spec ID for alignment only. Rejected because the user explicitly requested updating `specs/028-manifest-queue`.

## Decision 3: Preserve manifest contract/runtime behavior while hardening request semantics
- **Decision**: Do not modify `normalize_manifest_job_payload()` behavior or queue service normalization pathways in this scope.
- **Rationale**: Existing behavior already covers hashing, capability derivation, source normalization, secret scanning/ref extraction, and payload sanitization; changing it would expand risk beyond requested alignment.
- **Alternatives considered**:
  - Expand scope into additional manifest execution features. Rejected due mismatch with requested in-place alignment and task completion objective.

## Decision 4: Keep runtime mode behavior explicit in plan artifacts
- **Decision**: Record this feature as runtime-mode scoped (runtime code + tests), not docs-only mode, and keep plan/task language aligned with that scope guard.
- **Rationale**: The feature input explicitly requires production runtime changes and validation tests; planning artifacts must not imply documentation-only delivery.
- **Alternatives considered**:
  - Treat the step as docs-only alignment. Rejected because it would conflict with the runtime scope guard and constitutional spec-alignment requirements.
