# Implementation Plan: Remediation Context Artifacts

**Branch**: `221-remediation-context-artifacts` | **Date**: 2026-04-21 | **Spec**: `specs/221-remediation-context-artifacts/spec.md`
**Input**: Single-story feature specification from `/specs/221-remediation-context-artifacts/spec.md`

## Summary

Implement MM-432 by adding a narrow Remediation Context Builder at the Temporal execution and artifact service boundary. The builder will load the persisted remediation link from MM-431, read compact target execution metadata, normalize selectors and policy hints from `parameters.task.remediation`, write a complete `reports/remediation_context.json` Temporal artifact linked to the remediation execution, and record the artifact ref on the remediation link. Tests cover the builder boundary and migration/model changes while keeping live follow, action execution, and evidence read tools out of scope.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | Remediation links exist, but no context builder exists | Add service boundary for remediation-linked executions only | unit |
| FR-002 | missing | Temporal artifact service exists, but remediation links have no context ref | Add link column, migration, artifact creation and execution linkage | unit |
| FR-003 | missing | Target metadata exists in canonical execution records | Build deterministic context payload | unit |
| FR-004 | missing | Remediation request parameters are persisted | Normalize compact evidence and policy snapshots | unit |
| FR-005 | missing | No remediation evidence bounding exists | Clamp tail lines and task run IDs | unit |
| FR-006 | missing | No context payload exists | Add redaction-safe payload assertions | unit |
| FR-007 | missing | No builder exists | Fail on missing remediation link before artifact write | unit |
| DESIGN-REQ-006 | missing | Desired-state docs only | Generate `remediation.context` artifact as the stable evidence entrypoint | unit |
| DESIGN-REQ-011 | missing | Desired-state docs only | Include compact identity, selectors, observability refs, summaries, policies, and live-follow state | unit |
| DESIGN-REQ-019 | missing | Desired-state docs only | Enforce bounded/ref-only payload shape with no raw logs, secrets, URLs, storage keys, or local paths | unit |
| DESIGN-REQ-022 | missing | Desired-state docs only | Store safe artifact metadata and refs that follow artifact presentation/redaction contracts | unit |
| DESIGN-REQ-023 | missing | Desired-state docs only | Represent missing and partial evidence as bounded degradation or fail-fast validation | unit |
| DESIGN-REQ-024 | implemented_verified | Out-of-scope in `spec.md` | No implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: SQLAlchemy async ORM, Pydantic-compatible JSON payloads, existing Temporal artifact service
**Storage**: Existing Temporal artifact tables plus one nullable remediation link column
**Unit Testing**: pytest via `./tools/test_unit.sh`
**Integration Testing**: Existing hermetic integration runner; this slice is covered at service/artifact unit boundary
**Target Platform**: Linux server / Docker Compose deployment
**Project Type**: Temporal execution service and artifact service boundary
**Performance Goals**: Context generation is bounded to one remediation link lookup, two execution lookups, one artifact write, and bounded payload serialization
**Constraints**: No raw secrets, raw logs, storage URLs, storage keys, absolute local paths, or unbounded diagnostics in context artifacts
**Scale/Scope**: One context artifact per remediation link generation call; first version overwrites the link reference with the latest generated artifact

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Adds orchestration evidence packaging without changing agents.
- II. One-Click Agent Deployment: PASS. Uses existing artifact and migration patterns.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior.
- IV. Own Your Data: PASS. Evidence context is stored in MoonMind-owned artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. No skill contract changes.
- VI. Replaceable Scaffolding: PASS. Builder is a thin service boundary over stable artifacts.
- VII. Runtime Configurability: PASS. No hardcoded provider or environment behavior.
- VIII. Modular Architecture: PASS. Changes stay in remediation and artifact boundaries.
- IX. Resilient by Default: PASS. Missing optional evidence degrades to empty refs; invalid links fail deterministically.
- X. Continuous Improvement: PASS. Provides structured remediation evidence for follow-up tasks.
- XI. Spec-Driven Development: PASS. This spec/plan/tasks set defines the story.
- XII. Canonical Docs vs Tmp: PASS. Desired-state docs remain unchanged.
- XIII. Pre-Release Compatibility: PASS. Adds the canonical field without aliases or compatibility transforms.

## Project Structure

### Documentation (this feature)

```text
specs/221-remediation-context-artifacts/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-context-artifacts.md
└── tasks.md
```

### Source Code

```text
api_service/
├── db/models.py
└── migrations/versions/

moonmind/workflows/temporal/
├── remediation_context.py
└── __init__.py

tests/unit/workflows/temporal/
└── test_remediation_context.py
```

## Complexity Tracking

None.
