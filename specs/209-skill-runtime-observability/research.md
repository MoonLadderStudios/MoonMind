# Research: Skill Runtime Observability and Verification

## Input Classification

Decision: Treat MM-408 as a single-story runtime feature request.
Evidence: `spec.md` (Input) contains one user story focused on operator/maintainer inspection and boundary verification.
Rationale: The brief has one actor, one operational goal, and one coherent acceptance set. It references implementation documents, but the selected mode is runtime, so those documents are source requirements rather than docs-only targets.
Alternatives considered: Route through story breakdown; rejected because no multiple independent stories are present.
Test implications: Unit, UI, and focused boundary tests are required.

## Existing Skill Runtime Evidence

Decision: Reuse existing materialization metadata as the source for compact runtime evidence.
Evidence: `moonmind/services/skill_materialization.py` returns `RuntimeSkillMaterialization` with `workspace_paths`, `prompt_index_ref`, and metadata such as `activeSkills`, `backingPath`, `visiblePath`, and `manifestPath`; `tests/unit/services/test_skill_materialization.py` verifies projection, manifest fields, no full body in hybrid metadata, and collision diagnostics.
Rationale: The story asks to surface and verify existing runtime facts, not to create a parallel materialization model.
Alternatives considered: Read `_manifest.json` during task detail serialization; rejected because task-detail render should remain compact and must not dereference full skill artifacts.
Test implications: Unit tests should serialize metadata from execution params and assert no skill body leakage.

## FR-001 Submit-Time Visibility

Decision: Mark implemented_unverified and preserve existing create API behavior.
Evidence: `tests/unit/api/routers/test_executions.py` verifies `task.skills` with sets, include, exclude, and materialization mode pass through create execution initial parameters.
Rationale: The selector contract already exists. This story may add coverage only if touched by the task-detail payload extension.
Alternatives considered: Add a new submit UI selector surface; rejected as outside MM-408's detail/lifecycle observability focus.
Test implications: Existing unit coverage plus final verification.

## FR-002 through FR-006 Task Detail Runtime Evidence

Decision: Add a compact `skillRuntime` execution-detail payload and render it in the existing `SkillProvenanceBadge`.
Evidence: `ExecutionModel` currently has `resolvedSkillsetRef` and `taskSkills`; `frontend/src/components/skills/SkillProvenanceBadge.tsx` renders explicit selection, delegated skill, and snapshot ref only. Materialization-shaped metadata may be present in execution input parameters as `skillsMaterialized` or related skill metadata.
Rationale: Operators need one safe detail surface for selected versions, provenance, materialization mode, visible path, backing path, read-only state, and artifact refs. Extending the existing skill provenance UI keeps the feature discoverable.
Alternatives considered: Add a separate debug-only route; rejected because standard task detail is explicitly in the acceptance criteria.
Test implications: API serialization unit tests and task-detail Vitest coverage.

## FR-007 Projection Diagnostics

Decision: Treat projection diagnostics as implemented_verified and keep regression coverage.
Evidence: `AgentSkillMaterializer._projection_error_message` includes path, object kind, attempted action, and remediation; `tests/unit/services/test_skill_materialization.py` verifies incompatible `.agents/skills` path failures preserve existing content and do not dump bodies.
Rationale: The current behavior already matches the acceptance criterion. This story should not refactor projection failure mechanics unless tests expose a gap.
Alternatives considered: Add a new diagnostic model; rejected because the existing message is already operator-visible and verified.
Test implications: Final regression only unless related code changes.

## FR-008 through FR-010 Lifecycle Intent

Decision: Verify and minimally extend lifecycle metadata so proposal, schedule, rerun, retry, and replay paths do not silently lose skill intent or resolved snapshot reuse.
Evidence: `moonmind/schemas/task_proposal_models.py` includes `taskSkills`; task editing helpers reconstruct `taskSkills`; runtime request models carry `resolvedSkillsetRef`; schedule mapping tests do not currently demonstrate skill intent. `AgentSkillMaterializer` consumes a supplied `ResolvedSkillSet` rather than re-resolving sources.
Rationale: The source design requires lifecycle intent to be explicit. Where payload support exists, tests should prove it; where schedule metadata is missing, add the smallest explicit representation rather than relying on convention.
Alternatives considered: Treat lifecycle behavior as documentation-only; rejected because selected mode is runtime and the acceptance criteria require inspectable metadata.
Test implications: Unit tests for proposal preview skills, rerun/edit reconstruction, schedule payload skill intent, and no silent re-resolution.

## FR-011 Boundary Verification

Decision: Add focused unit and boundary tests only for gaps introduced or exposed by MM-408.
Evidence: `tests/unit/services/test_skill_materialization.py` already covers single-skill projection, multi-skill projection, read-only-ish link metadata, collision failure, and no full-body hybrid metadata. Additional evidence is needed for execution-detail serialization and lifecycle reuse semantics.
Rationale: The requirement is about proving real adapter/activity or API boundary behavior, not isolated helper behavior only.
Alternatives considered: Broad end-to-end Temporal tests; rejected for this story because the required evidence can be covered by existing unit boundaries without adding slow time-skipping workflows.
Test implications: Unit plus UI tests, with hermetic integration only if implementation crosses a compose-backed boundary.

## Traceability

Decision: Preserve MM-408 in all artifacts and final verification.
Evidence: `spec.md` includes the full MM-408 brief and this plan names MM-408.
Rationale: Jira, MoonSpec verification, commits, and PR metadata require source traceability.
Alternatives considered: Preserve only the summary; rejected because final verification compares against the original brief.
Test implications: Final verification traceability check.
