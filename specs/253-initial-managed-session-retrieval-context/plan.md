# Implementation Plan: Initial Managed-Session Retrieval Context

**Branch**: `253-initial-managed-session-retrieval-context` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/253-initial-managed-session-retrieval-context/spec.md`

## Summary

MM-505 is a runtime verification-first story. The repository already contains the core managed-session retrieval path in `moonmind/rag/context_injection.py`, `moonmind/rag/service.py`, `moonmind/rag/context_pack.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, and `moonmind/workflows/temporal/activity_runtime.py`, plus unit and workflow-boundary coverage for retrieval injection, artifact persistence, safety framing, and pre-command workspace preparation. The planning focus is to preserve MM-505 in MoonSpec artifacts, add or confirm boundary proof that initial managed-session retrieval remains durable and compact at adapter/runtime seams, and close the remaining gaps around reusable runtime coverage and compact durable-context publication evidence.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, `tests/unit/services/temporal/runtime/test_launcher.py` | none beyond final verify | unit |
| FR-002 | implemented_unverified | `moonmind/rag/service.py`, `moonmind/rag/context_pack.py`, `moonmind/rag/context_injection.py`, `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_pack.py` | add retrieval-path verification at the managed-session boundary; implementation only if verification exposes an unexpected extra hop or packaging gap | unit + integration |
| FR-003 | partial | `moonmind/rag/context_injection.py`, `moonmind/rag/context_pack.py`, `docs/Rag/WorkflowRag.md`, `tests/unit/rag/test_context_injection.py` | add boundary proof that durable artifact publication is the authoritative startup handoff and confirm large context bodies are not duplicated into durable workflow payloads; implement compact ref/publish hardening if missing | unit + integration |
| FR-004 | implemented_verified | `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py` | none beyond final verify | unit |
| FR-005 | partial | `moonmind/rag/context_injection.py`, `moonmind/rag/context_pack.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/runtime/strategies/claude_code.py` | verify reusable runtime contract boundaries and extend shared adapter coverage only where current behavior is Codex-specific | unit + integration |
| FR-006 | implemented_verified | `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/managed-session-retrieval-context-contract.md`, `quickstart.md` | preserve MM-505 through tasks and final verification output | traceability review |
| DESIGN-REQ-001 | implemented_verified | `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, `tests/unit/services/temporal/runtime/test_launcher.py` | none beyond final verify | unit |
| DESIGN-REQ-002 | partial | `moonmind/rag/context_injection.py`, `moonmind/rag/context_pack.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/rag/test_context_injection.py` | add proof that durable artifact/ref-backed context remains the startup truth and that durable payloads stay compact | unit + integration |
| DESIGN-REQ-005 | implemented_unverified | `moonmind/rag/service.py`, `moonmind/rag/context_pack.py`, `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_pack.py` | add end-to-end managed-session verification of embedding search plus deterministic packaging without a generative retrieval hop | unit + integration |
| DESIGN-REQ-006 | implemented_verified | `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py` | none beyond final verify | unit |
| DESIGN-REQ-008 | partial | `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `tests/unit/rag/test_context_injection.py` | add compact durable-publication verification and harden publication path only if retrieval output is not sufficiently observable or reusable | unit + integration |
| DESIGN-REQ-011 | partial | `moonmind/rag/context_pack.py`, `moonmind/rag/context_injection.py`, `docs/Rag/WorkflowRag.md` | verify durable artifact/backing evidence and add stronger observability or bounded metadata proof if current evidence is insufficient | unit + integration |
| DESIGN-REQ-017 | partial | `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/runtime/strategies/base.py` | verify Codex startup remains on the shared retrieval contract and add shared-strategy coverage if runtime reuse is under-specified | unit + integration |
| DESIGN-REQ-025 | partial | `moonmind/rag/service.py`, `moonmind/rag/context_injection.py`, `api_service/api/routers/retrieval_gateway.py`, `docs/Rag/WorkflowRag.md` | verify MoonMind-owned policy and adapter neutrality at runtime boundaries; implement only if gateway/direct policy handling diverges from the contract | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing MoonMind RAG services, Qdrant-backed retrieval, existing managed-runtime launcher/strategy stack, pytest  
**Storage**: No new persistent storage; existing artifact-backed context files under task workspaces plus existing Qdrant retrieval index and bounded workflow/runtime metadata  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`  
**Integration Testing**: `./tools/test_integration.sh` for any hermetic `integration_ci` coverage that can be added, plus targeted Temporal workflow-boundary verification such as `pytest tests/integration/workflows/temporal -k managed_session_retrieval_context -q --tb=short` when time-skipping coverage is required and intentionally non-`integration_ci`  
**Target Platform**: MoonMind worker runtime, managed-session adapter/runtime boundaries, and retrieval gateway/direct retrieval path  
**Project Type**: Backend runtime and verification story for managed-session retrieval context assembly and publication  
**Performance Goals**: Preserve current lean retrieval path and startup ordering without introducing an extra generative retrieval hop or large durable payload growth  
**Constraints**: Keep retrieved context behind durable artifacts or refs, preserve the untrusted-retrieved-text safety framing, maintain MM-505 traceability, avoid compatibility shims, and keep unit and integration verification explicit  
**Scale/Scope**: One story covering initial retrieval resolution, deterministic `ContextPack` assembly, durable publication, adapter injection, and shared-runtime contract reuse for managed-session startup

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - planning stays on MoonMind-owned retrieval and adapter boundaries instead of inventing a runtime-specific retrieval flow.
- II. One-Click Agent Deployment: PASS - no new operator prerequisite or service is introduced.
- III. Avoid Vendor Lock-In: PASS - the story remains on provider-neutral retrieval contracts and bounded runtime adapters.
- IV. Own Your Data: PASS - retrieval outputs remain on operator-controlled artifacts and existing vector infrastructure.
- V. Skills Are First-Class and Easy to Add: PASS - no change to the skill/tool surface; this is a runtime contract verification story.
- VI. Replaceable AI Scaffolding: PASS - focus stays on durable retrieval contracts, artifacts, and verification evidence rather than agent-specific reasoning scaffolds.
- VII. Runtime Configurability: PASS - existing retrieval settings, transport selection, and budgeting remain configuration-driven.
- VIII. Modular and Extensible Architecture: PASS - likely code changes stay localized to `moonmind/rag/*`, runtime strategy boundaries, and feature-local planning artifacts.
- IX. Resilient by Default: PASS - planning emphasizes compact durable publication, explicit fallback behavior, and workflow-boundary verification.
- X. Facilitate Continuous Improvement: PASS - final verification will produce concrete MM-505 evidence and any remaining runtime gaps.
- XI. Spec-Driven Development: PASS - the preserved MM-505 Jira brief and `spec.md` remain the source of truth.
- XII. Canonical Documentation Separation: PASS - desired-state retrieval design stays in `docs/Rag/WorkflowRag.md`; planning artifacts remain feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility wrappers are planned; any runtime contract changes must update all callers directly.

## Project Structure

### Documentation (this feature)

```text
specs/253-initial-managed-session-retrieval-context/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-retrieval-context-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/rag/
├── context_injection.py
├── context_pack.py
├── service.py
└── settings.py

moonmind/workflows/temporal/
├── activity_runtime.py
└── runtime/
    ├── launcher.py
    └── strategies/
        ├── base.py
        ├── claude_code.py
        └── codex_cli.py

api_service/api/routers/
└── retrieval_gateway.py

tests/unit/rag/
├── test_context_injection.py
├── test_context_pack.py
└── test_service.py

tests/unit/workflows/temporal/runtime/strategies/
└── test_remaining_strategies.py

tests/unit/workflows/temporal/
└── test_agent_runtime_activities.py

tests/unit/services/temporal/runtime/
└── test_launcher.py

tests/integration/
└── workflows/temporal/
```

**Structure Decision**: MM-505 stays on the existing retrieval and managed-runtime boundaries. No new persistence model or public API surface is required; the likely implementation work, if any, is boundary hardening and focused unit/integration verification around durable publication, compact payload discipline, and reusable runtime behavior.

## Complexity Tracking

No constitution violations.
