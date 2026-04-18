# Research: Skill Selection and Snapshot Resolution

## Classification

Decision: Treat MM-406 as a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md`; `specs/207-skill-selection-snapshot-resolution/spec.md`.
Rationale: The Jira brief contains one actor, one operational goal, and one independently testable behavior: resolve task/step skill intent into an immutable snapshot before runtime launch.
Alternatives considered: Treating `docs/Tasks/AgentSkillSystem.md` as a broad design was rejected because the Jira brief selected a bounded story from that design.
Test implications: Unit and workflow-boundary tests are required.

## FR-001 / DESIGN-REQ-006

Decision: Partial; selector schemas exist but runtime pre-launch collection and resolution from task/step intent is not complete.
Evidence: `TaskExecutionSpec.skills` and `TaskStepSpec.skills` in `moonmind/workflows/tasks/task_contract.py`; `tests/unit/workflows/tasks/test_task_contract.py`; no `agent_skill.resolve` invocation was found in `moonmind/workflows/temporal/workflows/run.py`.
Rationale: Input normalization is present, but the story requires resolution before runtime launch.
Alternatives considered: Rely on existing plan `registry_snapshot` behavior; rejected because the Jira brief specifically concerns task and step skill intent.
Test implications: Add workflow-boundary coverage that task/step skill intent triggers pre-launch resolution.

## FR-002 / DESIGN-REQ-008 / SC-001

Decision: Missing; task-level and step-level skill selectors parse independently, but no deterministic inheritance/override merge helper was found.
Evidence: `TaskSkillSelectors` validation exists in `task_contract.py`; no merge helper appeared in repo search for task/step skill selectors.
Rationale: The acceptance scenario requires step exclusions to override inherited task skills without mutating the task-level intent.
Alternatives considered: Put merge behavior inline in the workflow; rejected because focused helper tests are simpler and keep workflow code smaller.
Test implications: Add unit tests for include union, set union, step exclusion, materialization mode override, and unchanged task selector input.

## FR-003 / FR-005 / DESIGN-REQ-010 / SC-002

Decision: Implemented but not fully verified through the launch path.
Evidence: `AgentSkillResolver.resolve()` returns `ResolvedSkillSet`; `tests/unit/services/test_skill_resolution.py` covers pinned version mismatch and duplicate same-source failure.
Rationale: Resolver fail-fast behavior exists, but the selected story also needs proof that such failures stop before runtime launch.
Alternatives considered: Add only resolver tests; rejected because the story is about orchestration before launch.
Test implications: Add workflow-boundary failure coverage proving pinned failure prevents AgentRun launch.

## FR-004

Decision: Implemented and verified.
Evidence: `SkillResolutionContext.allow_repo_skills`, `allow_local_skills`; `RepoSkillLoader` and `LocalSkillLoader`; tests for allowed and denied repo/local sources in `tests/unit/services/test_skill_resolution.py`.
Rationale: Source policy is explicit and deny-by-default for repo/local sources.
Alternatives considered: Add new source policy model; rejected because current context already supports the required policy inputs.
Test implications: None beyond final verification.

## FR-006 / FR-007 / DESIGN-REQ-007 / SC-003

Decision: Implemented but needs selected-story boundary proof.
Evidence: `ResolvedSkillSet` in `moonmind/schemas/agent_skill_models.py`; `AgentSkillsActivities.resolve_skills()` writes the resolved set as an artifact and sets `manifest_ref`; `AgentSkillMaterializer` writes `active_manifest.json`.
Rationale: Artifact discipline exists, but runtime preparation must prove it threads compact refs rather than large payloads.
Alternatives considered: Treat activity artifact persistence as sufficient; rejected because the workflow launch path remains unverified.
Test implications: Add boundary assertions for manifest ref and compact `resolvedSkillsetRef`.

## FR-008 / DESIGN-REQ-009

Decision: Partial; activity/service boundaries exist, but the workflow is not yet invoking them for task/step intent.
Evidence: `AgentSkillsActivities.resolve_skills`; `AgentSkillResolver`; `AgentSkillMaterializer`; activity catalog entries for `agent_skill.resolve`, `agent_skill.build_prompt_index`, and `agent_skill.materialize`.
Rationale: The architectural boundary is ready, but the selected runtime path needs to call it.
Alternatives considered: Resolve inside deterministic workflow code; rejected by source requirements and Temporal constraints.
Test implications: Workflow-boundary test should observe an `agent_skill.resolve` activity call or equivalent wrapper invocation before launch.

## FR-009 / SC-004

Decision: Implemented for plan registry snapshots, unverified for task/step resolved snapshots.
Evidence: `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py` covers retry/rerun reuse of `registry_snapshot_ref`.
Rationale: Adjacent snapshot-pinning behavior exists, but MM-406 requires the resolved skill snapshot produced from task/step intent.
Alternatives considered: Reuse the existing tests unchanged; rejected because they use plan registry snapshots rather than task/step selectors.
Test implications: Add focused coverage that the new resolved snapshot ref is reused.

## FR-010 / DESIGN-REQ-019 / SC-005

Decision: Implemented but not verified from the new resolution source.
Evidence: `_build_agent_execution_request(..., resolved_skillset_ref=...)` propagates the ref; `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` covers direct propagation; adapter code preserves `resolved_skillset_ref`.
Rationale: The request field exists, but new tests must prove the resolved snapshot ref from task/step intent reaches the request.
Alternatives considered: Add adapter-only tests; rejected because propagation from the resolution call is the missing behavior.
Test implications: Add launch-path unit or workflow-boundary coverage.

## FR-011

Decision: Implemented and verified.
Evidence: `AgentSkillMaterializer` writes `.agents/skills_active`; `tests/unit/services/test_skill_materialization.py` asserts `.agents/skills` is not written.
Rationale: This preserves the canonical active snapshot path without mutating checked-in skill folders.
Alternatives considered: Change materializer output to `.agents/skills`; rejected because runtime adapters map active snapshots to canonical paths separately.
Test implications: None beyond final verification.

## FR-012 / SC-006

Decision: Implemented and verified at Specify/Plan stages.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md`; `specs/207-skill-selection-snapshot-resolution/spec.md`; this plan.
Rationale: MM-406 and source coverage IDs are preserved.
Alternatives considered: Link only to Jira; rejected because downstream verification needs local artifact traceability.
Test implications: Traceability `rg` check.
