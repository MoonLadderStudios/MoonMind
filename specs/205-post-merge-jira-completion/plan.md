# Implementation Plan: Post-Merge Jira Completion

**Branch**: `mm-403-01346526` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/205-post-merge-jira-completion/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed branch name `mm-403-01346526` does not match the script's expected numeric feature-branch pattern. This plan follows the same output contract manually using the active `.specify/feature.json` directory.

## Summary

MM-403 adds post-merge Jira completion to Jira-backed PR publishing runs. The current repository already has parent-owned merge automation, resolver dispositions, Jira readiness gates, trusted Jira tool service operations, and merge automation artifacts, but merge automation currently returns `merged` or `already_merged` immediately after the resolver child succeeds. The implementation will add a trusted, activity-bound post-merge Jira completion step before terminal success, including deterministic issue-key resolution, safe done-transition selection, idempotent already-done handling, and operator-visible completion evidence. Unit tests will cover helper/model/selector behavior and trusted Jira service edge cases; workflow-boundary tests will cover the merge automation success path, blocked paths, and replay/idempotency behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/temporal/workflows/merge_automation.py` returns terminal success after resolver success without Jira completion | insert post-merge Jira completion before terminal success | workflow integration |
| FR-002 | partial | `DISPOSITION_MERGED` and `DISPOSITION_ALREADY_MERGED` are accepted success dispositions | route both dispositions through the same completion path | workflow integration |
| FR-003 | implemented_unverified | completion does not exist, so it cannot run early; existing resolver gating is in merge automation | add regression proving completion is not invoked for waiting, remediation, manual review, or failed dispositions | unit + workflow integration |
| FR-004 | partial | `jiraIssueKey` exists on merge automation input; Jira readiness gate has optional issue key | define Jira-backed classification from configured/captured issue context | unit |
| FR-005 | missing | no post-merge issue-key resolver exists | add deterministic candidate collection and precedence | unit |
| FR-006 | partial | `JiraToolService.get_issue()` exists and enforces project/action policy | validate candidate keys through trusted activity/service boundary | unit + integration |
| FR-007 | missing | no missing/ambiguous post-merge target handling exists | return blocked/failed completion evidence without Jira mutation | unit + workflow integration |
| FR-008 | missing | no already-done post-merge completion path exists | treat done-category issue status as successful no-op | unit + workflow integration |
| FR-009 | partial | `JiraToolService.get_transitions()` and `transition_issue()` exist; transition issue validates current availability when configured | add done-category selector and explicit transition override validation | unit + integration |
| FR-010 | partial | transition service can reject stale transition IDs; required field handling is not planned for post-merge selector | fail closed on zero/multiple done transitions or required fields without defaults | unit + integration |
| FR-011 | missing | merge automation artifacts exist, but no post-merge Jira idempotency state exists | persist compact decision state and make repeated evaluation no-op when already completed/done | unit + workflow integration |
| FR-012 | partial | merge automation summaries and artifact refs exist | add Jira completion decision/evidence to summary and artifacts | unit + workflow integration |
| FR-013 | partial | parent run consumes merge automation result summaries; no post-merge Jira status exists | expose completion status/no-op/blocker through merge automation result summary | workflow integration |
| FR-014 | partial | trusted Jira service exists; merge automation does not call it for post-merge completion | add activity-bound service use; avoid resolver script or shell credentials | unit + static grep |
| FR-015 | missing | no post-merge resolver exists; no guard against fuzzy/multi-issue completion exists | constrain candidate sources to explicit/captured/strict key extraction only | unit |
| FR-016 | missing | no tests cover post-merge Jira completion cases | add red-first unit and workflow/integration tests for listed cases | unit + integration |
| FR-017 | partial | `spec.md` preserves MM-403 and original brief | preserve MM-403 in plan, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | missing | resolver success path returns directly from merge automation | add transition-success path before merged result | workflow integration |
| SCN-002 | missing | already-merged path returns directly from merge automation | add same completion path before already_merged result | workflow integration |
| SCN-003 | missing | no already-done issue handling exists | add no-op decision path | unit + workflow integration |
| SCN-004 | missing | no post-merge target resolver exists | add missing/ambiguous blocked outcome | unit + workflow integration |
| SCN-005 | missing | no post-merge transition selector exists | add transition ambiguity blocked outcome | unit + integration |
| SCN-006 | missing | no post-merge idempotency state exists | add replay/retry safe decision storage and tests | unit + workflow integration |
| DESIGN-REQ-001 | partial | parent-owned merge automation already gates resolver success | add Jira completion before terminal success | workflow integration |
| DESIGN-REQ-002 | partial | merge automation owns resolver lifecycle | keep completion in merge automation workflow/activity boundary | workflow integration |
| DESIGN-REQ-003 | missing | no canonical post-merge issue resolver exists | add resolver helper/model | unit |
| DESIGN-REQ-004 | partial | trusted transition tools exist | add strict done-transition selector | unit + integration |
| DESIGN-REQ-005 | missing | no post-merge idempotency exists | add no-op/completed evidence and retry behavior | unit + workflow integration |
| DESIGN-REQ-006 | partial | merge automation summary artifacts exist | extend artifacts and summary with Jira completion evidence | unit + workflow integration |
| DESIGN-REQ-007 | partial | trusted Jira service exists | use service from activity boundary only | unit + static grep |
| DESIGN-REQ-008 | missing | no post-merge resolver source constraints exist | prohibit fuzzy search and multi-issue completion in selector/resolver | unit |
| DESIGN-REQ-009 | missing | no tests cover MM-403 cases | add targeted unit and integration coverage | unit + integration |
| DESIGN-REQ-010 | partial | spec preserves issue and brief | continue traceability through all artifacts | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing MoonMind Jira tool service, existing artifact and workflow activity catalogs  
**Storage**: Existing Temporal workflow history, memo/search attributes, and artifact outputs; no new persistent database tables planned  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh <python test targets>`  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage when Docker is available; workflow-boundary tests may run through the unit runner when they use stubs/time-skipping helpers  
**Target Platform**: MoonMind backend/workers on Linux containers with local Temporal orchestration  
**Project Type**: Backend workflow orchestration and trusted integration feature  
**Performance Goals**: Post-merge completion performs bounded Jira reads/transition attempts per merge-success disposition and does not duplicate transitions across retries/replay  
**Constraints**: Use trusted Jira service boundaries only; do not expose raw Jira credentials; preserve Temporal workflow payload compatibility or document an explicit cutover; fail closed on ambiguous issue or transition selection; keep large data out of workflow history  
**Scale/Scope**: One Jira issue per Jira-backed merge automation run; no fuzzy search or multi-issue completion in this story

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan extends orchestration around existing agents and trusted Jira tools rather than changing agent behavior.
- **II. One-Click Agent Deployment**: PASS. No mandatory external infrastructure is added beyond the existing optional Jira integration.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior stays behind the existing Jira service/tool boundary.
- **IV. Own Your Data**: PASS. Completion evidence is stored in operator-controlled workflow/artifact outputs.
- **V. Skills Are First-Class and Easy to Add**: PASS. `pr-resolver` remains a skill invoked through the existing child run path.
- **VI. Scientific Method Scaffold**: PASS. The plan requires red-first unit and workflow/integration tests before production code.
- **VII. Runtime Configurability**: PASS. Transition overrides/defaults are modeled as runtime configuration, not hardcoded workflow assumptions.
- **VIII. Modular and Extensible Architecture**: PASS. New behavior is planned behind helper/model/activity boundaries with minimal workflow changes.
- **IX. Resilient by Default**: PASS with required guard. Workflow/activity payload changes are compatibility-sensitive; implementation must preserve existing invocation shapes and add boundary coverage.
- **X. Facilitate Continuous Improvement**: PASS. The feature adds operator-visible evidence and failure reasons.
- **XI. Spec-Driven Development**: PASS. This plan follows the single-story spec in `specs/205-post-merge-jira-completion/spec.md`.
- **XII. Documentation Separation**: PASS. Runtime desired-state docs will be updated in canonical docs; migration notes remain in spec artifacts.
- **XIII. Pre-Release Compatibility Policy**: PASS with required guard. Remove superseded internal shapes in the same change if any are replaced; do not add hidden aliases for billing/semantic inputs.

## Project Structure

### Documentation (this feature)

```text
specs/205-post-merge-jira-completion/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── post-merge-jira-completion.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_models.py
├── integrations/
│   └── jira/
│       ├── models.py
│       └── tool.py
└── workflows/
    └── temporal/
        ├── activity_runtime.py
        └── workflows/
            ├── merge_automation.py
            └── run.py

tests/
└── unit/
    ├── integrations/
    │   └── test_jira_tool_service.py
    └── workflows/
        └── temporal/
            ├── test_merge_gate_models.py
            ├── test_post_merge_jira_completion.py
            └── workflows/
                ├── test_merge_automation_temporal.py
                └── test_run_parent_owned_merge_automation_boundary.py

docs/
├── Tasks/
│   ├── PrMergeAutomation.md
│   └── TaskPublishing.md
└── Tools/
    └── JiraIntegration.md
```

**Structure Decision**: Implement the feature in the backend workflow and trusted Jira integration layers. Keep data contracts in schema modules, side effects in activity/service boundaries, orchestration decisions in `MoonMind.MergeAutomation`, and tests split between helper/unit coverage and workflow-boundary coverage.

## Complexity Tracking

No constitution violations are planned.
