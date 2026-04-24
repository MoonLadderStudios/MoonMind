# Implementation Plan: Claude Manual Auth API

**Branch**: `236-claude-manual-auth-api` | **Date**: 2026-04-22 | **Spec**: `specs/236-claude-manual-auth-api/spec.md`
**Input**: Single-story feature specification from `specs/236-claude-manual-auth-api/spec.md`

## Summary

Add the backend half of Claude Anthropic manual token enrollment for MM-447. The runtime story introduces a dedicated provider-profile manual-auth commit path that validates the submitted Claude token, stores token material only in Managed Secrets, converts the `claude_anthropic` profile to a secret-reference launch shape, syncs runtime-visible provider profile state, and returns only secret-free readiness metadata. Repo inspection shows this branch already contains the endpoint, secret upsert behavior, profile update behavior, `db://` slug resolution support, and focused unit/API boundary tests, so planning classifies the current implementation as verified and preserves final verification work rather than generating duplicate code tasks.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/provider_profiles.py` defines `/{profile_id}/manual-auth/commit`; `tests/unit/api_service/api/routers/test_provider_profiles.py` posts to the route | preserve route behavior | route/API unit |
| FR-002 | implemented_verified | route calls `_require_profile_management`; `test_claude_manual_auth_commit_rejects_non_owner_without_validating_or_persisting` covers unauthorized callers before validation/persistence | preserve provider-profile authorization | route/API unit |
| FR-003 | implemented_verified | `_require_claude_anthropic_profile` rejects non-Claude Anthropic rows; `test_claude_manual_auth_commit_rejects_unsupported_profile_without_persisting` covers no validation/persistence | preserve unsupported-profile guard | route/API unit |
| FR-004 | implemented_verified | `_looks_like_claude_manual_token` and `validate_claude_manual_token`; success test monkeypatches and asserts validation call | preserve validation boundary | route/API unit |
| FR-005 | implemented_verified | `_upsert_managed_secret` writes `ManagedSecret`; success test verifies stored ciphertext and response excludes token | preserve secret-only storage | route/API unit |
| FR-006 | implemented_verified | route sets `ProviderCredentialSource.SECRET_REF` and `RuntimeMaterializationMode.API_KEY_ENV`; success test verifies fetched profile fields | preserve profile shape | route/API unit |
| FR-007 | implemented_verified | route clears `volume_ref`/`volume_mount_path`, sets `secret_refs`, `clear_env_keys`, and `env_template`; success test verifies these fields | preserve launch-safe binding | route/API unit |
| FR-008 | implemented_verified | route calls `sync_provider_profile_manager`; success test monkeypatches and verifies runtime sync | preserve manager sync | route/API unit |
| FR-009 | implemented_verified | `ClaudeManualAuthCommitResponse` and success response include readiness metadata; test verifies connected, backing secret, launch-ready, and no token | preserve secret-free response | route/API unit |
| FR-010 | implemented_verified | route converts away from volume-backed fields; no OAuth session calls are used in the backend path | preserve separation from OAuth sessions | route/API unit + final verify |
| FR-011 | implemented_verified | malformed-token test proves failure response is generic and secret-free before persistence | preserve secret-free failures | route/API unit |
| FR-012 | implemented_verified | success and failure tests assert submitted token is absent from response/profile text; redaction allowlist preserves non-secret auth metadata only | preserve redaction behavior | route/API unit + final verify |
| FR-013 | implemented_verified | `DatabaseSecretResolver` supports `db://` slug refs; resolver unit test verifies slug resolution | preserve runtime secret resolution | unit |
| FR-014 | implemented_verified | MM-447 is present in orchestration input and this spec; final verification will check traceability | preserve issue key and source mappings | final verify |
| SCN-001 | implemented_verified | success route test exercises commit from volume-backed profile to secret-ref ready response | no new work | route/API unit |
| SCN-002 | implemented_verified | success route test fetches profile after commit and asserts no submitted token appears | no new work | route/API unit |
| SCN-003 | implemented_verified | resolver unit test covers `db://` resolution path produced by commit | no new work | unit |
| SCN-004 | implemented_verified | malformed-token route test covers secret-free rejection before persistence | no new work | route/API unit |
| SCN-005 | implemented_verified | route does not depend on `oauth_sessions.py`; success test starts from a volume-backed profile and verifies converted fields | no new work | route/API unit + final verify |
| SC-001 | implemented_verified | success route test verifies Managed Secret storage and token-free response | no new work | route/API unit |
| SC-002 | implemented_verified | success route test verifies profile fields and token absence after fetch | no new work | route/API unit |
| SC-003 | implemented_verified | malformed-token test verifies no persisted secret and generic failure | no new work | route/API unit |
| SC-004 | implemented_verified | route tests cover non-owner and unsupported-profile rejection before validation or secret persistence | preserve guard evidence | route/API unit + final verify |
| SC-005 | implemented_verified | resolver unit test verifies `db://` slug references | no new work | unit |
| SC-006 | implemented_verified | route is in provider profile router and does not invoke OAuth session surfaces | no new work | final verify |
| SC-007 | implemented_verified | MM-447 and DESIGN-REQ mappings are present in `spec.md`; verification will preserve them | no new work | final verify |
| DESIGN-REQ-002 | implemented_verified | dedicated provider-profile manual-auth route and no OAuth session finalization dependency | no new work | route/API unit + final verify |
| DESIGN-REQ-004 | implemented_verified | route writes `secret_ref`, `api_key_env`, secret ref, clear env keys, and env template | no new work | route/API unit |
| DESIGN-REQ-006 | implemented_verified | route does not require `volume_ref`, `volume_mount_path`, or `oauth_home`; it clears those fields | no new work | route/API unit |
| DESIGN-REQ-010 | implemented_verified | route enforces permission, validation, secret upsert, profile update, manager sync, and readiness response | no new work | route/API unit |
| DESIGN-REQ-012 | implemented_verified | tests assert token absence from response/profile/failure text; Managed Secret is the only raw token storage | no new work | route/API unit |
| DESIGN-REQ-014 | implemented_verified | backend endpoint/service behavior and manager sync are implemented and covered | no new work | route/API unit |

## Technical Context

**Language/Version**: Python 3.12 with Pydantic v2 models and SQLAlchemy async ORM 
**Primary Dependencies**: FastAPI router patterns, SQLAlchemy async session fixtures, Pydantic response models, `httpx`, existing Managed Secret and Provider Profile services, existing provider profile manager sync helper 
**Storage**: Existing `managed_secrets` and `managed_agent_provider_profiles` tables; no new persistent tables 
**Unit Testing**: `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py` and `./tools/test_unit.sh tests/unit/workflows/adapters/test_secret_redaction.py` for focused verification; final `./tools/test_unit.sh` 
**Integration Testing**: Route-level ASGI tests in `tests/unit/api_service/api/routers/test_provider_profiles.py` exercise the API/service boundary with a real test database; no compose-backed `integration_ci` test is required because no external service or Temporal workflow contract changed 
**Target Platform**: MoonMind FastAPI control plane and managed-runtime provider profile materialization 
**Project Type**: Backend API/service boundary 
**Performance Goals**: Manual auth commit remains one operator-triggered request and does not add background polling or runtime startup overhead beyond normal provider profile materialization 
**Constraints**: No raw token in provider profiles, workflow-shaped payloads, route responses, logs captured by tests, or validation failure messages; no reuse of volume-first OAuth session finalization; preserve existing Codex OAuth behavior 
**Scale/Scope**: One provider-profile route, one managed secret upsert path, provider profile manager sync, and runtime secret resolver support

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story keeps Claude auth as provider-profile orchestration and does not build a separate agent runtime.
- II. One-Click Agent Deployment: PASS. No new deployment prerequisites, services, or mandatory external storage.
- III. Avoid Vendor Lock-In: PASS. Claude-specific behavior stays behind provider-profile metadata and route boundaries.
- IV. Own Your Data: PASS. Token material is stored in operator-controlled Managed Secrets.
- V. Skills Are First-Class and Easy to Add: PASS. No executable or agent skill contract changes.
- VI. Replaceable Scaffolding: PASS. The flow is a thin service path over existing profile/secret contracts.
- VII. Runtime Configurability: PASS. Launch shape is profile-driven through secret refs and env templates.
- VIII. Modular Architecture: PASS. Changes stay inside provider profile API/service boundaries and secret resolution.
- IX. Resilient by Default: PASS. Unsupported profiles and invalid tokens fail fast with secret-free errors; profile sync uses the existing retry-safe manager boundary.
- X. Continuous Improvement: PASS. Evidence is captured in MoonSpec artifacts and tests.
- XI. Spec-Driven Development: PASS. This plan resumes the existing branch from the first missing MoonSpec stage and preserves MM-447 traceability.
- XII. Canonical Documentation Separation: PASS. Migration/runtime notes stay under `specs/` and `local-only handoffs`; canonical docs are not rewritten.
- XIII. Pre-Release Velocity: PASS. No compatibility alias is introduced for old internal contracts; the new path uses existing pre-release profile fields directly.

## Project Structure

### Documentation (this feature)

```text
specs/236-claude-manual-auth-api/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── claude-manual-auth-api.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/
└── provider_profiles.py

moonmind/workflows/adapters/
└── secret_boundary.py

moonmind/utils/
└── logging.py

tests/unit/api_service/api/routers/
└── test_provider_profiles.py

tests/unit/workflows/adapters/
└── test_secret_redaction.py

└── MM-447-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing provider profile API router and test module boundaries. Use route-level ASGI tests for the API/service persistence boundary and adapter unit tests for runtime secret-reference resolution.

## Complexity Tracking

No constitution violations.
