# Research: Skill Projection Noninterference

## Setup Script

Decision: Proceed manually because setup script is blocked by managed branch naming.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` returned `ERROR: Not on a feature branch. Current branch: change-jira-issue-mm-608-to-status-in-pr-995031b1`; `.specify/feature.json` points to `specs/314-skill-projection-noninterference`.
Rationale: The active feature directory is already selected and valid; branch renaming is outside this managed step.
Alternatives considered: Stop planning until branch rename; rejected because the task instruction asks to run planning when artifacts are missing and the feature pointer is explicit.
Test implications: none beyond final artifact validation.

## FR-001 / FR-002 / DESIGN-REQ-002

Decision: implemented_unverified. Existing materializer preserves repo-authored `.agents/skills`, but the plan should still require managed-turn boundary proof.
Evidence: `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/moonmind/services/skill_materialization.py` checks `_is_repo_authored_skills_dir()` and sets `require_agents_link=False`; `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/tests/unit/services/test_skill_materialization.py` has `test_materializer_preserves_existing_agents_skills_directory_on_success`.
Rationale: Unit evidence covers the service-level behavior, but MM-608 is about managed runtime workspaces and final verification contamination, so boundary evidence remains required.
Alternatives considered: Mark implemented_verified; rejected because no integration-style managed-run proof was found.
Test implications: unit + integration boundary.

## FR-003 / DESIGN-REQ-003

Decision: implemented_unverified. Active backing paths are run-scoped and outside `repo` for managed job workspaces.
Evidence: `AgentSkillMaterializer._active_backing_dir()` returns `workspace_root.parent / runtime / skills_active / snapshot_id` when workspace root is named `repo`; `TemporalAgentRuntimeActivities._materialize_selected_agent_skill_for_turn()` passes `run_root / runtime / skills_active / snapshot_id`.
Rationale: This matches the target model; tests should prove the publishable checkout is not polluted with a root `skills_active` during managed materialization.
Alternatives considered: Introduce a new backing path setting; rejected because current behavior already follows the desired path.
Test implications: unit + managed activity boundary.

## FR-004 / DESIGN-REQ-001 / DESIGN-REQ-004

Decision: implemented_unverified. Activation summary uses `visiblePath`, and when alias is unavailable it warns that `.agents/skills` is repo-authored source.
Evidence: `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/moonmind/workflows/temporal/activity_runtime.py` builds active snapshot text from `skill_materialization_metadata["visiblePath"]`; `tests/unit/workflows/temporal/test_agent_runtime_activities.py` includes checked-in skill preservation coverage.
Rationale: The behavior exists, but planned tests should lock the exact operator-facing contract and selected `SKILL.md` path.
Alternatives considered: Hard-code `.agents/skills`; rejected by spec and existing implementation.
Test implications: unit.

## FR-005

Decision: partial. Metadata contains the core fields but may need stricter contract checks and operator-oriented diagnostics.
Evidence: Materializer metadata includes `activeSkills`, `backingPath`, `visiblePath`, `canonicalAliasPath`, `canonicalAliasAvailable`, `canonicalAliasSkippedReason`, `compatibilityPaths`, `manifestPath`, and `repoSkillSourcePreserved`.
Rationale: The spec asks for explicit metadata. Existing fields are close, but tests should verify the complete shape and naming used by execution projections.
Alternatives considered: Add a new Pydantic model immediately; deferred to implementation because current metadata dict may be enough if covered.
Test implications: unit.

## FR-006 / FR-007 / DESIGN-REQ-006

Decision: implemented_unverified. Workspace link ownership checks exist and block unknown symlinks.
Evidence: `is_moonmind_owned_projection()` in `moonmind/workflows/skills/workspace_links.py`; `tests/unit/workflows/test_workspace_links.py` covers unknown symlink rejection.
Rationale: The helper now distinguishes created, reused, skipped, blocked, and failed statuses. Broader table tests should lock safe alias behavior.
Alternatives considered: Keep replacing any symlink; rejected because it can unlink user-owned paths.
Test implications: unit.

## FR-008 / DESIGN-REQ-005

Decision: implemented. Preserve-and-link is not the normal flow, and the dormant helper methods that encoded the old move/restore behavior have been removed.
Evidence: `tests/unit/services/test_skill_materialization.py` asserts `AgentSkillMaterializer` no longer exposes `_move_visible_source_to_preservation_root()`, `_restore_preserved_visible_source()`, or `_should_preserve_visible_source_dir()`.
Rationale: The story requires preserve-and-link not be default and, if retained, be explicit and lease-backed. Removing the inaccessible helpers avoids an accidental return to the old unsafe normal path.
Alternatives considered: Ignore dormant helpers; rejected because they encode the old unsafe behavior and may be reused accidentally.
Test implications: unit.

## FR-009 / FR-010 / DESIGN-REQ-008

Decision: implemented_unverified. Loader guards exist.
Evidence: `BuiltInSkillLoader.skills_root` uses configured/packaged candidates, not `Path.cwd()`; `_is_moonmind_active_projection()` protects `RepoSkillLoader` and `LocalSkillLoader`; `tests/unit/services/test_skill_resolution.py` covers built-in CWD projection, repo projection, runtime-root projection, and hidden local overlay.
Rationale: Existing behavior maps directly to the spec. Keep tests as regression evidence.
Alternatives considered: Allow active projection as repo input; rejected because it contaminates source resolution.
Test implications: unit.

## FR-011 / DESIGN-REQ-009

Decision: partial. MoonSpec verify skill text contains the requested preflight, but a code-level full-suite preflight boundary was not found.
Evidence: `tests/unit/agents/test_moonspec_verify_skill.py` asserts the `moonspec-verify` skill includes `test ! -L .agents/skills`, `test ! -L .gemini/skills`, `git status --porcelain -- .agents/skills .gemini/skills skills_active`, and `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION`.
Rationale: The selected skill may be the owning boundary for MoonSpec verification, but implementation should decide whether to add a runtime helper or stronger activity-level check for full-suite verification.
Alternatives considered: Mark implemented_verified; rejected because only skill-text evidence was found.
Test implications: unit + possibly integration.

## FR-012

Decision: implemented_unverified. Publish filtering is ownership-aware for symlink projections but needs a real repo-authored directory preservation test.
Evidence: `TemporalAgentRuntimeActivities._should_exclude_publish_path()` excludes `.agents/skills`, `.gemini/skills`, and `skills_active` only when they are symlinks to manifest-backed or owned active roots; existing tests cover generated symlink exclusions.
Rationale: Positive generated-state filtering is covered. The opposite case, a real checked-in `.agents/skills` directory, must be locked.
Alternatives considered: Exclude all `.agents/skills`; rejected because it would suppress real repo-authored skill sources.
Test implications: unit.

## FR-013

Decision: partial. Alias status is present in metadata, but structured operator diagnostic event evidence is not obvious.
Evidence: Materializer returns `canonicalAliasSkippedReason` and `compatibilityPaths`; no dedicated `skill_projection_alias_skipped` event was found in searched runtime paths.
Rationale: Operators need clear collision and skip diagnostics. Metadata might be sufficient if surfaced through execution details, otherwise add a structured log/event.
Alternatives considered: Rely only on exception strings; rejected because skipped aliases are nonterminal and still need visibility.
Test implications: unit + integration projection metadata evidence.

## FR-014

Decision: partial. Unit coverage is broad, but the original failure was a verification/workspace interaction.
Evidence: Materializer, resolver, runtime instruction, publish filter, and verify-skill unit tests exist; no single representative hermetic integration test was found that exercises a managed selected-skill turn in a checkout with tracked repo skills and then validates clean verification conditions.
Rationale: A boundary test reduces regression risk across service seams.
Alternatives considered: Unit tests only; rejected because MM-608 was triggered by cross-boundary verification failure.
Test implications: integration_ci or activity boundary unit test with realistic filesystem.

## FR-015

Decision: implemented_unverified. Traceability is preserved in `spec.md` and must continue in downstream artifacts.
Evidence: `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/specs/314-skill-projection-noninterference/spec.md` contains `MM-608` and the original preset brief.
Rationale: Planning should preserve the issue key, and tasks/verification/PR metadata must keep it.
Alternatives considered: None.
Test implications: final MoonSpec verify.

## Test Strategy

Decision: Use TDD with focused unit tests first, then a managed-runtime boundary test.
Evidence: Existing repo conventions require `./tools/test_unit.sh`; relevant suites are under `tests/unit/services`, `tests/unit/workflows`, `tests/unit/agents`, and `tests/unit/agents/codex_worker`.
Rationale: Unit tests can lock the path and metadata contracts cheaply; one integration or realistic activity boundary catches the cross-module failure mode.
Alternatives considered: Full suite only; rejected because it would not localize regressions during implementation.
Test implications: unit + integration.
