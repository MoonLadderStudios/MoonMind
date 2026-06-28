# Omnigent Adapter Story Breakdown

- Source: `docs/Omnigent/OmnigentAdapter.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-981`
- Coverage gate: PASS - every major design point is owned by at least one story.
- Extracted at: 2026-06-28T06:45:33Z
- Requested output mode: `jira`

## Design Summary

MoonMind delegates one v1 AgentRun step to Omnigent through a gated external-provider streaming activity while Temporal remains the durable orchestration boundary and MoonMind artifacts remain the durable evidence layer.

Outcomes:
- Operators can choose Omnigent as a single canonical external provider.
- Repository and host targeting are validated before provider calls.
- Retries reattach to the same session without duplicate first messages.
- Dashboard and audit surfaces can inspect MoonMind artifacts instead of transient Omnigent state.

Boundaries:
- Omnigent owns live session execution.
- MoonMind owns workflow orchestration, artifact storage, result contracts, and policy enforcement.
- V1 excludes polling/session reuse and managed-runtime ownership.

Non-goals:
- No provider-specific top-level aliases.
- No raw credentials in workflow payloads or artifacts.
- No undocumented diff endpoint requirement.
- No non-terminal execute result in v1.

## Coverage Points

- `DESIGN-REQ-001` (requirement) External provider identity: Omnigent v1 is a single external provider registered as agentId omnigent, not a family of top-level aliases. Owners: STORY-001.
- `DESIGN-REQ-002` (security) Runtime gate and secret boundary: Registration and execution require an explicit enablement gate and activity-side secret resolution with redaction. Owners: STORY-001.
- `DESIGN-REQ-003` (integration) Streaming gateway activity: V1 execution uses integration.omnigent.execute as a streaming gateway that returns terminal results or errors. Owners: STORY-003.
- `DESIGN-REQ-004` (requirement) Canonical request nesting: Omnigent-specific endpoint, agent, session, prompt, workspace, and capture controls live under parameters.omnigent. Owners: STORY-002.
- `DESIGN-REQ-005` (constraint) Managed session workspace validation: Managed sessions cannot include host_id or local paths and must receive repository URLs for repository edit tasks unless empty workspace is explicit. Owners: STORY-002.
- `DESIGN-REQ-006` (constraint) External host validation: External-host sessions use hostId plus absolute host path workspaces rather than repository URLs. Owners: STORY-002.
- `DESIGN-REQ-007` (requirement) Agent target resolution: The activity resolves Omnigent targets through agent id, name lookup, bundle upload, default agent name, then integration_error. Owners: STORY-002.
- `DESIGN-REQ-008` (integration) Thin Omnigent client: Transport logic is isolated in an HTTP/SSE client with confirmed operations, structured errors, redaction, and no assumed diff endpoint. Owners: STORY-002.
- `DESIGN-REQ-009` (state-model) Session creation and lifecycle: The execute activity validates, resolves, creates or reuses sessions, streams events, waits for terminal status, and harvests resources. Owners: STORY-003.
- `DESIGN-REQ-010` (requirement) First-message construction: The first message is assembled from ordered prompt sources and includes a non-secret marker when configured. Owners: STORY-004.
- `DESIGN-REQ-011` (state-model) First-message idempotency state: Durable not_prepared/prepared/posting/posted/terminal transitions prevent duplicate first messages across retries. Owners: STORY-004.
- `DESIGN-REQ-012` (state-model) Provider-specific run mapping: omnigent_external_runs stores retry/session/idempotency state and v1 avoids a generic externalSession abstraction. Owners: STORY-004.
- `DESIGN-REQ-013` (requirement) State normalization: Omnigent observations normalize into canonical states internally and unknown raw states are contract errors. Owners: STORY-003.
- `DESIGN-REQ-014` (artifact) Stream and snapshot artifacts: Initial/final snapshots and raw/normalized SSE stream artifacts are captured with defined schemas. Owners: STORY-005.
- `DESIGN-REQ-015` (durability) Heartbeat and retry reattach: The activity heartbeats compact progress and retries reconnect to the existing session. Owners: STORY-004.
- `DESIGN-REQ-016` (artifact) Workspace and session resource harvest: Changed files, current workspace contents, manifests, session files, and metadata are copied into MoonMind artifacts. Owners: STORY-005.
- `DESIGN-REQ-017` (artifact) Patch and PR artifacts: Patch artifacts come from GitHub PRs, host helpers, or future capability probes, with patch-unavailable diagnostics when absent. Owners: STORY-005.
- `DESIGN-REQ-018` (observability) Diagnostics always produced: Successful and failed runs produce diagnostics with provider, transport, idempotency, and capture metadata. Owners: STORY-005.
- `DESIGN-REQ-019` (artifact) Child session capture: Child sessions are recorded and may have snapshots/resources captured in v1. Owners: STORY-005.
- `DESIGN-REQ-020` (requirement) Cancellation and cleanup: Cancellation interrupts, then stops, then harvests before optional deletion governed by explicit policy. Owners: STORY-006.
- `DESIGN-REQ-021` (security) Security redaction and no raw credentials: No raw tokens or credentials enter workflow payloads, labels, artifacts, logs, diagnostics, or parameters; existing redaction helpers are reused. Owners: STORY-006.
- `DESIGN-REQ-022` (requirement) Failure class mapping: Provider failures map to canonical MoonMind failure classes with retryability and user/system/integration distinctions. Owners: STORY-002, STORY-004, STORY-006.
- `DESIGN-REQ-023` (artifact) Canonical result contract: Results are compact terminal AgentRunResult objects with refs and metadata, not provider-native top-level payloads. Owners: STORY-003, STORY-005.
- `DESIGN-REQ-024` (observability) Observability surface mapping: Captured artifacts feed summaries, logs, diagnostics, step evidence, merged logs, and primary output surfaces. Owners: STORY-005.
- `DESIGN-REQ-025` (non-goal) V2 and helper boundaries: Session reuse, polling mode, non-terminal streaming results, and host-side helpers are future explicit extensions, not v1 behavior. Owners: STORY-006.
- `DESIGN-REQ-026` (constraint) Test strategy: Unit, adapter contract, fake-server integration, and live smoke tests cover the adapter boundary and retry/capture behavior. Owners: STORY-007.
- `DESIGN-REQ-027` (migration) Implementation sequence: Implementation can proceed through gate/models/registration, execute capture/idempotency, harvesting, PR processing, and optional future modes. Owners: STORY-007.
- `DESIGN-REQ-028` (constraint) Open question containment: Unresolved endpoint, external-host, cleanup, callback, reset, patch-helper, and empty-workspace choices must remain explicit rather than implicit behavior. Owners: STORY-002, STORY-006, STORY-007.
- `DESIGN-REQ-029` (constraint) Durable authority invariant: Omnigent owns live execution while MoonMind owns durable orchestration and evidence represented as artifact refs. Owners: STORY-003, STORY-004, STORY-005.
- `DESIGN-REQ-030` (non-goal) Non-goal guardrails: V1 must avoid replacing managed runtimes, becoming a second workflow engine, embedding credentials, top-level aliases, undocumented diff assumptions, and multi-step reuse. Owners: STORY-001, STORY-003, STORY-006.

## Stories

### STORY-001: Register Omnigent as a gated external provider

- Short name: `omnigent-provider-registration`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 1. Purpose, 2.1 Treat Omnigent as an external provider in v1, 5. Provider identity and registration, 6. Adapter classification, 20. v1 adapter and registry sketch
- Claim IDs: `docs-omnigent-omnigentadapter-purpose-c01`, `docs-omnigent-omnigentadapter-decision-summary-c01`, `docs-omnigent-omnigentadapter-provider-registration-c01`, `docs-omnigent-omnigentadapter-adapter-classification-c01`, `docs-omnigent-omnigentadapter-implementation-c01`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-030`
- Dependencies: None

As a MoonMind operator, I need Omnigent exposed as one gated external provider so routing, policy, metrics, and adapter ownership remain unambiguous.

Why: This establishes the canonical provider identity and prevents alias-driven policy drift before execution paths are added.

Independent test: With Omnigent disabled the registry omits the provider; with valid enablement the registry accepts only agentKind external plus agentId omnigent, rejects alias IDs, and reports streaming_gateway capability with polling hooks that fail loudly.

Scope:
- Runtime gate settings for Omnigent enablement and endpoint configuration.
- Conditional external adapter registration for canonical agentId omnigent.
- Provider capability descriptor declaring streaming_gateway execution and disabled polling/callback capabilities.
- Loud failures for unused v1 polling hooks and rejection of top-level provider aliases.

Out of scope:
- Launching or supervising Omnigent hosts directly.
- Adding managed-runtime adapter behavior.
- Adding alternate top-level agent IDs for Claude, Codex, Polly, or session variants.

Acceptance criteria:
- Given OMNIGENT_ENABLED is false or missing, Omnigent is not registered as an available external provider.
- Given OMNIGENT_ENABLED=1 and OMNIGENT_SERVER_URL is configured, the registry exposes exactly the canonical provider identity omnigent for external agent requests.
- Requests using omnigent_session, omnigent_claude, omnigent_codex, or omnigent_polly as top-level agentId are rejected.
- Provider capability metadata declares executionStyle streaming_gateway and does not advertise callback, polling result fetch, or direct cancel support.
- The adapter does not read or expose OMNIGENT_API_TOKEN outside activity-side execution boundaries.

Requirements:
- Register Omnigent only through the runtime gate.
- Use BaseExternalAgentAdapter for v1.
- Keep agentKind external and agentId omnigent as the only top-level identity.
- Fail loudly for unused polling hooks.

Source design coverage:
- `DESIGN-REQ-001`: Omnigent v1 is a single external provider registered as agentId omnigent, not a family of top-level aliases.
- `DESIGN-REQ-002`: Registration and execution require an explicit enablement gate and activity-side secret resolution with redaction.
- `DESIGN-REQ-030`: V1 must avoid replacing managed runtimes, becoming a second workflow engine, embedding credentials, top-level aliases, undocumented diff assumptions, and multi-step reuse.

Assumptions:
- Existing external adapter registry seams can conditionally register providers from runtime settings.

### STORY-002: Validate Omnigent requests and resolve execution targets

- Short name: `omnigent-request-targeting`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 7. Declarative request contract, 7.4 Managed vs external session validation, 7.5 Target resolution order, 8. Omnigent client, 17. Error classification
- Claim IDs: `docs-omnigent-omnigentadapter-request-contract-c01`, `docs-omnigent-omnigentadapter-session-validation-c01`, `docs-omnigent-omnigentadapter-target-resolution-c01`, `docs-omnigent-omnigentadapter-client-c01`, `docs-omnigent-omnigentadapter-errors-c01`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-005`, `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-008`, `DESIGN-REQ-022`, `DESIGN-REQ-028`
- Dependencies: STORY-001

As a workflow author, I need Omnigent-specific target selection validated under parameters.omnigent so managed and external sessions launch against the intended agent and workspace without leaking provider details into top-level MoonMind contracts.

Why: Execution cannot be safe or reproducible until target, workspace, host, and prompt/capture settings are represented and rejected consistently at the adapter boundary.

Independent test: Unit and adapter-boundary tests construct AgentExecutionRequest payloads for valid managed repository sessions, explicit empty workspaces, invalid local managed paths, valid external-host paths, missing targets, and Omnigent client transport failures.

Scope:
- Typed parsing of parameters.omnigent endpoint, agent, session, workspaceContext, prompt, and capture blocks.
- Managed and external session validation rules.
- Agent target resolution through id, name lookup, bundle upload, and default name.
- Thin Omnigent client operations needed for target resolution and session creation.
- Canonical failure classes for bad requests, unresolved targets, transport failures, and contract drift.

Out of scope:
- Executing the full streaming session.
- Harvesting terminal resources.
- Inventing undocumented Omnigent diff endpoints.

Acceptance criteria:
- All Omnigent-specific selection fields are accepted only under parameters.omnigent and do not alter top-level agent identity.
- Managed sessions reject caller-provided hostId and absolute/local host workspace paths.
- Managed repository edit tasks pass repository URL plus optional branch through session.workspace rather than only prompt context.
- External-host sessions require hostId and an absolute host workspace path and reject repository URLs as host paths.
- Agent target resolution follows agentId, agentName lookup, bundle upload, default agent name, then integration_error when unresolved.
- The Omnigent client exposes confirmed API operations, redacts transport diagnostics, structures non-2xx responses, and does not require get_workspace_diff.

Requirements:
- Represent all Omnigent target selection in parameters.omnigent.
- Validate hostType-specific workspace rules before calling Omnigent.
- Normalize repository workflow context into managed session.workspace for repository tasks.
- Resolve target agent deterministically and fail with canonical errors.

Source design coverage:
- `DESIGN-REQ-004`: Omnigent-specific endpoint, agent, session, prompt, workspace, and capture controls live under parameters.omnigent.
- `DESIGN-REQ-005`: Managed sessions cannot include host_id or local paths and must receive repository URLs for repository edit tasks unless empty workspace is explicit.
- `DESIGN-REQ-006`: External-host sessions use hostId plus absolute host path workspaces rather than repository URLs.
- `DESIGN-REQ-007`: The activity resolves Omnigent targets through agent id, name lookup, bundle upload, default agent name, then integration_error.
- `DESIGN-REQ-008`: Transport logic is isolated in an HTTP/SSE client with confirmed operations, structured errors, redaction, and no assumed diff endpoint.
- `DESIGN-REQ-022`: Provider failures map to canonical MoonMind failure classes with retryability and user/system/integration distinctions.
- `DESIGN-REQ-028`: Unresolved endpoint, external-host, cleanup, callback, reset, patch-helper, and empty-workspace choices must remain explicit rather than implicit behavior.

Assumptions:
- Bundle upload uses existing MoonMind artifact refs for agent bundles rather than raw file contents in workflow payloads.

Needs clarification:
- [NEEDS CLARIFICATION] Should absence of repository context be enough to permit an empty managed workspace, or should v1 require an explicit allowEmptyWorkspace flag?

### STORY-003: Execute Omnigent sessions through a terminal streaming gateway

- Short name: `omnigent-streaming-execute`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 2.2 Use streaming-gateway execution first, 2.4 Durability boundary in v1, 3. Conceptual mapping, 9. Execute activity lifecycle, 11. State normalization, 18. Canonical result contract
- Claim IDs: `docs-omnigent-omnigentadapter-decision-summary-c02`, `docs-omnigent-omnigentadapter-decision-summary-c04`, `docs-omnigent-omnigentadapter-conceptual-mapping-c01`, `docs-omnigent-omnigentadapter-execute-lifecycle-c01`, `docs-omnigent-omnigentadapter-state-normalization-c01`, `docs-omnigent-omnigentadapter-result-c01`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-009`, `DESIGN-REQ-013`, `DESIGN-REQ-023`, `DESIGN-REQ-029`, `DESIGN-REQ-030`
- Dependencies: STORY-001, STORY-002

As a workflow operator, I need integration.omnigent.execute to run one Omnigent session for one AgentRun and return a terminal MoonMind result or explicit failure so v1 fits the existing streaming-gateway workflow contract.

Why: This provides the core execution surface while preserving Temporal workflow determinism and avoiding premature polling/session continuation semantics.

Independent test: Against a fake Omnigent server, execute a successful run and a failed run and assert the activity returns terminal AgentRunResult values, normalizes internal states, and never exposes provider-native payloads as top-level result fields.

Scope:
- Activity registration for integration.omnigent.execute with AgentExecutionRequest input and AgentRunResult output.
- Lifecycle orchestration from validation through session create/reuse, stream attachment, first snapshot, message post, terminal wait, final snapshot, harvest handoff, and result return.
- Internal normalization of Omnigent observations into canonical statuses.
- Terminal-only return behavior for v1 streaming gateway.
- Compact AgentRunResult metadata and refs without provider-native top-level payloads.

Out of scope:
- Returning non-terminal intervention_requested results from execute.
- Durable checkpointing inside a session beyond activity heartbeat and retry behavior.
- Multi-step Omnigent session reuse.

Acceptance criteria:
- integration.omnigent.execute is the v1 execution activity for Omnigent-backed AgentRuns.
- The activity accepts AgentExecutionRequest and returns AgentRunResult only when the run has reached a terminal completed, failed, canceled, timed_out, or equivalent terminal outcome.
- Omnigent waiting with active elicitation is recognized internally, but execute does not return a non-terminal intervention or approval status in v1.
- Unknown Omnigent status values are treated as adapter contract errors rather than passed through to workflow code.
- The final result contains outputRefs, diagnosticsRef, summary, failureClass/providerErrorCode when relevant, and compact provider metadata only.

Requirements:
- Use streaming-gateway execution first.
- Keep Temporal durable orchestration at activity boundaries.
- Normalize provider observations internally.
- Return compact terminal canonical results.

Source design coverage:
- `DESIGN-REQ-003`: V1 execution uses integration.omnigent.execute as a streaming gateway that returns terminal results or errors.
- `DESIGN-REQ-009`: The execute activity validates, resolves, creates or reuses sessions, streams events, waits for terminal status, and harvests resources.
- `DESIGN-REQ-013`: Omnigent observations normalize into canonical states internally and unknown raw states are contract errors.
- `DESIGN-REQ-023`: Results are compact terminal AgentRunResult objects with refs and metadata, not provider-native top-level payloads.
- `DESIGN-REQ-029`: Omnigent owns live execution while MoonMind owns durable orchestration and evidence represented as artifact refs.
- `DESIGN-REQ-030`: V1 must avoid replacing managed runtimes, becoming a second workflow engine, embedding credentials, top-level aliases, undocumented diff assumptions, and multi-step reuse.

Assumptions:
- The existing streaming-gateway workflow path treats returned AgentRunResult values as completed, matching the design constraint.

### STORY-004: Make Omnigent retries reattach without duplicate first messages

- Short name: `omnigent-idempotent-reattach`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 9.4 First message construction, 9.5 First message idempotency, 10. Idempotency and run mapping, 10.1 Shared external-session table decision, 12.5 Heartbeating, worker death, and re-attach, 17. Error classification
- Claim IDs: `docs-omnigent-omnigentadapter-first-message-c01`, `docs-omnigent-omnigentadapter-run-mapping-c01`, `docs-omnigent-omnigentadapter-stream-capture-c01`, `docs-omnigent-omnigentadapter-errors-c01`
- Coverage IDs: `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-015`, `DESIGN-REQ-022`, `DESIGN-REQ-029`
- Dependencies: STORY-003

As an operator relying on Temporal retries, I need Omnigent execution retries to reuse the original session and reconcile first-message delivery so a worker failure does not create duplicate sessions or duplicate prompts.

Why: The v1 durability promise depends on retry reattachment and fail-closed ambiguity handling at the first-message boundary.

Independent test: Fake-server retry tests simulate crashes after session create, after prepared, during posting, after successful first-message response, ambiguous posting reconciliation, and same idempotency key with a different digest.

Scope:
- Provider-specific omnigent_external_runs durable mapping.
- First-message digest and optional non-secret marker construction.
- Durable first-message state transitions before and after POST.
- Retry reattachment to existing Omnigent session.
- Reconciliation of posting state using snapshots, pending inputs, transcripts, and consumed input events.
- Fail-fast digest mismatch and fail-closed ambiguity handling.

Out of scope:
- Generic externalSession abstraction.
- Per-message idempotency for multi-turn v2 sessions.
- Embedding raw prompt content into durable Temporal payloads beyond artifact references and digest metadata.

Acceptance criteria:
- A persisted record keyed by idempotencyKey stores Omnigent session id, endpoint ref, target metadata, first-message state, digest/marker, artifact refs, and terminal refs.
- Retries for an existing idempotencyKey reuse the existing Omnigent session and never create a second session unless an explicit operator reset has occurred.
- The first-message state is persisted as prepared before POST, posting immediately before POST, and posted immediately after successful POST.
- Retries finding posted skip the first message; retries finding posting reconcile before deciding whether the message can be skipped or, only when absence is proved, retried.
- Ambiguous posting reconciliation raises or returns a terminal integration_error and never returns non-terminal intervention_requested from execute.
- Reusing an idempotencyKey with a different first-message digest fails fast as user_error.

Requirements:
- Persist provider-specific retry mapping outside activity attempts.
- Digest and marker the first message without secrets.
- Heartbeat compact progress while streaming.
- Reattach rather than recreate after worker death.
- Fail closed when first-message acceptance cannot be determined.

Source design coverage:
- `DESIGN-REQ-010`: The first message is assembled from ordered prompt sources and includes a non-secret marker when configured.
- `DESIGN-REQ-011`: Durable not_prepared/prepared/posting/posted/terminal transitions prevent duplicate first messages across retries.
- `DESIGN-REQ-012`: omnigent_external_runs stores retry/session/idempotency state and v1 avoids a generic externalSession abstraction.
- `DESIGN-REQ-015`: The activity heartbeats compact progress and retries reconnect to the existing session.
- `DESIGN-REQ-022`: Provider failures map to canonical MoonMind failure classes with retryability and user/system/integration distinctions.
- `DESIGN-REQ-029`: Omnigent owns live execution while MoonMind owns durable orchestration and evidence represented as artifact refs.

Assumptions:
- A database or durable activity-side store is available for omnigent_external_runs with transactional state updates around the POST boundary.

### STORY-005: Capture Omnigent streams, snapshots, resources, and diagnostics as MoonMind artifacts

- Short name: `omnigent-artifact-capture`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 2.3 MoonMind remains the artifact authority, 12. Stream and snapshot capture, 13. Artifact harvesting, 14. Child sessions, 18. Canonical result contract, 19. Observability surfaces, 26. Design invariant
- Claim IDs: `docs-omnigent-omnigentadapter-decision-summary-c03`, `docs-omnigent-omnigentadapter-stream-capture-c01`, `docs-omnigent-omnigentadapter-artifact-harvest-c01`, `docs-omnigent-omnigentadapter-child-sessions-c01`, `docs-omnigent-omnigentadapter-result-c01`, `docs-omnigent-omnigentadapter-observability-c01`, `docs-omnigent-omnigentadapter-invariant-c01`
- Coverage IDs: `DESIGN-REQ-014`, `DESIGN-REQ-016`, `DESIGN-REQ-017`, `DESIGN-REQ-018`, `DESIGN-REQ-019`, `DESIGN-REQ-023`, `DESIGN-REQ-024`, `DESIGN-REQ-029`
- Dependencies: STORY-003, STORY-004

As a MoonMind operator, I need every important Omnigent input, stream, snapshot, workspace change, session file, child session, PR signal, and diagnostic copied into MoonMind artifacts so the dashboard and later audits do not depend on transient Omnigent state.

Why: This delivers the evidence layer promised by the design and lets successful and failed runs be inspected through existing MoonMind observability surfaces.

Independent test: A fake-server integration test completes an Omnigent run with SSE frames, changed files, session files, a child-session event, and a PR URL, then asserts all required MoonMind artifacts and result refs are produced; a no-patch-source case produces output.workspace.patch_unavailable.json.

Scope:
- Required request/response, raw SSE, normalized SSE, initial/final snapshot, transcript, final response, changed-file, current-file, manifest, session-file, PR, and diagnostics artifacts.
- Patch artifact sourcing from GitHub PRs, host helpers, or explicit future capability probes.
- Patch-unavailable diagnostics when no patch source exists.
- Child session id logging and authorized final snapshot/resource capture.
- Mapping artifact refs into AgentRunResult and observability surfaces.

Out of scope:
- Treating Omnigent resource URLs or file ids as substitutes for ArtifactRefs.
- Requiring a public Omnigent diff endpoint.
- Host-side stdout/stderr capture without an explicit helper.

Acceptance criteria:
- The activity captures redacted session-create and first-message request/response artifacts.
- The activity stores initial and final snapshots, raw SSE JSONL, normalized SSE JSONL, transcript JSONL, and final response markdown.
- Terminal harvesting stores changed-files index, current workspace file contents, workspace manifest, session files index/content/metadata, and always a diagnostics artifact.
- Patch output is created only from GitHub PR diff, host-side helper output, or an explicit future capability probe; otherwise patch_unavailable diagnostics are stored without failing solely for missing patch.
- Detected PR URLs are persisted into GitHub PR metadata artifacts and compact result metadata.
- Child session ids are recorded and authorized child snapshots/resources are linked from diagnostics or output refs.
- Dashboard-facing observability links can derive summaries, logs, diagnostics, step evidence, merged logs, and final primary output from MoonMind artifacts.

Requirements:
- Copy Omnigent-observable resources into MoonMind artifacts.
- Represent durable evidence through ArtifactRefs.
- Always produce diagnostics.
- Do not rely on transient Omnigent session state for authoritative evidence.

Source design coverage:
- `DESIGN-REQ-014`: Initial/final snapshots and raw/normalized SSE stream artifacts are captured with defined schemas.
- `DESIGN-REQ-016`: Changed files, current workspace contents, manifests, session files, and metadata are copied into MoonMind artifacts.
- `DESIGN-REQ-017`: Patch artifacts come from GitHub PRs, host helpers, or future capability probes, with patch-unavailable diagnostics when absent.
- `DESIGN-REQ-018`: Successful and failed runs produce diagnostics with provider, transport, idempotency, and capture metadata.
- `DESIGN-REQ-019`: Child sessions are recorded and may have snapshots/resources captured in v1.
- `DESIGN-REQ-023`: Results are compact terminal AgentRunResult objects with refs and metadata, not provider-native top-level payloads.
- `DESIGN-REQ-024`: Captured artifacts feed summaries, logs, diagnostics, step evidence, merged logs, and primary output surfaces.
- `DESIGN-REQ-029`: Omnigent owns live execution while MoonMind owns durable orchestration and evidence represented as artifact refs.

Assumptions:
- Existing artifact APIs can store redacted JSONL, markdown, current file content, manifests, and metadata with link types consumed by observability surfaces.

Needs clarification:
- [NEEDS CLARIFICATION] Should v1 require a host-side helper for first-class patch artifacts, or is GitHub PR post-processing sufficient?

### STORY-006: Enforce Omnigent cancellation, cleanup, security, and v1 boundaries

- Short name: `omnigent-safety-boundaries`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 4. Non-goals, 15. Cancellation and cleanup, 16. Security and secret handling, 17. Error classification, 21. v2 polling/session mode, 22. Host-side capture helper, 25. Open questions
- Claim IDs: `docs-omnigent-omnigentadapter-non-goals-c01`, `docs-omnigent-omnigentadapter-cancellation-c01`, `docs-omnigent-omnigentadapter-security-c01`, `docs-omnigent-omnigentadapter-errors-c01`, `docs-omnigent-omnigentadapter-v2-helper-c01`, `docs-omnigent-omnigentadapter-open-questions-c01`
- Coverage IDs: `DESIGN-REQ-020`, `DESIGN-REQ-021`, `DESIGN-REQ-022`, `DESIGN-REQ-025`, `DESIGN-REQ-028`, `DESIGN-REQ-030`
- Dependencies: STORY-003

As a platform maintainer, I need cancellation, cleanup, redaction, error classification, v1 non-goals, and future-extension boundaries enforced so Omnigent delegation remains safe and auditable.

Why: This story owns the cross-cutting guardrails that prevent v1 from silently expanding into unsupported execution, credential, cleanup, or workflow semantics.

Independent test: Unit and fake-server tests cover cancellation before completion, stop escalation, harvest-before-delete ordering, delete_branch policy rejection, redaction of secret-like fields in diagnostics, error-class mapping, and attempts to use unsupported v2/non-goal behavior.

Scope:
- Activity cancellation handler that sends interrupt and escalates to stop_session.
- Harvest-before-delete cleanup behavior and default session preservation.
- Policy guard for destructive delete_branch behavior.
- Use of existing redaction helpers for headers, cookies, tokens, secret-like fields, logs, artifacts, and diagnostics.
- Canonical failure class mapping for transport, auth, validation, provisioning, runtime, timeout, harvest, stream schema, and idempotency failures.
- Explicit rejection or deferral of v2 polling/session reuse, non-terminal streaming returns, and host-side helper semantics unless implemented behind a future contract.

Out of scope:
- Implementing v2 continuation, send_message, harvest_session, or session epoch semantics.
- Deleting Omnigent branches by default.
- Adding Omnigent-specific redaction code that bypasses existing MoonMind helpers.

Acceptance criteria:
- Temporal activity cancellation sends an Omnigent interrupt event and escalates to stop_session after a bounded grace period when the session remains active.
- The adapter harvests available artifacts before any optional session deletion and preserves Omnigent sessions by default.
- delete_branch=true is blocked unless an explicit operator or workflow policy permits destructive cleanup.
- OMNIGENT_API_TOKEN, auth headers, cookies, runtime credentials, raw secret fields, and secret-like values are never stored in workflow payloads, labels, artifacts, logs, or diagnostics.
- Existing MoonMind redaction helpers are used for Omnigent request, response, stream, and diagnostics data.
- Failures are classified into canonical user_error, integration_error, system_error, execution_error, timed_out, or canceled outcomes according to the design table.
- V1 does not expose multi-step session reuse, status-bearing streaming results, or host-side helper capture unless a later story changes the workflow contract explicitly.

Requirements:
- Cancel through interrupt and stop_session.
- Preserve sessions by default and require policy for destructive branch deletion.
- Reuse central redaction helpers.
- Map failures to canonical classes.
- Keep v2 and host-helper capabilities out of v1 behavior.

Source design coverage:
- `DESIGN-REQ-020`: Cancellation interrupts, then stops, then harvests before optional deletion governed by explicit policy.
- `DESIGN-REQ-021`: No raw tokens or credentials enter workflow payloads, labels, artifacts, logs, diagnostics, or parameters; existing redaction helpers are reused.
- `DESIGN-REQ-022`: Provider failures map to canonical MoonMind failure classes with retryability and user/system/integration distinctions.
- `DESIGN-REQ-025`: Session reuse, polling mode, non-terminal streaming results, and host-side helpers are future explicit extensions, not v1 behavior.
- `DESIGN-REQ-028`: Unresolved endpoint, external-host, cleanup, callback, reset, patch-helper, and empty-workspace choices must remain explicit rather than implicit behavior.
- `DESIGN-REQ-030`: V1 must avoid replacing managed runtimes, becoming a second workflow engine, embedding credentials, top-level aliases, undocumented diff assumptions, and multi-step reuse.

Assumptions:
- Activity cancellation can execute best-effort provider calls before surfacing terminal cancellation to MoonMind.

Needs clarification:
- [NEEDS CLARIFICATION] Should v1 allow external-host sessions immediately, or require hostType=managed until separate policy and smoke tests exist?
- [NEEDS CLARIFICATION] Should successful CI-style runs delete Omnigent sessions after harvest by default or only through explicit request policy?

### STORY-007: Prove the Omnigent adapter through boundary and fake-server tests

- Short name: `omnigent-boundary-tests`
- Source reference path: `docs/Omnigent/OmnigentAdapter.md`
- Source sections: 23. Testing strategy, 24. Implementation sequencing, 25. Open questions
- Claim IDs: `docs-omnigent-omnigentadapter-testing-c01`, `docs-omnigent-omnigentadapter-implementation-c01`, `docs-omnigent-omnigentadapter-open-questions-c01`
- Coverage IDs: `DESIGN-REQ-026`, `DESIGN-REQ-027`, `DESIGN-REQ-028`
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006

As a maintainer reviewing the Omnigent adapter, I need focused tests at the unit, adapter contract, fake-server integration, and live-smoke boundaries so the adapter contract is verifiable before later workflow phases build on it.

Why: The design’s testing strategy is broad enough to warrant a dedicated verification story that can be completed independently after the core surfaces exist.

Independent test: Run the targeted unit suite and hermetic fake-server integration suite for Omnigent adapter areas; optionally run live smoke tests only when a real Omnigent endpoint and disposable repository credentials are configured.

Scope:
- Unit tests for validation, registration, status normalization, SSE parsing, redaction, idempotency, and digest behavior.
- Adapter contract tests for provider registration, accepted and rejected agent IDs, streaming capabilities, and polling hook failures.
- Hermetic fake Omnigent server integration tests for success, failure, managed host launch delay, workspace validation, stream disconnect, elicitation, resource harvest, patch unavailable diagnostics, child sessions, cancellation, and idempotent retries.
- Live smoke test guidance against a disposable Omnigent server and repository.

Out of scope:
- Provider verification in required CI when credentials are unavailable.
- Implementing product behavior not already delivered by prior stories.
- Resolving open product questions inside tests rather than asserting the chosen explicit behavior.

Acceptance criteria:
- Unit tests cover target block validation, managed/external workspace rules, endpoint resolution, alias rejection, state normalization, SSE parsing, redaction, manifest generation, cancellation sequencing, and idempotency behavior.
- Adapter contract tests prove registration only when enabled, acceptance of agentId omnigent, rejection of unknown and alias IDs, streaming capability declaration, and loud v1 polling hook failure.
- Fake-server integration tests cover successful and failed runs, managed host launch delay, workspace validation, stream disconnect reconciliation, elicitation handling, resource harvest, patch unavailable diagnostics, child sessions, cancellation, and idempotent retry crash windows.
- Ambiguous posting reconciliation is tested to fail closed rather than returning intervention_requested from execute.
- Live smoke tests are documented or gated as provider verification rather than required hermetic CI when credentials are absent.

Requirements:
- Add or update tests at adapter and activity boundaries.
- Use a fake Omnigent server for hermetic integration coverage.
- Keep provider smoke tests separate from required credential-free CI.
- Tie unresolved open questions to explicit asserted behavior.

Source design coverage:
- `DESIGN-REQ-026`: Unit, adapter contract, fake-server integration, and live smoke tests cover the adapter boundary and retry/capture behavior.
- `DESIGN-REQ-027`: Implementation can proceed through gate/models/registration, execute capture/idempotency, harvesting, PR processing, and optional future modes.
- `DESIGN-REQ-028`: Unresolved endpoint, external-host, cleanup, callback, reset, patch-helper, and empty-workspace choices must remain explicit rather than implicit behavior.

Assumptions:
- Hermetic integration infrastructure can run a fake Omnigent server without real provider credentials.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-003
- `DESIGN-REQ-004` -> STORY-002
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-002
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-004
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-003
- `DESIGN-REQ-014` -> STORY-005
- `DESIGN-REQ-015` -> STORY-004
- `DESIGN-REQ-016` -> STORY-005
- `DESIGN-REQ-017` -> STORY-005
- `DESIGN-REQ-018` -> STORY-005
- `DESIGN-REQ-019` -> STORY-005
- `DESIGN-REQ-020` -> STORY-006
- `DESIGN-REQ-021` -> STORY-006
- `DESIGN-REQ-022` -> STORY-002, STORY-004, STORY-006
- `DESIGN-REQ-023` -> STORY-003, STORY-005
- `DESIGN-REQ-024` -> STORY-005
- `DESIGN-REQ-025` -> STORY-006
- `DESIGN-REQ-026` -> STORY-007
- `DESIGN-REQ-027` -> STORY-007
- `DESIGN-REQ-028` -> STORY-002, STORY-006, STORY-007
- `DESIGN-REQ-029` -> STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-030` -> STORY-001, STORY-003, STORY-006
- `docs-omnigent-omnigentadapter-adapter-classification-c01` -> STORY-001
- `docs-omnigent-omnigentadapter-artifact-harvest-c01` -> STORY-005
- `docs-omnigent-omnigentadapter-cancellation-c01` -> STORY-006
- `docs-omnigent-omnigentadapter-child-sessions-c01` -> STORY-005
- `docs-omnigent-omnigentadapter-client-c01` -> STORY-002
- `docs-omnigent-omnigentadapter-conceptual-mapping-c01` -> STORY-003
- `docs-omnigent-omnigentadapter-decision-summary-c01` -> STORY-001
- `docs-omnigent-omnigentadapter-decision-summary-c02` -> STORY-003
- `docs-omnigent-omnigentadapter-decision-summary-c03` -> STORY-005
- `docs-omnigent-omnigentadapter-decision-summary-c04` -> STORY-003
- `docs-omnigent-omnigentadapter-errors-c01` -> STORY-002, STORY-004, STORY-006
- `docs-omnigent-omnigentadapter-execute-lifecycle-c01` -> STORY-003
- `docs-omnigent-omnigentadapter-first-message-c01` -> STORY-004
- `docs-omnigent-omnigentadapter-implementation-c01` -> STORY-001, STORY-007
- `docs-omnigent-omnigentadapter-invariant-c01` -> STORY-005
- `docs-omnigent-omnigentadapter-non-goals-c01` -> STORY-006
- `docs-omnigent-omnigentadapter-observability-c01` -> STORY-005
- `docs-omnigent-omnigentadapter-open-questions-c01` -> STORY-006, STORY-007
- `docs-omnigent-omnigentadapter-provider-registration-c01` -> STORY-001
- `docs-omnigent-omnigentadapter-purpose-c01` -> STORY-001
- `docs-omnigent-omnigentadapter-request-contract-c01` -> STORY-002
- `docs-omnigent-omnigentadapter-result-c01` -> STORY-003, STORY-005
- `docs-omnigent-omnigentadapter-run-mapping-c01` -> STORY-004
- `docs-omnigent-omnigentadapter-security-c01` -> STORY-006
- `docs-omnigent-omnigentadapter-session-validation-c01` -> STORY-002
- `docs-omnigent-omnigentadapter-state-normalization-c01` -> STORY-003
- `docs-omnigent-omnigentadapter-stream-capture-c01` -> STORY-004, STORY-005
- `docs-omnigent-omnigentadapter-target-resolution-c01` -> STORY-002
- `docs-omnigent-omnigentadapter-testing-c01` -> STORY-007
- `docs-omnigent-omnigentadapter-v2-helper-c01` -> STORY-006

## Out Of Scope

- Creating spec.md files or specs/ directories: Breakdown produces temporary story candidates only; specify owns spec creation.
- Implementing adapter code: This run is limited to story decomposition.
- Creating Jira issues: Requested output mode is Jira-ready story artifacts, not issue publication.

## Gate Result

PASS - every major design point is owned by at least one story.

Recommended first story for `/speckit.specify`: `STORY-001` Register Omnigent as a gated external provider.

TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`. Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.
