# Implementation Plan: Step Approval Policy

**Branch**: `086-step-review-gate` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/086-step-review-gate/spec.md`

## Summary

Add an optional approval policy that, when enabled, wraps every plan-node execution in `MoonMind.Run` with an LLM-powered validation step. The reviewer evaluates step outputs against input aims. Failed steps are retried with structured feedback. Implemented as a `ApprovalPolicyPolicy` on `PlanPolicy`, a `step.review` Temporal Activity, and a review-retry loop in the execution stage.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Temporal SDK (`temporalio`), Pydantic, existing LLM activity infrastructure
**Storage**: Temporal workflow history (activity results); finish summary artifact (JSON)
**Testing**: pytest via `./tools/test_unit.sh`
**Target Platform**: Linux server (Temporal worker fleet)
**Project Type**: Backend service (Temporal workflows + activities)
**Constraints**: Temporal workflow determinism; history size < 50K events; review activity timeout ≤ 120s default

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ No new external dependencies introduced
- ✅ No new services/containers required
- ✅ Uses existing LLM activity infrastructure
- ✅ All nondeterministic work (LLM calls) in Activities, not Workflow code
- ✅ Backward compatible — disabled by default
- ✅ Tests via `./tools/test_unit.sh`

## Project Structure

### Documentation (this feature)

```text
specs/086-step-review-gate/
├── plan.md                          # This file
├── spec.md                          # Feature specification with DOC-REQ-*
├── research.md                      # Phase 0: research findings
├── data-model.md                    # Phase 1: data model
├── contracts/                       # Phase 1: contracts
│   └── requirements-traceability.md # DOC-REQ-* mapping
├── quickstart.md                    # Phase 1: quickstart guide
├── checklists/
│   └── requirements.md             # Spec quality checklist
└── tasks.md                         # Phase 2: implementation tasks
```

### Source Code (repository root)

```text
moonmind/workflows/skills/tool_plan_contracts.py     # MODIFY: add ApprovalPolicyPolicy, extend PlanPolicy
moonmind/workflows/skills/approval_policy.py             # NEW: ReviewRequest, ReviewVerdict, prompt builder
moonmind/workflows/temporal/activities/step_review.py # NEW: step.review activity
moonmind/workflows/temporal/activity_catalog.py       # MODIFY: register step.review route
moonmind/workflows/temporal/workflows/run.py          # MODIFY: review-retry loop in _run_execution_stage

tests/unit/workflows/skills/test_approval_policy_contracts.py  # NEW: contract tests
tests/unit/workflows/skills/test_approval_policy_policy.py     # NEW: policy parsing tests
tests/unit/workflows/temporal/test_step_review_activity.py  # NEW: activity tests
tests/unit/workflows/temporal/test_run_approval_policy.py       # NEW: workflow loop tests
```

**Structure Decision**: All changes are within the existing `moonmind/workflows/` and `tests/unit/workflows/` directories. No new top-level packages or services.

## Complexity Tracking

No constitution violations. Feature uses existing patterns (dataclasses in contracts, Activities for LLM calls, loop modifications in workflow).
