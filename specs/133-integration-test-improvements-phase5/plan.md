# Implementation Plan: Integration Test Improvements — Phase 5 (Repo Conventions & Specs)

**Branch**: `133-integration-test-improvements-phase5` | **Date**: 2026-04-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/133-integration-test-improvements-phase5/spec.md`

## Summary

Phase 5 closes the documentation and convention gaps left by Phases 1–4 of the integration test taxonomy effort. The technical work is purely additive and documentary: add the `integration_ci` marker to ~15+ hermetic integration test files that lack it, create a PowerShell provider-verification script as the counterpart to `test_jules_provider.sh`, update AGENTS.md to explicitly document the hermetic-integration vs. provider-verification distinction, and update spec docs that still describe Jules as "required compose-backed integration." No test logic, assertions, or CI workflow YAMLs change.

## Technical Context

**Language/Version**: Python 3.12 (pytest markers), Bash, PowerShell
**Primary Dependencies**: pytest (markers already defined in `pyproject.toml`), Docker Compose (for test runner scripts)
**Storage**: N/A — no data storage changes
**Testing**: pytest markers (`integration_ci`, `provider_verification`, `jules`, `requires_credentials`) — all already registered in `pyproject.toml` from Phase 1
**Target Platform**: Linux/macOS (Bash scripts), Windows (PowerShell script)
**Project Type**: Single project (Python backend + Temporal workflows)
**Performance Goals**: N/A — no performance-sensitive code changes
**Constraints**: No changes to test logic, CI YAMLs, or `pyproject.toml` marker definitions. Marker additions only (pytest decorator `@pytest.mark.integration_ci`).
**Scale/Scope**: ~31 integration test files to evaluate; 23 files received the `integration_ci` marker (bringing the total to 27); 1 new PowerShell script; 1 AGENTS.md section update; 2 doc updates.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | **PASS** | No orchestration changes |
| II. One-Click Agent Deployment | **PASS** | No compose changes |
| III. Avoid Vendor Lock-In | **PASS** | Jules provider script is adapter-neutral; mirrors existing bash script |
| IV. Own Your Data | **PASS** | No data ingestion changes |
| V. Skills Are First-Class | **PASS** | No skill system changes |
| VI. Bittersweet Lesson | **PASS** | No scaffolding changes |
| VII. Powerful Runtime Configurability | **PASS** | Config follows existing `MOONMIND_DOCKER_NETWORK` pattern |
| VIII. Modular and Extensible | **PASS** | New script mirrors existing structure |
| IX. Resilient by Default | **PASS** | Provider script fails fast on missing credentials (mirrors bash) |
| X. Facilitate Continuous Improvement | **PASS** | No run outcome changes |
| XI. Spec-Driven Development | **PASS** | This plan is driven by the spec |
| XII. Canonical Documentation | **PASS** | Updates to AGENTS.md and docs are declarative desired-state docs |
| XIII. Pre-Release Velocity | **PASS** | No compatibility shims; clean additive changes |

## Project Structure

### Documentation (this feature)

```text
specs/133-integration-test-improvements-phase5/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
tools/
├── test-provider.ps1                      # NEW: PowerShell provider verification script
├── test_integration.sh                    # MODIFIED: no changes (already correct)
└── test_jules_provider.sh                 # MODIFIED: no changes (already correct)

AGENTS.md                                  # MODIFIED: update Testing Instructions section

tests/integration/                         # MODIFIED: add @pytest.mark.integration_ci to ~15-20 files
├── temporal/
│   ├── test_compose_foundation.py
│   ├── test_execution_rescheduling.py
│   ├── test_integrations_monitoring.py
│   ├── test_interventions_temporal.py
│   ├── test_live_logs_performance.py
│   ├── test_managed_runtime_live_logs.py
│   ├── test_manifest_ingest_runtime.py
│   ├── test_namespace_retention.py
│   ├── test_oauth_session.py
│   └── test_upgrade_rehearsal.py
├── services/temporal/workflows/
│   └── test_agent_run.py
├── workflows/temporal/
│   ├── test_schedule_timezone_handling.py
│   └── test_task_5_14.py
└── workflows/temporal/workflows/
    ├── test_run_agent_dispatch.py
    └── test_run.py

docs/Temporal/
├── ActivityCatalogAndWorkerTopology.md    # MODIFIED: remove Jules as "required compose-backed"
└── IntegrationsMonitoringDesign.md        # MODIFIED: clarify Jules is optional provider
```

**Structure Decision**: Single-project layout. Changes span 4 areas: (1) a new tool script, (2) AGENTS.md documentation, (3) pytest marker additions to existing test files, and (4) doc clarifications. No new directories or modules.

## Complexity Tracking

No violations. All changes are additive markers, documentation, and one mirror script.
