# Implementation Plan: Auth Security Boundaries

**Branch**: `mm-335-fcb06df0` | **Date**: 2026-04-15 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/178-auth-security-boundaries/spec.md`

## Summary

Enforce and verify OAuth credential security boundaries for MM-335 by tightening provider-profile/OAuth management authorization, sanitizing workload logs and metadata before artifact/result publication, and proving Docker-backed workloads fail closed when managed-runtime auth volumes could be inherited implicitly. The implementation will use focused pytest coverage at the API router and workload contract/launcher boundaries, with integration-style tests for real serialization and publication shapes where possible.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK boundary models, existing Docker workload launcher abstractions  
**Storage**: Existing PostgreSQL/SQLite-compatible tables for provider profiles and OAuth sessions; no new persistent storage  
**Unit Testing**: `pytest` via `./tools/test_unit.sh`  
**Integration Testing**: Existing pytest integration tier via `./tools/test_integration.sh` for compose-backed checks when needed  
**Target Platform**: Linux containers under Docker Compose and MoonMind managed-agent worker containers  
**Project Type**: Web-service control plane plus workflow/runtime support libraries  
**Performance Goals**: Redaction and authorization checks must be bounded, deterministic, and negligible compared with existing API and workload launch latency  
**Constraints**: Do not expose raw credentials, raw auth-volume listings, token values, environment dumps, or private key blocks in persisted or browser-visible output; do not introduce compatibility aliases for internal contracts; do not add new credential-requiring workload profiles  
**Scale/Scope**: One security-boundary story covering API surfaces, workload launch contracts, artifact publication, and verification evidence for MM-335

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Work stays within existing API/router, runtime model, and Docker workload adapter boundaries.
- **II. One-Click Agent Deployment**: PASS. No new external services or required secrets are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Credential-boundary rules are provider-neutral while preserving Codex OAuth-specific source requirements.
- **IV. Own Your Data**: PASS. Sanitized refs and artifacts remain locally stored under operator-controlled infrastructure.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not alter skill contracts.
- **VI. Scientific Method / Verify**: PASS. The plan is test-first with boundary evidence for each source requirement.
- **VII. Runtime Configurability**: PASS. No hardcoded external provider behavior or new operator config is required.
- **VIII. Modular and Extensible Architecture**: PASS. Changes remain in router/service/model boundaries that already own provider profile, OAuth session, and workload behavior.
- **IX. Resilient by Default**: PASS. Fail-closed workload auth behavior and sanitized diagnostics improve recovery safety.
- **X. Continuous Improvement**: PASS. Verification produces deterministic evidence and final artifacts.
- **XI. Spec-Driven Development**: PASS. This plan follows the single-story spec.
- **XII. Canonical Documentation Separation**: PASS. Implementation notes remain under `specs/` and `docs/tmp`; canonical docs are not rewritten.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or hidden fallbacks are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/178-auth-security-boundaries/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── auth-security-boundaries.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   └── routers/
│       ├── oauth_sessions.py
│       └── provider_profiles.py
└── api/schemas_oauth_sessions.py

moonmind/
├── schemas/
│   └── workload_models.py
└── workloads/
    └── docker_launcher.py

tests/
├── unit/
│   ├── api_service/api/routers/test_oauth_sessions.py
│   ├── api_service/api/routers/test_provider_profiles.py
│   └── workloads/
│       ├── test_docker_workload_launcher.py
│       └── test_workload_contract.py
└── integration/
    └── services/temporal/workflows/
```

**Structure Decision**: Use existing API router tests for provider-profile and OAuth browser/control surfaces, existing workload model tests for fail-closed auth-volume validation, and Docker launcher unit tests for sanitized artifact/result publication. Add integration coverage only if a changed workflow/activity boundary is touched.

## Complexity Tracking

No constitution violations.
