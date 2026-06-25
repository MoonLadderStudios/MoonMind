# Workflow Proposal System

Status: Active desired state
Owners: MoonMind Engineering
Last Updated: 2026-06-21
Related: `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/WorkflowFinishSummarySystem.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/WorkflowConsoleArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/SkillAndPlanContracts.md`

---

## 1. Summary

MoonMind proposals are reviewable follow-up work items discovered during a `MoonMind.UserWorkflow`. They preserve useful next steps without automatically starting new executions.

The proposal architecture is GitHub-Issues-native:

1. MoonMind generates candidate follow-up work during the `proposals` stage of a `MoonMind.UserWorkflow`.
2. MoonMind validates, normalizes, deduplicates, and stores each proposal as an internal delivery record.
3. MoonMind routes each proposal to exactly one GitHub repository:
   - MoonMind-related proposals are delivered to the configured MoonMind repository, `MoonLadderStudios/MoonMind`.
   - Workflow-repo-related proposals are delivered to the workflow repository in `workflowCreateRequest.payload.repository`, for example `MoonLadderStudios/Tactics`.
4. MoonMind creates or updates a GitHub Issue in the resolved repository.
5. Reviewers triage proposals in GitHub Issues.
6. Promotion creates a new `MoonMind.UserWorkflow` only after a verified GitHub reviewer action.
7. MoonMind comments on the GitHub Issue with execution, pull request, error, and final status links.

Each proposal has two representations:

1. **Executable source of truth:** MoonMind's stored, validated `workflowCreateRequest` snapshot or artifact reference.
2. **Human review artifact:** the GitHub Issue rendered from that snapshot.

The GitHub Issue is the review surface, not the executable contract. MoonMind never executes arbitrary edited issue Markdown, comments, labels, or fields as a Workflow payload. Promotion always uses MoonMind's stored proposal snapshot plus bounded, validated reviewer controls.

The proposal system is Temporal-native:

1. `MoonMind.UserWorkflow` executes work.
2. A dedicated `proposals` stage runs after execution and before finalization.
3. Generator activities create candidate follow-up Workflow Executions from run artifacts and normalized agent results.
4. Submission activities validate candidates, resolve GitHub routing, persist delivery records, and create or update GitHub Issues.
5. GitHub webhook or sync activities observe reviewer actions.
6. Promotion creates a new Temporal execution only after human approval.

Canonical agent-skill storage, precedence, and snapshot semantics live in `docs/Steps/SkillSystem.md`. This document defines how proposals preserve and promote Workflow-facing skill intent.

---

## 2. Core Invariants

These rules are fixed:

1. `workflowCreateRequest` is the canonical promote-to-Workflow payload.
2. Proposal creation is not execution; promotion is the only action that starts new work.
3. Promotion creates a new `MoonMind.UserWorkflow` execution.
4. Human review is required before any proposal becomes durable running work.
5. Human review occurs in GitHub Issues.
6. The dashboard may link to proposal GitHub Issues, but it does not host a proposal queue, proposal decision page, or browser-side proposal review controls.
7. The GitHub Issue body is a rendered review artifact, not an executable payload.
8. Proposal generation is best-effort and must never compromise the correctness of the parent run result.
9. Proposal payloads must conform to the canonical Temporal submit contract used by `/api/executions`.
10. Proposal origin metadata must identify the durable workflow that produced the proposal.
11. `workflowCreateRequest.payload.repository` is the canonical workflow repository for workflow-repo proposals.
12. MoonMind-related proposals are routed to the configured MoonMind repository and use that repository as the promoted execution repository.
13. Workflow-repo-related proposals are routed to the repository that the promoted execution will operate on.
14. Proposal routing is deterministic and fail-closed when the destination cannot be resolved or is not allowed.
15. When a proposal depends on agent skill context, that context must be preserved explicitly in the stored `workflowCreateRequest` or through documented inheritance semantics.
16. Proposal promotion must not silently drift to unrelated skill defaults when the original proposal logic depended on explicit skill selection.
17. Preset-derived metadata in proposals is advisory review and reconstruction metadata, not a runtime dependency.
18. Proposal promotion validates and submits the reviewed flat Workflow payload; it does not depend on live preset catalog lookup or live preset re-expansion for correctness.
19. Deduplication is destination-repository-aware and deterministic.
20. Delivery to GitHub is idempotent. A repeated proposal with the same dedup target updates or links to the existing GitHub Issue instead of creating reviewer-facing duplicates.
21. GitHub credentials and webhook secrets remain inside trusted MoonMind integration boundaries and are never injected into managed agent runtimes.

---

## 3. `MoonMind.UserWorkflow` Lifecycle Integration

### 3.1 Submit-time contract

Workflow-shaped submit requests may include:

1. `task.proposeTasks`
2. `task.proposalPolicy`
3. `task.skills`
4. `step.skills`

These values are part of the durable run contract and must be preserved in `initialParameters` for the run. These fields directly impact downstream proposal generation, delivery, and promotion.

The canonical direction is:

1. Preserve the raw `task.proposalPolicy` object in `initialParameters`.
2. Resolve global defaults plus per-Workflow overrides inside the proposal stage or inside `proposal.submit`.
3. Persist enough resolved policy metadata on proposal delivery records to explain why a proposal was delivered to its GitHub repository.

### 3.2 Canonical submission-path normalization

All Workflow submission paths must normalize proposal intent into the same canonical nested Workflow payload accepted by `/api/executions` before `MoonMind.UserWorkflow` starts.

Required mapping:

1. session-level or adapter-level proposal opt-in becomes `initialParameters.task.proposeTasks`
2. session-level or adapter-level proposal routing overrides become `initialParameters.task.proposalPolicy`
3. Workflow/runtime/repository metadata flows through the canonical Workflow payload

For session-capable runtimes, this applies both when a user submits a Workflow Execution targeting a managed-session runtime and when a managed session creates additional MoonMind Workflow Executions through the internal API path.

### 3.3 Run states

For proposal-capable runs, the relevant state vocabulary is:

1. `scheduled`
2. `initializing`
3. `waiting_on_dependencies`
4. `planning`
5. `awaiting_slot`
6. `executing`
7. `awaiting_external`
8. `proposals`
9. `finalizing`
10. `completed`
11. `failed`
12. `canceled`

The proposal system uses this lifecycle vocabulary consistently across:

1. workflow state
2. execution API responses
3. dashboard status mapping
4. finish summaries
5. GitHub Issue comments and status labels
6. related documentation

### 3.4 When the `proposals` stage runs

The `proposals` stage runs only when both conditions are true:

1. global proposal generation is enabled by workflow settings
2. the submitted Workflow Execution enables proposals through `task.proposeTasks`

This gives operators a global off switch while still allowing Workflow-level opt-in when the feature is enabled system-wide.

For managed-session originated runs, the durable gate is the canonical nested value preserved in `initialParameters.task.proposeTasks`.

### 3.5 Proposal generation

Proposal generation happens in Temporal activities, never in workflow code.

Generators analyze:

1. run artifacts
2. plan-step outcomes
3. normalized `AgentRunResult` data from managed and external agents
4. finish-summary signals and execution diagnostics
5. resolved skill snapshot metadata and skill-related execution context, using artifact-backed skill metadata where needed
6. authored preset metadata and per-step source provenance when the parent run exposes reliable preset provenance

Generators must:

1. treat run inputs and logs as untrusted
2. use artifact-backed references for large context
3. avoid side effects such as commits, pushes, issue creation, or Workflow creation
4. redact or exclude secrets and unsafe command output
5. preserve skill selectors only when they materially affect correctness or expected behavior of the follow-up work
6. preserve reliable preset provenance only when the candidate work is genuinely derived from authored preset metadata or reliable step source metadata
7. avoid fabricating `task.authoredPresets`, binding IDs, include paths, or preset-derived labels for work that lacks reliable binding evidence

### 3.6 Proposal submission and delivery

Proposal submission is a separate side-effecting activity that:

1. validates candidate entries, including any `task.skills` or `step.skills` selectors
2. resolves proposal target classification and GitHub destination
3. normalizes origin metadata
4. enforces repository and run-quality routing rules
5. preserves explicit skill-selection intent when present
6. rejects or normalizes malformed skill-selection fields before storage
7. computes deduplication fields
8. creates or updates a MoonMind proposal delivery record
9. creates or updates the resolved GitHub Issue
10. writes GitHub issue identifiers and URLs back to the delivery record

Submission creates proposals and delivery artifacts only. It never promotes them.

### 3.7 Finish summary integration

The run finish summary records proposal outcomes in both the typed result and `reports/run_summary.json`.

At minimum it records:

1. whether proposal generation was requested
2. generated candidate count
3. submitted proposal count
4. delivered proposal count
5. redacted validation errors
6. GitHub delivery failures
7. GitHub Issue links for delivered proposals
8. dedup updates where new candidates were attached to existing issues

---

## 4. Proposal Policy and Routing

Proposal routing follows global defaults plus optional per-Workflow proposal policy. The delivery provider is always GitHub.

`proposalPolicy` controls whether proposals are generated, which proposal target classes are enabled, and bounded rendering options for GitHub Issues. It does not independently redefine agent skill precedence. Skill selection is preserved from the candidate payload or inherited from canonical system semantics, not recomputed by the proposal policy itself.

### 4.1 Global controls

The canonical global controls are:

1. workflow-level proposal enable switch
2. default proposal targets: `workflow_repo`, `moonmind`, or `both`
3. default per-target proposal caps
4. default MoonMind severity floor
5. configured MoonMind repository, normally `MoonLadderStudios/MoonMind`
6. allowed workflow repositories for proposal delivery
7. GitHub installation or token binding
8. GitHub delivery labels and issue body template controls
9. GitHub webhook verification settings
10. delivery retry controls

Operator-facing configuration remains the source of truth for these defaults.

### 4.2 Per-Workflow policy contract

`task.proposalPolicy` controls:

1. `targets`
2. `maxItems.workflowRepo`
3. `maxItems.moonmind`
4. `minSeverityForMoonMind`
5. `defaultRuntime`
6. `delivery.github.labels`
7. `delivery.github.issueTemplate`

`targets` may be `workflow_repo`, `moonmind`, or `both`. A Workflow may narrow proposal generation to the workflow repository, to MoonMind run-quality items, or to both target classes.

`defaultRuntime` supplies the default `task.runtime.mode` for generated proposals when the candidate does not already specify one. It does not block operators from overriding runtime during promotion.

`delivery.github` supplies bounded issue-rendering overrides. It does not grant arbitrary repository writes, arbitrary HTTP access, or credentials to agent runtimes. The destination repository is derived from the proposal target class and cannot be replaced by the Workflow payload.

### 4.3 Policy resolution

Policy resolution happens at proposal submission time.

The resolved policy must:

1. merge global defaults with `task.proposalPolicy`
2. preserve explicit candidate values over defaults
3. enforce per-target capacity limits
4. enforce MoonMind severity and tag gates
5. stamp `defaultRuntime` only when the candidate omitted a runtime
6. classify the proposal as `workflow_repo` or `moonmind`
7. resolve the GitHub destination repository
8. verify that the destination repository is allowed by operator policy
9. persist the resolved target and delivery decision for auditability

### 4.4 Workflow-repo proposals

Workflow-repo proposals are used for follow-up feature work, cleanup, tests, documentation, or project-local quality work discovered during a run.

For a workflow-repo proposal:

1. `workflowCreateRequest.payload.repository` remains the workflow repository.
2. The GitHub Issue is delivered to that repository.
3. The promoted execution operates on that repository.
4. Deduplication uses that repository as the destination repository.

Example: a run against `MoonLadderStudios/Tactics` discovers a Tactics follow-up. MoonMind creates or updates an issue in `MoonLadderStudios/Tactics`.

### 4.5 MoonMind proposals

MoonMind proposals are reserved for MoonMind run-quality improvements and MoonMind-system follow-up work.

For a MoonMind proposal:

1. `workflowCreateRequest.payload.repository` is set to the configured MoonMind repository.
2. The GitHub Issue is delivered to the configured MoonMind repository.
3. Category is normalized to `run_quality` for run-quality signals.
4. Signal severity must meet the configured floor.
5. Tags must match approved run-quality signal tags when the proposal is generated from run-quality telemetry.
6. The promoted execution operates on the configured MoonMind repository.

Example: a run against `MoonLadderStudios/Tactics` exposes a MoonMind runtime quality problem. MoonMind creates or updates an issue in `MoonLadderStudios/MoonMind`.

### 4.6 Fail-closed routing

MoonMind must not guess a destination repository.

Proposal submission fails safely when:

1. the proposal target class cannot be determined
2. a workflow-repo proposal has no valid `workflowCreateRequest.payload.repository`
3. the resolved repository is not in the allowed GitHub repository set
4. a MoonMind proposal is missing required run-quality evidence
5. GitHub delivery credentials do not cover the resolved repository

A fail-closed proposal is recorded as a delivery failure with a sanitized reason and recoverable next action. It is included in the parent run finish summary.

---

## 5. Proposal Delivery Records

Each submitted proposal creates or updates a MoonMind proposal delivery record.

The delivery record stores:

1. MoonMind proposal delivery ID
2. provider: `github`
3. target class: `workflow_repo` or `moonmind`
4. GitHub repository
5. GitHub issue number
6. GitHub issue URL
7. workflow repository
8. dedup key
9. dedup hash
10. status
11. title
12. summary
13. category
14. tags
15. review priority
16. `workflowCreateRequest` snapshot or artifact reference
17. origin source
18. origin ID
19. origin metadata
20. proposed-by identity
21. delivered-at timestamp
22. last-synced-at timestamp
23. promoted execution ID, when promoted
24. decision actor and note, when decided
25. sanitized delivery error, when delivery fails

The delivery record is an audit, idempotency, and execution-safety record. It is not a human review page.

---

## 6. Deduplication

Before creating a GitHub Issue, MoonMind computes the proposal dedup key and hash.

The dedup identity is derived from:

1. target class
2. GitHub destination repository
3. normalized proposal category
4. normalized proposal title

MoonMind then searches:

1. local open delivery records for the same destination and dedup hash
2. GitHub Issues with the hidden MoonMind marker or dedup label

If an open matching issue exists, MoonMind updates that issue instead of creating a new one. Updates may include:

1. a comment describing the new origin signal
2. elevated priority labels
3. linked origin metadata
4. a backlink to the triggering run
5. refreshed similar-proposal context

If no matching issue exists, MoonMind creates a new GitHub Issue.

Deduplication must not merge a MoonMind run-quality proposal with a workflow-repo proposal unless they share the same target class, destination repository, category, and normalized title.

---

## 7. GitHub Issue Contract

GitHub Issues are the only human review surface for proposals.

A proposal GitHub Issue has:

1. a title prefixed with `[MoonMind proposal]`
2. labels identifying the item as a MoonMind proposal
3. labels for state, target class, category, priority, and dedup short hash
4. a body rendered from the stored proposal snapshot
5. a hidden MoonMind marker containing delivery ID, target class, snapshot reference, and dedup hash
6. links to the source run and relevant artifacts
7. reviewer action instructions
8. a stored snapshot notice

Canonical GitHub labels include:

1. `moonmind:proposal`
2. `moonmind:state:open`
3. `moonmind:state:promoted`
4. `moonmind:state:dismissed`
5. `moonmind:state:deferred`
6. `moonmind:target:workflow-repo`
7. `moonmind:target:moonmind`
8. `moonmind:category:<category>`
9. `moonmind:priority:<low|normal|high|urgent>`
10. `moonmind:dedup:<short_hash>`

Canonical GitHub reviewer commands are:

```text
/moonmind promote
/moonmind promote runtime=<runtime> priority=<integer> maxAttempts=<integer>
/moonmind dismiss reason="<reason>"
/moonmind defer until=<date> reason="<reason>"
/moonmind priority <low|normal|high|urgent>
/moonmind request-revision reason="<reason>"
```

The rendered body must include this stored snapshot notice:

```text
MoonMind executes the stored proposal snapshot. Edited issue text, comments, labels, or fields are review artifacts and are never used as replacement executable task payloads.
```

---

## 8. GitHub Reviewer Decisions

GitHub reviewer decisions are accepted only from verified GitHub webhook events or trusted sync observations.

Decision handling must verify:

1. webhook authenticity
2. repository allowlist membership
3. issue marker ownership
4. delivery record identity
5. GitHub actor authorization
6. provider event idempotency
7. command syntax
8. bounded runtime and priority controls

Supported decisions are:

1. `promote`
2. `dismiss`
3. `defer`
4. `reprioritize`
5. `request_revision`

Unsupported commands are ignored and recorded with a sanitized reason.

Decision events must be idempotent by GitHub event ID. Replayed events must not create duplicate executions, duplicate comments, or conflicting delivery states.

### 8.1 Promotion

Promotion uses:

1. the stored `workflowCreateRequest`
2. the stored proposal origin metadata
3. bounded reviewer controls from the accepted GitHub command
4. current execution contract validation

Promotion may apply only these reviewer-controlled overrides:

1. runtime mode
2. execution priority
3. max attempts

Promotion must not accept replacement instructions, replacement steps, replacement repository, arbitrary environment variables, arbitrary credentials, or arbitrary tool configuration from GitHub issue text.

When promotion succeeds, MoonMind must:

1. create a new `MoonMind.UserWorkflow`
2. record the promoted execution ID on the proposal delivery record
3. update GitHub state labels
4. comment on the GitHub Issue with the new execution link
5. preserve the original issue as the human audit trail

### 8.2 Dismissal, deferral, reprioritization, and revision requests

When a reviewer dismisses, defers, reprioritizes, or requests revision, MoonMind must:

1. validate the command
2. record the decision actor and note
3. update the delivery record
4. update GitHub state and priority labels
5. comment with the accepted decision when useful for auditability

These decisions do not mutate the stored executable snapshot.

---

## 9. Dashboard Surfaces

The dashboard is not the proposal review surface.

The dashboard surfaces proposal outcomes only as links and status context:

1. workflow detail pages show generated proposal GitHub Issue links
2. finish summaries show GitHub Issue links and delivery status
3. run artifacts include proposal counts, GitHub links, and sanitized delivery errors
4. administrative diagnostics may expose delivery-record health without enabling browser-side proposal review actions

The dashboard must not provide:

1. a proposal queue page
2. proposal promote buttons
3. proposal dismiss buttons
4. editable proposal payload forms
5. proposal detail pages as a review workflow

All human triage and review decisions happen in GitHub Issues.

---

## 10. Security Requirements

Proposal delivery and promotion cross a trust boundary between generated work, GitHub review artifacts, and executable Workflow creation.

Required controls:

1. Generated proposal text is untrusted.
2. GitHub issue text is untrusted.
3. GitHub comments are untrusted until parsed into bounded commands.
4. Credentials remain in trusted MoonMind services.
5. Managed agent runtimes never receive GitHub delivery credentials or webhook secrets.
6. Secret-like provider metadata is rejected or redacted before persistence and rendering.
7. Large snapshots are stored as artifacts and referenced by stable IDs.
8. Outbound issue text is redacted and scanned before delivery.
9. Promotion validates the final stored payload against the canonical execution contract.
10. Repository routing is allowlist-enforced.
11. MoonMind proposal routing requires approved run-quality evidence.
12. Audit logs include sanitized failure reasons and recovery actions.

---

## 11. Observability and Recovery

Proposal delivery emits structured telemetry for:

1. generation requested
2. candidates generated
3. candidates rejected
4. proposals submitted
5. GitHub issues created
6. GitHub issues updated through deduplication
7. delivery failures
8. reviewer decisions observed
9. decisions accepted
10. decisions rejected
11. promotions started
12. promotions completed
13. promotions failed

Recovery tools must support:

1. replaying GitHub delivery for failed records
2. resyncing delivery records from GitHub issue markers
3. reprocessing verified GitHub reviewer events idempotently
4. reporting proposals that cannot be routed
5. linking orphaned delivery records to existing GitHub Issues when the marker and dedup hash match

Recovery actions must not bypass stored-snapshot promotion rules.

---

## 12. Testing Requirements

Required coverage includes:

1. workflow-repo proposal routing to the workflow repository
2. MoonMind proposal routing to `MoonLadderStudios/MoonMind`
3. Tactics workflow proposal delivery to `MoonLadderStudios/Tactics`
4. MoonMind run-quality proposal delivery from a Tactics-triggered run to `MoonLadderStudios/MoonMind`
5. fail-closed behavior for missing repositories
6. fail-closed behavior for disallowed repositories
7. dedup update instead of duplicate GitHub Issue creation
8. stored-snapshot rendering without exposing raw executable payloads
9. GitHub command parsing for each supported reviewer decision
10. webhook authenticity and actor authorization checks
11. idempotent GitHub event replay
12. promotion from stored snapshot with bounded runtime, priority, and max-attempt overrides
13. rejection of replacement executable payloads from GitHub issue text or comments
14. workflow finish summaries containing GitHub Issue links
15. dashboard routes not exposing proposal review controls

The acceptance standard is: generated proposals are discoverable and reviewable in the correct GitHub repository, but executable follow-up work starts only through verified GitHub approval of the stored MoonMind proposal snapshot.
