# Implementation Plan: OAuth Terminal Docker Verification

**Branch**: `194-oauth-terminal-docker-verification` | **Date**: 2026-04-16 | **Spec**: `specs/194-oauth-terminal-docker-verification/spec.md` 
**Input**: Single-story feature specification from `specs/194-oauth-terminal-docker-verification/spec.md`

## Summary

MM-363 is a runtime verification-closure story for OAuthTerminal managed-session auth behavior. The primary work is to run Docker-backed hermetic integration evidence for managed Codex launch volume targeting, per-run Codex home seeding, and OAuth terminal auth runner/PTY bridge lifecycle, then update prior verification reports only when passing evidence exists. If the active runtime lacks Docker, the implementation must record the exact blocker and leave closure incomplete.

**Note**: The standard prerequisite script rejects the managed branch name `mm-363-cf9908a4`; use `SPECIFY_FEATURE=194-oauth-terminal-docker-verification` when running Moon Spec helper scripts in this workspace.

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, Docker Compose, existing OAuth session workflow/activity catalog, managed Codex session controller/runtime helpers 
**Storage**: Existing workflow artifacts and verification reports only; no new persistent storage 
**Unit Testing**: Focused OAuthTerminal and managed Codex unit targets from `quickstart.md` when harness fixes are needed 
**Integration Testing**: `./tools/test_integration.sh` for compose-backed `integration_ci` coverage; focused direct integration targets may be used during diagnosis before the full script 
**Target Platform**: MoonMind API and Temporal worker/runtime containers on Linux with Docker available for hermetic integration 
**Project Type**: Backend/runtime verification story with Temporal, Docker, and artifact/report boundaries 
**Performance Goals**: Integration verification should complete within the existing hermetic suite timeout budget; no new long-running workflow boundary tests should be added to required CI 
**Constraints**: Preserve `MM-363` traceability; do not claim report closure without Docker-backed evidence; do not copy credentials, tokens, auth volume listings, or sensitive environment dumps into reports; preserve existing runtime ownership boundaries 
**Scale/Scope**: One independently testable verification story covering OAuthTerminal managed-session auth behavior and the existing verification reports for specs 175, 180, and 183

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS. The story verifies existing managed runtime and OAuth terminal orchestration rather than replacing provider behavior.
- II One-Click Agent Deployment: PASS. The required path uses the repo's existing Docker Compose-backed integration runner.
- III Avoid Vendor Lock-In: PASS. Codex is the current concrete managed-session target, with provider-specific behavior behind existing runtime/provider boundaries.
- IV Own Your Data: PASS. Evidence remains in repo artifacts and reports; credentials stay in auth volumes and are not copied into reports.
- V Skills Are First-Class: PASS. No executable skill contracts or skill materialization behavior are changed.
- VI Bittersweet Lesson: PASS. The work verifies contracts and records evidence so obsolete blockers can be removed only when proven.
- VII Runtime Configurability: PASS. Docker, volume names, and auth mount paths remain governed by existing environment/configuration.
- VIII Modular Architecture: PASS. Any harness fixes must stay within existing OAuth session, managed-session controller/runtime, or integration test boundaries.
- IX Resilient by Default: PASS. Verification records exact blockers and avoids false closure when required evidence is unavailable.
- X Continuous Improvement: PASS. Prior ADDITIONAL_WORK_NEEDED reports are updated only with evidence or precise blockers.
- XI Spec-Driven Development: PASS. MM-363 has isolated spec and planning artifacts before runtime verification closure.
- XII Canonical Docs Separation: PASS. Canonical docs remain source requirements; migration/run evidence stays under `specs/` and `local-only handoffs`.
- XIII Pre-Release Compatibility: PASS. No compatibility aliases are planned; existing internal contract behavior is verified as-is.

## Project Structure

### Documentation (this feature)

```text
specs/194-oauth-terminal-docker-verification/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── oauth-terminal-docker-verification.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/runtime/
├── managed_session_controller.py
├── codex_session_runtime.py
└── terminal_bridge.py

moonmind/workflows/temporal/activities/
└── oauth_session_activities.py

tests/integration/services/temporal/
├── test_codex_session_runtime.py
└── test_codex_session_task_creation.py

tests/integration/temporal/
└── test_oauth_session.py

specs/
├── 175-launch-codex-auth-materialization/verification.md
├── 180-codex-volume-targeting/verification.md
└── 183-oauth-terminal-flow/verification.md
```

**Structure Decision**: Use the existing runtime and integration test layout. This story should not add new runtime packages, APIs, database tables, or frontend surfaces unless a small test harness fix is required to observe the documented contract.

## Test Strategy

- Unit strategy: run the focused unit command defined in `quickstart.md` only if diagnosis identifies a harness or runtime behavior gap that can be validated without Docker.
- Integration strategy: run `./tools/test_integration.sh` in a Docker-enabled environment. If it fails because `/var/run/docker.sock` is missing, record that blocker and do not update prior verification reports to closure. If Docker is available and failures are specific to OAuthTerminal managed-session auth behavior, make the smallest runtime or test harness fix and rerun.

## Complexity Tracking

None.
