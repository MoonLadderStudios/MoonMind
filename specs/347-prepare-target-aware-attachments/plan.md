# Implementation Plan: Prepare-Time Target-Aware Attachment Materialization

**Branch**: `[347-prepare-target-aware-attachments]` | **Date**: 2026-05-13 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/347-prepare-target-aware-attachments/spec.md`

## Summary

Deliver `MM-648` by tightening existing target-aware attachment preparation so step-scoped attachments require stable step identity, prepared manifests expose stable materialization/status metadata, and regression coverage proves reorder, preset apply, and text edits cannot silently retarget attachments. The implementation builds on `moonmind/workflows/tasks/prepared_context.py`, `moonmind/agents/codex_worker/worker.py`, and existing workflow boundary tests from prior target-aware input work.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
|----|--------|----------|--------------|----------------|
| FR-001 | implemented_verified | `moonmind/workflows/tasks/prepared_context.py` rejects inline content; full unit verification passed | Keep refs/metadata-only behavior traceable | unit + final verification |
| FR-002 | implemented_verified | `moonmind/agents/codex_worker/worker.py` materializes objective and step attachments into target-specific `.moonmind/inputs/...` paths; worker tests passed | No additional implementation | unit |
| FR-003 | implemented_verified | Prepared-context and worker manifests are generated and asserted in tests | No additional implementation | unit |
| FR-004 | implemented_verified | Manifest entries include artifact metadata, target kind/ref, workspace path, content type, size, and status | No additional implementation | unit |
| FR-005 | implemented_verified | Existing target-aware workflow and vision context tests cover per-target context grouping; focused integration passed | No additional implementation | integration + final verification |
| FR-006 | implemented_verified | Reorder/text edit regression tests prove bindings remain keyed by stable step refs | No additional implementation | unit |
| FR-007 | implemented_verified | Worker download failure tests and stable-ref validation fail explicitly with target diagnostics | No additional implementation | unit |
| FR-008 | implemented_verified | Prepared metadata assertions exclude data URLs/base64; workflow payloads carry refs | No additional implementation | unit + final verification |
| FR-009 | implemented_verified | `prepared_context.py` and `worker.py` reject step attachments without `id`, `stepRef`, or `ref` | No additional implementation | unit |
| FR-010 | implemented_verified | `spec.md`, this plan, `tasks.md`, and `verification.md` preserve `MM-648` and the original preset brief | Keep traceability through final delivery metadata | final verification |
| SC-001 | implemented_verified | Worker manifest tests assert one entry per authored objective/step attachment | No additional implementation | unit |
| SC-002 | implemented_verified | Prepared-context tests cover reorder and text edit binding stability | No additional implementation | unit |
| SC-003 | implemented_unverified | Unit worker failure tests identify failed target; no integration test currently proves target-specific preparation failure | add integration verification first; repair preparation boundary only if verification fails | integration + conditional implementation |
| SC-004 | implemented_verified | Existing vision/target-aware workflow evidence plus focused integration confirms per-target refs | No additional implementation | integration |
| SC-005 | implemented_verified | Verification report maps `MM-648`, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 | No additional implementation | final verification |
| DESIGN-REQ-002 | implemented_verified | Binary inputs remain artifact/workspace refs and are not embedded in workflow-visible payloads | No additional implementation | unit + final verification |
| DESIGN-REQ-020 | implemented_verified | Preparation materialization, manifest metadata, target-aware context refs, and explicit failures are covered | No additional implementation | unit + integration |
| DESIGN-REQ-029 | implemented_verified | Stable step-ref enforcement removes array-index retargeting fallback | No additional implementation | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing artifact/vision/prepared-context helpers  
**Storage**: Existing artifact store and workspace-local `.moonmind/` prepared files only; no new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest hermetic integration through `./tools/test_integration.sh` where needed  
**Target Platform**: MoonMind worker/runtime execution on Linux containers  
**Project Type**: Backend workflow/runtime library  
**Performance Goals**: Manifest generation remains linear in attachment count and avoids loading binary bytes into workflow-visible payloads  
**Constraints**: No binary bytes in workflow history; target binding must be explicit; fail fast on invalid preparation; no compatibility aliases for internal contracts  
**Scale/Scope**: One task with objective attachments and multiple step attachments across normalized/preset-derived step payloads

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - uses existing workflow/runtime boundaries and provider adapters.
- II. One-Click Agent Deployment: PASS - no new mandatory external service or secret.
- III. Avoid Vendor Lock-In: PASS - target-aware refs are provider-neutral.
- IV. Own Your Data: PASS - prepared files and context refs stay in operator-controlled artifact/workspace paths.
- V. Skills Are First-Class and Easy to Add: PASS - no skill source mutation or runtime skill contract changes.
- VI. Scientific Method / Tests Anchor: PASS - unit and workflow-boundary tests validate the contract.
- VII. Runtime Configurability: PASS - no hardcoded operator settings added.
- VIII. Modular Architecture: PASS - changes remain in prepared-context and worker preparation boundaries.
- IX. Resilient by Default: PASS - invalid attachments fail explicitly before step execution.
- X. Continuous Improvement: PASS - final evidence preserves `MM-648` traceability.
- XI. Spec-Driven Development: PASS - this plan follows `spec.md` and leads to `tasks.md`.
- XII. Canonical Documentation Separation: PASS - implementation notes stay in feature artifacts, not canonical docs.
- XIII. Pre-Release Velocity: PASS - unsupported ambiguous step attachment payloads fail fast rather than receiving compatibility fallback retargeting.

## Project Structure

### Documentation (this feature)

```text
specs/347-prepare-target-aware-attachments/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── prepared-attachment-manifest.md
├── quickstart.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/prepared_context.py
tests/unit/workflows/tasks/test_prepared_context.py
moonmind/agents/codex_worker/worker.py
tests/unit/agents/codex_worker/test_attachment_materialization.py
tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py
```

**Structure Decision**: Keep pure target-binding and compact workflow metadata in `moonmind/workflows/tasks/prepared_context.py`; keep actual file materialization and `.moonmind/attachments_manifest.json` writing in the Codex worker preparation boundary.

## Complexity Tracking

No constitution violations or additional complexity exceptions.
