# Implementation Plan: Retrieval Transport and Configuration Separation

**Branch**: `256-retrieval-transport-separation` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/256-retrieval-transport-separation/spec.md`

## Summary

MM-508 is a runtime Workflow RAG contract story. The repository already separates much of retrieval configuration into `moonmind/rag/settings.py` and `moonmind/rag/service.py`, exposes a retrieval gateway in `api_service/api/routers/retrieval_gateway.py`, and keeps managed-runtime provider profiles under `api_service/api/routers/provider_profiles.py` and related adapter code. The remaining work is to harden and verify the separation boundary: keep provider profiles focused on runtime launch, make gateway preference defensible when runtime embedding credentials are unavailable, preserve direct retrieval as an allowed path when policy permits it, make local fallback explicitly degraded, and prove overlay and budget knobs stay coherent across transport choices without shifting retrieval ownership into profile semantics.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/rag/settings.py` resolves embedding provider/model, collection, retrieval URL, overlay mode, and budgets from environment-driven retrieval settings; provider-profile APIs in `api_service/api/routers/provider_profiles.py` model runtime launch concerns separately, but no boundary test proves retrieval settings remain independent from runtime profile launch data across execution creation and runtime launch | add verification around execution/runtime boundaries and normalize any remaining coupling between retrieval settings and provider-profile selection metadata | unit + integration |
| FR-002 | partial | `RagRuntimeSettings.resolved_transport()` in `moonmind/rag/settings.py` prefers `gateway` when `MOONMIND_RETRIEVAL_URL` is configured, and `moonmind/rag/service.py` supports gateway execution; `api_service/api/routers/retrieval_gateway.py` exposes the gateway surface, but worker-token auth is temporarily unavailable and no managed-runtime boundary test proves gateway preference when runtime embedding credentials are absent | add test-first proof for gateway preference and fix any auth or runtime-boundary gaps required to make gateway the defensible default under the documented conditions | unit + integration |
| FR-003 | implemented_unverified | `moonmind/rag/settings.py` preserves `direct` as a resolved transport when gateway is absent and provider-specific embedding credentials are configured, and `moonmind/rag/service.py` executes direct retrieval through Qdrant plus embedding clients | add verification-first coverage for direct retrieval selection and preserve the current code path unless tests expose a contract gap | unit + integration |
| FR-004 | partial | `moonmind/rag/context_injection.py` gates local fallback through `_LOCAL_FALLBACK_ALLOWED_SKIP_REASONS`, records `transport="local_fallback"`, and injects untrusted-reference framing, but there is limited proof that all degraded fallback cases remain explicit and do not present as normal semantic retrieval | strengthen degraded-mode verification and adjust fallback metadata or gating only if the new tests expose ambiguity | unit + integration |
| FR-005 | partial | `moonmind/rag/settings.py`, `moonmind/rag/service.py`, and `api_service/api/routers/retrieval_gateway.py` already carry top-k, overlay policy, repository filters, and latency or token budgets, but no coherent boundary test proves those knobs behave consistently across direct, gateway, and degraded flows | add contract tests for knob propagation and normalize any transport-specific drift uncovered by those tests | unit + integration |
| FR-006 | partial | docs and code separate retrieval settings from runtime profile launch data conceptually, yet managed runtime boundaries still carry provider-profile metadata through execution creation and adapter launch paths without explicit tests proving retrieval ownership stays outside provider-profile semantics | add execution and adapter-boundary coverage and adjust metadata handling if retrieval transport or embedding config still depends on profile semantics | unit + integration |
| FR-007 | implemented_verified | `spec.md` preserves the original MM-508 preset brief and issue key; planning artifacts for this feature continue that traceability | preserve MM-508 through tasks, implementation notes, and final verification | traceability review |
| DESIGN-REQ-004 | partial | retrieval settings live in `moonmind/rag/settings.py` while provider profiles model runtime launch in `api_service/api/routers/provider_profiles.py` and adapters, but no end-to-end proof ensures profiles are not treated as generic retrieval credentials | add boundary verification and harden the separation contract | unit + integration |
| DESIGN-REQ-009 | partial | gateway support and default preference exist in settings and service code, but runtime-boundary verification is missing and gateway auth readiness needs proof | add tests and targeted code changes only if the documented gateway preference is not actually reachable | unit + integration |
| DESIGN-REQ-010 | implemented_unverified | direct retrieval is already available via settings and service code, but not fully proven against the MM-508 source brief | add verification-first tests | unit + integration |
| DESIGN-REQ-014 | partial | local fallback transport is explicit in `moonmind/rag/context_injection.py`, but degraded-mode observability and gating need stronger proof | add verification and tighten metadata or gating if needed | unit + integration |
| DESIGN-REQ-016 | partial | overlay and budget knobs flow through `RagRuntimeSettings`, `ContextRetrievalService`, and the retrieval gateway request model, but coherent cross-transport proof is missing | add cross-transport contract tests | unit + integration |
| DESIGN-REQ-019 | partial | retrieval environment settings are configuration driven in `moonmind/config/settings.py` and `moonmind/rag/settings.py`, but execution and adapter boundaries do not yet prove full separation from runtime-launch shaping | add boundary verification | unit + integration |
| DESIGN-REQ-024 | implemented_unverified | the gateway and context injection paths keep retrieval bounded and do not expose raw database administration surfaces, but the contract is not yet proven in MM-508-focused tests | add verification-first coverage | unit + integration |
| DESIGN-REQ-025 | partial | the desired-state document keeps Workflow RAG as a shared MoonMind contract, but no boundary tests currently guard against regressions that push retrieval transport into profile-specific semantics | add shared contract verification | unit + integration |

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
