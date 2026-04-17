# Implementation Plan: Managed GitHub Secret Materialization

**Branch**: `201-managed-github-secret-materialization` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/201-managed-github-secret-materialization/spec.md`

## Summary

Refactor managed Codex session GitHub authentication so launch request durable data carries a non-sensitive GitHub credential descriptor while token resolution happens immediately before host git workspace preparation. The implementation adds typed request data, activity/controller boundary handling, launch-scoped credential helper materialization, redaction-safe diagnostics, and unit plus integration-style boundary tests.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK activity boundary, existing managed secret resolvers, existing Docker managed-session controller  
**Storage**: Existing managed secret store only; no new persistent tables  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: pytest integration boundary tests via `./tools/test_integration.sh` where Docker is available; focused workflow/activity boundary tests in the unit suite for managed-agent containers  
**Target Platform**: Linux managed-agent worker and Docker-backed managed Codex session runtime  
**Project Type**: Backend orchestration/runtime service  
**Performance Goals**: No additional network or database calls when no GitHub credential is required; one late credential resolution per launch requiring GitHub auth  
**Constraints**: No raw GitHub credentials in workflow history, durable launch payloads, container environment, artifacts, logs, or diagnostics; preserve in-flight worker-bound invocation compatibility; preserve username-free repo input  
**Scale/Scope**: One managed Codex session launch path and its host git workspace preparation commands

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change keeps Codex as the managed runtime and only adjusts MoonMind's launch/materialization boundary.
- **II. One-Click Agent Deployment**: PASS. Local-first `GITHUB_TOKEN`/`GITHUB_PAT` managed secrets remain supported without new infrastructure.
- **IV. Own Your Data**: PASS. Credentials remain operator-managed and are represented as references in durable contracts.
- **VII. Powerful Runtime Configurability**: PASS. Existing env/config and managed secret precedence remain observable through non-sensitive descriptors.
- **IX. Resilient by Default**: PASS. Boundary tests cover the launch invocation shape and redaction-safe failures.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime work lives in code/tests/spec artifacts; canonical docs are only source requirements.
- **XIII. Pre-Release Velocity**: PASS. Superseded raw-token launch behavior is removed from the managed-session contract path instead of hidden behind compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/201-managed-github-secret-materialization/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-github-secret-materialization.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── managed_session_models.py
└── workflows/
    └── temporal/
        ├── activity_runtime.py
        └── runtime/
            ├── managed_api_key_resolve.py
            └── managed_session_controller.py

tests/
├── unit/
│   ├── schemas/test_managed_session_models.py
│   ├── workflows/temporal/test_agent_runtime_activities.py
│   └── services/temporal/runtime/test_managed_session_controller.py
└── integration/services/temporal/
```

**Structure Decision**: Reuse the existing managed-session schema, activity boundary, controller, and pytest test locations because the story changes an existing runtime launch path rather than adding a new subsystem.

## Complexity Tracking

No constitution violations.
