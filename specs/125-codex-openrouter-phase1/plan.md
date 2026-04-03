# Implementation Plan: Codex CLI OpenRouter Phase 1

**Branch**: `125-codex-openrouter-phase1` | **Date**: 2026-04-03 | **Spec**: [`spec.md`](./spec.md)

## Summary

Implement Phase 1 of `docs/ManagedAgents/CodexCliOpenRouter.md` by finishing the launch-contract plumbing required for a managed `codex_cli` run to target `provider_id=openrouter`. The implementation stays inside the existing provider-profile and managed-runtime architecture: richer provider-profile fields flow from DB to adapter to launcher, the materializer becomes path-aware for generated config bundles, `CODEX_HOME` is set from the profile, and auto-seeding can create the reference OpenRouter profile.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy, Pydantic v2, Temporal runtime adapters  
**Storage**: Provider profile DB rows plus per-run filesystem support directory under `.moonmind/`  
**Testing**: `./tools/test_unit.sh` plus focused unit coverage for adapter/materializer/strategy/auto-seed  
**Target Platform**: MoonMind managed worker containers and local dev startup  
**Project Type**: Backend orchestration/runtime  

## Constitution Check

| Principle | Status | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Extends the existing `codex_cli` runtime instead of adding a new runtime. |
| II. One-Click Agent Deployment | PASS | Auto-seeding enables local startup when `OPENROUTER_API_KEY` is provided. |
| III. Avoid Vendor Lock-In | PASS | OpenRouter remains a provider profile, not a core runtime specialization. |
| IV. Own Your Data | PASS | Secrets remain refs; generated config is per-run support state. |
| V. Skills Are First-Class | N/A | No skill-runtime behavior changes. |
| VI. Design for Deletion | PASS | Adds reusable materialization primitives instead of OpenRouter-only launch code. |
| VII. Powerful Runtime Configurability | PASS | Launch shaping remains data-driven via provider profiles. |
| VIII. Modular and Extensible Architecture | PASS | Changes stay within schema, adapter, materializer, launcher, and strategy boundaries. |
| IX. Resilient by Default | PASS | Uses existing managed-runtime lifecycle and boundary tests. |
| X. Facilitate Continuous Improvement | PASS | Adds exact boundary tests for the new launch contract. |
| XI. Spec-Driven Development | PASS | This spec/plan/tasks set drives the implementation. |
| XII. Canonical Documentation | PASS | Canonical design doc remains the requirements source; implementation traceability lives under `specs/125-codex-openrouter-phase1/`. |
| XIII. Pre-Release Velocity | PASS | Removes the remaining legacy-shaped launch gap instead of adding a parallel OpenRouter path. |

## Project Structure

### Documentation

```text
specs/125-codex-openrouter-phase1/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── tasks.md
├── speckit_analyze_report.md
└── contracts/
    └── requirements-traceability.md
```

### Code Touch Points

```text
api_service/main.py
api_service/api/routers/provider_profiles.py
moonmind/schemas/agent_runtime_models.py
moonmind/workflows/adapters/managed_agent_adapter.py
moonmind/workflows/adapters/materializer.py
moonmind/workflows/temporal/artifacts.py
moonmind/workflows/temporal/runtime/launcher.py
moonmind/workflows/temporal/runtime/strategies/codex_cli.py
tests/unit/api_service/test_provider_profile_auto_seed.py
tests/unit/workflows/adapters/test_managed_agent_adapter.py
tests/unit/workflows/adapters/test_materializer.py
tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py
```

## Design Decisions

1. Keep OpenRouter on `runtime_id=codex_cli` and implement the provider distinction entirely through provider profiles.
2. Use a new `RuntimeFileTemplate` launch contract so generated config files are path-addressed and typed by format.
3. Preserve existing secret-resolution behavior at launch time while allowing richer `env_template` values such as `{"from_secret_ref": ...}`.
4. Seed the OpenRouter profile only when `OPENROUTER_API_KEY` is present, leaving the existing OAuth Codex profile untouched.
5. Let Codex strategy suppress only the default model flag; explicit request model overrides still win.
