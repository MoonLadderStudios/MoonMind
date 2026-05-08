# Research: Resolve On-Demand Skill Requests

## Story Classification

Decision: Treat MM-614 as a single-story runtime feature request.
Evidence: `specs/317-resolve-on-demand-skill-requests/spec.md`; `docs/Steps/SkillsOnDemand.md` sections 5.3, 5.4, 8.2, 9.3, 9.4, and 12.
Rationale: The Jira brief selects only request activation into derived snapshots, while disabled control and query discovery are covered by adjacent specs.
Alternatives considered: Treating all of `docs/Steps/SkillsOnDemand.md` as one broad design was rejected because it would collapse multiple independently testable stories.
Test implications: Unit and activity-boundary integration tests for the request path.

## Existing Implementation Gap

Decision: Enabled request mode is missing and must be implemented.
Evidence: `SkillsOnDemandService.request()` currently returns `enabled_mode_not_implemented`; `agent_skill.request_on_demand` constructs the service but does not resolve or materialize additions.
Rationale: Existing code supports disabled denial and query metadata, but not `activated` or `no_change` request outcomes.
Alternatives considered: Leaving enabled mode denied was rejected because it fails the MM-614 acceptance criteria.
Test implications: Add red-first tests proving no-change, activation, validation, and denial mapping.

## Contract Strategy

Decision: Extend request-specific status and denial code types while leaving query result behavior unchanged.
Evidence: `moonmind/schemas/agent_skill_models.py` currently defines `SkillsOnDemandStatus = Literal["ok", "denied"]` shared by query and request outputs.
Rationale: Query and request have different status vocabularies; keeping them separate avoids invalid `ok` request states and supports `activated`/`no_change`.
Alternatives considered: Reusing the query status literal was rejected because it cannot express request outcomes.
Test implications: Schema tests through existing service tests and serialization assertions.

## Derived Snapshot Strategy

Decision: Build a derived `ResolvedSkillSet` from the active snapshot plus approved requested Skill entries, with compact lineage in `source_trace` and no Skill bodies in the result.
Evidence: `ResolvedSkillSet` already carries `source_trace`, `resolution_inputs`, `manifest_ref`, and `skills`; `AgentSkillResolver.resolve()` can enforce normal selector gates.
Rationale: This fits existing immutable snapshot contracts and keeps workflow payloads compact.
Alternatives considered: Mutating the active snapshot in place was rejected because snapshots are immutable.
Test implications: Unit tests must assert parent snapshot unchanged, derived snapshot contains expected names, and result serialization omits Skill bodies/body refs.

## Activity Boundary

Decision: The Temporal activity should validate through the service, call `AgentSkillResolver.resolve()` for enabled allowed requests, persist file-backed content/manifest when artifact service exists, materialize through `AgentSkillMaterializer`, then pass compact refs back through the service result builder.
Evidence: `AgentSkillsActivities.resolve_skills()` already uses resolver, artifact persistence helpers, and materializer activity exists separately.
Rationale: The activity boundary owns source loading, artifact persistence, and materialization, while service logic remains deterministic and testable.
Alternatives considered: Performing resolver calls inside `SkillsOnDemandService` was rejected because source loading and artifact/materialization dependencies belong at activity/service boundaries, not pure contract logic.
Test implications: ActivityEnvironment tests patch resolver/materializer to cover invocation shape and compact result mapping.

## Failure Mapping

Decision: Map validation errors to `invalid_request`, resolver missing-selection errors to `skill_not_found` or `version_not_found`, policy/runtime/materialization exceptions to safe structured denial codes, and preserve current snapshot refs on every denial.
Evidence: `docs/Steps/SkillsOnDemand.md` section 12 lists expected failure categories.
Rationale: Managed runtimes need deterministic, safe diagnostics and the previous active snapshot must remain authoritative.
Alternatives considered: Passing raw exception messages directly was rejected because it could expose unsafe internals.
Test implications: Unit tests cover invalid shape and resolver failure mapping; activity tests cover materialization failure preservation.
