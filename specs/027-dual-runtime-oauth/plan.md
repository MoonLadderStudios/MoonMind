# Implementation Plan: Dual OAuth Setup for Codex + Claude with Default Task Runtime

**Branch**: `027-dual-runtime-oauth` | **Date**: 2026-02-19 | **Spec**: [`spec.md`](./spec.md)  
**Input**: Feature specification from `/specs/027-dual-runtime-oauth/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Enable MoonMind workers to operate with both Codex OAuth and Claude OAuth in one environment by introducing Claude auth volume wiring, a dedicated Claude auth helper script, and runtime-aware preflight auth checks. Add a configurable default task runtime fallback for canonical queue jobs while preserving explicit runtime override precedence and existing codex-only behavior.

## Technical Context

**Language/Version**: Python 3.11, Bash, Docker Compose YAML  
**Primary Dependencies**: FastAPI settings stack (Pydantic), queue task normalization service, queue worker CLI preflight, Docker named volumes  
**Storage**: Docker named volumes for CLI auth persistence (`codex_auth_volume`, new Claude auth volume), `.env`/`.env-template` environment configuration  
**Testing**: `./tools/test_unit.sh` (unit coverage for settings, queue service defaults, and worker preflight behavior)  
**Target Platform**: Linux containers orchestrated by `docker compose`  
**Project Type**: Backend service + worker runtime + deployment tooling  
**Performance Goals**: Maintain current worker startup timing; auth checks remain fast, deterministic, and non-interactive  
**Constraints**: Backward compatibility for codex-only setups; no secrets in queue payloads; preserve explicit task runtime precedence  
**Scale/Scope**: Queue worker auth/bootstrap and task runtime defaulting only (no API schema migration required)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Initial Gate: PASS. `.specify/memory/constitution.md` remains template-only and does not define enforceable project principles.
- Runtime Scope Gate: PASS. Planned work includes production runtime code changes and validation tests.
- Post-Design Gate: PASS. Phase 0/1 artifacts map all functional requirements to implementation and validation tasks.

## Project Structure

### Documentation (this feature)

```text
specs/027-dual-runtime-oauth/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── worker-runtime-auth-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
tools/
├── auth-codex-volume.sh
└── auth-claude-volume.sh                  # new

moonmind/
├── config/settings.py                     # default task runtime + Claude auth path settings
├── agents/codex_worker/cli.py             # runtime-specific preflight auth checks
└── workflows/agent_queue/service.py       # default runtime fallback application

tests/
├── unit/agents/codex_worker/test_cli.py
├── unit/config/test_settings.py
└── unit/workflows/agent_queue/test_service_hardening.py

runtime/
├── docker-compose.yaml                    # Claude volume wiring + env propagation
└── .env-template                          # Claude auth defaults + task runtime default

README.md                                  # operator setup docs (two commands)
```

**Structure Decision**: Keep changes within existing runtime/config surfaces and add one new script, avoiding API contract expansion or worker architecture changes.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
