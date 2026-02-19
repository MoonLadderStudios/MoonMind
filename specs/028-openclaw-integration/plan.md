# Implementation Plan: OpenClaw Dedicated Integration

**Branch**: `028-openclaw-integration` | **Date**: 2026-02-19 | **Spec**: specs/028-openclaw-integration/spec.md
**Input**: Feature specification from `/specs/028-openclaw-integration/spec.md`

## Summary

Deliver the OpenClaw integration in staged increments. This branch provides the operator bootstrap script (`tools/bootstrap-openclaw-codex-volume.sh`), `.env-template` surface area, and design/runbook documentation (`docs/OpenClawIntegration.md`) for a dedicated Codex auth volume and model-lock strategy. Compose wiring, image/entrypoint, adapter enforcement code, and verification tests land in follow-up implementation branches.

## Technical Context

**Language/Version**: Python 3.11 (OpenClaw runtime + `api_service` utilities), Bash (bootstrap script)  
**Primary Dependencies**: Docker Compose v2, Codex CLI, Celery-compatible logging/metrics hooks, `api_service/scripts/ensure_codex_config.py`  
**Storage**: Docker named volumes `openclaw_codex_auth_volume`, `openclaw_data` (no database schema changes)  
**Testing**: `./tools/test_unit.sh` (Pytest suite) with new unit tests for model-lock adapter + bootstrap script behaviors (use `pytest` invoked via wrapper)  
**Target Platform**: Linux containers (WSL + native) attached to external `local-network`  
**Project Type**: Multi-service backend composed via Docker Compose  
**Performance Goals**: N/A – focus on deterministic startup + auth validation  
**Constraints**: Dedicated auth volume (never shared), single model enforcement (`force`/`reject` modes), profile-gated deployment, non-root `app` user, reuse existing Codex config enforcement, no disruption to existing workers  
**Scale/Scope**: Single optional service + helper script + accompanying docs/tests; no UI features or additional queues

## Constitution Check

The repository constitution file is currently a placeholder with no ratified principles. We will still honor standard MoonMind guardrails (test coverage via `./tools/test_unit.sh`, CLI-first tooling, and documentation) to avoid blocking downstream constitution checks. **Status: PASS (no actionable violations).**

## Project Structure

### Documentation (this feature)

```text
specs/028-openclaw-integration/
├── plan.md              # This plan
├── research.md          # Phase 0 decisions & trade-offs
├── data-model.md        # Logical config entities (service, volume, policy)
├── quickstart.md        # Runbook for enabling OpenClaw
├── contracts/
│   └── openclaw-compose.yaml   # Compose/API contract snippets
└── spec.md
```

### Source Code (repository root)

```text
repo/
├── docs/OpenClawIntegration.md              # Published integration design & runbook
├── docker-compose.yaml                      # Adds profile-gated service + volumes
├── .env-template                            # Adds OpenClaw-specific vars
├── services/openclaw/
│   ├── Dockerfile                           # Mirrors worker base image w/ OpenClaw bits
│   └── entrypoint.sh                        # Validates model + Codex auth before start
├── api_service/scripts/ensure_codex_config.py# Reused without modification (idempotent)
├── tools/bootstrap-openclaw-codex-volume.sh # New script to clone + verify auth volume
├── tests/openclaw/
│   ├── __init__.py
│   └── test_model_lock.py                   # Covers force/reject behavior
└── tests/tools/
    └── test_bootstrap_openclaw_volume.py    # Script argument/error handling via pytest
```

**Structure Decision**: Reuse the existing backend/service layout by introducing a new `services/openclaw/` component, documentation under `docs/`, and targeted tests under `tests/openclaw/` + `tests/tools/` to keep scope localized.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | Feature fits existing repo complexity budget | N/A |
