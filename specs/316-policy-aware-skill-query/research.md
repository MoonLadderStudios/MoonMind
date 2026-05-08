# Research: Policy-Aware Skill Query

## Classification

Decision: MM-613 is a single-story runtime feature request.
Evidence: The Jira brief contains one actor, one query capability, one source document, and one acceptance set focused on `moonmind.skills.query`.
Rationale: The story does not ask to implement Skill activation requests or split the full Skills On Demand design.
Alternatives considered: Treating `docs/Steps/SkillsOnDemand.md` as a broad design was rejected because the Jira coverage IDs and acceptance criteria select only query metadata behavior.
Test implications: Unit and activity-boundary tests are enough for the selected story.

## Current Implementation Gap

Decision: Existing code is partial and disabled-first; enabled query behavior is missing.
Evidence: `moonmind/services/skills_on_demand.py` returns `enabled_mode_not_implemented` when enabled; `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` asserts that placeholder behavior.
Rationale: The current implementation intentionally covered disabled controls from MM-612/MM-315, not enabled metadata search for MM-613.
Alternatives considered: Marking behavior implemented was rejected because no query result metadata is produced.
Test implications: Replace enabled-not-implemented tests with red-first tests for enabled metadata query behavior while preserving disabled tests.

## FR-001 Governed Query Surface

Decision: Use the existing `SkillsOnDemandService.query` and `agent_skill.query_on_demand` activity boundary.
Evidence: `moonmind/workflows/agent_skills/agent_skills_activities.py` already registers `agent_skill.query_on_demand`; activity routing exists in `moonmind/workflows/temporal/activity_runtime.py` and `activity_catalog.py`.
Rationale: This preserves MoonMind-mediated access and avoids adding a parallel runtime command path.
Alternatives considered: Adding a new API route was rejected because the Jira story targets managed runtime workflow/activity use.
Test implications: ActivityEnvironment coverage should invoke `query_on_demand` with settings enabled.

## FR-002 Query Validation

Decision: Keep Pydantic max-result bounds and add service-level query/runtime/snapshot validation for unsafe blank or malformed inputs.
Evidence: `SkillsOnDemandQueryRequest.max_results` already has `ge=1, le=100`, but `query` defaults to empty and runtime/snapshot strings are unconstrained.
Rationale: Validation belongs at the service contract before catalog results are returned.
Alternatives considered: Letting blank query list all Skills was rejected because the spec requires bounded, policy-aware discovery and safe empty-query behavior.
Test implications: Add unit tests for whitespace query, invalid result limits through Pydantic, and unknown runtime/snapshot handling.

## FR-003 Metadata Result Shape

Decision: Add a typed `SkillCatalogSearchResult` model and narrow `SkillsOnDemandQueryResult.results` to that model.
Evidence: Current results are `list[dict[str, Any]]`, which cannot prevent body/content-ref leakage by type.
Rationale: Typed fields make metadata-only guarantees testable and readable at workflow boundaries.
Alternatives considered: Continue using dictionaries with ad hoc filtering; rejected because the contract needs schema enforcement.
Test implications: Unit tests should assert exact result fields and absence of unsafe attributes.

## FR-004 Content Exposure Limits

Decision: Project `ResolvedSkillEntry` to safe metadata and explicitly omit `content_ref`, `content_digest`, source paths, and body text.
Evidence: `ResolvedSkillEntry` can carry `content_ref`, `content_digest`, and `provenance.source_path`.
Rationale: Query callers need discovery metadata, not body retrieval handles.
Alternatives considered: Returning content digests for debugging was rejected because direct body-readable refs and hidden source details are out of scope.
Test implications: Unit tests must fail if result serialization includes content/body/source path fields.

## FR-005 Current Snapshot Membership

Decision: Accept an optional active snapshot on the query request for activity/service calls and mark `in_current_snapshot` by skill name.
Evidence: The request currently carries `current_snapshot_ref` but not the snapshot content; request activation models already include `active_snapshot` for side-effect-free context.
Rationale: Existing activity tests can pass compact active snapshot context without adding storage reads. A future artifact-backed snapshot loader can satisfy ref-only calls.
Alternatives considered: Reading snapshot artifacts in the query story was rejected as unnecessary for this single story and riskier for body exposure.
Test implications: Add a unit test where an active snapshot contains one matching Skill and the result marks it active.

## FR-006 Policy and Source Restrictions

Decision: Reuse resolver-backed candidate loading with the same source allow flags and loader precedence used by initial resolution, then search metadata across candidates.
Evidence: `AgentSkillResolver` and loaders already merge built-in, deployment, repo, and local sources with policy flags in `SkillResolutionContext`.
Rationale: Query must not create a parallel catalog or bypass policy.
Alternatives considered: Scanning `.agents/skills` directly was rejected because repo/local source policy and deployment-loaded skills would drift.
Test implications: Add unit tests with fake candidates from different source kinds and verify allowed/ineligible behavior.

## FR-007 Ineligible Diagnostics

Decision: Return eligible false with a compact `eligibility_summary` when a matched Skill is known but blocked by source/runtime constraints.
Evidence: The Jira acceptance criteria allow filtering or explicitly marking ineligible; diagnostics are useful for managed agents.
Rationale: Compact diagnostics help agents understand why help is unavailable without exposing hidden bodies.
Alternatives considered: Always filtering ineligible matches was rejected because the source brief explicitly allows diagnostic summaries.
Test implications: Add a test covering an ineligible source/runtime match.

## FR-008 Query Side Effects

Decision: Keep query side-effect free: no materialization, no derived snapshot, no active snapshot mutation.
Evidence: Existing disabled activity tests assert materializer is not called for request/query paths.
Rationale: Query is discovery only; activation belongs to `moonmind.skills.request`.
Alternatives considered: Precomputing derived snapshots during query was rejected as a scope and security violation.
Test implications: Activity-boundary tests should patch materializer/resolver mutation paths and assert no materialization.

## FR-009 Bounded Payloads

Decision: Enforce `max_results` and typed compact result fields; omit large or nested body-bearing data.
Evidence: `max_results` already has numeric limits, but enabled query has no result projection yet.
Rationale: Workflow/activity payloads must remain compact and deterministic.
Alternatives considered: Returning all matches and asking agents to filter was rejected.
Test implications: Add tests for result count limiting and serialized result size/field set.

## FR-010 Observability

Decision: Return compact query outcome metadata in the service result for later audit/event integration, without storing raw long query text in high-cardinality fields.
Evidence: `docs/Steps/SkillsOnDemand.md` section 13 defines query event fields; no current implementation exists.
Rationale: A deterministic result metadata field is enough for this story while avoiding a new persistent audit store.
Alternatives considered: Adding a new database table was rejected because the Jira brief planned no new persistent storage.
Test implications: Unit tests should assert result metadata includes result count and denial status without body content.
