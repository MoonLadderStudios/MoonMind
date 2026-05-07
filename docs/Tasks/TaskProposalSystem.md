# Task Proposal System

Status: Active desired state
Owners: MoonMind Engineering
Last Updated: 2026-05-06
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskFinishSummarySystem.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/MissionControlArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`, `docs/Tasks/AgentSkillSystem.md`, `docs/Tasks/SkillAndPlanContracts.md`

---

## 1. Summary

MoonMind proposals are reviewable follow-up work items discovered during a `MoonMind.Run`. They preserve useful next steps without automatically starting new executions.

The desired proposal architecture is external-tracker-native:

1. MoonMind generates candidate follow-up work during the `proposals` stage of a `MoonMind.Run`.
2. MoonMind validates, normalizes, deduplicates, and stores each proposal as a control-plane delivery record.
3. MoonMind delivers each proposal to the configured human-review tracker as either a GitHub Issue or Jira issue.
4. Reviewers triage proposals in GitHub or Jira, not on a dedicated MoonMind task proposal page.
5. Promotion creates a new `MoonMind.Run` only after a verified human approval action in the configured tracker.
6. MoonMind comments or transitions the external issue with execution, pull request, error, and final status links.

Each proposal has two representations:

1. **Executable source of truth:** MoonMind's stored, validated `taskCreateRequest` snapshot or artifact reference.
2. **Human review artifact:** the GitHub Issue or Jira issue rendered from that snapshot.

The external issue is the review surface, not the executable contract. MoonMind never executes arbitrary edited issue Markdown, Jira ADF, labels, or comments as a task payload. Promotion always uses MoonMind's stored proposal snapshot plus bounded, validated reviewer controls.

The proposal system is Temporal-native:

1. `MoonMind.Run` executes work.
2. A dedicated `proposals` stage runs after execution and before finalization.
3. Generator activities create candidate follow-up tasks from run artifacts and normalized agent results.
4. Submission activities validate candidates, resolve routing policy, persist delivery records, and create or update external issues.
5. Webhook or sync activities observe approved/rejected/deferred tracker actions.
6. Promotion creates a new Temporal execution only after human approval.

Canonical agent-skill storage, precedence, and snapshot semantics live in `docs/Tasks/AgentSkillSystem.md`. This document defines how proposals preserve and promote task-facing skill intent.

---

## 2. Core Invariants

These rules are fixed:

1. `taskCreateRequest` is the canonical promote-to-task payload.
2. Proposal creation is not execution; promotion is the only action that starts new work.
3. Promotion creates a new `MoonMind.Run` execution, not a legacy queue job.
4. `taskCreateRequest.payload.repository` is the canonical repository target for deduplication and future execution.
5. Human review is required before any proposal becomes durable running work.
6. Human review occurs in GitHub Issues or Jira, according to resolved proposal delivery policy.
7. MoonMind does not provide a dedicated task proposal review page as a primary workflow surface.
8. The external issue body is a rendered review artifact, not an executable payload.
9. Proposal generation is best-effort and must never compromise the correctness of the parent run result.
10. Proposal payloads must conform to the canonical Temporal submit contract used by `/api/executions`.
11. Proposal origin metadata must identify the durable workflow that produced the proposal.
12. When a proposal depends on agent skill context, that context must be preserved explicitly in the stored `taskCreateRequest` or through documented inheritance semantics.
13. Proposal promotion must not silently drift to unrelated skill defaults when the original proposal logic depended on explicit skill selection.
14. Preset-derived metadata in proposals is advisory review and reconstruction metadata, not a runtime dependency.
15. Proposal promotion validates and submits the reviewed flat task payload; it does not depend on live preset catalog lookup or live preset re-expansion for correctness.
16. Deduplication is repository-aware and deterministic.
17. Delivery to GitHub or Jira is idempotent. A repeated proposal with the same dedup target updates or links to the existing external issue instead of creating reviewer-facing duplicates.
18. Tracker credentials and webhook secrets remain inside trusted MoonMind integration boundaries and are never injected into managed agent runtimes.

---

## 3. `MoonMind.Run` Lifecycle Integration

### 3.1 Submit-time contract

Task-shaped submit requests may include:

1. `task.proposeTasks`
2. `task.proposalPolicy`
3. `task.skills`
4. `step.skills`

These values are part of the durable run contract and must be preserved in `initialParameters` for the run. These fields directly impact downstream proposal generation, delivery, and promotion.

The canonical direction is:

1. Preserve the raw `task.proposalPolicy` object in `initialParameters`.
2. Resolve global defaults plus per-task overrides inside the proposal stage or inside `proposal.submit`.
3. Do not rely on a parallel flattened proposal-policy contract for new work.
4. Persist enough resolved policy metadata on proposal delivery records to explain why a proposal was delivered to GitHub or Jira.

### 3.1.1 Canonical submission-path normalization

All new task submission paths must normalize proposal intent into the same canonical nested task payload accepted by `/api/executions` before `MoonMind.Run` starts.

Codex managed sessions do not define a parallel proposal contract. They are the highest-risk producer because session containers may create additional MoonMind tasks through the internal API path, but the rule also applies to ordinary API submission, proposal promotion, schedules, and any future task-creation surface.

Required mapping:

1. session-level or adapter-level proposal opt-in becomes `initialParameters.task.proposeTasks`
2. session-level or adapter-level proposal routing overrides become `initialParameters.task.proposalPolicy`
3. task/runtime/repository metadata continues to flow through the canonical task payload instead of a Codex-only side channel

Non-canonical locations such as root-level `initialParameters.proposeTasks`, turn metadata, session binding metadata, container environment, or adapter-local state are not the durable write contract for new work.

Workflow code may read root-level `initialParameters.proposeTasks` or older policy shapes only for replay and in-flight compatibility. New submission paths must write the nested `task.*` fields so future behavior does not depend on compatibility fallbacks.

For Codex, this applies both when a user submits a task targeting a managed Codex runtime and when a Codex managed session creates additional MoonMind tasks through the internal API path.

### 3.2 Run states

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
3. Mission Control status mapping
4. finish summaries
5. external issue comments or status updates
6. related documentation

### 3.3 When the `proposals` stage runs

The `proposals` stage runs only when both conditions are true:

1. global proposal generation is enabled by workflow settings
2. the submitted task enables proposals through `task.proposeTasks`

This gives operators a real global off switch while still allowing task-level opt-in when the feature is enabled system-wide.

For Codex managed-session originated runs, the durable gate is the canonical nested value preserved in `initialParameters.task.proposeTasks`; session-local flags alone must not determine whether the workflow enters `proposals`.

### 3.4 Proposal generation

Proposal generation happens in Temporal activities, never in workflow code.

Generators analyze:

1. run artifacts
2. plan-step outcomes
3. normalized `AgentRunResult` data from managed and external agents
4. finish-summary signals and execution diagnostics
5. resolved skill snapshot metadata and skill-related execution context, using artifact-backed skill metadata where needed rather than treating the runtime workspace as the sole source of truth
6. authored preset metadata and per-step source provenance when the parent run exposes reliable preset provenance

Generators must:

1. treat run inputs and logs as untrusted
2. use artifact-backed references for large context
3. avoid side effects such as commits, pushes, issue creation, or task creation
4. redact or exclude secrets and unsafe command output
5. preserve skill selectors only when they materially affect correctness or expected behavior of the follow-up work
6. preserve reliable preset provenance only when the candidate work is genuinely derived from authored preset metadata or reliable step source metadata
7. avoid fabricating `task.authoredPresets`, binding IDs, include paths, or preset-derived labels for work that lacks reliable binding evidence

### 3.5 Proposal submission and delivery

Proposal submission is a separate side-effecting activity that:

1. validates candidate entries, including any `task.skills` or `step.skills` selectors
2. resolves proposal policy and tracker delivery policy
3. normalizes origin metadata
4. enforces repository and run-quality routing rules
5. preserves explicit skill-selection intent when present
6. rejects or normalizes malformed skill-selection fields before storage
7. computes deduplication fields
8. creates or updates a MoonMind proposal delivery record
9. creates or updates the configured GitHub Issue or Jira issue
10. writes external issue identifiers and URLs back to the delivery record

Submission creates proposals and delivery artifacts only. It never promotes them.

### 3.6 Finish summary integration

The run finish summary records proposal outcomes in both the typed result and `reports/run_summary.json`.

At minimum it records:

1. whether proposal generation was requested
2. generated candidate count
3. submitted proposal count
4. delivered proposal count
5. provider-specific delivery failures
6. redacted validation errors
7. external GitHub Issue or Jira issue links for delivered proposals
8. dedup updates where new candidates were attached to existing issues

---

## 4. Proposal Policy

Proposal routing follows global defaults plus optional per-task overrides.

`proposalPolicy` controls whether proposals are generated, which repositories receive proposed work, and which external tracker receives reviewer-facing issues. It does not independently redefine agent skill precedence. Skill selection is preserved from the candidate payload or inherited from canonical system semantics, not recomputed by the proposal policy itself.

### 4.1 Global controls

The canonical global controls are:

1. workflow-level proposal enable switch
2. default proposal targets
3. default per-target proposal caps
4. default MoonMind severity floor
5. MoonMind repository target
6. default delivery provider: `github`, `jira`, or `auto`
7. repository-to-tracker bindings
8. GitHub delivery settings, including installation, repository allowlist, labels, and issue template controls
9. Jira delivery settings, including site binding, project allowlist, issue type, workflow transitions, labels, custom fields, and retry controls

Operator-facing configuration remains the source of truth for these defaults.

### 4.2 Per-task policy contract

`task.proposalPolicy` controls:

1. `targets`
2. `maxItems.project`
3. `maxItems.moonmind`
4. `minSeverityForMoonMind`
5. `defaultRuntime`
6. `delivery.provider`
7. `delivery.github.repository`
8. `delivery.github.labels`
9. `delivery.jira.projectKey`
10. `delivery.jira.issueType`
11. `delivery.jira.labels`
12. `delivery.jira.components`

`defaultRuntime` supplies the default `task.runtime.mode` for generated proposals when the candidate does not already specify one. It does not block operators from overriding runtime during promotion.

`delivery` supplies bounded tracker routing overrides. It does not grant arbitrary HTTP access, arbitrary repository writes, arbitrary Jira project writes, or credentials to agent runtimes.

### 4.3 Policy resolution

Policy resolution happens at proposal submission time.

The resolved policy must:

1. merge global defaults with `task.proposalPolicy`
2. preserve explicit candidate values over defaults
3. enforce per-target capacity limits
4. enforce MoonMind severity and tag gates
5. stamp `defaultRuntime` only when the candidate omitted a runtime
6. resolve the proposal delivery provider and destination
7. verify that the destination is allowed by operator policy
8. persist the resolved target and delivery decision for auditability

Delivery provider resolution follows this order:

1. explicit `task.proposalPolicy.delivery.provider`
2. repository-specific tracker binding
3. global default delivery provider
4. `auto`, which selects the repository's configured tracker and otherwise defaults to GitHub Issues for GitHub repositories

### 4.4 Project-targeted proposals

Project-targeted proposals keep the triggering repository as `taskCreateRequest.payload.repository`.

They are used for follow-up feature work, cleanup, tests, or project-local quality work discovered during the run.

Their external issue is delivered to the tracker associated with the project repository unless the task policy explicitly selects another allowed destination.

### 4.5 MoonMind-targeted proposals

MoonMind-targeted proposals are reserved for MoonMind run-quality improvements.

When routed to MoonMind:

1. the repository is rewritten to the configured MoonMind repository
2. category is normalized to `run_quality`
3. signal severity must meet the configured floor
4. tags must match approved run-quality signal tags
5. the external issue is delivered to MoonMind's configured proposal tracker

This keeps generic project follow-ups out of MoonMind's internal run-quality backlog.

---

## 5. External Proposal Delivery

### 5.1 Delivery model

Each submitted proposal creates or updates a MoonMind proposal delivery record.

The delivery record stores:

1. MoonMind proposal delivery ID
2. provider: `github` or `jira`
3. external issue key or number
4. external issue URL
5. repository
6. dedup key
7. dedup hash
8. status
9. title
10. summary
11. category
12. tags
13. review priority
14. `taskCreateRequest` snapshot or artifact reference
15. origin source
16. origin ID
17. origin metadata
18. proposed-by identity
19. delivered-at timestamp
20. last-synced-at timestamp
21. promoted execution ID, when promoted
22. decision actor and note, when decided

The delivery record is an audit and idempotency record. It is not a separate human-review page.

### 5.2 Dedup-first delivery

Before creating an external issue, MoonMind computes the proposal dedup key and hash.

The dedup key is derived from:

1. canonical repository target
2. normalized proposal title

MoonMind then searches:

1. local open delivery records for the same provider, destination, and dedup hash
2. provider-specific issue metadata such as labels, hidden markers, custom fields, or issue properties

If an open matching issue exists, MoonMind updates that issue instead of creating a new one. Updates may include:

1. a comment describing the new origin signal
2. an elevated priority label or field
3. linked origin metadata
4. a backlink to the triggering run
5. refreshed similar-proposal context

If no matching issue exists, MoonMind creates a new external issue.

### 5.3 GitHub Issues delivery

GitHub Issues are the desired review surface for repositories configured with GitHub delivery.

A GitHub proposal issue has:

1. a title prefixed with `[MoonMind proposal]`
2. labels identifying the item as a MoonMind proposal
3. labels for category, priority, status, and dedup short hash
4. a body rendered from the stored proposal snapshot
5. a hidden MoonMind marker containing delivery ID, snapshot reference, and dedup hash
6. links to the source run and relevant artifacts
7. reviewer action instructions

Canonical GitHub labels include:

1. `moonmind:proposal`
2. `moonmind:open`
3. `moonmind:promoted`
4. `moonmind:dismissed`
5. `moonmind:deferred`
6. `moonmind:category:<category>`
7. `moonmind:priority:<low|normal|high|urgent>`
8. `moonmind:dedup:<short_hash>`

Canonical GitHub reviewer commands are:

```text
/moonmind promote
/moonmind promote runtime=<runtime> priority=<integer> maxAttempts=<integer>
/moonmind dismiss reason="<reason>"
/moonmind defer until=<date> reason="<reason>"
/moonmind priority <low|normal|high|urgent>
```

GitHub delivery may also support label-driven actions when configured by repository policy, but comment commands are the canonical explicit audit path.

### 5.4 Jira delivery

Jira issues are the desired review surface for projects configured with Jira delivery.

A Jira proposal issue has:

1. summary prefixed with `[MoonMind proposal]`
2. ADF-rendered description derived from the stored proposal snapshot
3. labels identifying category, priority, status, and dedup short hash
4. configured issue type, normally `Task`, `Story`, or `MoonMind Proposal`
5. configured custom fields for MoonMind delivery ID, dedup hash, repository, origin run, runtime, max attempts, and snapshot reference
6. links to the source run and relevant artifacts
7. issue links to related or duplicate proposals

Canonical Jira workflow states are:

1. `Proposed`
2. `Approved`
3. `Promoted`
4. `Rejected`
5. `Deferred`

Jira promotion may be triggered by:

1. transition to `Approved`
2. configured approval field update
3. `/moonmind promote` comment command

Jira dismissal and deferral may be triggered by workflow transitions or comment commands. Transitions are preferred when the Jira project uses workflow permissions for reviewer control.

### 5.5 External issue rendering

External issue text should include:

1. proposal title and summary
2. repository target
3. runtime preview
4. publish mode preview
5. priority and max attempts
6. category and tags
7. source run links
8. origin metadata summary
9. dedup hash
10. similar or duplicate issue links
11. reviewer action instructions
12. explicit notice that MoonMind executes the stored proposal snapshot, not edited issue text

Large payloads, logs, artifacts, and diagnostics are linked by reference rather than embedded directly.

---

## 6. Canonical Proposal Payload Contract

### 6.1 Canonical direction

Stored proposals must use the same task-shaped contract accepted by Temporal submission through `/api/executions`.

That means:

1. `taskCreateRequest` stores a normal task payload
2. `task.runtime.mode` selects the runtime
3. `task.tool` and `step.tool`, when present, use the canonical Temporal submit shape
4. `task.tool.type` must be `skill` when a tool selector is provided, representing an executable tool rather than an agent instruction bundle
5. proposal payloads do not use `tool.type = "agent_runtime"`
6. `task.skills` and `step.skills` may be included and must follow the canonical Agent Skill System contract defined in `docs/Tasks/AgentSkillSystem.md`
7. delivery metadata is stored alongside the proposal delivery record, not inside the executable task payload unless it is part of the user's explicit task contract

### 6.2 Candidate example

```json
[
  {
    "title": "Add regression coverage for retry loop detection",
    "summary": "The run retried the same recoverable failure pattern without a targeted regression test.",
    "category": "run_quality",
    "tags": ["retry", "loop_detected"],
    "signal": {
      "severity": "high"
    },
    "taskCreateRequest": {
      "type": "task",
      "priority": 0,
      "maxAttempts": 3,
      "payload": {
        "repository": "MoonLadderStudios/MoonMind",
        "task": {
          "instructions": "Add a regression test covering retry loop detection in the Temporal runtime.",
          "tool": {
            "type": "skill",
            "name": "auto",
            "version": "1.0"
          },
          "skills": {
            "sets": ["deployment-default", "temporal-runtime-quality"],
            "include": [
              { "name": "moonmind-doc-writer", "version": "2.3.0" }
            ]
          },
          "authoredPresets": [
            {
              "presetId": "runtime-quality-followup",
              "version": "2026-04-17",
              "includePath": ["root", "regression-coverage"]
            }
          ],
          "runtime": {
            "mode": "codex"
          },
          "publish": {
            "mode": "pr"
          },
          "steps": [
            {
              "title": "Add regression coverage",
              "instructions": "Add a regression test covering retry loop detection in the Temporal runtime.",
              "source": {
                "kind": "preset-derived",
                "presetId": "runtime-quality-followup",
                "includePath": ["root", "regression-coverage"],
                "originalStepId": "add-regression-test"
              }
            }
          ]
        }
      }
    }
  }
]
```

### 6.3 Payload rules

1. `taskCreateRequest` must already validate against the canonical task contract.
2. `taskCreateRequest.payload.repository` determines deduplication and future execution target.
3. `taskCreateRequest.payload.task.runtime.mode` uses current supported task runtimes: `codex`, `gemini_cli`, `claude`, and optionally `jules`.
4. Candidate payloads must not include secrets, raw credentials, or unsafe log dumps.
5. Promotion-time overrides must also validate against the same canonical task contract before execution starts.
6. If `task.skills` or `step.skills` are present, they must validate against the canonical agent-skill contract.
7. Proposals must not embed full agent skill bodies inline when refs or selectors are the correct contract.
8. Proposals preserve execution intent, not raw runtime materialization state.
9. Proposal payloads must not store mutable `.agents/skills` directory state, runtime-local materialization outputs, or ephemeral prompt bundles produced only for one adapter session.
10. Proposal-capable work originating from a Codex managed session must already carry `task.proposeTasks` and any `task.proposalPolicy` in the canonical task payload.
11. Proposal generation must not reconstruct proposal intent from session-local metadata.
12. Proposal payloads may include optional `task.authoredPresets` and `steps[].source` provenance when the metadata is reliable.
13. Preset provenance fields are reconstruction and review metadata only; they do not change the executable meaning of the flat task payload.
14. Proposal payloads must not contain unresolved preset include objects as runtime work.
15. Preset-derived proposals must be execution-ready flat payloads before storage and promotion.

---

## 7. Origin, Identity, and Naming

### 7.1 Origin source

Temporal-backed proposals use:

1. `origin.source = "workflow"`
2. `origin.id = workflow_id`

`temporal` is not a canonical proposal origin value.

### 7.2 Origin metadata

Origin metadata uses snake_case keys consistently across workflow payloads, stored proposal delivery records, API responses, issue rendering, and docs.

Canonical keys are:

1. `workflow_id`
2. `temporal_run_id`
3. `trigger_repo`
4. `starting_branch`
5. `working_branch`
6. `trigger_job_id`
7. `trigger_step_id`
8. `signal`

### 7.3 Identity rules

1. `workflow_id` is the durable source task identity for workflow-originated proposals.
2. `temporal_run_id` is diagnostic metadata, not the durable task handle.
3. Task-oriented Temporal surfaces continue to use `taskId == workflowId`.
4. Continue-as-new does not change the proposal origin identity; the durable workflow remains the source.
5. External issue keys are delivery identities, not execution identities.
6. The MoonMind proposal delivery ID binds the stored proposal snapshot to the external issue.

---

## 8. Review, Promotion, and Execution

Proposal creation does not start work. Promotion does.

### 8.1 Review surface

The desired human review surface is the configured external tracker.

Reviewers use:

1. GitHub Issues for GitHub-delivered proposals
2. Jira issues for Jira-delivered proposals

MoonMind UI surfaces may link to proposals and show delivery status, but they are not the primary proposal queue and do not provide a dedicated proposal review page.

### 8.2 Reviewer actions

The canonical proposal decisions are:

1. promote
2. dismiss
3. defer
4. reprioritize
5. request revision

Actions must be attributable to a verified human actor. The integration layer maps provider-native events to MoonMind decisions.

GitHub actions are expressed through comment commands or configured labels.

Jira actions are expressed through workflow transitions, configured field updates, or comment commands.

### 8.3 Promotion flow

Promotion must follow this algorithm:

1. receive a verified GitHub or Jira approval event
2. verify that the external actor has permission to approve proposals for the destination
3. load the MoonMind proposal delivery record
4. load the stored proposal snapshot
5. verify the proposal is still open or approved-but-not-promoted
6. parse bounded promotion controls such as `priority`, `maxAttempts`, `note`, and `runtimeMode`
7. apply validated bounded controls to the execution request without replacing the stored task payload
8. validate the stored payload against the canonical task contract, including verification of any agent-skill selectors and executable step types
9. preserve explicit skill-selection intent from the stored proposal
10. preserve `task.authoredPresets` and per-step `source` provenance from the stored proposal
11. submit the reviewed task through the same Temporal-backed create path used by `/api/executions`
12. create a new `MoonMind.Run` through `TemporalExecutionService.create_execution()`
13. store the promoted workflow or execution identifier on the proposal delivery record
14. update the external issue status to promoted
15. comment on or transition the external issue with execution metadata

Promotion does not accept a full task payload replacement from the external issue body. If a future proposal refresh, issue-edit ingestion, or preset re-expansion flow exists, it must be an explicit validate-and-confirm action that creates a new stored proposal revision before promotion.

Promotion is a control-plane-to-Temporal bridge, not a proposal-local mutation only.

### 8.4 Dismissal and deferral

Dismissal marks the proposal delivery as rejected or dismissed without starting work.

Deferral marks the proposal delivery as deferred until a requested timestamp or tracker state changes.

For both actions, MoonMind records:

1. external actor
2. provider event identity
3. reason or note
4. timestamp
5. external issue state

MoonMind updates the external issue with the final decision when the provider action itself does not already represent the final state.

### 8.5 Skill preservation and inheritance

Proposal promotion preserves agent skill intent from the original execution.

1. When the original task relied only on deployment/default inheritance, proposal payloads may omit redundant explicit skill selectors.
2. When the original task included explicit non-default skill selection that materially affects execution behavior, proposals should preserve that selection explicitly.
3. Operators may override runtime at promotion time.
4. Agent skill selectors are preserved by default.
5. Changing the runtime does not automatically erase or rewrite agent skill intent.
6. If a selected skill set is incompatible with the chosen runtime, promotion must fail validation or require an explicit override path.
7. Proposal promotion does not re-resolve skill-source precedence as an undocumented side effect.

### 8.6 Preset provenance preservation

Proposal promotion preserves reliable preset provenance from the stored proposal as review and reconstruction metadata.

Rules:

1. Default promotion uses the reviewed flat `taskCreateRequest` payload and does not perform live preset catalog lookup for correctness.
2. Default promotion does not re-expand live presets.
3. Catalog changes after the proposal was created must not silently change the promoted execution.
4. `task.authoredPresets` and `steps[].source` are preserved by default when present and valid in the stored proposal payload.
5. If an operator intentionally edits or removes provenance through a validated proposal revision, the revised stored payload is the source of truth.
6. A future refresh-latest workflow may exist, but it must be explicit operator-selected behavior that re-resolves presets before submission and presents the refreshed flat payload for validation.
7. Flattened-only proposals remain valid.
8. When reliable authored preset binding metadata is absent, promotion must not invent bindings or infer executable preset semantics from descriptive text.

### 8.7 Runtime selection

Proposal promotion supports operator runtime selection.

Rules:

1. the backend-served runtime list is the source of truth
2. supported task-facing runtime values are `codex`, `gemini_cli`, `claude`, and optionally `jules`
3. runtime overrides apply to the promoted execution request, not to the stored proposal snapshot
4. disabled runtimes must fail validation before a workflow is created

### 8.8 Promotion result

A successful promotion response or external issue update includes:

1. the updated proposal delivery record
2. the created execution identifier for the new `MoonMind.Run`
3. execution URL
4. expected output or PR publication behavior
5. any warnings about applied bounded overrides

---

## 9. API and Integration Contract

### 9.1 Proposal submission API

`POST /api/proposals` remains the canonical internal proposal submission API.

Its desired response includes delivery information:

```json
{
  "id": "proposal-delivery-id",
  "status": "delivered",
  "provider": "github",
  "externalKey": "123",
  "externalUrl": "https://github.com/org/repo/issues/123",
  "dedupHash": "...",
  "taskPreview": {}
}
```

Internal callers do not need to know whether the reviewer will use GitHub or Jira before submission unless they intentionally provide an allowed per-task delivery override.

### 9.2 Webhook endpoints

MoonMind exposes provider webhook endpoints for tracker-originated review actions:

1. `POST /api/integrations/github/issues/webhook`
2. `POST /api/integrations/jira/webhook`

Webhook handling must:

1. verify provider signatures or shared secrets
2. normalize provider events into proposal decision events
3. verify actor permissions
4. enforce idempotency by provider event ID
5. avoid logging secrets or raw credentials
6. load the stored proposal snapshot before promotion
7. update proposal delivery state and external issue state consistently

### 9.3 Admin and recovery APIs

MoonMind may expose admin-oriented APIs for delivery inspection and recovery:

1. `GET /api/proposal-deliveries/{id}`
2. `POST /api/proposal-deliveries/{id}/redeliver`
3. `POST /api/proposal-deliveries/{id}/sync`
4. `POST /api/proposal-deliveries/{id}/promote`

These APIs are not the normal reviewer workflow. They exist for operators, tests, and recovery.

### 9.4 Provider adapters

Proposal delivery uses provider adapters behind a common service boundary.

The provider adapter contract includes:

1. find existing open issue by dedup metadata
2. create issue
3. update issue
4. add comment
5. link related issues
6. read current issue state
7. map provider event to MoonMind decision
8. map MoonMind status to provider status, labels, transitions, or comments

Provider-specific credentials, retry logic, redaction, and policy enforcement stay inside trusted integration packages.

---

## 10. Observability and UI Contract

### 10.1 Status mapping

`proposals` is a first-class workflow state and must be visible across API and UI surfaces.

For dashboard compatibility mapping:

1. `scheduled -> queued`
2. `waiting_on_dependencies -> waiting`
3. `awaiting_slot -> queued`
4. `proposals -> running`
5. `completed -> completed`

### 10.2 Proposal delivery visibility

Mission Control and execution details surface proposal delivery status, not a dedicated proposal review queue.

The system must show:

1. `mm_state = proposals` while proposal generation or delivery is in progress
2. proposal counts and errors in finish summary data
3. external issue links from execution detail
4. provider, external key, delivery status, and last sync timestamp
5. whether a proposal was created as a new issue or attached to an existing dedup issue
6. compact task summary: runtime, repository, publish mode, priority, max attempts, skill context, and preset provenance
7. promotion result links after approval

### 10.3 No dedicated proposal page

MoonMind does not maintain a dedicated human-facing task proposal page for normal proposal triage.

The following surfaces are valid:

1. source run finish summary
2. execution detail delivery links
3. compact delivery status cards
4. admin/recovery views
5. GitHub Issues
6. Jira issues

The following surface is not part of the desired review workflow:

1. a standalone MoonMind proposal queue page with Promote and Dismiss buttons as the primary reviewer experience

### 10.4 Failure handling

Proposal generation, submission, and delivery are best-effort.

Rules:

1. a successful run may still report proposal-stage or delivery-stage errors
2. malformed candidates are skipped, not promoted
3. malformed or incompatible skill selectors in generated candidates are skipped with a visible validation error and not silently dropped in a way that changes execution meaning
4. submission retries must be bounded and idempotent
5. external tracker delivery uses an outbox or equivalent retry-safe mechanism
6. partial success must be visible through generated, submitted, delivered, updated, and failed counts
7. external provider errors must be redacted before storage, logs, issue comments, or API responses

---

## 11. Security and Integrity

External trackers are collaborative systems. MoonMind treats their content as untrusted unless it has been verified and parsed through an explicit command, transition, field, or label contract.

Rules:

1. MoonMind never executes edited issue body text as instructions.
2. MoonMind never extracts arbitrary Markdown or Jira ADF as a replacement `taskCreateRequest`.
3. Promotion uses the stored MoonMind snapshot plus bounded reviewer controls.
4. Bounded controls include runtime, priority, max attempts, note, and explicit approved fields defined by the provider adapter.
5. Larger revisions require a new validated proposal revision before promotion.
6. Webhooks must verify provider signatures or secrets.
7. Actor identity and permissions must be checked before decisions are accepted.
8. Provider event IDs must be used for idempotency.
9. Tracker credentials must be resolved just in time inside trusted integration code.
10. Tracker credentials must not be placed into managed agent environments.
11. Errors, comments, and logs must be redacted.
12. Delivery adapters must enforce repository, organization, Jira site, Jira project, and action allowlists.

---

## 12. Worker-Boundary Architecture

The proposal system follows the same Temporal rule as the rest of MoonMind: workflows orchestrate and activities perform side effects.

Queue placement is:

1. proposal generation on the LLM-capable activity fleet
2. proposal submission, storage, and external issue delivery on a control-plane or integrations-capable activity fleet
3. webhook decision handling and promotion on a trusted control-plane or integrations-capable activity fleet

Generation and submission are intentionally separate concerns. LLM-facing work and control-plane writes must not share an undifferentiated activity boundary.

Proposal generation may inspect resolved skill snapshot metadata or related execution artifacts. Proposal submission strictly validates skill-selection payload fields but avoids materializing runtime skill context.

---

## 13. Desired Data Model

The durable model is a proposal delivery record. Existing storage may keep the `task_proposals` name, but its desired role is delivery, audit, idempotency, and promotion linkage rather than a MoonMind-hosted review queue.

Canonical fields are:

1. `id`
2. `provider`
3. `external_key`
4. `external_url`
5. `repository`
6. `dedup_key`
7. `dedup_hash`
8. `status`
9. `title`
10. `summary`
11. `category`
12. `tags`
13. `review_priority`
14. `priority_override_reason`
15. `task_create_request` or `task_snapshot_ref`
16. `origin_source`
17. `origin_id`
18. `origin_metadata`
19. `proposed_by_worker_id`
20. `proposed_by_user_id`
21. `delivered_at`
22. `last_synced_at`
23. `promoted_at`
24. `promoted_execution_id`
25. `promoted_by_actor`
26. `decided_by_actor`
27. `decision_note`
28. `created_at`
29. `updated_at`

Canonical statuses are:

1. `pending_delivery`
2. `delivered`
3. `delivery_failed`
4. `open`
5. `approved`
6. `promoted`
7. `dismissed`
8. `deferred`
9. `superseded`

Provider-specific metadata may be stored in a separate structured object when it does not belong in the canonical field set.

---

## 14. Acceptance Criteria

The desired system satisfies these acceptance criteria:

1. A generated proposal creates or updates exactly one GitHub Issue or Jira issue per provider destination and dedup target.
2. Reviewers can promote, dismiss, defer, and reprioritize proposals without opening a MoonMind proposal review page.
3. Promotion executes only from MoonMind's stored validated task snapshot plus bounded reviewer controls.
4. GitHub or Jira issue links appear in task run details and finish summaries.
5. Duplicate proposals comment on or link to an existing external issue instead of creating duplicate reviewer-facing issues.
6. Security, tests, and run-quality proposals preserve category, tags, origin metadata, priority, and notification behavior.
7. External delivery failures are retried idempotently and visible in run summaries, delivery records, and operator diagnostics.
8. Webhook decision handling is idempotent, permission-checked, and redacted.
9. GitHub and Jira provider adapters enforce destination allowlists and do not expose arbitrary provider HTTP to agents.
10. Mission Control surfaces proposal delivery status and external links but does not act as the primary proposal queue.
