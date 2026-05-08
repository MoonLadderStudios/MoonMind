# Story Breakdown: Skills On Demand

- Source design: `docs/Steps/SkillsOnDemand.md`
- Original source document reference path: `docs/Steps/SkillsOnDemand.md`
- Story extraction date: `2026-05-08T01:28:49Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines a feature-gated Skills On Demand extension for managed runtimes. Agents may query bounded Skill metadata and request additional Skills, but MoonMind remains the only resolver, policy enforcer, snapshot creator, materializer, and auditor. The extension preserves existing Skill System invariants: compact workflow refs, immutable snapshots, governed source policy, read-only runtime projections, no direct hidden catalog access, and no large Skill bodies in workflow history. The first version is globally disabled by default, scoped to managed runtimes, avoids live mid-turn projection races where necessary, and excludes per-user/per-skill fetchability, approvals, external-agent support, and retrieval-mode serving from v1.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Feature-gated Skills On Demand scope** - Skills On Demand is controlled by one global boolean flag, defaults disabled, and only permits MoonMind-mediated runtime requests when enabled. Source: 1. Purpose; 3. Desired-State Summary; 4. Feature Flag.
- `DESIGN-REQ-002` (constraint): **Preserve Skill System authority and invariants** - Agents cannot fetch hidden catalogs, mutate projections, resolve locally, broaden active skills, or receive large Skill bodies in workflow history. Source: 2. Relationship to SkillSystem.md; 6. Core Invariants.
- `DESIGN-REQ-003` (requirement): **Disabled runtime behavior** - Commands are hidden where possible and attempted calls return denied/feature_disabled without query results or new snapshots. Source: 7.1 When disabled; 16.1 Disabled feature.
- `DESIGN-REQ-004` (integration): **Enabled activation summary and command exposure** - Supported managed runtimes receive compact instructions and controlled query/request commands only when the feature is enabled. Source: 7.2 When enabled; 8. Runtime Commands; 15. Implementation Notes.
- `DESIGN-REQ-005` (integration): **Metadata-only bounded query contract** - Queries return bounded Skill metadata and eligibility information, never body content or content refs that bypass governance. Source: 5.2 On-Demand Skill Query; 8.1 moonmind.skills.query; 9.2 On-demand query; 16.2 Query metadata.
- `DESIGN-REQ-006` (security): **Policy-filtered catalog eligibility** - Query and request paths continue to enforce source restrictions, local/repo policy, allowlists, runtime compatibility, version pins, Tool policy, and artifact validation. Source: 4.3 Interaction with existing policy; 8.1 moonmind.skills.query; 14. Security Rules.
- `DESIGN-REQ-007` (integration): **Governed request payload and outcomes** - Requests carry current snapshot refs and selector intent, then return activated, denied, no_change, or future reserved requires_approval outcomes. Source: 5.3 On-Demand Skill Request; 8.2 moonmind.skills.request; 16.3-16.5.
- `DESIGN-REQ-008` (state-model): **Derived immutable snapshot lifecycle** - Approved additions create a new ResolvedSkillSet with parent lineage, requested Skills, reason, policy result, and resulting active Skills. Source: 5.4 Derived Skill Snapshot; 9.3 On-demand request; 9.4 Snapshot lineage.
- `DESIGN-REQ-009` (constraint): **Initial launch remains normal resolution** - Skills On Demand does not replace pre-runtime selection, manifest persistence, materialization, or compact snapshot refs for the initial run. Source: 1. Purpose; 9.1 Initial launch.
- `DESIGN-REQ-010` (artifact): **Materialization before activation** - Derived snapshots are materialized and verified before activation; v1 may defer visibility to the next managed-session turn or steer point to avoid projection races. Source: 10.1 Preferred behavior; 10.2 First implementation behavior; 16.4 Request allowed Skill; 16.6 Materialization failure.
- `DESIGN-REQ-011` (state-model): **Failure leaves current snapshot unchanged** - Denied requests, resolution failures, artifact/checksum failures, materialization failures, and runtime refresh failures must not partially activate or change the active snapshot. Source: 6. Core Invariants; 8.2 moonmind.skills.request; 12. Failure Behavior.
- `DESIGN-REQ-012` (observability): **Audit and observability events** - Every query and request records bounded audit fields, avoids raw long query text in high-cardinality metrics, and uses diagnostics artifacts when detail is needed. Source: 13. Observability and Audit.
- `DESIGN-REQ-013` (security): **Security and source protection** - No secrets, arbitrary artifact/database access, hidden body disclosure, repo projection mutation, or local-only policy bypass are allowed. Source: 14. Security Rules.
- `DESIGN-REQ-014` (constraint): **Managed-runtime-only v1 boundary** - External agents, retrieval-mode serving, approval workflows, cost budgets, semantic catalog search, and per-scope permissions are future extensions, not v1 requirements. Source: 11. External Agents; 17. Future Extensions.
- `DESIGN-REQ-015` (requirement): **Implementation primitives and tests** - The design expects settings, schemas, activities, catalog registration, runtime control surfaces, lineage metadata, refresh support or fallback, and tests for disabled, query, no-change, activation, policy denial, and materialization failure paths. Source: 15. Implementation Notes; 16. Test Cases.

## Story Candidates

### STORY-001: Gate Skills On Demand command exposure for managed runtimes

- Short name: `skill-demand-gate`
- Source reference path: `docs/Steps/SkillsOnDemand.md`
- Source sections: 1. Purpose, 3. Desired-State Summary, 4. Feature Flag, 7. User-Facing Behavior, 8. Runtime Commands, 16.1 Disabled feature
- Why: This creates the smallest safe entry point and preserves existing Skill System behavior when the feature is off.
- Narrative: As an operator, I want Skills On Demand disabled by default and exposed only through a deployment-level flag so managed agents cannot discover or request extra Skills unless the deployment intentionally allows it.
- Independent test: Run unit and adapter-boundary tests with the flag false and true, verifying command exposure/instructions and feature_disabled responses without creating any derived snapshot.
- Dependencies: None
- Scope:
  - Add the global Skills On Demand setting with the documented aliases and false default.
  - Make managed-runtime activation/instruction preparation expose or hide command availability from that setting.
  - Return denied/feature_disabled from attempted query and request calls when disabled.
  - Preserve the normal initial Skill snapshot flow regardless of the flag.
- Out of scope:
  - Per-user, per-workspace, per-skill, per-source, or per-runtime fetchability controls.
  - Approval workflow behavior beyond reserving the future result value.
  - External-agent support.
- Acceptance criteria:
  - Given the flag is unset or false, managed runtime activation does not expose Skills On Demand commands where controllable.
  - Given the flag is false and a query or request handler is called, the response is denied with code feature_disabled.
  - Given the flag is true, supported managed runtimes receive a compact activation note and controlled command availability.
  - Initial Skill resolution and snapshot refs continue to be produced through the existing launch path in both flag states.
- Requirements:
  - Introduce the safe-by-default boolean configuration and documented environment aliases.
  - Apply the flag consistently at runtime instruction preparation and command handler boundaries.
  - Do not create any on-demand catalog result or derived snapshot while disabled.
- Source design coverage:
  - `DESIGN-REQ-001`: Owns the deployment-level feature gate and default disabled behavior.
  - `DESIGN-REQ-003`: Owns disabled command exposure and feature_disabled responses.
  - `DESIGN-REQ-004`: Owns enabled activation note and command exposure for supported managed runtimes.
  - `DESIGN-REQ-009`: Ensures initial launch remains normal resolution.
- Assumptions:
  - The existing managed runtime instruction preparation path is the control point for command exposure messaging.
- Needs clarification: None

### STORY-002: Provide metadata-only Skill catalog queries

- Short name: `skill-query-metadata`
- Source reference path: `docs/Steps/SkillsOnDemand.md`
- Source sections: 5.2 On-Demand Skill Query, 8.1 moonmind.skills.query, 9.2 On-demand query, 13. Observability and Audit, 14. Security Rules, 16.2 Query metadata
- Why: This provides useful discovery while keeping MoonMind in control of catalog access and policy filtering.
- Narrative: As a managed agent, I want to ask MoonMind for relevant Skill metadata so I can discover available help without receiving ungoverned Skill bodies or bypassing source policy.
- Independent test: With the flag enabled, call the query handler for a representative search and assert bounded metadata results, eligibility fields, no body text/content refs, and a query audit event; also cover ineligible diagnostic results when policy excludes a match.
- Dependencies: STORY-001
- Scope:
  - Define and validate the query request/result schema.
  - Search Skill metadata across allowed sources using existing resolver/catalog boundaries.
  - Return bounded metadata, eligibility summaries, and current-snapshot membership without body content or direct content refs.
  - Record query audit events with hashed query text and denial information.
- Out of scope:
  - Activating or materializing any requested Skill.
  - Semantic catalog search beyond existing available search capability.
  - Returning full Skill files, artifact refs that permit direct body reads, or arbitrary catalog/database access.
- Acceptance criteria:
  - Query input requires a non-empty query and honors bounded max_results behavior.
  - Returned results include names, descriptions or titles when available, source kind, supported runtimes, eligibility, and in_current_snapshot.
  - Responses never include full Skill bodies or content refs that let the agent read hidden Skill content directly.
  - Policy-ineligible matches are omitted where practical or returned only with eligible false and an eligibility summary.
  - A query audit event records workflow/run/step/runtime context, snapshot id, query hash, result count, and denial code when applicable.
- Requirements:
  - Expose moonmind.skills.query through MoonMind-controlled managed-runtime command handling.
  - Use existing source, runtime, and policy gates for query eligibility.
  - Keep query payloads safe for workflow/activity boundaries.
- Source design coverage:
  - `DESIGN-REQ-002`: Maintains MoonMind as the catalog authority instead of agent-local resolution.
  - `DESIGN-REQ-005`: Owns the metadata-only bounded query contract.
  - `DESIGN-REQ-006`: Owns query-side eligibility filtering.
  - `DESIGN-REQ-012`: Owns query audit event creation.
  - `DESIGN-REQ-013`: Owns no body disclosure or arbitrary artifact/database access in query results.
- Assumptions:
  - Existing Skill catalog or resolver metadata can be queried without loading full bodies into the runtime response.
- Needs clarification: None

### STORY-003: Resolve governed on-demand Skill requests into derived snapshots

- Short name: `skill-request-resolve`
- Source reference path: `docs/Steps/SkillsOnDemand.md`
- Source sections: 5.3 On-Demand Skill Request, 5.4 Derived Skill Snapshot, 8.2 moonmind.skills.request, 9.3 On-demand request, 9.4 Snapshot lineage, 12. Failure Behavior, 16.3-16.5
- Why: This is the core value path: agents can request help, but only MoonMind can validate, resolve, and create a new active Skill set.
- Narrative: As a managed agent, I want MoonMind to evaluate my requested Skills against the active snapshot and deployment policy so approved additions become a new immutable resolved snapshot and denied requests leave the current snapshot untouched.
- Independent test: Exercise the request handler with already-active, allowed, unknown skill, version-not-found, policy-denied, runtime-incompatible, and checksum/artifact failure cases, asserting snapshot lineage on success and unchanged current snapshot on all denials/failures.
- Dependencies: STORY-001
- Scope:
  - Define and validate request/result schemas, including current snapshot ref, requested skills, optional versions, reason, runtime_id, and step_id.
  - Load the current active snapshot by ref and combine currently active Skills with requested additions as selector intent.
  - Apply source, version, runtime, Tool, autonomy, checksum, artifact, and allowlist policy through existing resolution services.
  - Return no_change when all requested Skills are already active.
  - Persist a derived immutable ResolvedSkillSet and lineage metadata when resolution is approved.
  - Return structured denial or failure responses without changing the active snapshot.
- Out of scope:
  - Human approval workflows for requires_approval.
  - Policy compatibility aliases or fallback transformations for unsupported values.
  - Runtime projection refresh or activation delivery, which is owned separately.
- Acceptance criteria:
  - Request payload validation fails fast for missing current_snapshot_ref, malformed requested_skills, or invalid versions.
  - When all requested Skills are already active, the result is no_change and the current snapshot ref is preserved.
  - When a requested Skill is allowed, a new immutable snapshot is created with parent snapshot, requested skills, requester, reason, and result metadata.
  - When policy or resolution denies a request, the response includes a stable code and the active snapshot is unchanged.
  - The result payload contains compact refs and activation summary material only, not full Skill bodies.
- Requirements:
  - Add the moonmind.skills.request activity/handler boundary.
  - Preserve snapshot lineage in manifest metadata or source_trace_ref rather than large workflow history payloads.
  - Use existing AgentSkillResolver and ResolvedSkillSet primitives instead of agent-local resolution.
- Source design coverage:
  - `DESIGN-REQ-002`: Prevents local agent resolution and broadening of active skills.
  - `DESIGN-REQ-006`: Owns request-side policy validation.
  - `DESIGN-REQ-007`: Owns request payloads and outcome states.
  - `DESIGN-REQ-008`: Owns derived immutable snapshot creation and lineage.
  - `DESIGN-REQ-011`: Owns unchanged snapshot behavior for denials and resolution failures.
  - `DESIGN-REQ-015`: Owns schemas, request activity, lineage metadata, and request tests.
- Assumptions:
  - Derived snapshot metadata can be persisted using existing resolved manifest or source trace artifact mechanisms.
- Needs clarification: None

### STORY-004: Materialize derived snapshots and refresh managed runtimes safely

- Short name: `skill-runtime-refresh`
- Source reference path: `docs/Steps/SkillsOnDemand.md`
- Source sections: 10. Materialization and Runtime Refresh, 8.2 moonmind.skills.request, 12. Failure Behavior, 14. Security Rules, 16.4 Request allowed Skill, 16.6 Materialization failure
- Why: Resolution is not useful until the runtime can consume the new Skill set safely, and the design explicitly prioritizes avoiding projection races.
- Narrative: As a managed runtime, I want approved on-demand Skill additions to become visible only after MoonMind has fully materialized and verified the derived snapshot so agents never observe a partially written active Skill projection.
- Independent test: Use adapter-boundary tests around materializer success, checksum failure, materialization failure, atomic switch or next-turn fallback, and runtime refresh failure to prove partial activation cannot occur.
- Dependencies: STORY-003
- Scope:
  - Materialize approved derived snapshots into a new run-scoped backing location.
  - Verify manifest and checksums before marking the derived snapshot active.
  - Switch runtime-visible projection atomically where supported or defer activation to the next managed-session turn or controlled steer point.
  - Return materialization mode, visible path when appropriate, manifest ref, and compact activation summary.
  - Report materialization_failed or runtime_refresh_failed without partial activation.
- Out of scope:
  - Retrieval-mode Skill serving beyond preserving the future enum value.
  - Mid-read mutation of .agents/skills.
  - Publishing runtime projection changes as repo-authored source changes.
- Acceptance criteria:
  - A derived snapshot is fully materialized in a run-scoped backing store before any activation update is sent.
  - Manifest and checksum validation happens before projection switch or next-turn activation.
  - Where atomic projection switching is unavailable, the runtime receives the new activation only at the next controlled turn or steer point.
  - On materialization or refresh failure, the response code is materialization_failed or runtime_refresh_failed and the current snapshot remains active.
  - Runtime projection changes do not mutate checked-in repo Skill folders or local-only source inputs.
- Requirements:
  - Use AgentSkillMaterializer or equivalent service boundary for filesystem projection.
  - Represent materialization as prompt_bundled, workspace_mounted, hybrid, or future retrieval in compact response metadata.
  - Add boundary tests for materialization and refresh behavior.
- Source design coverage:
  - `DESIGN-REQ-002`: Maintains read-only MoonMind-owned active projection semantics.
  - `DESIGN-REQ-010`: Owns safe materialization and activation timing.
  - `DESIGN-REQ-011`: Owns no partial activation on materialization or refresh failure.
  - `DESIGN-REQ-013`: Owns protection against repo-authored projection mutation.
  - `DESIGN-REQ-015`: Owns materialization refresh support or fallback and tests.
- Assumptions:
  - At least one managed runtime adapter can receive a compact activation update at a controlled turn boundary.
- Needs clarification: None

### STORY-005: Enforce audit, failure, security, and v1 boundary guardrails

- Short name: `skill-demand-guardrails`
- Source reference path: `docs/Steps/SkillsOnDemand.md`
- Source sections: 6. Core Invariants, 11. External Agents, 12. Failure Behavior, 13. Observability and Audit, 14. Security Rules, 15. Implementation Notes, 16. Test Cases, 17. Future Extensions
- Why: The design has many cross-cutting constraints that must be explicitly owned rather than left as implied behavior across earlier stories.
- Narrative: As an operator, I want every Skills On Demand query, denial, approval, failure, and boundary decision to be auditable and secure so the feature can be enabled without weakening source policy or observability.
- Independent test: Run cross-path contract tests that verify failure-code normalization, audit records for query/request outcomes, redacted or bounded diagnostics, external-agent denial, and absence of body/secret/ref leakage in responses.
- Dependencies: STORY-002, STORY-003, STORY-004
- Scope:
  - Normalize failure codes and denial responses across query, request, materialization, and refresh paths.
  - Record request audit events with requested skill names, result, result code, derived snapshot id, manifest ref, and diagnostics ref.
  - Ensure detailed diagnostics use artifacts when needed while high-cardinality metrics use bounded fields or hashes.
  - Enforce security rules covering secrets, hidden body disclosure, arbitrary artifact/database reads, local-only policy, and repo source immutability.
  - Document and test that external agents and future extensions are not enabled in v1.
- Out of scope:
  - Implementing future per-skill queryable/fetchable flags, source-kind permissions, approval UI, semantic search, retrieval-mode serving, or external-agent support.
  - Creating Jira issues or specs from this breakdown.
- Acceptance criteria:
  - Every denied or failed query/request path uses a documented stable code and actionable message.
  - Every request records an audit event with parent snapshot, requested skills, result, derived snapshot or diagnostics refs when present, and runtime/step context.
  - Metrics avoid raw long natural-language query text and use hashes or bounded fields.
  - Responses never expose secrets, arbitrary artifact/database access, or full Skill bodies.
  - External agents are denied or left on initial snapshots only until an adapter satisfies the documented requirements.
  - Future-extension capabilities are not implemented as hidden v1 behavior.
- Requirements:
  - Centralize or consistently share failure code definitions.
  - Add audit/event tests for both query and request paths.
  - Add security regression coverage for body/ref leakage, local-only policy bypass, and repo projection mutation.
  - Add explicit tests or documentation that external-agent support is outside v1.
- Source design coverage:
  - `DESIGN-REQ-006`: Ensures policy guardrails are enforced and tested across the whole feature.
  - `DESIGN-REQ-011`: Ensures all failures preserve the current active snapshot.
  - `DESIGN-REQ-012`: Owns request audit events and telemetry hygiene.
  - `DESIGN-REQ-013`: Owns security rules across query, request, and projection paths.
  - `DESIGN-REQ-014`: Owns managed-runtime-only v1 and future extension boundaries.
  - `DESIGN-REQ-015`: Owns cross-path failure, audit, and security test coverage.
- Assumptions:
  - Existing observability or audit-event mechanisms can record these events without adding a new persistent table.
- Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-002, STORY-003, STORY-004
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-002, STORY-003, STORY-005
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-003
- `DESIGN-REQ-009` -> STORY-001
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-012` -> STORY-002, STORY-005
- `DESIGN-REQ-013` -> STORY-002, STORY-004, STORY-005
- `DESIGN-REQ-014` -> STORY-005
- `DESIGN-REQ-015` -> STORY-003, STORY-004, STORY-005

## Dependencies

- `STORY-001` depends on no prior story.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-001.
- `STORY-004` depends on STORY-003.
- `STORY-005` depends on STORY-002, STORY-003, STORY-004.

## Out Of Scope

- Creating or modifying spec.md files or specs/ directories: Breakdown only produces story candidates; specify happens later.
- Per-user, per-workspace, per-skill, per-source, or per-runtime fetchability controls: The declarative design explicitly defines only one global v1 feature flag.
- Approval workflows and requires_approval handling: The result value is reserved for future use; v1 may return activated, denied, or no_change.
- External-agent support: Initial scope is managed runtimes only until adapters meet stronger control-call and audit requirements.
- Retrieval-mode Skill serving, semantic catalog search, cost budgets, and UI approval controls: These are future extensions that must preserve the same lifecycle.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
