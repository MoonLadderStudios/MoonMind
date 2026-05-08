# Research: Policy-Aware Skill Query

## Classification

Decision: MM-613 is a single-story runtime feature request.
Evidence: The Jira brief contains one actor, one query capability, one source document, and one acceptance set focused on `moonmind.skills.query`.
Rationale: The story does not ask to implement Skill activation requests or split the full Skills On Demand design.
Alternatives considered: Treating `docs/Steps/SkillsOnDemand.md` as a broad design was rejected because the Jira coverage IDs and acceptance criteria select only query metadata behavior.
Test implications: Unit and activity-boundary tests cover the selected story.

## Current Implementation Coverage

Decision: The current code implements and verifies the MM-613 query story.
Evidence: `moonmind/schemas/agent_skill_models.py`, `moonmind/services/skills_on_demand.py`, `moonmind/services/skill_resolution.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, and `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`.
Rationale: Enabled query mode now returns typed metadata-only results, validates unsafe input, preserves disabled behavior, records compact result metadata, marks policy-ineligible matches, marks active snapshot membership, and avoids materialization.
Alternatives considered: Leaving the original enabled-not-implemented placeholder was rejected because MM-613 requires enabled metadata discovery.
Test implications: Focused unit, activity-boundary, and full unit verification remain required evidence.

## FR-001 Governed Query Surface

Decision: Use the existing `SkillsOnDemandService.query` and `agent_skill.query_on_demand` activity boundary.
Evidence: `moonmind/workflows/agent_skills/agent_skills_activities.py`; `test_enabled_activity_query_returns_typed_result`.
Rationale: This preserves MoonMind-mediated access and avoids adding a parallel runtime command path.
Alternatives considered: Adding a new API route was rejected because the Jira story targets managed runtime workflow/activity use.
Test implications: ActivityEnvironment coverage invokes `query_on_demand` with settings enabled.

## FR-002 Query Validation

Decision: Keep Pydantic result bounds and service-level validation for blank query/runtime/snapshot context.
Evidence: `SkillsOnDemandQueryRequest.max_results`; `SkillsOnDemandService._validate_query_request`; validation tests.
Rationale: Validation belongs at the service contract before catalog results are returned.
Alternatives considered: Letting blank query list all Skills was rejected because the spec requires bounded, policy-aware discovery and safe empty-query behavior.
Test implications: Unit tests cover whitespace query and blank context values.

## FR-003 Metadata Result Shape

Decision: Use typed `SkillCatalogSearchResult` results.
Evidence: `moonmind/schemas/agent_skill_models.py`; metadata-only result tests.
Rationale: Typed fields make metadata-only guarantees testable and readable at workflow boundaries.
Alternatives considered: Continuing generic dictionaries was rejected because the contract needs schema enforcement.
Test implications: Unit tests assert exact result fields and absence of unsafe attributes.

## FR-004 Content Exposure Limits

Decision: Project `ResolvedSkillEntry` to safe metadata and omit `content_ref`, `content_digest`, source paths, and body text.
Evidence: `SkillsOnDemandService._project_entry`; serialization guard tests.
Rationale: Query callers need discovery metadata, not body retrieval handles.
Alternatives considered: Returning content digests for debugging was rejected because direct body-readable refs and hidden source details are out of scope.
Test implications: Unit tests fail if result serialization includes content/body/source path fields.

## FR-005 Current Snapshot Membership

Decision: Use optional compact active snapshot context for `in_current_snapshot` calculation.
Evidence: `SkillsOnDemandQueryRequest.active_snapshot`; membership test.
Rationale: Existing activity/service callers can pass compact active snapshot context without adding storage reads.
Alternatives considered: Reading snapshot artifacts in the query story was rejected as unnecessary for this single story and riskier for body exposure.
Test implications: Unit test confirms a matching active Skill is marked active.

## FR-006 Policy and Source Restrictions

Decision: Reuse resolver-backed candidate loading with source precedence and mark disallowed repo/local matches ineligible when present.
Evidence: `AgentSkillResolver.query_catalog`; `SkillsOnDemandService._eligibility_for`; ineligible source test.
Rationale: Query must not create a parallel catalog or bypass policy.
Alternatives considered: Scanning `.agents/skills` directly was rejected because repo/local source policy and deployment-loaded skills would drift.
Test implications: Unit tests cover allowed and ineligible source behavior.

## FR-007 Ineligible Diagnostics

Decision: Return `eligible=false` with compact `eligibility_summary` when matched metadata is known but blocked by source policy.
Evidence: `SkillsOnDemandService._eligibility_for`; ineligible local match test.
Rationale: Compact diagnostics help agents understand why help is unavailable without exposing hidden bodies.
Alternatives considered: Always filtering ineligible matches was rejected because the source brief explicitly allows diagnostic summaries.
Test implications: Unit test covers an ineligible source match.

## FR-008 Query Side Effects

Decision: Keep query side-effect free: no materialization, no derived snapshot, no active snapshot mutation.
Evidence: `query_on_demand` activity test patches `AgentSkillMaterializer.materialize` and asserts it is not called.
Rationale: Query is discovery only; activation belongs to `moonmind.skills.request`.
Alternatives considered: Precomputing derived snapshots during query was rejected as a scope and security violation.
Test implications: Activity-boundary tests verify no materialization.

## FR-009 Bounded Payloads

Decision: Enforce `max_results` and typed compact result fields; omit large or nested body-bearing data.
Evidence: `SkillsOnDemandQueryRequest.max_results`; max result test.
Rationale: Workflow/activity payloads must remain compact and deterministic.
Alternatives considered: Returning all matches and asking agents to filter was rejected.
Test implications: Unit tests cover result count limiting.

## FR-010 Observability

Decision: Return compact query outcome metadata with result count, denial state, and a hash of normalized query text.
Evidence: `SkillsOnDemandService.query` metadata construction.
Rationale: A deterministic result metadata field is enough for this story while avoiding a new persistent audit store.
Alternatives considered: Adding a new database table was rejected because the Jira brief planned no new persistent storage.
Test implications: Unit tests assert compact metadata is present for success and denial cases.
