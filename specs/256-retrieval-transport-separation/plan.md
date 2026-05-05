# Implementation Plan: Retrieval Transport and Configuration Separation

**Branch**: `256-retrieval-transport-separation` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/256-retrieval-transport-separation/spec.md`

## Summary

MM-508 is a runtime Workflow RAG contract story. The repository already separates much of retrieval configuration into `moonmind/rag/settings.py` and `moonmind/rag/service.py`, exposes a retrieval gateway in `api_service/api/routers/retrieval_gateway.py`, and keeps managed-runtime provider profiles under `api_service/api/routers/provider_profiles.py` and related adapter code. The remaining work is to harden and verify the separation boundary: keep provider profiles focused on runtime launch, make gateway preference defensible when runtime embedding credentials are unavailable, preserve direct retrieval as an allowed path when policy permits it, make local fallback explicitly degraded, and prove overlay and budget knobs stay coherent across transport choices without shifting retrieval ownership into profile semantics.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | Runtime strategies pass the materialized run environment into `ContextInjectionService`, while retrieval settings continue to resolve from `moonmind/rag/settings.py` rather than provider-profile launch fields; focused launcher and activity tests cover the runtime boundary. | none | unit + integration |
| FR-002 | implemented_verified | `RagRuntimeSettings` prefers gateway when `MOONMIND_RETRIEVAL_URL` is configured, requires scoped `MOONMIND_RETRIEVAL_TOKEN` auth, and the gateway accepts OIDC or scoped retrieval-token requests without reviving worker-token auth. | none | unit + integration |
| FR-003 | implemented_verified | Direct transport remains available when policy and provider-specific embedding/Qdrant configuration permit it; retrieval service tests cover direct embedding plus Qdrant execution. | none | unit + integration |
| FR-004 | implemented_verified | Local fallback remains policy gated, records `retrievalMode=degraded_local_fallback`, and publishes degraded reason metadata through context injection and runtime boundaries. | none | unit + integration |
| FR-005 | implemented_verified | Gateway and direct service tests prove query, filters, top-k, overlay policy, and token/latency budgets propagate through the selected transport and remain visible in `ContextPack` artifacts. | none | unit + integration |
| FR-006 | implemented_verified | Provider profiles remain runtime-launch focused; retrieval transport/auth/settings are resolved from shared RAG configuration and runtime materialized environment, not from provider-profile semantics. | none | unit + integration |
| FR-007 | implemented_verified | `spec.md` preserves the original MM-508 preset brief and issue key; planning artifacts for this feature continue that traceability | preserve MM-508 through tasks, implementation notes, and final verification | traceability review |
| DESIGN-REQ-004 | implemented_verified | Boundary tests prove retrieval settings are resolved from shared RAG configuration and materialized runtime environment, not from provider-profile launch metadata. | none | unit + integration |
| DESIGN-REQ-009 | implemented_verified | Gateway transport is preferred when configured and is usable from managed runtimes through scoped RetrievalGateway-token auth. | none | unit + integration |
| DESIGN-REQ-010 | implemented_verified | Direct retrieval remains explicitly tested and available when environment and policy permit embedding and Qdrant access. | none | unit + integration |
| DESIGN-REQ-014 | implemented_verified | Degraded local fallback is explicit in metadata, prompt context, and runtime-boundary extraction. | none | unit + integration |
| DESIGN-REQ-016 | implemented_verified | Overlay, filter, top-k, and budget knobs are verified through service and gateway tests. | none | unit + integration |
| DESIGN-REQ-019 | implemented_verified | Retrieval settings remain configuration driven and separate from runtime launch profile concerns. | none | unit + integration |
| DESIGN-REQ-024 | implemented_verified | Session retrieval is bounded through gateway auth, required repo/repository filters, optional repository allowlists, and no raw datastore admin exposure. | none | unit + integration |
| DESIGN-REQ-025 | implemented_verified | Workflow RAG transport choice remains in shared RAG contracts and is documented in `docs/Rag/WorkflowRag.md`. | none | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, FastAPI, existing MoonMind RAG services, existing managed-runtime adapter and provider-profile stack, pytest  
**Storage**: No new persistent storage; reuse existing retrieval settings, runtime metadata, provider-profile rows, and artifact-backed retrieval outputs  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_settings.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/api/routers/test_retrieval_gateway.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/api_service/api/routers/test_provider_profiles.py`  
**Integration Testing**: `./tools/test_integration.sh` when compatible, plus targeted retrieval/runtime boundary coverage such as `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and any new focused retrieval transport scenario added under `tests/integration/workflows/temporal/`  
**Target Platform**: MoonMind retrieval gateway, managed-runtime retrieval injection, execution creation, and provider-profile boundaries  
**Project Type**: Backend runtime and API contract hardening for Workflow RAG transport selection and configuration ownership  
**Performance Goals**: preserve deterministic transport resolution, compact retrieval metadata, and existing retrieval budget enforcement without adding a new persistence or transport layer  
**Constraints**: provider profiles must remain runtime-launch focused; gateway preference must not require embedding credentials in managed-runtime environments by default; local fallback must stay explicit and degraded; no compatibility wrappers for superseded internal contracts  
**Scale/Scope**: one story covering retrieval configuration separation, gateway/direct/fallback selection, coherent overlay and budget knobs, and preservation of the shared Workflow RAG contract

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - the plan keeps retrieval transport and configuration in MoonMind-owned RAG services and boundaries rather than moving those concerns into runtime profiles.
- II. One-Click Agent Deployment: PASS - no new operator-managed service or secret system is introduced.
- III. Avoid Vendor Lock-In: PASS - direct, gateway, and fallback retrieval remain transport choices behind shared RAG contracts.
- IV. Own Your Data: PASS - retrieval authority remains on MoonMind-controlled configuration, gateway, artifacts, and vector-store surfaces.
- V. Skills Are First-Class and Easy to Add: PASS - no skill-system changes are required.
- VI. Replaceable AI Scaffolding: PASS - work focuses on stable contracts, configuration ownership, and test evidence.
- VII. Runtime Configurability: PASS - the story is about preserving runtime-configurable retrieval settings and transport selection.
- VIII. Modular and Extensible Architecture: PASS - planned work stays on RAG settings, gateway, context injection, execution/router, and adapter boundaries.
- IX. Resilient by Default: PASS - transport selection, degraded fallback, and bounded metadata remain explicit and testable.
- X. Facilitate Continuous Improvement: PASS - final verification will show whether current transport behavior already satisfies or still violates the desired-state contract.
- XI. Spec-Driven Development: PASS - `spec.md` and the preserved MM-508 Jira preset brief remain the source of truth.
- XII. Canonical Documentation Separation: PASS - all volatile planning artifacts remain under `specs/256-retrieval-transport-separation/`.
- XIII. Pre-release Compatibility Policy: PASS - no backward-compatibility aliases are planned; any contract adjustments will update callers and tests directly.

## Project Structure

### Documentation (this feature)

```text
specs/256-retrieval-transport-separation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── retrieval-transport-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/rag/
├── context_injection.py
├── service.py
├── settings.py
└── context_pack.py

api_service/api/routers/
├── retrieval_gateway.py
├── provider_profiles.py
└── executions.py

moonmind/workflows/temporal/
├── activity_runtime.py
└── runtime/
    └── launcher.py

tests/unit/rag/
├── test_context_injection.py
├── test_service.py
└── test_settings.py

tests/unit/api/routers/
└── test_retrieval_gateway.py

tests/unit/api_service/api/routers/
└── test_provider_profiles.py

tests/unit/services/temporal/runtime/
└── test_launcher.py

tests/unit/workflows/temporal/
└── test_agent_runtime_activities.py

tests/integration/workflows/temporal/
└── test_managed_session_retrieval_context.py
```

**Structure Decision**: MM-508 stays on the existing Workflow RAG and runtime-boundary surfaces. No new storage system is needed; the likely work is contract hardening plus boundary-level verification for transport resolution, fallback visibility, and retrieval-versus-profile ownership.

## Complexity Tracking

No constitution violations.
