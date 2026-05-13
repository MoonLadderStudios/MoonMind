# Implementation Plan: Runtime Prompt Boundary

**Branch**: `change-jira-issue-mm-650-to-status-in-pr-d5d827af` | **Date**: 2026-05-13 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:50961052-74bb-4c16-979a-1d3698facd1f/repo/specs/349-runtime-prompt-boundary/spec.md`

**Note**: The standard setup script `.specify/scripts/bash/setup-plan.sh --json` was attempted but refused this managed branch because it is not named with a numeric feature prefix. Planning proceeds from `.specify/feature.json` and the active feature directory.

## Summary

Deliver MM-650 by making the runtime/prompt boundary explicit for image attachments: the control plane continues to pass normalized task intent plus artifact references, text-first runtimes consume generated image context through the canonical `INPUT ATTACHMENTS` contract, and multimodal/external runtime paths consume raw artifact references without changing the canonical task contract or inventing new attachment target kinds. Repo analysis found strong existing coverage for target-aware prepared context, text-first Codex prompt injection, and objective/step target validation, but multimodal/raw-reference adapter behavior needs explicit contract tests and likely small boundary hardening if those tests expose gaps.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/workflows/tasks/prepared_context.py` builds compact manifests and `moonmind/workflows/temporal/workflows/run.py` merges selected prepared refs, but end-to-end proof across runtime modes is incomplete | add boundary verification for normalized task intent plus artifact refs across text-first and multimodal paths; implement only if verification fails | unit + integration |
| FR-002 | implemented_verified | `moonmind/agents/codex_worker/worker.py` renders `INPUT ATTACHMENTS`; `tests/unit/agents/codex_worker/test_worker.py` verifies context before `WORKSPACE` and target context paths | preserve behavior; include in final traceability | final verify |
| FR-003 | partial | `PreparedContext` carries `rawInputRefs`; external adapters can use `input_refs`, but no explicit multimodal/raw-image adapter contract test proves the canonical task contract remains unchanged | add contract tests for multimodal/external raw artifact refs; harden adapter boundary if tests fail | unit + integration |
| FR-004 | implemented_verified | `PreparedInputEntry.target_kind` is limited to `objective`/`step`; `moonmind/vision/service.py` rejects unsupported target kinds; `tests/unit/workflows/tasks/test_prepared_context.py` covers binding validation | preserve behavior; include in final traceability | final verify |
| FR-005 | partial | Current step selection excludes sibling step refs and inline content, but adapter-introduced targeting rules are not explicitly tested at runtime adapter boundary | add failing guardrail test proving adapters cannot broaden targets or add non-canonical target rules | unit + integration |
| FR-006 | partial | `tests/unit/workflows/tasks/test_prepared_context.py`, `tests/unit/agents/codex_worker/test_attachment_materialization.py`, and `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` cover target preservation for prepared context and text-first execution; multimodal raw-ref preservation needs explicit proof | add cross-runtime verification for objective and step-scoped image attachments | unit + integration |
| FR-007 | implemented_unverified | `PreparedContextFailure` and integration tests expose missing artifact IDs with target diagnostics; vision context unavailable states are diagnostic but selected-runtime failure behavior needs explicit proof | add verification for missing generated context/raw refs in selected runtime paths; implement explicit diagnostic if absent | unit + integration |
| FR-008 | implemented_verified | `spec.md` preserves MM-650 and the full Jira preset brief; this plan preserves MM-650 and DESIGN-REQ-026 | preserve in research, data model, contracts, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_verified | Codex worker unit tests assert `INPUT ATTACHMENTS` appears before `WORKSPACE` with vision context paths | no new implementation; final verify only | final verify |
| SCN-002 | partial | Raw refs exist in prepared context, but multimodal adapter behavior is not explicitly covered | add contract/integration test for raw refs through a multimodal/external runtime path | unit + integration |
| SCN-003 | partial | Prepared context rejects unsupported target shapes but adapter-level broadening is not directly tested | add adapter-boundary guard test | unit + integration |
| SCN-004 | implemented_unverified | Runtime-specific request construction exists for managed Codex and external agents, but a paired runtime-selection test is needed | add paired runtime-selection verification proving canonical task payload remains stable | integration |
| SC-001 | implemented_verified | Existing Codex worker tests cover generated context injection for text-first runtime prompt preparation | no new implementation; final verify only | final verify |
| SC-002 | partial | Existing external adapter input refs do not yet prove multimodal raw image references with stable canonical contract | add multimodal/raw-ref coverage | unit + integration |
| SC-003 | partial | No explicit adapter-introduced target guard coverage | add guardrail test and implementation contingency | unit + integration |
| SC-004 | partial | Objective and step target preservation is covered in prepared context and Codex paths; cross-runtime evidence is incomplete | add cross-runtime objective/step verification | unit + integration |
| SC-005 | implemented_verified | `spec.md` and this plan preserve traceability | preserve through remaining artifacts | final verify |
| DESIGN-REQ-026 | partial | Source rules are implemented in pieces across prepared context, vision service, and Codex worker; multimodal/raw-ref boundary needs explicit proof | complete missing adapter-boundary tests and any required hardening | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 models, Temporal Python SDK workflows/activities, existing MoonMind runtime adapters, existing vision context service  
**Storage**: Existing artifact store and workspace-local prepared files/manifests only; no new persistent storage planned  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets during iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` tests, with focused pytest for workflow-boundary iteration  
**Target Platform**: MoonMind managed runtime and external-agent execution on Linux containers  
**Project Type**: Python orchestration service with Temporal workflows and runtime adapters  
**Performance Goals**: Runtime preparation remains bounded to compact metadata and artifact references; no binary payloads are introduced into workflow history or prompt text  
**Constraints**: Preserve canonical task contract; do not introduce provider-specific payload semantics into control-plane task payloads; no compatibility aliases for internal pre-release contracts; keep secrets and binary content out of logs/history/prompts  
**Scale/Scope**: One runtime-boundary story covering text-first and multimodal image attachment handling for objective and step-scoped attachments

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan keeps provider-specific multimodal behavior behind runtime adapters and does not rebuild agent behavior.
- **II. One-Click Agent Deployment**: PASS. No new required external services or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Text-first and multimodal paths are described through portable artifact references and adapter boundaries.
- **IV. Own Your Data**: PASS. Image context and raw refs remain operator-owned artifacts or workspace-local prepared files.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes are planned; workflow-facing contracts remain runtime-neutral.
- **VI. Replaceable Scaffolding, Thick Contracts**: PASS. The work strengthens boundary contracts and tests instead of embedding cognition or provider specifics.
- **VII. Runtime Configurability**: PASS. Runtime selection remains request/config driven; no hardcoded provider behavior is added to the control plane.
- **VIII. Modular and Extensible Architecture**: PASS. Planned changes stay within prepared context, adapter boundary, and tests.
- **IX. Resilient by Default**: PASS. The plan requires explicit diagnostics for missing prepared context or unsupported target mappings.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence and traceability remain artifact-backed for later workflow summaries.
- **XI. Spec-Driven Development**: PASS. `spec.md` exists and this plan preserves requirement traceability before task generation.
- **XII. Canonical Documentation Separation**: PASS. Time-bound rollout and implementation details remain in this feature directory; canonical docs are source requirements, not a migration checklist target.
- **XIII. Pre-Release Velocity**: PASS. Any superseded internal boundary behavior should be replaced directly without compatibility shims.
- **Security / secret hygiene**: PASS. The feature uses artifact refs and metadata only; no secrets are introduced.
- **Observability & Mission Control**: PASS. Missing preparation state and target diagnostics are planned as operator-visible evidence.

## Project Structure

### Documentation (this feature)

```text
specs/349-runtime-prompt-boundary/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-prompt-boundary.md
└── tasks.md              # Phase 2 output; not created by this plan step
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── tasks/prepared_context.py
│   ├── temporal/workflows/run.py
│   └── adapters/
├── agents/codex_worker/worker.py
└── vision/service.py

tests/
├── unit/
│   ├── workflows/tasks/test_prepared_context.py
│   ├── workflows/adapters/
│   └── agents/codex_worker/test_worker.py
└── integration/
    └── workflows/temporal/workflows/test_run_target_aware_inputs.py
```

**Structure Decision**: Use the existing orchestration-service layout. Contract/model changes belong at prepared-context and adapter boundaries; text-first prompt behavior stays in the Codex worker; workflow-boundary tests stay under Temporal workflow tests.

## Complexity Tracking

No constitution violations identified.
