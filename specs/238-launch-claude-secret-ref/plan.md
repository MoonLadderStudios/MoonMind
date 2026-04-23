# Implementation Plan: Launch Claude Secret Ref

**Branch**: `238-launch-claude-secret-ref` | **Date**: 2026-04-22 | **Spec**: `specs/238-launch-claude-secret-ref/spec.md`
**Input**: Single-story feature specification from `specs/238-launch-claude-secret-ref/spec.md`

## Summary

Implement and verify MM-448 runtime launch behavior for Claude Code from the `claude_anthropic` secret-reference provider profile. Repo inspection shows the generic provider-profile materializer already clears configured environment keys, resolves profile secret references, applies `env_template`, and the managed runtime launcher already routes profile secret refs through `resolve_managed_api_key_reference`; however, the exact `claude_anthropic` alias-based `anthropic_api_key -> ANTHROPIC_API_KEY` launch path and missing-secret failure behavior are not directly covered. The planned work is test-first verification at the materializer and launcher boundaries, with a small implementation contingency only if those tests expose a gap.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/workflows/temporal/runtime/launcher.py` invokes `ProviderProfileMaterializer` for managed runtime launches | add Claude-specific launcher regression test | unit |
| FR-002 | implemented_unverified | `ManagedRuntimeProfile` carries profile materialization fields; materializer consumes `secret_refs`, `clear_env_keys`, and `env_template` generically | add `claude_anthropic` profile-shape materializer test | unit |
| FR-003 | implemented_unverified | launcher resolves each `profile.secret_refs` entry through `resolve_managed_api_key_reference`; resolver supports `db://` | add launcher test for `db://` `anthropic_api_key` binding | unit |
| FR-004 | implemented_unverified | materializer supports `env_template` values with `from_secret_ref`; existing tests cover generic `ANTHROPIC_API_KEY` direct refs, not alias-based Claude profile shape | add materializer + launcher assertions for final `ANTHROPIC_API_KEY` | unit |
| FR-005 | implemented_unverified | materializer clears `profile.clear_env_keys`; existing launcher test covers `OPENAI_API_KEY` only | add assertions for `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY` | unit |
| FR-006 | implemented_verified | no new runtime selector or runtime mode is needed; launcher operates on the selected profile object | preserve existing launcher profile path | final verify |
| FR-007 | implemented_verified | launch workflow passes compact `secret_refs` strings and resolves secrets at launch boundary | preserve compact refs; no workflow schema changes | final verify |
| FR-008 | implemented_unverified | materializer and launcher do not intentionally log resolved values, but exact failure/no-leak case is not covered | add no-secret-in-error assertion for missing secret binding | unit |
| FR-009 | implemented_unverified | `resolve_managed_api_key_reference` raises on missing/unreadable refs; materializer raises on unresolved aliases | add secret-free failure regression test | unit |
| FR-010 | implemented_verified | provider-profile materialization remains generic and existing non-Claude tests cover OpenRouter/MiniMax profiles | preserve non-Claude tests | final verify |
| FR-011 | implemented_verified | MM-448 is present in spec and orchestration input | preserve traceability through tasks and verification | final verify |
| SCN-001 | implemented_unverified | generic launch materialization exists | add launcher boundary test for successful Claude profile | unit |
| SCN-002 | implemented_unverified | clear-env behavior exists generically | extend exact Claude conflict-key assertions | unit |
| SCN-003 | implemented_verified | durable payloads carry refs before launch; resolved values remain local to launch env | preserve behavior | final verify |
| SCN-004 | implemented_unverified | missing ref errors already raise, but exact message/no-secret proof is absent | add missing-ref failure test | unit |
| SCN-005 | implemented_verified | no runtime selector changes found or planned | no new work | final verify |
| SC-001 | implemented_unverified | final env injection appears supported | add boundary assertions | unit |
| SC-002 | implemented_unverified | conflict clearing appears supported | add boundary assertions | unit |
| SC-003 | implemented_unverified | no-leak behavior appears supported but is not directly asserted for this path | add failure no-leak assertion | unit |
| SC-004 | implemented_unverified | missing refs raise before launch | add pre-start failure assertion | unit |
| SC-005 | implemented_verified | no new selection model is required | no new work | final verify |
| SC-006 | implemented_verified | spec preserves MM-448 and mapped source requirements | preserve in tasks and verification | final verify |
| DESIGN-REQ-006 | implemented_unverified | MM-447 backend path writes this shape; launch boundary needs exact profile-shape proof | add materializer/launcher tests | unit |
| DESIGN-REQ-013 | implemented_unverified | launcher performs profile-driven materialization and command launch | add end-to-end launcher boundary test | unit |
| DESIGN-REQ-014 | implemented_verified | provider profile list and manual auth sync preserve secret-ref launch fields | preserve adjacent tests | final verify |

## Technical Context

**Language/Version**: Python 3.12 with Pydantic v2 models  
**Primary Dependencies**: Existing managed runtime launcher, provider profile materializer, secret-ref parser/resolver, pytest async tests  
**Storage**: Existing Managed Secret storage referenced by `db://` secret refs; no new persistent storage  
**Unit Testing**: Focused pytest through `./tools/test_unit.sh tests/unit/workflows/adapters/test_materializer.py tests/unit/services/temporal/runtime/test_launcher.py`; final unit verification through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` when feasible  
**Integration Testing**: Boundary-style unit tests cover the launcher/materializer contract without real Claude Code or external credentials; no compose-backed `integration_ci` test is required because no Temporal workflow payload shape or database schema changes are planned  
**Target Platform**: MoonMind managed runtime launch on Linux containers  
**Project Type**: Backend runtime orchestration boundary  
**Performance Goals**: Secret-ref launch materialization adds only existing per-profile secret resolution work and no background polling  
**Constraints**: Do not expose raw Anthropic token values in durable payloads, logs, diagnostics, artifacts, or failure summaries; do not introduce a new runtime selector; preserve existing provider-profile materialization behavior for non-Claude profiles  
**Scale/Scope**: One `claude_anthropic` profile launch path, materializer behavior, launcher boundary tests, and traceability artifacts

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Claude Code remains an external managed runtime launched through the existing adapter/materialization path.
- II. One-Click Agent Deployment: PASS. No new service, storage, or deployment prerequisite is introduced.
- III. Avoid Vendor Lock-In: PASS. Claude-specific launch behavior is profile metadata, not core orchestration branching.
- IV. Own Your Data: PASS. Credentials remain in operator-controlled Managed Secrets and are resolved only at launch.
- V. Skills Are First-Class and Easy to Add: PASS. No executable skill contract changes.
- VI. Replaceable Scaffolding: PASS. Work is boundary tests and narrow launch behavior over existing contracts.
- VII. Runtime Configurability: PASS. The behavior is driven by provider-profile fields and secret refs.
- VIII. Modular Architecture: PASS. Scope stays within runtime launcher/materializer boundaries.
- IX. Resilient by Default: PASS. Missing or unreadable secret bindings fail before process start with secret-free output.
- X. Continuous Improvement: PASS. Evidence is captured in MoonSpec artifacts and tests.
- XI. Spec-Driven Development: PASS. This plan starts from the MM-448 single-story spec.
- XII. Canonical Documentation Separation: PASS. Implementation notes remain under `specs/` and `docs/tmp`; canonical docs are not rewritten.
- XIII. Pre-Release Velocity: PASS. No compatibility alias or translation layer is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/238-launch-claude-secret-ref/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-secret-ref-launch.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/adapters/
├── materializer.py
└── secret_boundary.py

moonmind/workflows/temporal/runtime/
├── launcher.py
└── managed_api_key_resolve.py

tests/unit/workflows/adapters/
└── test_materializer.py

tests/unit/services/temporal/runtime/
└── test_launcher.py

docs/tmp/jira-orchestration-inputs/
└── MM-448-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing runtime launcher and materializer modules. Add focused boundary tests beside the existing materializer and launcher coverage, with production changes only if those tests expose a behavioral gap.

## Complexity Tracking

No constitution violations.
