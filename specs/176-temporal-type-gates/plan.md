# Implementation Plan: Temporal Type-Safety Gates

**Branch**: `mm-331-from-tool-board-39bd254c` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `/specs/176-temporal-type-gates/spec.md`

## Summary

Add enforceable Temporal type-safety review gates for Jira issue MM-331 from TOOL board. The implementation will codify compatibility evidence, escape-hatch justification, and known anti-pattern checks so unsafe Temporal contract changes fail before they can affect live or replayed workflow histories. The test strategy is TDD-first: focused unit tests for rule evaluation and finding output, plus workflow-boundary or replay-style coverage for representative Temporal contract changes.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, existing MoonMind Temporal workflow test helpers  
**Storage**: No new persistent storage; review findings are produced as deterministic validation output and test evidence  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; targeted pytest for Temporal gate/rule, schema, and workflow unit tests during iteration  
**Integration Testing**: `./tools/test_integration.sh` for the required hermetic `integration_ci` suite when Docker is available; targeted Temporal workflow-boundary or replay-style tests for compatibility-sensitive cases  
**Target Platform**: MoonMind backend Temporal workflow runtime and repository review/test surface  
**Project Type**: Backend Python service and Temporal workflow runtime in a single repository  
**Performance Goals**: Review-gate checks complete within normal unit-test runtime and add no network calls or workflow runtime polling  
**Constraints**: Preserve Temporal workflow/activity/message names; keep compatibility handling at public boundaries; do not introduce compatibility aliases that change billing-relevant or execution semantics; keep source-design migration notes out of canonical docs  
**Scale/Scope**: Representative high-risk Temporal boundaries covered by existing schema, managed-session, activity, typed-execution, and workflow tests; no full boundary inventory implementation in this story

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change strengthens orchestration safety gates without replacing agent behavior.
- **II. One-Click Agent Deployment**: PASS. No new service, secret, cloud dependency, or startup prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The gates target MoonMind Temporal contracts and provider-neutral workflow-facing shapes.
- **IV. Own Your Data**: PASS. The story keeps large and provider-specific bodies out of workflow histories and favors compact refs/evidence.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill runtime behavior is not changed.
- **VI. The Bittersweet Lesson**: PASS. The gates are thin, test-backed policy checks that can evolve as Temporal boundaries become fully typed.
- **VII. Powerful Runtime Configurability**: PASS. No runtime configuration semantics are changed.
- **VIII. Modular and Extensible Architecture**: PASS. Gate logic, rule definitions, and tests stay in existing Temporal/schema/tooling boundaries.
- **IX. Resilient by Default**: PASS. Replay and in-flight safety evidence is required for compatibility-sensitive changes.
- **X. Facilitate Continuous Improvement**: PASS. Failures produce actionable review findings that improve future migrations.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan derives from the single-story spec for MM-331.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs are treated as source requirements; implementation tracking remains in spec artifacts or docs/tmp.
- **XIII. Pre-Release Compatibility Policy**: PASS. The plan rejects hidden compatibility aliases and preserves stable Temporal-facing names unless an explicit cutover plan exists.

## Project Structure

### Documentation (this feature)

```text
specs/176-temporal-type-gates/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── temporal-type-safety-gates.md
└── tasks.md             # Phase 2 output; not created by /speckit.plan
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   ├── temporal_activity_models.py
│   ├── temporal_payload_policy.py
│   ├── temporal_signal_contracts.py
│   └── managed_session_models.py
└── workflows/
    └── temporal/
        ├── activity_catalog.py
        ├── typed_execution.py
        └── workflows/
            └── agent_session.py

tools/
└── validate_temporal_type_safety.py

tests/
├── unit/
│   ├── schemas/
│   │   ├── test_temporal_activity_models.py
│   │   ├── test_temporal_payload_policy.py
│   │   ├── test_temporal_signal_contracts.py
│   │   └── test_managed_session_models.py
│   └── workflows/
│       └── temporal/
│           ├── test_activity_catalog.py
│           ├── test_temporal_type_safety_gates.py
│           └── workflows/
│               └── test_agent_session.py
└── integration/
    └── temporal/
        └── test_temporal_type_safety_gates.py
```

**Structure Decision**: Use the existing Temporal schema, payload policy, activity catalog, typed execution, and workflow test layout. Add a narrowly scoped validation entry point only if implementation needs a reusable review gate outside normal pytest assertions.

## Phase 0: Research Summary

Research is captured in [research.md](./research.md). Key decisions:

1. Model review gate output as deterministic rule findings so failures are actionable and testable.
2. Keep compatibility evidence requirements close to Temporal boundary tests and replay-style fixtures rather than in canonical docs.
3. Treat static anti-pattern checks as focused repository validation with explicit rule IDs.
4. Keep escape-hatch acceptance narrow: public boundary only, transitional, bounded, and compatibility-justified.
5. Use required unit verification first, then run hermetic integration when Docker is available.

## Phase 1: Design Outputs

- [data-model.md](./data-model.md): Defines compatibility evidence, review gate findings, escape-hatch justifications, rule definitions, and anti-pattern cases.
- [contracts/temporal-type-safety-gates.md](./contracts/temporal-type-safety-gates.md): Captures rule IDs, finding output shape, pass/fail behavior, and required evidence.
- [quickstart.md](./quickstart.md): Lists targeted TDD commands, full unit verification, and integration strategy.

## Implementation Strategy

1. Add failing unit tests for compatibility-evidence requirements, anti-pattern fixtures, and escape-hatch justification rules before production changes.
2. Introduce a small rule/finding layer that evaluates representative Temporal type-safety migration inputs and reports actionable failures.
3. Add or update focused checks for raw dictionary activity payloads, public raw dictionary handlers, generic action envelopes, provider-shaped top-level workflow-facing results, unnecessary untyped status leaks, nested raw bytes, and large workflow-history state.
4. Add replay-style or workflow-boundary tests for at least one compatibility-sensitive Temporal change requiring evidence and at least one safe additive change.
5. Preserve existing Temporal activity, workflow, update, signal, and query names; require explicit migration or cutover notes for unsafe non-additive contract changes.
6. Run targeted unit tests first, then `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; run `./tools/test_integration.sh` when Docker is available or record Docker socket unavailability as the blocker.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS.
- **II. One-Click Agent Deployment**: PASS.
- **III. Avoid Vendor Lock-In**: PASS.
- **IV. Own Your Data**: PASS.
- **V. Skills Are First-Class and Easy to Add**: PASS.
- **VI. The Bittersweet Lesson**: PASS.
- **VII. Powerful Runtime Configurability**: PASS.
- **VIII. Modular and Extensible Architecture**: PASS.
- **IX. Resilient by Default**: PASS.
- **X. Facilitate Continuous Improvement**: PASS.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS.
- **XIII. Pre-Release Compatibility Policy**: PASS.

## Complexity Tracking

No constitution violations or complexity exceptions.
