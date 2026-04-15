# Implementation Plan: Launch Codex Auth Materialization

**Branch**: `175-launch-codex-auth-materialization` | **Date**: 2026-04-15 | **Spec**: `specs/175-launch-codex-auth-materialization/spec.md`

## Summary

Managed Codex sessions must launch with task-scoped runtime state while using OAuth-backed Provider Profiles as credential sources. The existing adapter and controller already pass compact profile metadata, conditionally mount the auth volume at `MANAGED_AUTH_VOLUME_PATH`, seed eligible auth entries into the per-run Codex home, and start Codex App Server with that home. This implementation closes the remaining in-container validation gap by rejecting auth-target and Codex-home path equality at the runtime materialization boundary and adds focused tests around the adapter and runtime boundaries.

## Technical Context

- Language/version: Python 3.12
- Primary dependencies: Pydantic managed-session models, Temporal activity/adapter boundary, Docker-backed managed session controller, Codex App Server runtime client
- Storage: shared `agent_workspaces` volume for per-run workspace/session/artifact/Codex home paths; durable Codex auth volume as source-only credential store
- Unit testing: `./tools/test_unit.sh` with pytest
- Integration testing: `./tools/test_integration.sh` for compose-backed `integration_ci` suite when Docker is available
- Target platform: MoonMind managed-agent worker and managed Codex session container
- Constraints: no raw credentials in workflow payloads/logs/artifacts; auth volume target must be explicit and distinct; runtime must fail fast on unsafe path shape
- Scale/scope: one managed Codex session launch path and session runtime materialization boundary

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. Keeps Codex App Server as provider runtime and only controls launch/materialization boundaries.
- II One-Click Agent Deployment: PASS. Uses existing compose-backed volumes and local test runner.
- III Avoid Vendor Lock-In: PASS. Codex-specific behavior remains in Codex adapter/runtime boundaries.
- IV Own Your Data: PASS. Credentials remain in operator-controlled volumes and task workspaces.
- V Skills Are First-Class: PASS. No executable skill contract changes.
- VI Bittersweet Lesson: PASS. Thin validation added at runtime boundary with tests.
- VII Runtime Configurability: PASS. Uses Provider Profile metadata and configured volume mount target.
- VIII Modular Architecture: PASS. Changes stay in adapter tests and Codex session runtime.
- IX Resilient by Default: PASS. Unsafe launch state fails fast before session startup.
- X Continuous Improvement: PASS. Verification evidence is recorded in tasks and final report.
- XI Spec-Driven Development: PASS. This feature directory tracks spec, plan, tasks, implementation, and verification.
- XII Canonical Docs Separation: PASS. No migration checklist is added to canonical docs.
- XIII Pre-Release Compatibility: PASS. No compatibility alias or fallback is introduced.

## Project Structure

- `moonmind/workflows/adapters/codex_session_adapter.py`: builds launch payload from selected Provider Profile
- `moonmind/workflows/temporal/runtime/managed_session_controller.py`: validates launch request and mounts workspace/auth volumes
- `moonmind/workflows/temporal/runtime/codex_session_runtime.py`: validates in-container runtime paths, seeds auth entries, starts Codex App Server
- `tests/unit/workflows/adapters/test_codex_session_adapter.py`: adapter boundary tests
- `tests/unit/services/temporal/runtime/test_managed_session_controller.py`: launcher boundary tests
- `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`: in-container runtime tests

## Research

See `research.md`.

## Data Model

See `data-model.md`.

## Contracts

See `contracts/managed-codex-auth-materialization.md`.

## Test Strategy

- Unit: verify adapter launch payload for OAuth-backed profile, controller auth mount separation, session runtime path rejection, one-way credential seeding, and `CODEX_HOME` app-server environment.
- Integration: use existing compose-backed managed Codex session launch tests when Docker is available; no new credentialed provider verification is required.

## Complexity Tracking

None.
