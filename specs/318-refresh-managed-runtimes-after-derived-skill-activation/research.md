# Research: Refresh Managed Runtimes After Derived Skill Activation

## FR-001 / SC-001 / DESIGN-REQ-003

Decision: Treat activation-after-materialization as implemented but insufficiently verified at the real boundary.
Evidence: `moonmind/workflows/agent_skills/agent_skills_activities.py` materializes a derived set before returning `activated`; `tests/integration/temporal/test_skills_on_demand_request_activation.py` verifies activation with a mocked materializer.
Rationale: The sequence is present, but the integration evidence does not prove real materialization completes before activation output is accepted.
Alternatives considered: Marking implemented_verified was rejected because the materializer is mocked in the activation integration test.
Test implications: Integration test using real or faithful materialization fixture.

## FR-002 / DESIGN-REQ-001 / DESIGN-REQ-003

Decision: Add manifest and content integrity verification before activation.
Evidence: `moonmind/services/skill_materialization.py` writes `_manifest.json` and records `content_digest`, but no digest comparison against extracted/read payload was found.
Rationale: The spec explicitly requires manifest and checksum verification before runtime activation.
Alternatives considered: Rely on artifact service integrity only; rejected because MM-615 calls out manifest/checksum verification at activation time.
Test implications: Unit tests for checksum mismatch and integration activity tests for denied activation on verification failure.

## FR-003 / SC-002

Decision: Add staged projection or equivalent proof that runtimes cannot observe partial `.agents/skills` projection state.
Evidence: `ensure_shared_skill_links()` creates/reuses symlinks after backing dir creation, but `AgentSkillMaterializer` clears and writes the target active dir directly.
Rationale: New snapshot aliases appear after writing, but retry/same-snapshot cases and partial-write failures need explicit safety.
Alternatives considered: Document current behavior only; rejected because the acceptance criteria require proof, not just intent.
Test implications: Unit tests for partial write failures, symlink replacement, and existing active snapshot visibility.

## FR-004 / FR-008 / SC-004

Decision: Keep compact activation result shape and add activation-boundary tests.
Evidence: `SkillsOnDemandRequestResult` has `activation_summary` and compact `materialization`; tests assert content refs and body text do not leak.
Rationale: Current model is aligned with MM-615 but needs boundary evidence that all activation paths remain compact.
Alternatives considered: Add body-readable refs for runtime convenience; rejected by source security rules.
Test implications: Unit and integration assertions over serialized activation and failure outputs.

## FR-005 / SC-003 / DESIGN-REQ-004

Decision: Add explicit activation timing/guidance metadata for `atomic` vs `next_turn` or controlled steer-point activation.
Evidence: No field or metadata convention was found that tells a runtime whether activation is immediate or deferred.
Rationale: v1 may defer projection mutation, but the runtime needs compact guidance for when to read active Skill files.
Alternatives considered: Infer timing from visible path; rejected as ambiguous and fragile.
Test implications: Unit tests for materialization summary/guidance and integration test for non-atomic fallback.

## FR-006 / DESIGN-REQ-007

Decision: Treat materialization failure preservation as already verified.
Evidence: `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and `tests/integration/temporal/test_skills_on_demand_request_activation.py` verify `materialization_failed`, active snapshot preservation, and no persisted manifest after failure.
Rationale: Existing tests match the requirement and acceptance criteria for materialization failure.
Alternatives considered: Add only final verification; acceptable unless implementation changes this path.
Test implications: None beyond final verify unless related code is edited.

## FR-007 / FR-012 / SC-005

Decision: Add a concrete runtime refresh failure path and tests that distinguish it from materialization failure.
Evidence: `_skills_on_demand_runtime_code()` can return `runtime_refresh_failed`, but no runtime refresh delivery boundary or direct test was found.
Rationale: MM-615 requires distinct `materialization_failed` and `runtime_refresh_failed` outcomes.
Alternatives considered: Treat all RuntimeError values as materialization failures; rejected because the spec requires distinguishable diagnostics.
Test implications: Unit and integration failure simulations for post-materialization refresh/update failure.

## FR-009 / SC-006 / DESIGN-REQ-006

Decision: Add explicit negative coverage for external agents.
Evidence: External agent adapters are separate from managed runtime activities, but no test explicitly proves Skills On Demand activation is not exposed to external agents in v1.
Rationale: The source design makes external-agent exclusion an operator-visible v1 boundary.
Alternatives considered: Rely on absence of call sites; rejected because regressions could add accidental exposure.
Test implications: Unit boundary test against external-agent adapter/capability surfaces.

## FR-010 / FR-011 / DESIGN-REQ-008

Decision: Strengthen adapter-boundary tests for projection ownership and repo/local source separation.
Evidence: `AgentSkillMaterializer` skips repo-authored `.agents/skills`; `workspace_links.py` refuses unknown symlink targets; tests cover source preservation and non-symlink handling.
Rationale: Existing behavior is close but MM-615 requires runtime refresh not to publish projection changes as repo-authored changes and adapters not to broaden active Skill sets.
Alternatives considered: Treat materializer-only tests as sufficient; rejected because adapter boundaries are explicitly in scope.
Test implications: Unit tests for materializer/link helpers and integration/worker-boundary tests for adapter-visible active paths.

## FR-013 / SC-007

Decision: Preserve MM-615 and the canonical Jira preset brief through all artifacts.
Evidence: `spec.md` contains the original preset brief; this plan references MM-615.
Rationale: Final verification and PR metadata need the original source.
Alternatives considered: Summarize the Jira issue only; rejected by the task instruction and spec.
Test implications: Final MoonSpec verification.

## Test Tooling

Decision: Use `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration verification.
Evidence: Repository instructions define these as the canonical runners. Existing relevant tests are under `tests/unit/...` and `tests/integration/temporal/...` with `integration_ci` markers.
Rationale: The feature changes Temporal activity/materializer contracts and must be verified at unit and integration boundaries.
Alternatives considered: Direct pytest only; acceptable for iteration but not final verification.
Test implications: Unit and integration strategies remain separate in tasks and quickstart.
