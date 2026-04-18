# Phase 0 Research: Jira Breakdown and Orchestrate Skill

## Input Classification

Decision: Treat MM-404 as one runtime story for a composite Jira Breakdown and Orchestrate workflow surface.

Evidence: `specs/207-jira-breakdown-orchestrate-skill/spec.md` contains exactly one `## User Story - Jira Breakdown to Ordered Orchestration` section and preserves the MM-404 Jira preset brief.

Rationale: The story has one independently testable outcome: one source issue/design can be broken down into ordered stories, converted into downstream Jira Orchestrate tasks, and wired with dependencies.

Alternatives considered: Running `moonspec-breakdown` was rejected because the Jira brief is not a broad technical design requiring multiple new specs. Treating this as docs-only was rejected because runtime mode was selected and the behavior is executable orchestration.

Test implications: No tests beyond final traceability verification for classification.

## Existing Jira Breakdown Surface

Decision: Reuse the existing `jira-breakdown` seeded preset rather than replacing it.

Evidence: `api_service/data/task_step_templates/jira-breakdown.yaml` runs `moonspec-breakdown` followed by `story.create_jira_issues`. `tests/unit/api/test_task_step_templates_service.py` and `tests/integration/test_startup_task_template_seeding.py` verify the seeded preset, story output mode, Jira target fields, and `linear_blocker_chain` dependency mode.

Rationale: MM-404 explicitly says the new skill should perform the normal Jira Breakdown workflow. Reusing the seeded preset keeps existing behavior authoritative.

Alternatives considered: Duplicating breakdown instructions in a new agent prompt was rejected because it would drift from the normal Jira Breakdown workflow.

Test implications: Add seed expansion tests proving the composite preset includes the normal breakdown/Jira issue creation path before downstream task creation.

## Existing Jira Orchestrate Surface

Decision: Create downstream tasks that run the existing Jira Orchestrate flow for each generated Jira story issue.

Evidence: `api_service/data/task_step_templates/jira-orchestrate.yaml` already moves one issue through In Progress, blocker preflight, MoonSpec lifecycle, PR creation, and Code Review. Existing unit/integration seed tests verify the ordered Jira Orchestrate steps.

Rationale: MM-404 asks each generated story to run Jira Orchestrate. The safest interpretation is to create one top-level Jira Orchestrate task per created story, not to inline its stages inside the breakdown run.

Alternatives considered: Inlining Jira Orchestrate steps inside the composite run was rejected because the spec excludes downstream story implementation inline inside the breakdown step and would make each story less independently observable.

Test implications: Add tests proving downstream task requests select or expand Jira Orchestrate behavior and do not include implementation work inside the breakdown result step.

## Story Issue Creation and Ordering

Decision: Use existing `story.create_jira_issues` output as the source of ordered Jira story issue mappings.

Evidence: `moonmind/workflows/temporal/story_output_tools.py` returns `jira.issueMappings` with `storyId`, `storyIndex`, `summary`, and `issueKey`, and returns `linkResults`, `dependencyMode`, and `dependencyChainComplete`. Existing tests cover issue creation, existing issue reuse, dependency mode `none`, dependency mode `linear_blocker_chain`, and partial link failures.

Rationale: The issue mappings are already the normalized bridge from breakdown stories to concrete Jira issue keys. The composite orchestration should consume that structured output rather than parsing prompt text.

Alternatives considered: Reading story markdown output was rejected because structured issue mappings already exist and are easier to validate.

Test implications: Unit tests should use representative issue mappings directly, including three-story, one-story, zero-story, and partial-failure cases.

## Task Dependency Contract

Decision: Create downstream task dependencies through create-time `payload.task.dependsOn` using the workflow ID of the immediately earlier downstream task.

Evidence: `docs/Tasks/TaskDependencies.md` defines task dependencies as `MoonMind.Run` to `MoonMind.Run` create-time dependencies. `api_service/api/routers/executions.py` normalizes `payload.task.dependsOn`, and `moonmind/workflows/temporal/service.py` validates and persists dependency edges. The operator guide states template-authored dependency graphs are not supported.

Rationale: A later task can only depend on an earlier task after the earlier task has a concrete workflow ID. Sequential downstream task creation satisfies the create-time-only contract while keeping each task separately visible and rerunnable.

Alternatives considered: Adding dependency annotations to task templates was rejected because the current contract explicitly does not support template-authored dependency graphs. Using Jira blocker links alone was rejected because MM-404 asks for task dependencies, not only Jira issue relationships.

Test implications: Add unit tests that stub task creation and verify the second created task depends on the first workflow ID, third depends on second, and the first has no dependency.

## Downstream Task Creation Boundary

Decision: Add a narrow deterministic tool or service helper that consumes ordered Jira issue mappings and creates one task-shaped `MoonMind.Run` per issue.

Evidence: `TemporalExecutionService.create_execution()` is the durable execution creation boundary, while `/api/executions` validates task-shaped payloads and dependency declarations. No existing helper creates a sequence of new Jira Orchestrate tasks from a story output result.

Rationale: The behavior is side-effecting and should be bound to a trusted service/activity path, not raw shell commands or prompt-only instructions. A narrow helper also makes idempotency and partial outcomes testable.

Alternatives considered: Asking an agent to call the UI manually was rejected because it would be non-deterministic and hard to verify. Creating Jira issues without MoonMind tasks was rejected because it is already covered by Jira Breakdown and does not satisfy MM-404.

Test implications: Unit tests should stub execution creation and assert payload shapes, dependency order, idempotency keys, and partial failure reporting.

## Idempotency and Partial Outcomes

Decision: The downstream task creator should use stable per-story idempotency keys and return a structured orchestration result with created tasks, skipped stories, dependency edges, and failures.

Evidence: `TemporalExecutionService.create_execution()` supports `idempotency_key`; `story.create_jira_issues` already returns structured partial Jira/link results.

Rationale: Creating multiple executions is side-effecting. Stable idempotency prevents duplicate tasks on retry, and a structured result prevents false success when only part of the chain was created.

Alternatives considered: Treating any downstream failure as an exception only was rejected because operators need to know which tasks were already created before retrying or remediating.

Test implications: Add unit tests for partial task creation failure, dependency validation failure, and duplicate/idempotent creation behavior.

## Trusted Jira Boundary

Decision: Keep Jira issue creation and issue-link operations on existing trusted Jira tool surfaces; downstream task creation should consume trusted outputs, not raw Jira credentials.

Evidence: `story.create_jira_issues` uses `JiraToolService`; `.agents/skills/jira-issue-creator/SKILL.md` requires trusted Jira tools and forbids raw credentials.

Rationale: The feature composes Jira-backed work but should not introduce a new Jira client or expose credentials to the runtime.

Alternatives considered: Direct Jira REST calls from the composite skill were rejected for security and policy reasons.

Test implications: Static checks and review should confirm no raw Jira credential access is introduced. Unit tests should rely on stubs.

## Unit Test Strategy

Decision: Use targeted unit tests for seed expansion, downstream task payload construction, dependency wiring, partial results, and existing story-output behavior.

Evidence: Existing relevant coverage lives in `tests/unit/api/test_task_step_templates_service.py`, `tests/unit/workflows/temporal/test_story_output_tools.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/workflows/temporal/test_temporal_service.py`.

Rationale: Most behavior can be tested hermetically without live Jira or Docker.

Alternatives considered: Provider verification tests were rejected because this story should not require live Jira credentials.

Test implications: Iterate with targeted `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh ...` commands and run the full unit suite before final verification.

## Integration Test Strategy

Decision: Add or extend startup seed integration coverage for the new preset, and use Docker-backed integration only when available.

Evidence: `tests/integration/test_startup_task_template_seeding.py` verifies seeded Jira Breakdown and Jira Orchestrate templates. Repo instructions note managed containers may not expose Docker.

Rationale: The seeded preset must survive startup synchronization, while the task-creation logic can be tested with stubs.

Alternatives considered: Live end-to-end Jira runs were rejected because they require external credentials and are not required for CI.

Test implications: Quickstart includes `./tools/test_integration.sh`, with Docker-unavailable blockers recorded when needed.
