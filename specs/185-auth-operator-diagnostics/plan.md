# Implementation Plan: Auth Operator Diagnostics

**Branch**: `185-auth-operator-diagnostics` | **Date**: 2026-04-16 | **Spec**: `specs/185-auth-operator-diagnostics/spec.md`

## Input

Single-story feature specification from `specs/185-auth-operator-diagnostics/spec.md` generated from Jira issue `MM-336` and the preserved preset brief in `spec.md` (Input).

## Summary

This story adds safe operator diagnostics to existing OAuth session and managed Codex launch projections. The implementation stays inside established API schema/router and Temporal activity/controller boundaries: OAuth APIs return a compact provider profile summary, and managed session launch returns non-secret auth diagnostics for successful and failed materialization. Unit tests cover redaction and schema behavior; integration-style boundary tests cover the activity/controller launch path.

## Technical Context

- Language/version: Python 3.12.
- Primary dependencies: Pydantic v2, FastAPI routers, Temporal activity runtime contracts, pytest, existing managed-session controller and provider profile materializer.
- Storage: existing OAuth session, provider profile, managed-session records, and artifact/log refs only; no new persistent storage.
- Unit testing: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification; focused Python tests through `./tools/test_unit.sh --python-only ...`.
- Integration testing: `./tools/test_integration.sh` for compose-backed `integration_ci` when Docker is available; focused boundary coverage can run in unit files when Docker is mocked.
- Target platform: MoonMind API service, Temporal worker activity runtime, managed Codex session launch boundary, and Mission Control consumers of those projections.
- Project type: backend API and orchestration runtime.
- Performance goals: diagnostics are derived from already-available request/profile/session metadata and add no network calls on the launch hot path.
- Constraints: preserve MM-336 traceability; do not expose credentials, raw auth-volume listings, runtime-home contents, terminal scrollback, or environment dumps; keep large content out of workflow history.
- Scale/scope: one independently testable story; dependencies: STORY-001 and STORY-003.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The plan projects MoonMind-managed metadata without reimplementing provider auth runtimes.
- II One-Click Agent Deployment: PASS. Uses existing local-first API/runtime behavior and test tooling.
- III Avoid Vendor Lock-In: PASS. Diagnostics use provider profile and managed-session abstractions rather than provider-specific secrets.
- IV Own Your Data: PASS. Operator-visible evidence stays in local API/runtime/artifact surfaces.
- V Skills Are First-Class: PASS. No executable skill contract changes.
- VI Bittersweet Lesson: PASS. Adds thin projections over existing runtime boundaries.
- VII Runtime Configurability: PASS. Diagnostics reflect runtime/profile/request metadata and existing configuration.
- VIII Modular Architecture: PASS. Work stays in API schemas/router helpers, Temporal activity runtime, and managed-session controller tests.
- IX Resilient by Default: PASS. Failure diagnostics are sanitized and classified without hiding failure state.
- X Continuous Improvement: PASS. Verification evidence is captured in spec artifacts.
- XI Spec-Driven Development: PASS. Implementation follows this single-story MoonSpec.
- XII Canonical Docs Separation: PASS. Work tracking remains under specs/local-only handoffs.
- XIII Pre-Release Compatibility: PASS. Internal contracts are updated directly without compatibility aliases.

## Project Structure

- Spec: `specs/185-auth-operator-diagnostics/spec.md`
- Plan: `specs/185-auth-operator-diagnostics/plan.md`
- Research: `specs/185-auth-operator-diagnostics/research.md`
- Data model: `specs/185-auth-operator-diagnostics/data-model.md`
- Contract: `specs/185-auth-operator-diagnostics/contracts/auth-operator-diagnostics.md`
- Quickstart: `specs/185-auth-operator-diagnostics/quickstart.md`
- Likely production touchpoints: `api_service/api/schemas_oauth_sessions.py`, `api_service/api/routers/oauth_sessions.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- Unit test targets: `tests/unit/api_service/api/routers/test_oauth_sessions.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, `tests/unit/services/temporal/runtime/test_managed_session_controller.py`

## Test Strategy

- Unit strategy: add red-first tests for OAuth session profile summaries, secret/path redaction, auth diagnostics on managed session launch success, and sanitized launch failure classification. Run focused tests during iteration and the full unit suite before final verification when feasible.
- Integration strategy: use mocked controller/activity boundary tests for deterministic launch coverage; run `./tools/test_integration.sh` only if Docker is available, otherwise record the exact Docker socket blocker.

## Complexity Tracking

None.
