# Implementation Plan: Managed-Session Follow-Up Retrieval

**Branch**: `254-managed-session-followup-retrieval` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/254-managed-session-followup-retrieval/spec.md`

## Summary

MM-506 is a runtime boundary story for session-initiated follow-up retrieval. The repository already contains shared retrieval primitives in `moonmind/rag/service.py`, `moonmind/rag/context_pack.py`, `api_service/api/routers/retrieval_gateway.py`, and initial auto-context wiring in `moonmind/agents/codex_worker/handlers.py` and `moonmind/rag/context_injection.py`, but the managed-session contract is incomplete for explicit mid-run retrieval. The planning focus is to keep the existing retrieval layer and artifact discipline, add a runtime-facing capability signal plus a managed-session retrieval surface that uses MoonMind-owned routing only, and verify the contract through explicit unit and integration lanes before implementation is considered complete.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | no managed-session capability signal found in `moonmind/agents/codex_worker/handlers.py`, `moonmind/workflows/temporal/activity_runtime.py`, or runtime strategy surfaces; only initial retrieved-context framing exists in `moonmind/rag/context_injection.py` | add runtime-facing capability signal describing availability, request path, reference-data framing, and policy bounds | unit + integration |
| FR-002 | partial | `api_service/api/routers/retrieval_gateway.py` and `moonmind/rag/service.py` provide MoonMind-owned retrieval paths, but worker-token auth is intentionally unavailable and no managed-session tool/adapter hook exposes session follow-up retrieval | add managed-session retrieval surface that routes through MoonMind-owned retrieval and remove any ambiguity about direct unmanaged bypasses | unit + integration |
| FR-003 | partial | `api_service/api/routers/retrieval_gateway.py` defines `query`, `filters`, `top_k`, `overlay_policy`, and `budgets`; `moonmind/rag/service.py` enforces token/latency budgets, but the session-facing contract and policy-specific validation remain underdefined | tighten request contract, expose policy-bounded fields explicitly, and add tests for accepted and rejected request shapes | unit + integration |
| FR-004 | implemented_unverified | `moonmind/rag/context_pack.py` and `api_service/api/routers/retrieval_gateway.py` can return `ContextPack`-shaped data with `context_text`, but no success-path managed-session boundary test proves the session-facing response contract | add verification tests first; if they fail, align gateway/runtime response shape and observability metadata | unit + integration |
| FR-005 | partial | fail-fast paths exist for auth rejection in `tests/unit/api/routers/test_retrieval_gateway.py`, token/latency budgets in `tests/unit/rag/test_service.py`, and missing gateway URL in `moonmind/rag/service.py`, but there is no explicit disabled-session contract for managed runtimes | add deterministic disabled/unsupported follow-up retrieval responses and test them at runtime and gateway boundaries | unit + integration |
| FR-006 | partial | retrieval primitives are runtime-neutral, but follow-up retrieval is not yet proven across Codex and future managed runtimes; current evidence centers on initial retrieval and gateway primitives | keep contract runtime-neutral, implement at one managed runtime boundary, and preserve adapter-neutral semantics for later adopters | unit + integration |
| FR-007 | implemented_verified | `spec.md` preserves the original brief and MM-506; this plan and the generated Phase 0/1 artifacts will retain the same key | preserve MM-506 across all remaining artifacts and final verification output | traceability review |
| DESIGN-REQ-003 | partial | source-backed session retrieval is specified in `docs/Rag/WorkflowRag.md`, but no managed-session follow-up execution path is proven in runtime tests | add explicit session-initiated retrieval path and verification | unit + integration |
| DESIGN-REQ-007 | partial | gateway transport exists, but managed-session ownership is not yet enforced end to end because worker-token auth is stubbed and no runtime tool surface is exposed | add managed-session retrieval hook and boundary tests proving MoonMind-owned routing | unit + integration |
| DESIGN-REQ-015 | partial | bounded inputs exist in router/service code, but policy exposure and validation are not yet tested as the managed-session contract | define and verify request-field rules plus failure cases | unit + integration |
| DESIGN-REQ-019 | missing | no runtime-facing capability signal describing availability, request method, reference-data handling, or budgets was found | add runtime capability signal and verify runtime-visible content | unit + integration |
| DESIGN-REQ-020 | partial | some low-level fail-fast behavior exists, but session-disabled follow-up retrieval lacks a stable contract | add deterministic disabled-denial behavior and tests | unit + integration |
| DESIGN-REQ-023 | partial | retrieval layer is shared, but no follow-up retrieval runtime-boundary proof exists beyond lower-level primitives | preserve runtime-neutral contract and add boundary-level verification | unit + integration |
| DESIGN-REQ-025 | partial | retrieval service and gateway are MoonMind-owned and policy bounded at a low level, but observability and runtime-facing contract proof for session follow-up retrieval are incomplete | add boundary observability and runtime contract verification | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, existing MoonMind RAG services, existing managed-runtime launcher/session surfaces, httpx, pytest  
**Storage**: No new persistent storage; rely on existing retrieval artifacts, bounded workflow/runtime metadata, and existing retrieval index infrastructure  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_retrieval_gateway.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`  
**Integration Testing**: `./tools/test_integration.sh` for hermetic suites when compatible with the final implementation, plus a focused Temporal/runtime boundary test such as `pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short` if the required proof remains outside `integration_ci`  
**Target Platform**: MoonMind API service, managed-session runtime boundaries, retrieval gateway route, and shared retrieval service/runtime instruction surfaces  
**Project Type**: Backend runtime and contract-verification story for managed-session follow-up retrieval  
**Performance Goals**: Preserve bounded retrieval latency and token budget enforcement, keep response shapes compact and deterministic, and avoid introducing a second bespoke retrieval stack  
**Constraints**: Keep follow-up retrieval MoonMind-owned, preserve untrusted-reference-data framing, fail fast when disabled or unsupported, avoid compatibility shims, and keep unit and integration strategies explicit  
**Scale/Scope**: One story covering runtime capability signalling, request contract validation, MoonMind-owned follow-up retrieval routing, response semantics, fail-fast behavior, and runtime-neutral contract proof

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - plan stays on MoonMind-owned retrieval surfaces and managed-runtime boundaries instead of inventing runtime-specific retrieval logic.
- II. One-Click Agent Deployment: PASS - no new operator prerequisite or external service is required.
- III. Avoid Vendor Lock-In: PASS - the plan keeps retrieval behavior behind runtime-neutral contracts and shared services.
- IV. Own Your Data: PASS - retrieval output remains on MoonMind-controlled artifacts and existing retrieval infrastructure.
- V. Skills Are First-Class and Easy to Add: PASS - no skill/runtime sprawl is introduced; existing trusted surfaces remain the integration path.
- VI. Replaceable AI Scaffolding: PASS - focus remains on durable contracts, explicit evidence, and replaceable runtime adapters.
- VII. Runtime Configurability: PASS - enablement, overlay policy, and budgets remain configuration-driven rather than hardcoded.
- VIII. Modular and Extensible Architecture: PASS - planned changes stay localized to retrieval service, API/router, and runtime-boundary layers.
- IX. Resilient by Default: PASS - fail-fast disabled behavior, bounded budgets, and boundary-level verification are explicit requirements.
- X. Facilitate Continuous Improvement: PASS - final verification will provide concrete MM-506 evidence and expose any remaining gaps.
- XI. Spec-Driven Development: PASS - `spec.md` and the preserved Jira brief remain the source of truth.
- XII. Canonical Documentation Separation: PASS - planning details stay under `specs/254-managed-session-followup-retrieval/`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility wrappers are planned; callers and tests will be updated directly if contracts change.

## Project Structure

### Documentation (this feature)

```text
specs/254-managed-session-followup-retrieval/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-followup-retrieval-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/
└── retrieval_gateway.py

moonmind/rag/
├── context_injection.py
├── context_pack.py
├── guardrails.py
├── service.py
└── settings.py

moonmind/agents/codex_worker/
└── handlers.py

moonmind/workflows/temporal/
└── activity_runtime.py

tests/unit/api/routers/
└── test_retrieval_gateway.py

tests/unit/rag/
├── test_context_injection.py
└── test_service.py

tests/unit/agents/codex_worker/
└── test_handlers.py

tests/unit/workflows/temporal/
└── test_agent_runtime_activities.py

tests/unit/services/temporal/runtime/
└── test_launcher.py

tests/integration/
└── workflows/temporal/
```

**Structure Decision**: MM-506 stays on the existing retrieval gateway/service and managed-runtime boundaries. No new persistence model is planned; the likely work is contract hardening, runtime-surface wiring, and focused unit/integration verification around capability signalling, bounded request validation, and fail-fast semantics.

## Complexity Tracking

No constitution violations.
