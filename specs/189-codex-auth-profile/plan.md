# Implementation Plan: Codex Auth Volume Profile Contract

**Branch**: `189-codex-auth-profile` | **Date**: 2026-04-16 | **Spec**: `specs/189-codex-auth-profile/spec.md`

## Input

Single-story runtime feature specification from `specs/189-codex-auth-profile/spec.md`, generated from Jira issue `MM-355` and the canonical preset brief preserved in the spec.

## Summary

This story implements the Codex OAuth Provider Profile contract at the profile registration, update, validation, and serialization boundaries. The implementation should preserve durable auth-volume refs and slot policy metadata while keeping credential contents out of operator-facing responses, workflow payloads, logs, artifacts, and profile snapshots. Validation follows TDD: focused unit tests first, then hermetic integration coverage for the OAuth/profile boundary when Docker-backed integration infrastructure is available.

## Technical Context

- Language/version: Python 3.12; TypeScript only if an implementation task later discovers a Mission Control display change is required.
- Primary dependencies: Pydantic v2 models, FastAPI routers, SQLAlchemy models, Temporal Python SDK workflow/activity helpers, pytest, and existing MoonMind provider-profile and OAuth-session services.
- Storage: existing provider profile, OAuth session, workflow, and projection records only; no new persistent database tables are planned.
- Unit testing: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused pytest targets for provider profile schemas, OAuth-session profile registration, and profile serialization.
- Integration testing: `./tools/test_integration.sh` for compose-backed `integration_ci` coverage when Docker is available; required coverage target is the OAuth/profile workflow boundary.
- Target platform: MoonMind API service, Temporal worker/runtime services, provider profile manager, OAuth session workflows, and managed Codex profile selection.
- Project type: backend orchestration/runtime feature with API and workflow-facing contract surfaces.
- Performance goals: validation and serialization remain deterministic and low overhead relative to existing profile reads, writes, and workflow payload construction.
- Constraints: preserve `MM-355` traceability; fail fast on missing or unsafe Codex OAuth profile refs; do not expose raw credential contents, token values, auth file payloads, raw auth-volume listings, or environment dumps; keep Claude/Gemini task-scoped managed-session parity out of scope.
- Scale/scope: one independently testable story, dependencies: none.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The plan tightens MoonMind profile and workflow boundaries without rebuilding Codex authentication behavior.
- II One-Click Agent Deployment: PASS. Uses existing local-first Docker Compose and existing test tooling.
- III Avoid Vendor Lock-In: PASS. Codex-specific behavior stays in provider profile/runtime metadata and does not force other runtimes into the Codex shape.
- IV Own Your Data: PASS. Credential material remains operator-controlled in durable auth volumes and is represented by refs only.
- V Skills Are First-Class: PASS. No executable skill contract changes are required.
- VI Bittersweet Lesson: PASS. The story reinforces thin contracts and test evidence around volatile provider auth integration.
- VII Runtime Configurability: PASS. Profile behavior is driven by stored profile metadata and runtime configuration rather than hardcoded credential contents.
- VIII Modular Architecture: PASS. Work stays in established Provider Profile, OAuth session, schema, and workflow/activity boundaries.
- IX Resilient by Default: PASS. Unsafe profile input fails fast; workflow-facing payloads remain compact and secret-free.
- X Continuous Improvement: PASS. Verification evidence and blockers are captured in MoonSpec artifacts.
- XI Spec-Driven Development: PASS. This plan is derived from an isolated one-story spec with traceability to `MM-355`.
- XII Canonical Docs Separation: PASS. Planning and implementation tracking remain under `specs/` and `docs/tmp`, not canonical docs.
- XIII Pre-Release Compatibility: PASS. Unsupported internal contract values should fail through validation; no compatibility aliases or hidden transforms are planned.

## Project Structure

- Spec: `specs/189-codex-auth-profile/spec.md`
- Plan: `specs/189-codex-auth-profile/plan.md`
- Research: `specs/189-codex-auth-profile/research.md`
- Data model: `specs/189-codex-auth-profile/data-model.md`
- Contract: `specs/189-codex-auth-profile/contracts/codex-auth-profile.md`
- Quickstart: `specs/189-codex-auth-profile/quickstart.md`
- Likely production touchpoints:
  - `api_service/api/routers/provider_profiles.py`
  - `api_service/api/routers/oauth_sessions.py`
  - `api_service/api/schemas_oauth_sessions.py`
  - `api_service/services/provider_profile_service.py`
  - `moonmind/schemas/agent_runtime_models.py`
  - `moonmind/workflows/temporal/activities/oauth_session_activities.py`
  - `moonmind/workflows/temporal/workflows/oauth_session.py`
- Unit test targets:
  - `tests/unit/api_service/api/routers/test_provider_profiles.py`
  - `tests/unit/api/routers/test_oauth_sessions.py`
  - `tests/unit/auth/test_oauth_session_activities.py`
  - `tests/unit/schemas/test_agent_runtime_models.py`
- Integration test targets:
  - `tests/integration/temporal/test_oauth_session.py`

## Test Strategy

- Unit strategy: add red-first focused tests for Codex OAuth profile shape validation, missing/blank auth-volume refs, slot policy preservation, provider profile response redaction, OAuth finalization profile registration/update, and workflow/profile snapshot redaction. Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py` during iteration and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before final verification.
- Integration strategy: add or update hermetic integration coverage for the OAuth verification to Provider Profile registration boundary and any Temporal activity/workflow payload shape used by the worker binding. Run `./tools/test_integration.sh` when Docker is available; required coverage target is `tests/integration/temporal/test_oauth_session.py`. If the Docker socket is unavailable in the managed-agent container, record the exact blocker in verification output rather than treating integration as passed.

## Complexity Tracking

None.
