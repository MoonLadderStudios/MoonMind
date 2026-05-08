# Research: Disabled Skills On Demand Controls

## Setup Script

Decision: Use the setup script with an explicit feature override.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed on the managed branch name `run-jira-orchestrate-for-mm-612-add-disa-1222f797`; `SPECIFY_FEATURE=315-disabled-skills-on-demand-controls .specify/scripts/bash/setup-plan.sh --json` succeeded and returned this feature's spec and plan paths.
Rationale: The active branch is managed by Jira Orchestrate and does not use numeric MoonSpec naming. The explicit feature pointer resolves the correct artifact directory without renaming the branch.
Alternatives considered: Stop planning until branch rename; rejected because the feature directory and `.specify/feature.json` already identify the active spec.
Test implications: none beyond artifact validation.

## FR-001 / FR-002 / DESIGN-REQ-012

Decision: missing. Add a global disabled-by-default setting with both required names.
Evidence: Searches for `MOONMIND_SKILLS_ON_DEMAND_ENABLED`, `WORKFLOW_SKILLS_ON_DEMAND_ENABLED`, and `skills_on_demand` found only `docs/Steps/SkillsOnDemand.md`; `moonmind/config/settings.py` has many workflow settings but no Skills On Demand gate.
Rationale: The source brief explicitly requires deterministic default false behavior and both aliases. Settings are the existing repo pattern for operator-visible runtime configuration.
Alternatives considered: Hard-code disabled behavior without a setting; rejected because the operator must intentionally enable the capability later without code changes.
Test implications: unit tests for unset default and each alias.

## FR-003 / FR-004 / DESIGN-REQ-011

Decision: missing. Add disabled-first query and request command handling.
Evidence: No code references `moonmind.skills.query`, `moonmind.skills.request`, or `feature_disabled` outside docs. Existing `AgentSkillResolver` and `AgentSkillsActivities.resolve_skills` only cover initial selection and would create or return snapshots when invoked.
Rationale: Disabled on-demand attempts must return a deterministic denial before catalog lookup, resolution, artifact persistence, or materialization.
Alternatives considered: Let missing commands fail generically; rejected because the spec requires status `denied`, code `feature_disabled`, no query results, and no derived snapshot.
Test implications: unit tests for response shape and mocks proving resolver/materializer are not called; integration or activity-boundary tests for managed-runtime calls.

## FR-005 / FR-006

Decision: partial. Make disabled activation behavior explicit.
Evidence: `moonmind/agents/codex_worker/worker.py` and Temporal runtime instruction preparation tell agents where selected Skill content is available, and no on-demand commands are currently exposed. They do not include a Skills On Demand disabled statement or an explicit hide/disable decision.
Rationale: Incidental absence of commands is not enough once an on-demand control surface exists. Runtime activation needs a stable disabled contract.
Alternatives considered: Always omit mention of Skills On Demand; acceptable only where commands can be fully hidden, but runtimes without hidden command support need explicit disabled text.
Test implications: unit tests for activation text/control metadata in both hidden-command and cannot-hide-command paths.

## FR-007 / DESIGN-REQ-001

Decision: implemented_unverified. Preserve the initial selected Skill path and add regression evidence.
Evidence: `ResolvedSkillSet` and `RuntimeSkillMaterialization` models exist in `moonmind/schemas/agent_skill_models.py`; initial resolution/materialization activities exist in `moonmind/workflows/agent_skills/agent_skills_activities.py`; resolver and materializer services exist under `moonmind/services/`.
Rationale: The normal initial flow appears present. This feature must add a sidecar disabled gate without changing initial selected Skill behavior.
Alternatives considered: Rework initial resolution to support on-demand behavior; rejected because the story requires preserving normal initial resolution and compact refs.
Test implications: unit + integration regression proving selected active Skills still materialize when on-demand is disabled.

## FR-008

Decision: implemented_unverified. Existing policy enforcement exists for normal resolution; the new on-demand contract must route through the same policy when enabled later.
Evidence: `AgentSkillResolver` uses source loaders and `SkillResolutionContext` with repo/local policy flags; `docs/Steps/SkillSystem.md` distinguishes runtime commands from Skills and says runtime commands must not enter a `ResolvedSkillSet`.
Rationale: The disabled gate should decide callability only. It must not weaken source-kind, runtime, version, or policy enforcement for existing Skill resolution.
Alternatives considered: Add a separate on-demand resolver with independent policy semantics; rejected as unnecessary and risky.
Test implications: unit tests that disabled on-demand does not mutate policy summary or call resolution; future enabled tests should use the existing resolver boundary.

## FR-009 / SC-006

Decision: implemented_unverified. Traceability is present in `spec.md` and must continue.
Evidence: `specs/315-disabled-skills-on-demand-controls/spec.md` preserves `MM-612`, the full original preset brief, and source design mappings.
Rationale: Final verification depends on comparing implementation and artifacts against the preserved Jira source.
Alternatives considered: Rely on Jira link only; rejected because verification should not require a live external fetch.
Test implications: final MoonSpec verification.

## Data And Persistence

Decision: no new persistent storage.
Evidence: The story defines settings, runtime command responses, activation text, and prevention of derived snapshot creation while disabled. Existing resolved snapshots and artifacts remain unchanged.
Rationale: Disabled behavior should be deterministic runtime output and should not require database schema changes.
Alternatives considered: Persist each denied request; rejected for this story because audit/event behavior for enabled on-demand lifecycle is outside the selected disabled-control scope.
Test implications: unit tests assert no snapshot/materialization side effects.

## Test Strategy

Decision: Use TDD with settings/unit tests first, service contract unit tests second, then runtime/activity boundary tests.
Evidence: Repo instructions require `./tools/test_unit.sh`; existing relevant tests live under `tests/unit/services`, `tests/unit/workflows/agent_skills`, and `tests/unit/workflows/temporal`. Hermetic integration uses `./tools/test_integration.sh`.
Rationale: Settings and disabled response shape are low-level contracts. Runtime boundary tests prove the managed agent sees the correct activation behavior and that no derived snapshot is created.
Alternatives considered: Full unit suite only; rejected because it would not force red-first coverage for this new contract.
Test implications: unit + integration.

