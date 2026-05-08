# Skills On Demand Story Breakdown

- Source design: `docs/Steps/SkillsOnDemand.md`
- Story extraction date: `2026-05-08T01:56:46Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines a desired-state Skills On Demand extension for managed-agent executions. A disabled-by-default global feature flag controls whether managed runtimes can query Skill metadata or request additional Skills during execution. MoonMind remains the only resolver: requests are validated through existing source, version, runtime, tool, policy, artifact, and materialization gates, and approved additions create derived immutable snapshots rather than mutating active state. The design also specifies bounded runtime command contracts, compact workflow payloads, runtime refresh behavior, failure semantics, audit events, and explicit non-goals for v1 such as no per-skill fetchability, per-user policy, approval workflows, or external-agent support.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Feature flag controls availability** - Skills On Demand is disabled by default through a global boolean setting; disabled query/request calls fail with feature_disabled and do not expose commands or catalog results. Source: 4. Feature Flag; 7. User-Facing Behavior.
- `DESIGN-REQ-002` (constraint): **Existing skill-system invariants are preserved** - Managed agents cannot fetch arbitrary Skill bodies, scan hidden catalogs, mutate .agents/skills, bypass source policy, or broaden active Skills outside MoonMind resolution. Source: 2. Relationship to SkillSystem.md; 6. Core Invariants.
- `DESIGN-REQ-003` (integration): **Metadata-only on-demand query command** - moonmind.skills.query accepts bounded search input and returns policy-aware Skill metadata only, never body refs or content. Source: 5.2 On-Demand Skill Query; 8.1 moonmind.skills.query; 9.2 On-demand query.
- `DESIGN-REQ-004` (integration): **Governed on-demand request command** - moonmind.skills.request treats requested Skills as selector intent, validates shape/current snapshot, applies normal gates, and returns activated, denied, no_change, or reserved requires_approval results. Source: 5.3 On-Demand Skill Request; 8.2 moonmind.skills.request; 9.3 On-demand request.
- `DESIGN-REQ-005` (state-model): **Derived immutable snapshot lineage** - Approved additions create a new ResolvedSkillSet with parent snapshot, requested Skills, requester/reason, policy result, and active Skill metadata stored compactly. Source: 5.4 Derived Skill Snapshot; 9.4 Snapshot lineage.
- `DESIGN-REQ-006` (requirement): **Safe materialization and runtime refresh** - Derived snapshots are materialized and verified before activation; v1 may defer visible projection updates to a next-turn or controlled steer point to avoid partial activation races. Source: 10. Materialization and Runtime Refresh.
- `DESIGN-REQ-007` (non-goal): **Managed runtime scope and external-agent non-goal** - The first implementation is scoped to managed runtimes. External-agent support and richer policy/approval/query features are future extensions unless another feature requires them. Source: 11. External Agents; 17. Future Extensions.
- `DESIGN-REQ-008` (requirement): **Structured failure behavior preserves active snapshot** - Disabled, unsupported, invalid, missing, policy-denied, incompatible, artifact, checksum, materialization, and refresh failures return structured errors and must not change the active snapshot. Source: 12. Failure Behavior; 16. Test Cases.
- `DESIGN-REQ-009` (observability): **Audit and observability events** - Each query and request records an audit/observability event with bounded fields, hashed query text for metrics, denial/result codes, snapshot IDs, manifest refs, and diagnostics refs as applicable. Source: 13. Observability and Audit.
- `DESIGN-REQ-010` (security): **Security and content exposure limits** - Skills On Demand must not expose secrets, hidden bodies, direct arbitrary artifact/database reads, or repo/runtime projection mutations; denials must remain operator-understandable. Source: 14. Security Rules.
- `DESIGN-REQ-011` (constraint): **Initial resolution remains normal path** - Initial task/step skill selection continues to resolve before runtime launch and runtimes receive compact refs plus a read-only active projection. Source: 1. Purpose; 9.1 Initial launch.
- `DESIGN-REQ-012` (artifact): **Runtime activation summary communicates availability** - Runtime instruction preparation exposes commands and concise usage notes only when enabled, while reminding agents to use .agents/skills and avoid copying full bodies unnecessarily. Source: 7. User-Facing Behavior; 8. Runtime Commands.
- `DESIGN-REQ-013` (constraint): **Existing implementation primitives should be reused** - The implementation should build on SkillSelector, AgentSkillResolver, ResolvedSkillSet, agent_skill.resolve, AgentSkillMaterializer, and AgentExecutionRequest.resolvedSkillsetRef rather than creating a parallel system. Source: 15. Implementation Notes.
- `DESIGN-REQ-014` (requirement): **Required test cases cover disabled, query, no-change, activated, denied, and materialization failure** - Downstream specs must support unit and boundary tests for the explicit behavior matrix in the design. Source: 16. Test Cases.

## Story Candidates

### STORY-001: Add disabled-by-default Skills On Demand controls

- Short name: `skill-demand-flag`
- Source reference: `docs/Steps/SkillsOnDemand.md` sections: 1. Purpose, 3. Desired-State Summary, 4. Feature Flag, 7.1 When disabled, 9.1 Initial launch
- Why: Establishes the deployment safety boundary and preserves the existing initial skill snapshot behavior before any runtime command is exposed.
- Description: As a MoonMind operator, I want Skills On Demand controlled by a global disabled-by-default setting so managed agents cannot discover or request additional Skills unless the deployment intentionally enables the capability.
- Independent test: With the flag false or unset, invoke the runtime-facing query and request control handlers and verify feature_disabled responses, absent command exposure where controllable, and unchanged active snapshot refs.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Add the global settings/env aliases and safe default false behavior.
  - Ensure disabled query/request attempts return feature_disabled with no catalog data and no snapshot creation.
  - Prepare runtime activation instructions so command exposure and availability text follow the flag.
- Out of scope:
  - Implementing catalog search or skill request approval/resolution.
  - Adding per-user, per-workspace, per-runtime, or per-skill fetchability policy.
- Acceptance criteria:
  - The default setting is false and supports MOONMIND_SKILLS_ON_DEMAND_ENABLED and WORKFLOW_SKILLS_ON_DEMAND_ENABLED aliases.
  - When disabled, query and request calls return status denied, code feature_disabled, and no Skill catalog results.
  - No derived ResolvedSkillSet is created when the flag is disabled.
  - Runtime activation text does not expose Skills On Demand commands when command exposure is controllable, or reports disabled when hiding is not possible.
- Requirements:
  - Add a namespaced global feature flag with deterministic default false behavior.
  - Gate all Skills On Demand command paths before catalog lookup or resolution.
  - Preserve the normal initial skill resolution path and compact active snapshot refs.
- Source design coverage:
  - `DESIGN-REQ-001`: Owns the global feature gate, disabled response, and no-results/no-snapshot behavior.
  - `DESIGN-REQ-011`: Keeps initial pre-launch skill resolution as the default path.
  - `DESIGN-REQ-012`: Owns disabled/enabled availability messaging for runtime activation summaries.

### STORY-002: Expose policy-aware Skill metadata query for managed runtimes

- Short name: `skill-query-metadata`
- Source reference: `docs/Steps/SkillsOnDemand.md` sections: 5.2 On-Demand Skill Query, 6. Core Invariants, 8.1 moonmind.skills.query, 9.2 On-demand query, 14. Security Rules
- Why: Provides discovery while preserving source-policy, security, and workflow payload boundaries.
- Description: As a managed agent, I want to ask MoonMind for bounded metadata about available Skills so I can discover relevant help without receiving hidden Skill bodies or bypassing deployment policy.
- Independent test: With the flag enabled, query for a known Skill category and assert bounded metadata-only results, policy eligibility markings, no body refs/content, and a query audit event.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Define and validate the query payload/result schemas.
  - Search allowed Skill metadata through existing resolver/catalog services under the feature flag.
  - Return bounded metadata including eligibility and in-current-snapshot status without content refs or bodies.
  - Record query observability in a bounded form.
- Out of scope:
  - Returning full Skill body content.
  - Providing arbitrary artifact/database read refs.
  - Semantic catalog search beyond existing available metadata search unless already present.
- Acceptance criteria:
  - moonmind.skills.query validates query, runtime_id, current_snapshot_ref, and max_results inputs.
  - Results include metadata fields such as name, title, description, latest_version, source_kind, supported_runtimes, eligible, in_current_snapshot, and eligibility_summary where available.
  - Results never include full Skill bodies or direct content refs that permit body reads.
  - Ineligible matches are either filtered or explicitly marked eligible false with diagnostic summaries.
  - Query payloads and results remain bounded for workflow/activity use.
- Requirements:
  - Add SkillsOnDemandQuery and SkillsOnDemandQueryResult contracts.
  - Reuse existing Skill resolver/catalog primitives instead of creating a parallel catalog.
  - Enforce source-kind restrictions, runtime compatibility summaries, and content exposure limits on query results.
- Source design coverage:
  - `DESIGN-REQ-002`: Owns metadata-only discovery without local hidden-catalog scans or resolution bypass.
  - `DESIGN-REQ-003`: Owns the query command contract and bounded result shape.
  - `DESIGN-REQ-010`: Owns the no-secret, no-body, no-arbitrary-ref security rules for query.
  - `DESIGN-REQ-013`: Requires reuse of existing resolver/catalog primitives.
  - `DESIGN-REQ-014`: Covers the query metadata test case.

### STORY-003: Resolve approved on-demand Skill requests into derived snapshots

- Short name: `skill-request-snapshot`
- Source reference: `docs/Steps/SkillsOnDemand.md` sections: 5.3 On-Demand Skill Request, 5.4 Derived Skill Snapshot, 8.2 moonmind.skills.request, 9.3 On-demand request, 9.4 Snapshot lineage, 12. Failure Behavior
- Why: Delivers the central behavior of Skills On Demand while preserving immutable snapshots, policy gates, and no-change/denial semantics.
- Description: As a managed agent, I want MoonMind to evaluate my request for additional Skills and activate only policy-eligible additions as a new immutable snapshot so active Skill state changes are governed and traceable.
- Independent test: With the flag enabled and an existing active snapshot, request an already-active Skill, an allowed Skill, and a policy-denied Skill; verify no_change, activated derived snapshot lineage, and denied unchanged-snapshot outcomes.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Define and validate request/result/failure schemas.
  - Load and validate the current snapshot ref before resolution.
  - Combine current active Skills with requested additions as selector intent.
  - Apply normal source, version, runtime, tool, artifact, checksum, and policy gates.
  - Return no_change for already-active requests, activated for successful derived snapshots, and structured denials/errors without changing active state.
  - Persist lineage metadata for derived snapshots.
- Out of scope:
  - Human approval workflow for requires_approval.
  - Runtime projection switching or activation delivery beyond producing compact refs and activation data.
- Acceptance criteria:
  - moonmind.skills.request validates current_snapshot_ref, requested_skills, optional versions, reason, runtime_id, and step_id.
  - Every request is resolved by MoonMind using existing skill resolution policy, not by the agent or adapter.
  - Already-active requested Skills return no_change and keep the current snapshot ref.
  - Allowed additions create a derived immutable ResolvedSkillSet with parent snapshot lineage and requested Skill metadata.
  - Denied or failed requests preserve the previous active snapshot and return structured code/message data.
- Requirements:
  - Add SkillsOnDemandRequest, SkillsOnDemandRequestResult, and SkillsOnDemandFailure contracts.
  - Add or extend activity/service behavior for agent_skill.request_on_demand.
  - Persist compact lineage metadata including parent snapshot, request origin, reason, requested Skills, and resulting refs.
  - Keep workflow history compact by carrying refs and metadata rather than Skill bodies.
- Source design coverage:
  - `DESIGN-REQ-002`: Owns the rule that agents request but MoonMind resolves and validates.
  - `DESIGN-REQ-004`: Owns the request command lifecycle and result states.
  - `DESIGN-REQ-005`: Owns derived immutable snapshot lineage.
  - `DESIGN-REQ-008`: Owns unchanged active snapshot behavior for denial and failure cases.
  - `DESIGN-REQ-013`: Requires reuse of existing resolver/materializer artifacts and contracts.
  - `DESIGN-REQ-014`: Covers no-change, activated, denied, and failure-oriented tests.

### STORY-004: Refresh managed runtimes after derived Skill activation

- Short name: `skill-runtime-refresh`
- Source reference: `docs/Steps/SkillsOnDemand.md` sections: 6. Core Invariants, 7.2 When enabled, 10. Materialization and Runtime Refresh, 11. External Agents, 14. Security Rules
- Why: Connects derived snapshots to usable runtime context while protecting read-only projection ownership and avoiding mid-turn races.
- Description: As a managed runtime, I want MoonMind to materialize an approved derived Skill snapshot and provide a compact activation update only after the new bundle is ready so I never observe a partially active Skill set.
- Independent test: Force a successful derived snapshot materialization and a materialization failure; verify the runtime receives activation only after verified materialization, and failure leaves the previous active snapshot/projection unchanged with diagnostics.
- Dependencies: STORY-003
- Needs clarification: None
- Scope:
  - Materialize derived snapshots into a run-scoped backing store and verify manifest/checksums before activation.
  - Support either atomic projection switch where available or v1 next-turn/controlled-steer activation fallback.
  - Send compact activation updates containing refs, summary, and materialization status.
  - Keep .agents/skills MoonMind-owned runtime projection state.
  - Fail materialization or refresh safely without partially activating the new snapshot.
- Out of scope:
  - External-agent Skills On Demand support.
  - Retrieval-mode Skill serving except as future-compatible result metadata where already supported.
  - Direct agent mutation of .agents/skills.
- Acceptance criteria:
  - Derived snapshots are fully materialized and verified before a runtime is told they are active.
  - The runtime-visible projection is switched atomically where supported or deferred to a documented next-turn/controlled steer point.
  - Activation results include compact activation_summary and materialization fields without large Skill bodies.
  - Materialization_failed and runtime_refresh_failed outcomes preserve the current active snapshot and produce diagnostics.
  - External agents are not exposed to Skills On Demand in v1 unless equivalent authenticated controls and governed materialization exist.
- Requirements:
  - Extend AgentSkillMaterializer or adapter boundary behavior for derived snapshot refresh.
  - Ensure runtime adapters cannot independently broaden active Skill sets.
  - Keep repo-authored Skill sources and local-only overlays separate from MoonMind-owned runtime projection state.
- Source design coverage:
  - `DESIGN-REQ-006`: Owns safe materialization, verification, projection switch, and next-turn fallback behavior.
  - `DESIGN-REQ-007`: Owns the managed-runtime-only v1 scope and external-agent non-goal.
  - `DESIGN-REQ-010`: Owns projection/security limits during refresh.
  - `DESIGN-REQ-012`: Owns enabled activation summary behavior.
  - `DESIGN-REQ-014`: Covers materialization failure test expectations.

### STORY-005: Record audit events and failure diagnostics for Skills On Demand

- Short name: `skill-demand-audit`
- Source reference: `docs/Steps/SkillsOnDemand.md` sections: 12. Failure Behavior, 13. Observability and Audit, 14. Security Rules, 16. Test Cases
- Why: Makes the feature operable and reviewable, especially because agents can request additional runtime instruction bundles autonomously when enabled.
- Description: As an operator, I want every Skills On Demand query and request to leave bounded audit evidence and actionable diagnostics so I can understand approvals, denials, snapshot transitions, and failures without exposing secrets or high-cardinality raw text.
- Independent test: Run disabled, denied, activated, no-change, and materialization-failure paths and assert bounded audit events plus structured failure diagnostics without raw long query text or secret-like values.
- Dependencies: STORY-002, STORY-003, STORY-004
- Needs clarification: None
- Scope:
  - Emit query events with workflow/run/step/runtime/snapshot context, query hash, result count, and denial status/code.
  - Emit request events with requested Skill names, result, result code, derived snapshot/manifest refs, and diagnostics refs.
  - Ensure failure responses use the documented code set and keep detailed diagnostics in artifacts where needed.
  - Avoid secrets, raw long query text in metrics, and unrestricted refs in operator-visible outputs.
- Out of scope:
  - Building UI approval controls or policy-management screens.
  - Persisting new database tables unless existing event/artifact mechanisms cannot satisfy the evidence requirements.
- Acceptance criteria:
  - Each query records a skills_on_demand.query event with bounded fields and query_hash rather than raw high-cardinality query text in metrics.
  - Each request records a skills_on_demand.request event with result, result_code, requested Skill names, parent/derived snapshot identifiers where applicable, and diagnostics refs where applicable.
  - Failure responses use documented codes such as feature_disabled, policy_denied, snapshot_not_found, materialization_failed, and runtime_refresh_failed.
  - Audit and diagnostics outputs do not expose secrets, full Skill bodies, or arbitrary artifact/database access.
- Requirements:
  - Integrate Skills On Demand control paths with existing observability/audit or artifact-backed diagnostic mechanisms.
  - Normalize failure codes and diagnostics consistently across query, request, materialization, and refresh failures.
  - Include test coverage for the documented failure and observability matrix.
- Source design coverage:
  - `DESIGN-REQ-008`: Owns structured failure code consistency and unchanged snapshot evidence.
  - `DESIGN-REQ-009`: Owns query/request audit event fields.
  - `DESIGN-REQ-010`: Owns secret/content exposure limits in audit and diagnostics.
  - `DESIGN-REQ-014`: Covers disabled, denied, no-change, activated, and materialization-failure test verification.

## Coverage Matrix

- `DESIGN-REQ-001` Feature flag controls availability: STORY-001
- `DESIGN-REQ-002` Existing skill-system invariants are preserved: STORY-002, STORY-003
- `DESIGN-REQ-003` Metadata-only on-demand query command: STORY-002
- `DESIGN-REQ-004` Governed on-demand request command: STORY-003
- `DESIGN-REQ-005` Derived immutable snapshot lineage: STORY-003
- `DESIGN-REQ-006` Safe materialization and runtime refresh: STORY-004
- `DESIGN-REQ-007` Managed runtime scope and external-agent non-goal: STORY-004
- `DESIGN-REQ-008` Structured failure behavior preserves active snapshot: STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-009` Audit and observability events: STORY-005
- `DESIGN-REQ-010` Security and content exposure limits: STORY-002, STORY-004, STORY-005
- `DESIGN-REQ-011` Initial resolution remains normal path: STORY-001
- `DESIGN-REQ-012` Runtime activation summary communicates availability: STORY-001, STORY-004
- `DESIGN-REQ-013` Existing implementation primitives should be reused: STORY-002, STORY-003
- `DESIGN-REQ-014` Required test cases cover disabled, query, no-change, activated, denied, and materialization failure: STORY-002, STORY-003, STORY-004, STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-002, STORY-003, STORY-004

## Out Of Scope

- Per-user, per-workspace, per-skill, per-source, and per-runtime fetchability settings are future extensions; v1 uses one global feature flag.
- Approval-required Skills and UI approval controls are reserved for later work; requires_approval is only a future-compatible result value.
- External-agent support is out of scope until adapters provide authenticated MoonMind-mediated control calls and equivalent governed materialization/audit guarantees.
- Retrieval-mode Skill serving and semantic catalog search are future extensions and should not let agents fetch Skill bodies directly.

## Coverage Gate

PASS - every major design point is owned by at least one story.
