# Research: Agent Skill Catalog and Source Policy

## Classification

Decision: MM-405 is a single-story runtime feature request.
Evidence: `specs/206-agent-skill-catalog-source-policy/spec.md`; `docs/tmp/jira-orchestration-inputs/MM-405-moonspec-orchestration-input.md`.
Rationale: The brief has one operator story and one validation surface: distinguish agent-skill instruction bundles from executable tools, preserve immutable versions, and enforce source policy before resolution/materialization.
Alternatives considered: Documentation-only alignment was rejected because the user selected runtime mode and the brief describes system behavior.
Test implications: Unit and adapter-boundary verification are required.

## FR-001 and FR-002

Decision: implemented_verified.
Evidence: `moonmind/schemas/agent_skill_models.py`; `docs/Tasks/SkillAndPlanContracts.md`; `moonmind/workflows/agent_skills/selection.py`.
Rationale: Agent-skill models and tool/runtime command docs use distinct contract names and categories.
Alternatives considered: Renaming existing contracts was rejected because the current names already express the intended separation.
Test implications: None beyond final verification and traceability checks.

## FR-003 and FR-004

Decision: implemented_unverified.
Evidence: `api_service/db/models.py` defines `AgentSkillDefinition` and `AgentSkillVersion`; `api_service/services/agent_skills_service.py` creates version rows and blocks duplicate version strings; `tests/unit/api/test_agent_skills_service.py` covers successful and duplicate version creation.
Rationale: The insert-only version path exists, but the test suite should explicitly prove that creating a later version preserves the earlier version and exposes both versions.
Alternatives considered: Treating duplicate-version coverage as sufficient was rejected because MM-405 specifically calls for edit/new-version immutability.
Test implications: Add a focused unit test in `tests/unit/api/test_agent_skills_service.py`.

## FR-005, FR-006, and FR-007

Decision: implemented_verified.
Evidence: `AgentSkillSourceKind` includes built-in, deployment, repo, and local; `AgentSkillResolver` merges sources in deterministic order; `tests/unit/services/test_skill_resolution.py` covers source loaders, precedence, deterministic sorting, and provenance source kind.
Rationale: Existing resolver behavior satisfies allowed-source precedence and provenance for covered cases.
Alternatives considered: Reworking precedence was rejected because the current order matches the canonical source policy.
Test implications: No implementation required; optional hardening may add repo/local precedence coverage if nearby tests are edited.

## FR-008, FR-009, FR-010, and DESIGN-REQ-004

Decision: partial.
Evidence: `SkillResolutionContext.allow_local_skills` gates local sources; `LocalSkillLoader` returns no candidates when local is disallowed; `RepoSkillLoader` loads repo skills whenever `workspace_root` exists and has no explicit allow/deny policy.
Rationale: The story requires policy gates for repo and local-only sources before selection or materialization. Local is gated; repo is not.
Alternatives considered: Treating `workspace_root` as implicit repo permission was rejected because repo skill content is potentially untrusted and must not silently affect runs.
Test implications: Add failing resolver tests first for repo-denied exclusion and repo-allowed precedence, then update `SkillResolutionContext` and `RepoSkillLoader` to honor explicit repo policy.

## FR-011 and SC-006

Decision: implemented_verified.
Evidence: `ResolvedSkillSet` carries immutable snapshot metadata; `moonmind/services/skill_materialization.py` writes to `.agents/skills_active`; `tests/unit/services/test_skill_materialization.py` verifies materialization does not write to `.agents/skills`.
Rationale: Runtime materialization already uses a resolved snapshot directory instead of mutating checked-in skill folders.
Alternatives considered: No materializer rewrite is needed.
Test implications: Existing test remains final verification evidence.

## FR-012 and SC-007

Decision: implemented_verified.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-405-moonspec-orchestration-input.md`; `specs/206-agent-skill-catalog-source-policy/spec.md`.
Rationale: MM-405 and the original Jira brief are preserved in the source artifact and spec.
Alternatives considered: None.
Test implications: Add traceability checks in quickstart/tasks and final verification.

## Test Strategy

Decision: Use focused unit tests for resolver/service behavior, then the full unit suite for final verification.
Evidence: Existing test taxonomy in AGENTS instructions and local tests under `tests/unit/services/`, `tests/unit/api/`, and `tests/unit/workflows/agent_skills/`.
Rationale: The required changes are hermetic Python logic and service behavior. Integration CI remains relevant for final regression confidence when Docker is available.
Alternatives considered: Provider verification is unnecessary because this story has no external provider dependency.
Test implications: Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` before finalizing.
