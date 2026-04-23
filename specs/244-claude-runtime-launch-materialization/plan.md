# Implementation Plan: Claude OAuth Runtime Launch Materialization

**Branch**: `244-claude-runtime-launch-materialization` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/244-claude-runtime-launch-materialization/spec.md`

## Summary

Implement MM-481 by closing the Claude OAuth-backed runtime launch gap after provider-profile verification and registration. Repo inspection shows the Claude OAuth provider defaults and seeded `claude_anthropic` profile already exist, and generic materialization logic already strips `clear_env_keys` and validates OAuth-home profile fields. The remaining delivery risk is launch-boundary proof: current tests cover Claude API-key profile launching and Codex OAuth-session diagnostics, but they do not yet prove that a Claude task launched from the OAuth-backed `claude_anthropic` profile materializes the auth volume at the Claude home path, applies the Claude home environment consistently, clears competing API-key variables, and keeps auth-volume paths and contents out of operator-visible diagnostics and artifact surfaces. Plan work is therefore test-first around the Claude launch boundary, with production changes only where those tests expose a gap.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| -- | -- | -- | -- | -- |
| FR-001 | implemented_unverified | `api_service/main.py` auto-seeds `claude_anthropic`; launch/session payloads carry `profileRef` and `credentialSource` generically | add Claude OAuth launch/session boundary tests to prove profile resolution before startup | unit + integration contingency |
| FR-002 | partial | seeded `claude_anthropic` profile in `api_service/main.py`; schema validation in `moonmind/schemas/agent_runtime_models.py` | verify launch preparation preserves OAuth-home profile shape; fix launch shaping if profile fields are dropped or overridden | unit |
| FR-003 | partial | provider registry defaults in `moonmind/workflows/temporal/runtime/providers/registry.py`; managed-session diagnostics carry `volumeRef`/`authMountTarget` generically | add Claude-specific launch/session tests for auth-volume materialization at `/home/app/.claude`; implement if not propagated | unit + integration contingency |
| FR-004 | partial | `moonmind/agents/base/adapter.py` injects `CLAUDE_HOME`; `moonmind/agents/codex_worker/runtime_mode.py` validates OAuth `CLAUDE_HOME` | add boundary tests that prove `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH` are set for the Claude OAuth launch path; implement if missing | unit |
| FR-005 | partial | generic `ProviderProfileMaterializer` clears `profile.clear_env_keys`; seeded profile includes `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, `OPENAI_API_KEY` | add Claude OAuth launch tests proving ambient keys are absent before runtime start; implement if any key still leaks | unit |
| FR-006 | implemented_unverified | Codex auth-diagnostics sanitization tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`; spec-level security rules in `docs/ManagedAgents/ClaudeAnthropicOAuth.md` | add Claude launch/session sanitization tests for auth diagnostics and launch-visible metadata; implement redaction or exclusion fixes if needed | unit + integration contingency |
| FR-007 | missing | no current Claude launch test proves auth volume is excluded from workspace or artifact-backed paths | add tests for launch diagnostics, workspace setup, and artifact-related metadata; implement explicit exclusion if current behavior conflates auth volume and workspace/artifact paths | unit + integration |
| FR-008 | implemented_verified | `spec.md` preserves MM-481 and the original preset brief | preserve traceability through tasks, verification, and any implementation notes | none beyond final verify |
| SC-001 | implemented_unverified | generic launcher and managed-session boundaries exist, but no Claude OAuth-home proof | add focused launch/session tests first | unit |
| SC-002 | partial | generic `clear_env_keys` logic exists; no Claude OAuth-home test covers all three key families | add Claude launch env assertions and implement any missing clearing | unit |
| SC-003 | partial | provider registry and profile seeding define the intended mount path; no Claude runtime launch assertion exists | add launch/session tests for volume mount target and Claude home variables | unit |
| SC-004 | implemented_unverified | Codex diagnostics sanitization exists; no Claude launch-specific proof exists | add Claude auth-diagnostics and artifact-surface sanitization tests | unit + integration contingency |
| SC-005 | implemented_unverified | surrounding launcher and non-Claude tests exist | rerun focused existing launcher/runtime suites after changes | unit |
| DESIGN-REQ-003 | partial | OAuth-backed Claude profile shape is seeded with volume refs and clear-env keys | verify that the launch path consumes the seeded shape unchanged | unit |
| DESIGN-REQ-004 | implemented_unverified | provider profile shape and validation exist | add boundary proof that launch does not degrade to API-key or ad hoc auth | unit |
| DESIGN-REQ-015 | partial | provider registry defaults and auth-mount diagnostics exist | prove OAuth-home materialization at the Claude home path during launch | unit |
| DESIGN-REQ-017 | implemented_unverified | sanitized auth diagnostics exist for Codex; Claude launch path lacks direct proof | add Claude-specific no-leak tests | unit |
| DESIGN-REQ-018 | partial | generic materializer clears keys; auth-mount target is surfaced safely; no Claude launch proof for full contract | add launch/session boundary tests and implement any missing env/materialization behavior | unit + integration contingency |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, Temporal Python SDK, pytest, existing managed-runtime launcher/session services  
**Storage**: Existing provider-profile rows, managed session payloads, managed run workspace/runtime support directories, existing artifact surfaces; no new persistent tables  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and focused pytest targets under `tests/unit/services/temporal/runtime/`, `tests/unit/workflows/temporal/`, and Claude worker preflight tests  
**Integration Testing**: `./tools/test_integration.sh` when launch-surface behavior changes could affect hermetic managed runtime, artifact, or worker-topology seams  
**Target Platform**: Linux API and worker containers, managed runtime launch/session execution in MoonMind-managed environments  
**Project Type**: FastAPI control plane plus Temporal-backed managed runtime/session orchestration  
**Performance Goals**: Launch shaping remains bounded to existing environment/materialization work; no new network round-trips or persistent storage; diagnostics stay compact and sanitized  
**Constraints**: No raw credential file contents, secret-bearing paths, environment dumps, or auth-volume listings in workflow history, logs, diagnostics, or artifacts; no compatibility wrappers; preserve existing non-Claude and non-OAuth launch behavior  
**Scale/Scope**: One runtime (`claude_code`), one OAuth-backed profile (`claude_anthropic`), one launch materialization path, one independently testable story  

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan works within the existing managed runtime launcher and session activity boundaries.
- II. One-Click Agent Deployment: PASS. No new operator dependencies or cloud services are introduced.
- III. Avoid Vendor Lock-In: PASS. Claude-specific behavior remains behind runtime/provider boundaries rather than changing orchestration contracts globally.
- IV. Own Your Data: PASS. Auth material remains volume-backed and operator-controlled; only compact diagnostics leave the boundary.
- V. Skills Are First-Class: PASS. MoonSpec artifacts preserve MM-481 traceability for downstream automation.
- VI. Bittersweet Lesson: PASS. The plan keeps scaffolding thin and adds tests around stable launch contracts.
- VII. Runtime Configurability: PASS. Existing profile/env-driven materialization remains the control surface.
- VIII. Modular Architecture: PASS. Work stays inside provider profile seeding, launcher/session shaping, and runtime tests.
- IX. Resilient by Default: PASS. The plan adds boundary tests for fail-closed auth handling and sanitized diagnostics.
- X. Continuous Improvement: PASS. Requirement-status evidence and quickstart commands capture verification paths.
- XI. Spec-Driven Development: PASS. This plan follows one story from the active MM-481 spec.
- XII. Canonical Docs vs Tmp: PASS. Canonical docs remain source requirements; Jira brief remains under `docs/tmp`.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases, hidden fallbacks, or partial migrations are proposed.

## Project Structure

### Documentation

```text
specs/244-claude-runtime-launch-materialization/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-runtime-launch.md
└── tasks.md
```

### Source Code

```text
api_service/main.py
moonmind/workflows/temporal/runtime/providers/registry.py
moonmind/workflows/adapters/materializer.py
moonmind/workflows/adapters/managed_agent_adapter.py
moonmind/workflows/temporal/activity_runtime.py
moonmind/workflows/temporal/runtime/launcher.py
moonmind/agents/base/adapter.py
moonmind/agents/codex_worker/runtime_mode.py
moonmind/agents/codex_worker/cli.py
tests/unit/services/temporal/runtime/test_launcher.py
tests/unit/workflows/temporal/test_agent_runtime_activities.py
tests/unit/agents/codex_worker/test_cli.py
```

**Structure Decision**: Add failing Claude OAuth launch/session boundary tests first in the existing launcher/activity suites, then make the smallest production changes in shared materialization or Claude-specific launch shaping only where the new tests expose a gap.

## Complexity Tracking

No constitution violations.
