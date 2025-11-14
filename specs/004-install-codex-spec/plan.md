# Implementation Plan: Codex & Spec Kit Tooling Availability

**Branch**: `004-install-codex-spec` | **Date**: 2025-11-07 | **Spec**: [`specs/004-install-codex-spec/spec.md`](specs/004-install-codex-spec/spec.md)
**Input**: Feature specification from `/specs/004-install-codex-spec/spec.md`

MoonMind’s Spec workflow automation currently depends on Codex CLI and GitHub Spec Kit being present inside the `api_service` container image, yet the image does not install either CLI nor manage Codex’s approval policy. This plan scopes the Dockerfile changes, configuration management, and validation steps required to bundle both CLIs in the image, ensure Celery workers inherit them, and ship a managed `~/.codex/config.toml` with `approval_policy = "never"` so unattended Celery tasks never stall.

## Summary

Package Codex CLI and GitHub Spec Kit CLI directly into `api_service/Dockerfile`, bake a Codex config fragment that enforces `approval_policy = "never"`, and extend worker health checks plus docs so Celery-based Spec workflows always find the tooling and run without interactive approvals.

## Technical Context

**Language/Version**: Python 3.11 (per AGENTS.md)  
**Primary Dependencies**: FastAPI API service, Celery 5.4 worker, Codex CLI (npm package `@githubnext/codex-cli`), GitHub Spec Kit CLI (npm package `@githubnext/spec-kit`)  
**Storage**: PostgreSQL result backend, RabbitMQ broker, local filesystem volumes for artifacts  
**Testing**: pytest + docker-compose smoke tests; need container-level verification of packaged CLIs  
**Target Platform**: Containerized Linux (api_service + Celery workers share Docker image)  
**Project Type**: Backend service + worker  
**Performance Goals**: Maintain existing Celery throughput; ensure CLI installations add <5s to worker startup (**assumed**)  
**Constraints**: Non-interactive automation only; CLI installations must occur at image build time; `.codex/config.toml` must enforce `approval_policy = "never"` via a merge script that preserves other Codex settings  
**Scale/Scope**: Applies to every Spec workflow Celery worker (currently three Codex shards) and to any future worker derived from `api_service/Dockerfile`

Research outcomes:

1. Codex CLI will be installed from npm with a `CODEX_CLI_VERSION` build arg and verified via `codex --version`.  
2. GitHub Spec Kit CLI ships from npm with its own `SPEC_KIT_VERSION` build arg to keep worker behavior deterministic.  
3. `~/.codex/config.toml` will be merged from a template at entrypoint time to guarantee `approval_policy = "never"` without overwriting other keys.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` still contains placeholder headings with no enforceable principles, so there are currently no explicit gates to validate. Documenting this absence satisfies the pre-Phase-0 requirement; will re-check after design in case the constitution is populated before implementation.

## Project Structure

### Documentation (this feature)

```text
specs/004-install-codex-spec/
├── plan.md              # This file (/speckit.plan output)
├── research.md          # Phase 0 research (this run)
├── data-model.md        # Phase 1 deliverable
├── quickstart.md        # Phase 1 deliverable
├── contracts/           # Phase 1 API/CLI contracts
└── tasks.md             # Phase 2 deliverable (future /speckit.tasks)
```

### Source Code (repository root)

```text
api_service/
├── Dockerfile           # target image that must install Codex + Spec Kit CLIs
├── main.py              # FastAPI entrypoint (shares image with Celery)
└── pyproject/poetry     # Python dependencies already define runtime stack

celery_worker/
└── speckit_worker.py    # Runs on same image, consumes bundled CLIs

moonmind/workflows/speckit_celery/
├── tasks.py             # Invokes Codex/Spec Kit commands
└── job_container.py     # Defines workspace + env for CLI usage

docs/SpecKitAutomation*.md
└── Operational runbooks referencing expected tooling (update if needed)

specs/004-install-codex-spec/
└── Documentation artifacts for this feature (spec, plan, research, etc.)
```

**Structure Decision**: Single backend repository with FastAPI API + Celery worker sharing one Docker image; work is confined to the shared Dockerfile, worker runtime helpers, and docs/spec assets.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | Constitution currently has no actionable gates | N/A |
