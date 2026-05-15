# Implementation Plan: Normalize Slash-Leading Instructions

**Branch**: `run-jira-orchestrate-for-mm-684-normaliz-efb4d989` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/353-normalize-slash-commands/spec.md`

## Summary

Implement MM-684 by extending the canonical task contract normalization path so task-level and step-level slash-leading instructions produce authoritative runtime command metadata without rewriting authored instructions. The current snapshot builder preserves instructions but has no runtime command parser or metadata fields, so the feature requires test-first additions around `moonmind/workflows/tasks/task_contract.py` and the existing task contract tests. Unit tests will cover parser and snapshot semantics; integration-style task contract coverage will verify the real canonical payload/snapshot boundary.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `build_authoritative_task_input_snapshot()` preserves `objective.instructions` and step instructions but has no slash command metadata | add metadata without changing preserved text | unit + integration |
| FR-002 | missing | no `runtimeCommand` parsing found in task contract models or tests | parse task and step instruction fields | unit + integration |
| FR-003 | missing | no `RuntimeCommandInvocation` model or snapshot output | add compact model/normalization output | unit + integration |
| FR-004 | missing | steps currently preserve instructions, ids, attachments, dependencies only | attach per-step command metadata with source path and target step id | unit + integration |
| FR-005 | missing | no parser grammar exists | add conservative parser covering grammar and opaque/path-like cases | unit |
| FR-006 | missing | no hint status or unknown command handling exists | accept unknown valid commands as opaque for slash-pass-through runtimes | unit |
| FR-007 | missing | escaped slash inputs are treated as plain text only | record escaped literal metadata and safe recognition mode | unit |
| FR-008 | missing | no frontend-supplied metadata validation exists | reject malformed/conflicting supplied metadata in task and step payloads | unit + integration |
| FR-009 | missing | no runtime command policy metadata exists | apply default policy states for unsupported runtimes and malformed command lines | unit |
| FR-010 | missing | no hint model exists | include hint status without allowlist rejection | unit |
| FR-011 | implemented_unverified | `spec.md` preserves MM-684 and canonical brief | carry traceability through tasks/verification | final verify |
| SC-001 | missing | no `/review` snapshot test | add task-level detected command scenario | unit + integration |
| SC-002 | missing | no `/simplify` step snapshot test | add step-level detected command scenario | unit + integration |
| SC-003 | missing | no unknown command pass-through test | add opaque unknown command scenario | unit |
| SC-004 | missing | no escaped literal test | add escaped slash scenario | unit |
| SC-005 | missing | no malformed/unsupported runtime policy test | add path-like and unsupported-runtime scenarios | unit |
| SC-006 | missing | no supplied metadata validation test | add conflicting/malformed metadata tests | unit + integration |
| SC-007 | partial | source requirement mapping exists in `spec.md` | preserve mapping in tasks and verification | final verify |
| DESIGN-REQ-001 | partial | authored instructions are preserved today | add detected metadata while preserving text | unit + integration |
| DESIGN-REQ-002 | partial | `_clean_optional_str()` trims surrounding whitespace today | ensure parser never rewrites command token/body after normalization | unit |
| DESIGN-REQ-003 | missing | no structured command metadata | add runtime command metadata output | unit + integration |
| DESIGN-REQ-004 | missing | no runtime command fields | add required fields or documented compact equivalents | unit |
| DESIGN-REQ-005 | missing | no snapshot `runtimeCommand` fields | add objective and step metadata fields | integration |
| DESIGN-REQ-006 | missing | no parser | add conservative parser | unit |
| DESIGN-REQ-007 | missing | no unknown command handling | add opaque pass-through behavior | unit |
| DESIGN-REQ-008 | missing | no escaped command metadata | add escaped literal behavior | unit |
| DESIGN-REQ-010 | missing | no backend authority over frontend metadata | add validation/rejection behavior | unit + integration |
| DESIGN-REQ-019 | missing | no hint status | add non-blocking hint status defaults | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind task contract helpers  
**Storage**: Existing artifact-backed task input snapshots; no new persistent storage  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: pytest task/workflow boundary tests via `./tools/test_unit.sh`; hermetic integration only if API/workflow submission paths change  
**Target Platform**: MoonMind managed runtime/task orchestration service  
**Project Type**: Python backend contract/model layer  
**Performance Goals**: Runtime command detection must be deterministic and linear in instruction length, with negligible overhead during task submission  
**Constraints**: Preserve authored text; do not introduce allowlist rejection for unknown valid commands; no compatibility aliases for new internal contract names; keep workflow payloads compact  
**Scale/Scope**: One independently testable backend normalization story covering task and step instruction fields

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The feature records runtime command metadata for adapters and does not implement provider command behavior.
- II. One-Click Agent Deployment: PASS. No new external service or deployment dependency.
- III. Avoid Vendor Lock-In: PASS. Metadata is runtime-neutral and preserves unknown provider commands.
- IV. Own Your Data: PASS. Authored instructions and metadata remain in MoonMind-owned snapshots.
- V. Skills Are First-Class and Easy to Add: PASS. No skill-source mutation; this affects task contracts only.
- VI. Scientific Method: PASS. Unit and integration evidence are planned before implementation.
- VII. Runtime Configurability: PASS. Runtime policy is modeled as configurable behavior rather than hard-coded provider allowlists.
- VIII. Modular and Extensible Architecture: PASS. Parser and snapshot normalization stay inside the task contract boundary.
- IX. Resilient by Default: PASS. Payload compatibility risk is limited to adding compact metadata; tests cover real boundary shapes.
- X. Facilitate Continuous Improvement: PASS. Verification artifacts will preserve outcome evidence and MM-684 traceability.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md` and will generate tasks before implementation.
- XII. Canonical Docs vs Tmp: PASS. Implementation tracking stays in `specs/353-normalize-slash-commands/`.
- XIII. Pre-Release Velocity: PASS. New internal names will be direct; no legacy aliases are planned.

## Project Structure

### Documentation (this feature)

```text
specs/353-normalize-slash-commands/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-command-snapshot.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/
└── task_contract.py

tests/unit/workflows/tasks/
└── test_task_contract.py
```

**Structure Decision**: Keep the implementation in the existing canonical task contract module because task input snapshot construction and validation already live there. Add focused tests to the existing task contract test file so coverage runs through the real Pydantic and snapshot boundary used by task submissions.

## Complexity Tracking

No constitution violations or added architectural complexity.
