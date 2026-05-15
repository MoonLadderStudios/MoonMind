# Implementation Plan: Runtime Command Rendering After Context Preparation

**Branch**: `run-jira-orchestrate-for-mm-686-render-r-86f22e9e` | **Date**: 2026-05-15 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:03ecc585-89e8-4589-8bdf-05bc41f57e4a/repo/specs/356-runtime-command-rendering/spec.md`

## Summary

Deliver MM-686 by adding a managed-runtime command rendering boundary that runs after retrieval context injection, skill activation summary projection, and managed runtime note preparation, but before the final Codex CLI or Claude Code command is built. Existing task submission code already derives authoritative runtime command metadata, but managed runtime launch paths currently pass the mutated `instruction_ref` directly to runtimes, so prompt-prefix commands can be displaced by MoonMind-added context. The implementation should add red-first unit tests for render modes and strategy behavior, integration tests for launcher ordering, and code that keeps command recognition first while preserving prepared context and safe failure behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` and `claude_code.py` mutate `request.instruction_ref` during preparation; no final renderer exists. | Add final render step after all context preparation. | unit + integration |
| FR-002 | missing | `ManagedRuntimeStrategy` has `build_command()` and `prepare_workspace()` only; no render outcome boundary. | Add runtime-owned render contract and outcomes. | unit |
| FR-003 | missing | Codex and Claude build commands append current `request.instruction_ref`; no prompt-prefix reordering after context injection or skill summary. | Render prompt-prefix command before body and prepared context. | unit + integration |
| FR-004 | partial | Backend metadata records escaped/malformed literal cases in `moonmind/workflows/tasks/task_contract.py`; runtime launch has no literal render boundary. | Preserve literal outcomes in renderer. | unit |
| FR-005 | missing | Existing tests verify injected context reaches CLI, but not that it stays after a slash command. | Add order-preserving render and regression tests. | integration |
| FR-006 | missing | No typed runtime-command render failure or fallback event exists. | Add typed pre-launch failure/fallback handling through launcher-visible result. | unit + integration |
| FR-007 | missing | Codex and Claude strategies append prompt text but do not render runtime command metadata. | Implement prompt-prefix rendering for both strategies through common contract. | unit + integration |
| FR-008 | partial | Unknown valid commands are normalized as opaque pass-through in `task_contract.py`; runtime launch does not consume that metadata. | Carry opaque metadata to runtime rendering. | unit + integration |
| FR-009 | implemented_unverified | No materialized command renderer exists, so unknown commands are not materialized today by absence rather than explicit policy. | Add explicit guard that unknown commands cannot use materialized mode. | unit |
| FR-010 | partial | Escaped literal metadata exists in `task_contract.py`; launch path does not enforce non-command final rendering. | Add literal wrapper/prefix behavior at render time. | unit + integration |
| FR-011 | missing | Launcher failure classifications exist generally, but not `runtime_command_render_failed` or fallback event handling. | Add render failure classification and fallback event path. | unit + integration |
| FR-012 | partial | Submission normalization treats command text as data; runtime renderers do not yet exist. | Keep renderer inputs data-only and avoid shell construction from command fields. | unit |
| FR-013 | implemented_unverified | Launcher uses `SecretRedactor` and env shaping, but render-specific diagnostics do not exist. | Add tests proving render diagnostics/fallbacks do not expose command-adjacent secrets. | unit |
| FR-014 | implemented_verified | `task_contract.py` marks unknown valid commands as opaque pass-through instead of rejecting missing hints; tests cover this. | Preserve behavior through runtime render consumption. | final verify + targeted unit regression |
| FR-015 | missing | Existing retrieval/skill projection tests assert context is injected/prepended, not that final command recognition order is preserved. | Add launcher integration tests for retrieval, skill summaries, and managed notes after command. | integration |
| FR-016 | implemented_unverified | `spec.md` preserves MM-686 and original brief; later artifacts must keep it. | Preserve traceability in plan, tasks, implementation notes, and final verification. | final verify |
| SCN-001 | missing | No test for prompt-prefix `/review` with all prepared context after command. | Add red-first launcher integration test and implementation contingency. | integration |
| SCN-002 | missing | Codex/Claude strategy tests cover basic command building only. | Add strategy render tests for both runtimes without Create page provider markup. | unit |
| SCN-003 | partial | Unknown command snapshot tests exist, but launch/render tests do not. | Add runtime render tests for opaque unknown commands. | unit + integration |
| SCN-004 | implemented_unverified | No materialized command mode exists, so allowlist behavior needs explicit contract before future materialization. | Add contract and guard tests. | unit |
| SCN-005 | partial | Escaped slash metadata exists; final runtime literal handling absent. | Add literal render tests. | unit + integration |
| SCN-006 | missing | No render-specific failure/fallback event path exists. | Add failure/fallback tests and implementation. | unit + integration |
| DESIGN-REQ-006 | missing | Runtime render modes are documented but not represented in runtime strategy contracts. | Add render mode model and strategy support. | unit |
| DESIGN-REQ-011 | missing | Launcher order currently prepares context and skills before command build but lacks final command renderer. | Insert final renderer after all preparation and before process launch. | integration |
| DESIGN-REQ-012 | missing | Strategy interface lacks render method/result. | Extend strategy boundary. | unit |
| DESIGN-REQ-013 | missing | Codex/Claude prompt-prefix examples are not implemented. | Implement and test Codex/Claude prompt-prefix rendering. | unit + integration |
| DESIGN-REQ-016 | partial | Opaque unknown detection exists; render consumption missing. | Preserve opaque pass-through at runtime. | unit + integration |
| DESIGN-REQ-017 | partial | Escaped literal detection exists; literal runtime wrapping missing. | Add literal render behavior. | unit |
| DESIGN-REQ-018 | partial | Security principles exist in normalization/launcher, but render boundary has no specific validation. | Add untrusted-text and no-secret render tests. | unit |
| DESIGN-REQ-019 | missing | Failure and order tests are absent for runtime rendering. | Add failure, unsupported runtime, and ordering coverage. | unit + integration |
| SC-001 | missing | No Codex/Claude prompt-prefix launch tests exist. | Add 100% scenario coverage for tested runtimes. | unit + integration |
| SC-002 | missing | Retrieval/skill note tests do not assert order after slash command. | Add combined context-order integration tests. | integration |
| SC-003 | partial | Snapshot tests cover unknown valid commands; launch/render tests do not. | Add opaque command render coverage. | unit + integration |
| SC-004 | partial | Snapshot tests cover escaped slash; launch/render tests do not. | Add literal render coverage. | unit + integration |
| SC-005 | missing | No render failure tests exist. | Add failure and fallback tests. | unit + integration |
| SC-006 | implemented_unverified | No materialized command support exists; explicit future guard coverage missing. | Add materialization allowlist guard contract/tests. | unit |
| SC-007 | implemented_unverified | Spec preserves MM-686; downstream artifacts must keep it. | Maintain traceability through tasks and verification. | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing managed runtime launcher/strategy stack, existing task contract runtime command metadata  
**Storage**: No new persistent storage; use existing task input snapshot data, `AgentExecutionRequest` payload fields/parameters, workflow history, and launcher diagnostics  
**Unit Testing**: pytest via `./tools/test_unit.sh` with targeted Python test paths  
**Integration Testing**: pytest integration markers via `./tools/test_integration.sh`; targeted local iteration may use `pytest tests/integration/... -m integration_ci` where supported  
**Target Platform**: Linux managed runtime workers launching Codex CLI, Claude Code, and future managed runtime adapters  
**Project Type**: Python orchestration/runtime service  
**Performance Goals**: Rendering adds no extra network calls and performs deterministic string/model transformation before launch; command construction remains negligible relative to process launch  
**Constraints**: Runtime command metadata must remain compact at workflow boundaries; no raw secrets in rendered diagnostics; unsupported command values fail through explicit runtime policy instead of hidden fallbacks  
**Scale/Scope**: One runtime story covering final render ordering and outcomes for command-leading task instructions across Codex CLI, Claude Code, and future adapter boundaries

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Runtime adapters own command rendering while MoonMind orchestrates final input shape.
- II. One-Click Agent Deployment: PASS. No new infrastructure or required external services.
- III. Avoid Vendor Lock-In: PASS. The plan uses a runtime strategy contract with Codex/Claude implementations rather than Create page or launcher special cases.
- IV. Own Your Data: PASS. Inputs and diagnostics remain in existing MoonMind-owned artifacts/history.
- V. Skills Are First-Class and Easy to Add: PASS. Skill activation summaries remain context inputs; runtime commands are kept distinct from Agent Skills.
- VI. Evolving Scaffolds: PASS. Rendering is behind a replaceable adapter boundary with tests.
- VII. Runtime Configurability: PASS. Runtime capability and policy determine supported render modes.
- VIII. Modular Architecture: PASS. Changes are scoped to task contract consumption, managed runtime strategy interfaces, launcher boundaries, and tests.
- IX. Resilient by Default: PASS. Render failures stop before launch or publish an explicit policy-approved fallback event.
- X. Continuous Improvement: PASS. Failures and fallbacks are observable.
- XI. Spec-Driven Development: PASS. `spec.md` precedes implementation and this plan preserves requirement traceability.
- XII. Canonical Docs Separation: PASS. Runtime work is tracked in MoonSpec artifacts; canonical docs are read as source requirements.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms are planned; unsupported render outcomes fail explicitly.

## Project Structure

### Documentation (this feature)

```text
specs/356-runtime-command-rendering/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-command-rendering.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Created later by /speckit.tasks
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── agent_runtime_models.py
├── workflows/
│   ├── tasks/
│   │   └── task_contract.py
│   └── temporal/runtime/
│       ├── launcher.py
│       └── strategies/
│           ├── base.py
│           ├── codex_cli.py
│           └── claude_code.py
└── rag/
    └── context_injection.py

tests/
├── unit/
│   ├── workflows/tasks/test_task_contract.py
│   ├── workflows/temporal/runtime/strategies/test_remaining_strategies.py
│   └── services/temporal/runtime/test_launcher.py
└── integration/
    └── workflows/temporal/test_managed_session_retrieval_context.py
```

**Structure Decision**: Use the existing Python managed runtime strategy and launcher structure. Runtime command parsing and metadata remain in `task_contract.py`; final rendering belongs at the runtime strategy/launcher boundary after RAG, skill projection, and managed runtime notes are prepared.

## Complexity Tracking

No constitution violations require complexity tracking.
