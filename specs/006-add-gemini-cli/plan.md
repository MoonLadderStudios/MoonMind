# Implementation Plan: Integration of Gemini CLI into Orchestrator and Worker Environments

**Branch**: `006-add-gemini-cli` | **Date**: 2025-11-23 | **Spec**: [specs/006-add-gemini-cli/spec.md](specs/006-add-gemini-cli/spec.md)
**Input**: Feature specification from `/specs/006-add-gemini-cli/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

The feature involves adding the official `@google/gemini-cli` tool to the Docker environments for the Orchestrator and Celery Worker services. This allows these services to leverage the Gemini model for natural language processing and task execution directly via the command line, mirroring the existing `codex` CLI integration.

## Technical Context

**Language/Version**: Node.js 20+ (for CLI runtime), Python 3.11 (existing service)
**Primary Dependencies**: `@google/gemini-cli` (npm package)
**Storage**: N/A
**Testing**: Unit tests (shell), Integration tests (container smoke tests)
**Target Platform**: Linux (Debian Bookworm based Docker images)
**Project Type**: Dockerized Service
**Performance Goals**: CLI startup < 1s, reliable execution
**Constraints**: Must align with existing `codex-cli` installation pattern (multi-stage build, stub fallback)
**Scale/Scope**: Integration into `api_service` Docker image (shared by orchestrator and celery)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Library-First**: N/A (Tool integration)
- **CLI Interface**: The feature *is* adding a CLI interface.
- **Test-First**: Verification tests defined in spec.
- **Integration Testing**: Docker-based smoke tests required.

## Project Structure

### Documentation (this feature)

```text
specs/006-add-gemini-cli/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
api_service/
├── Dockerfile           # Update to install @google/gemini-cli
└── scripts/             # (Optional) Helper scripts if needed
```

**Structure Decision**: Modify existing `api_service/Dockerfile` to include the new CLI tool, following the pattern established for `codex-cli`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | | |