# Implementation Plan: Managed-Session Retrieval Durability Boundaries

**Branch**: `255-managed-session-retrieval-durability` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/255-managed-session-retrieval-durability/spec.md`

## Summary

MM-507 is a runtime durability story for managed-session retrieval context. The repository already persists `ContextPack` artifacts during startup in `moonmind/rag/context_injection.py`, keeps retrieval packaging in `moonmind/rag/context_pack.py`, and has managed-session reconcile and publication surfaces in `moonmind/workflows/temporal/runtime/managed_session_controller.py` and related runtime tests. The planning focus is to make retrieval durability and reset semantics explicit: keep retrieval truth on artifact/ref-backed MoonMind surfaces rather than session-local cache state, prove large retrieved bodies remain out of durable workflow payloads, and add boundary-level verification for reset or new-epoch behavior where the next step must rebuild retrieval context by rerunning retrieval or reattaching the latest durable context artifact.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/rag/context_injection.py` persists `ContextPack` JSON and records `retrievedContextArtifactPath`, while `docs/Rag/WorkflowRag.md` and shared managed-agent docs define durable truth outside session cache; no reset-era runtime contract proves those durable surfaces are the authoritative recovery path | formalize retrieval durability contract and add boundary tests proving durable artifact/ref state remains authoritative across session continuity changes | unit + integration |
| FR-002 | implemented_unverified | `moonmind/rag/context_injection.py` stores large retrieved bodies in `artifacts/context/` and records compact metadata; `tests/unit/rag/test_context_injection.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` verify compact artifact refs at startup, but no reset-specific proof confirms payload discipline remains intact after continuity changes | add verification-first tests for durable metadata and reset-era compactness; adjust code only if those tests fail | unit + integration |
| FR-003 | partial | retrieval artifacts are workspace-scoped and managed-session reconcile degrades or reattaches active sessions in `moonmind/workflows/temporal/runtime/managed_session_controller.py`, but no retrieval-specific test proves published retrieval evidence survives reset or session-epoch replacement | add reset/epoch boundary verification and make preservation expectations explicit in runtime-managed session paths | unit + integration |
| FR-004 | missing | `docs/Rag/WorkflowRag.md` requires rerun or reattach after reset, but no code or tests currently expose an explicit “latest context pack ref” reattach path for the next step | add a normalized recovery path that can rerun retrieval or reattach the latest durable context artifact/ref for the next step and verify both allowed behaviors | unit + integration |
| FR-005 | partial | Codex and Claude already share `ContextInjectionService` for startup in `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/runtime/strategies/claude_code.py`, and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py`, but reset/continuity durability semantics are not yet proven as the same cross-runtime contract | keep the durability contract runtime-neutral and add boundary proof that continuity semantics remain MoonMind-owned rather than runtime-specific | unit + integration |
| FR-006 | implemented_verified | `spec.md` preserves the original MM-507 preset brief and issue key; this planning phase will preserve them through all generated artifacts | preserve MM-507 through remaining artifacts and final verification output | traceability review |
| DESIGN-REQ-005 | partial | `moonmind/rag/context_injection.py` owns retrieval publication before runtime startup, but authoritative recovery from durable state is not yet proven through reset-era tests | add durability and reset-boundary verification | unit + integration |
| DESIGN-REQ-011 | implemented_unverified | artifact-backed context publication and compact metadata are already present, but proof is startup-centric rather than continuity-centric | add reset-aware compactness verification | unit + integration |
| DESIGN-REQ-012 | partial | docs and architecture files state session state is a continuity cache, but no runtime-boundary test asserts retrieval truth remains authoritative outside the session cache after reset | add explicit contract and test coverage for cache-vs-durable-truth behavior | unit + integration |
| DESIGN-REQ-013 | partial | session reconcile and epoch concepts exist in managed-session code and schemas, but retrieval-specific preservation behavior is not proven | add retrieval preservation checks across reset and epoch replacement | unit + integration |
| DESIGN-REQ-017 | missing | no explicit reattach-latest-context-pack or equivalent normalized retrieval recovery path was found in managed-session boundaries | add deterministic recovery behavior and test it | unit + integration |
| DESIGN-REQ-023 | partial | runtime-neutral startup retrieval exists, but durable truth semantics across continuity boundaries are not yet proven across runtimes | keep contract shared and verify it at runtime boundaries | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing MoonMind RAG services, existing managed-session runtime/controller stack, pytest  
**Storage**: No new persistent storage; reuse existing artifact-backed retrieval context files, existing managed-session continuity records, bounded runtime/workflow metadata, and existing retrieval index state  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_injection.py tests/unit/rag/test_context_pack.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py`  
**Integration Testing**: `./tools/test_integration.sh` for any compatible hermetic coverage, plus targeted workflow/runtime-boundary verification such as `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and an added reset-continuity scenario under `tests/integration/workflows/temporal/` when the proof remains outside `integration_ci`  
**Target Platform**: MoonMind managed-session runtime boundaries, retrieval publication/injection path, and Temporal-backed runtime/session lifecycle  
**Project Type**: Backend runtime and durability-boundary story for managed-session retrieval context  
**Performance Goals**: preserve compact durable payloads, keep retrieval recovery deterministic across resets, and avoid introducing a second persistence model for retrieval truth  
**Constraints**: session-local state must remain a continuity cache only, large retrieved bodies must stay behind durable artifacts/refs, no compatibility wrappers for superseded internal contracts, and unit plus integration strategies must stay explicit  
**Scale/Scope**: one story covering durable retrieval truth, payload compactness, reset and session-epoch continuity semantics, latest-context recovery, and cross-runtime contract consistency

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - plan keeps retrieval durability in MoonMind-owned artifacts, refs, and managed-session boundaries instead of shifting truth into runtime-local memory.
- II. One-Click Agent Deployment: PASS - no new operator setup or external persistence service is introduced.
- III. Avoid Vendor Lock-In: PASS - the durability contract remains runtime-neutral across Codex and future managed runtimes.
- IV. Own Your Data: PASS - authoritative retrieval state remains on MoonMind-controlled artifacts, metadata, and index infrastructure.
- V. Skills Are First-Class and Easy to Add: PASS - no skill-system changes are required.
- VI. Replaceable AI Scaffolding: PASS - focus stays on durable contracts, artifact discipline, and verification evidence.
- VII. Runtime Configurability: PASS - existing retrieval settings and managed-session policies remain configuration-driven.
- VIII. Modular and Extensible Architecture: PASS - likely work stays localized to retrieval publication/injection and managed-session lifecycle boundaries.
- IX. Resilient by Default: PASS - reset behavior, recovery paths, and boundary-level verification are the core of the story.
- X. Facilitate Continuous Improvement: PASS - final verification will produce explicit evidence for MM-507 and expose any remaining continuity gaps.
- XI. Spec-Driven Development: PASS - `spec.md` and the preserved MM-507 Jira preset brief remain the source of truth.
- XII. Canonical Documentation Separation: PASS - planning artifacts remain under `specs/255-managed-session-retrieval-durability/`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases are planned; any contract changes will update callers and tests directly.

## Project Structure

### Documentation (this feature)

```text
specs/255-managed-session-retrieval-durability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-retrieval-durability-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/rag/
├── context_injection.py
├── context_pack.py
└── service.py

moonmind/workflows/temporal/runtime/
├── launcher.py
├── managed_session_controller.py
├── managed_session_store.py
├── managed_session_supervisor.py
└── strategies/
    ├── claude_code.py
    └── codex_cli.py

moonmind/schemas/
├── agent_runtime_models.py
├── managed_session_models.py
└── temporal_models.py

tests/unit/rag/
├── test_context_injection.py
└── test_context_pack.py

tests/unit/services/temporal/runtime/
├── test_launcher.py
└── test_managed_session_controller.py

tests/unit/workflows/temporal/runtime/strategies/
└── test_remaining_strategies.py

tests/unit/workflows/temporal/
└── test_agent_runtime_activities.py

tests/integration/workflows/temporal/
└── test_managed_session_retrieval_context.py
```

**Structure Decision**: MM-507 stays on the existing retrieval publication and managed-session lifecycle boundaries. No new persistence system is planned; the likely work is contract hardening plus reset-aware unit and integration verification around durable artifact authority, latest-context recovery, and runtime-neutral continuity semantics.

## Complexity Tracking

No constitution violations.
