# Feature Specification: Agent Tool-Surface Isolation

**Feature Branch**: `335-agent-tool-surface-isolation`
**Created**: 2026-05-10
**Status**: Draft
**Input**: Trusted Jira preset brief for MM-680 from `/work/agent_jobs/mm:84c05579-71b9-4970-a650-3eb2341060d1/artifacts/moonspec/MM-680-orchestration-input.md`. Preserve `MM-680` and the original Jira preset brief for final verification.

Preserved source Jira preset brief: `MM-680` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response synthesized into `/work/agent_jobs/mm:84c05579-71b9-4970-a650-3eb2341060d1/artifacts/moonspec/MM-680-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-680` under `specs/`, so `Specify` was the first incomplete stage.
Runtime intent: Jira Orchestrate always runs as a runtime implementation workflow. Source design references in the brief are treated as runtime source requirements.

## Original Preset Brief

````text
# MM-680 MoonSpec Orchestration Input

## Source

- Jira issue: MM-680
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`; potentially related custom fields present in the response were empty or non-brief metadata.

## Canonical MoonSpec Feature Request

Jira issue: MM-680 from MM project
Summary: Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-680 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-680: Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows

Story: Generalizable Agent Tool-Surface Isolation for MoonMind-Mediated Workflows

  User Story

  As a MoonMind operator, I want agent runtimes (Claude Code, Codex, Gemini, future) to be structurally unable to reach external services or publish
  surfaces outside the MoonMind-contracted tool path, so that workflows produce deterministic, attributable outcomes regardless of which model or runtime
  ships next, and so that agent-side tool drift cannot silently break or duplicate MoonMind-owned operations (Jira writes, branch publish, PR creation).

  Problem Statement

  Workflow mm:fac9baaf-d35d-4966-8530-adeacbb46d66 (task: "Change Jira issue MM-574 to status In Progress before implementation starts.") failed with a
  non-retryable (fetch first) push error and repo.create_pr HTTP 422. Root cause was not the push race itself — it was that the agent runtime had two
  overlapping ways to reach Jira (MoonMind's mm.tool.execute and a Claude account-level Atlassian MCP wired to a different Jira site) and two overlapping
  ways to publish (MoonMind's repo.create_pr activity and in-workspace gh pr create). The agent picked the non-MoonMind path on both surfaces:

- It called the account-level Atlassian MCP, which had no visibility into the MM project, concluded MM-574 didn't exist, and never performed the Jira
  transition.
- It opened PR #2057 itself via gh, which then made MoonMind's own publish activity 422 because the PR already existed.

  This is not a Claude-specific bug. Any agent runtime with shell access, account-level connectors, or developer CLIs (gh, glab, jira, linear, etc.) on PATH
   can reproduce the same failure mode. Per-tool allow/denylists do not generalize: they are runtime-specific, brittle to renames and shell-outs, and cannot
   defend against a model that finds an alternate path (curl, python -c, an MCP added in a later release).

  Goal

  Establish four runtime-agnostic primitives so that the only viable path from an agent session to any external service is the MoonMind-contracted tool
  path. Make publish-step reconciliation idempotent so that any residual race or pre-existing remote state recovers cleanly instead of terminal-failing the
  workflow.

  Scope (In)

1. - Service-identity isolation for agent sessions. Agent runtimes launch under a MoonMind-managed service identity (API key / scoped credential), never the
     operator's Claude.ai/Codex/Gemini user OAuth. Account-level connector grants (Atlassian, Linear, Notion, GitHub, Jira, monday, etc.) are therefore not
     present in the session.
2. - Strict MCP/tool surface scoping at launch. A per-skill declarative tool/MCP contract is the source of truth for what the agent sees. The runtime
     launcher enforces it (--strict-mcp-config + --allowed-tools for Claude Code, equivalents for Codex/Gemini) so the agent cannot load any MCP or tool
     outside the contract.
3. - Egress mediation as the backstop. Agent containers reach external services only through a MoonMind-controlled HTTP egress proxy with an allowlist keyed
     to the skill contract. Direct curl, git push, gh, language-SDK, or future-MCP calls to non-allowlisted endpoints fail at the network layer regardless of
     tool naming.
4. - Publish ownership separation. Agent containers have no origin remote URL, no publish credentials, and no gh/glab/equivalents in the runtime image (or
     those binaries have nothing to authenticate as). All commit-attribution, push, and PR-creation happen in a MoonMind activity container with its own
     publish identity. The agent's deliverable ends at the working tree.
5. - Idempotent reconcile in publish-side activities. repo.create_pr queries for an existing PR matching head→base before opening; if found, adopts it
     (updates memo/refs) instead of 422-failing. repo.push_branch uses --force-with-lease against the last activity-recorded remote SHA, fetches and surfaces
     structured conflict errors instead of raising non-retryable application errors.
6. - Skill-contract enforcement. Skill manifests must declare exactly which MoonMind tools and MCPs they require; the launcher refuses to start a runtime
     that resolves any tool/MCP not on the manifest. Same enforcement for Claude Code, Codex, Gemini, and any future runtime registered with MoonMind.

  Out of Scope

- Per-runtime tool-name denylists as the primary control. (May still be present as model hints, but they are not load-bearing.)
- Migrating existing user-OAuth agent sessions for non-MoonMind use. The change applies to MoonMind-launched sessions only.
- Operator-facing UI for managing the egress allowlist beyond what the skill contract already declares.
- Changing the commit attribution policy (separate decision; this story only ensures attribution happens in the MoonMind boundary, not in the agent).

  Functional Requirements

- FR-001: All MoonMind-launched agent sessions authenticate via a MoonMind-managed service identity. The launcher must reject configurations that would
  inject operator-account OAuth or operator-account connector grants into the session.
- FR-002: Skill manifests declare a closed set of MoonMind tools and MCP servers. The launcher must refuse to start a runtime if the resolved tool/MCP set
  differs from the manifest, and must pin the runtime to the manifest at startup (--strict-mcp-config/equivalents).
- FR-003: Agent containers route all outbound HTTP through a MoonMind-controlled egress proxy. The proxy enforces an allowlist derived from the skill
  contract; non-allowlisted destinations fail-closed at the network layer.
- FR-004: Agent containers must not contain publish credentials or a configured origin remote that resolves to a writable URL. Commit/push/PR-creation
  binaries (git push, gh, glab) either are absent from the runtime image or have no usable credential surface.
- FR-005: repo.create_pr (and equivalent provider-specific publish activities) must look up an existing PR for the head→base pair before creating; if
  present, the activity must reconcile to it (record URL/number/sha into memo and outputs) and return success.
- FR-006: repo.push_branch must push with --force-with-lease against the last activity-recorded remote SHA, must fetch on lease miss, and must surface
  conflicts as structured retryable errors (not non-retryable ApplicationError).
- FR-007: Workflow boundary tests must cover: (a) skill manifest enforcement at launch, (b) agent attempting a non-allowlisted egress, (c) agent
  attempting gh pr create with no credentials, (d) repo.create_pr adopting a pre-existing PR, (e) repo.push_branch recovering from a --force-with-lease
  miss.
- FR-008: Telemetry must record any blocked-egress event, any rejected MCP/tool load, and any repo.create_pr reconciliation, so that operators can detect
  agents trying to bypass the contracted path.

  Acceptance Scenarios

- AS-1 (the original incident, replayed): A jira-orchestrate job for an issue that exists in MoonMind's Jira project but not in the operator's
  account-level Atlassian connector. Expected: agent has no account-level Atlassian connector visible; agent calls MoonMind's Jira tool; transition
  succeeds; MoonMind opens the PR exactly once; workflow completes.
- AS-2 (publish race): A second concurrent process pushes to the same branch between the agent's working-tree completion and MoonMind's push. Expected:
  repo.push_branch detects the lease miss, fetches, surfaces a structured conflict, the workflow either rebases or returns a retryable failure with a usable
  diagnostic — never a non-retryable (fetch first) terminal failure.
- AS-3 (pre-existing PR): A PR for the head→base pair already exists when MoonMind's repo.create_pr runs. Expected: activity adopts the existing PR,
  records its URL/sha, returns success.
- AS-4 (agent tries to shell out): Agent attempts gh pr create, curl https://api.github.com/repos/.../pulls,  git push origin HEAD, python -c "import
  requests; requests.post(...)". Expected: each attempt fails at the network or credential layer, the workflow's MoonMind-owned publish path still produces
  the correct PR, telemetry records the bypass attempt.
- AS-5 (new runtime parity): Adding a new agent runtime (e.g., a future model's CLI) requires only registering the launcher with the same skill-manifest
  contract and egress allowlist. No per-runtime denylist updates.

  Success Criteria

- SC-001: Zero MoonMind-launched agent sessions expose account-level connectors after rollout. Verified by session-bootstrap audit.
- SC-002: Zero repo.create_pr 422-already-exists workflow failures after rollout; reconciliation path absorbs them.
- SC-003: Zero non-retryable (fetch first) terminal failures from repo.push_branch; lease-miss path either succeeds or fails as a retryable structured
  error.
- SC-004: ≥99% of attempted agent egress to non-allowlisted hosts blocked at the proxy in load tests, with telemetry events for each.
- SC-005: Adding a new skill requires only updating the skill manifest's tool/MCP/egress allowlist; no runtime-launcher code change.
- SC-006: Adding a new agent runtime requires only registering its launcher adapter; no skill-by-skill or tool-by-tool change.

  Risks and Open Questions

- Egress proxy scope creep. The proxy must allowlist only what skills actually need. Over-broad allowlists (e.g., entire *.atlassian.net) re-create the
  original problem. Need a review process for proposed allowlist entries.
- Image hygiene. Removing gh/glab/credentials from agent runtime images may break workflows that legitimately read read-only public data via those CLIs.
  Audit existing skills for read-only CLI usage; route those reads through MoonMind tools or explicit read-only credentials.
- Service-identity grant management. The service identity used by agent sessions must have no connector grants of its own. Operationally, this means a
  separate Anthropic/OpenAI/Google API key (or org) used only for agent runtimes, with documented onboarding.
- Agent UX regressions. Agents that habitually run git commit / git push may produce confusing diagnostics when those commands fail. Consider stub git
  push/gh wrappers in the runtime image that return an explicit "MoonMind owns publish — produce working-tree changes only" message rather than a credential
  error.
- Backwards compatibility. Per Constitution Principle XIII, no compat shims. Existing skill manifests that don't declare tool/MCP scope must be updated in
  the same change, not migrated incrementally.

  References

- Failed workflow: mm:fac9baaf-d35d-4966-8530-adeacbb46d66 (run 019e0dd7-b08a-791e-8d8a-3e833a1629d0).
- Stomped PR: MoonLadderStudios/MoonMind#2057 (head ref change-jira-issue-mm-574-to-status-in-pr-e1677d0e, merge 432eb82e2).
- Step-12 agent stdout (taskRunId ea4c315f-c0f3-4fbf-808d-ad8bf99032aa): records the agent self-publishing PR #2057 and the Atlassian-connector miss for
  MM-574.
````

## User Story - Isolate Agent Tool Surfaces

**Summary**: As a MoonMind operator, I want MoonMind-launched agent runtimes to be structurally limited to MoonMind-contracted external-service and publish paths so workflows produce deterministic, attributable outcomes across current and future runtimes.

**Goal**: Agent sessions cannot use account-level connectors, unmanaged external-service routes, or in-session publish credentials to bypass MoonMind-owned tool and publish boundaries, while MoonMind-owned reconciliation handles pre-existing remote state without terminal workflow failures.

**Independent Test**: Can be fully tested by launching representative managed-agent workflows with a restricted skill contract, attempting Jira and repository bypass actions from inside the agent session, and verifying the bypasses fail while MoonMind-owned Jira and publish operations complete or reconcile with structured evidence.

**Acceptance Scenarios**:

1. **Given** a MoonMind-launched Jira workflow for an issue visible to MoonMind's Jira project, **When** the agent runtime starts, **Then** account-level external-service connectors are not available to the agent and the workflow uses MoonMind's trusted Jira path for Jira operations.
2. **Given** a managed agent session with a resolved skill contract, **When** the session attempts to load or call a tool, connector, or external service outside that contract, **Then** the attempt is denied before it can mutate external state and a diagnostic event records the denied surface.
3. **Given** an agent container tries to publish directly using repository or provider command-line paths, **When** it attempts to push, create a pull request, or call an unapproved provider endpoint, **Then** the action has no usable credential or network path and MoonMind remains the only publishing authority.
4. **Given** a pull request already exists for the intended head and base, **When** MoonMind's publish step runs, **Then** the publish path adopts the existing pull request, records its identity, and returns success rather than failing as a duplicate.
5. **Given** the remote branch changes between agent completion and MoonMind publishing, **When** MoonMind attempts to publish the branch, **Then** the workflow detects the conflict, fetches current remote state, and surfaces a retryable structured conflict instead of a non-retryable terminal failure.
6. **Given** a new managed runtime is registered, **When** it launches the same skill contract, **Then** it receives equivalent tool, external-service, and publish isolation without per-runtime denylist work.

### Edge Cases

- The selected skill contract omits a tool or external host required for the requested work; the runtime fails closed with an actionable launch or execution diagnostic rather than silently granting broader access.
- A local runtime image still contains generic publishing or network utilities; those utilities must not have credentials or network authorization sufficient to bypass MoonMind-owned paths.
- A pre-existing pull request has the expected head and base but stale metadata; MoonMind records the adopted pull request identity and exposes enough evidence for the operator to reconcile follow-up work.
- External-service denial telemetry volume is high during load tests; operator-visible evidence remains attributable without exposing secrets or raw credentials.

## Assumptions

- The story applies to MoonMind-launched managed agent sessions only; non-MoonMind user-managed sessions remain out of scope.
- Runtime implementation is required, not documentation-only alignment.
- The selected story covers one coherent runtime boundary: external-service and publish isolation for managed agent sessions plus publish-step reconciliation for residual races.
- Existing commit attribution policy remains unchanged; this story only requires that attribution and publishing happen at the MoonMind boundary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind-launched agent sessions MUST authenticate with a MoonMind-managed service identity and MUST reject session configuration that injects operator-account OAuth, account-level connector grants, or equivalent user-scoped external-service access into the agent runtime.
- **FR-002**: Skill manifests or resolved skill contracts MUST declare the closed set of MoonMind tools, MCP servers, connector surfaces, and external-service destinations available to a managed agent session.
- **FR-003**: The runtime launcher MUST fail closed when the resolved tool, MCP, connector, or external-service set differs from the selected skill contract.
- **FR-004**: Managed agent sessions MUST route outbound external-service traffic through a MoonMind-controlled mediation boundary that denies destinations not allowed by the selected skill contract.
- **FR-005**: Managed agent sessions MUST lack usable publish credentials and writable repository remote authority inside the agent runtime.
- **FR-006**: Direct in-session publish attempts, including branch pushes, pull request creation, or provider mutation calls outside MoonMind-owned tools, MUST fail without mutating external repository state.
- **FR-007**: MoonMind-owned publish operations MUST check for an existing pull request matching the intended head and base before creating a new pull request, and MUST adopt the existing pull request when found.
- **FR-008**: MoonMind-owned branch publishing MUST detect remote lease misses, refresh current remote state, and surface conflicts as structured retryable outcomes rather than non-retryable terminal failures.
- **FR-009**: Blocked external-service access, rejected tool or connector loads, direct publish attempts, adopted pull requests, and publish conflicts MUST produce sanitized, attributable telemetry or diagnostic evidence for operators.
- **FR-010**: The isolation contract MUST apply consistently across Claude Code, Codex, Gemini, and future managed runtimes through runtime adapter registration rather than per-runtime denylist rules.
- **FR-011**: Regression coverage MUST include workflow or adapter boundary tests for skill-contract enforcement at launch, non-allowlisted external-service denial, direct publish denial, existing pull request adoption, and remote lease-miss conflict handling.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-680` and the original Jira preset brief for traceability.

### Key Entities

- **Managed Agent Session**: A MoonMind-launched runtime execution context with a service identity, selected skill contract, external-service mediation boundary, and repository workspace.
- **Skill Contract**: The resolved declaration of tools, MCP servers, connector surfaces, and external-service destinations allowed for one managed agent run.
- **Publish Operation**: A MoonMind-owned operation that pushes branch state, creates or adopts a pull request, records publish identity, and exposes structured diagnostics.
- **Isolation Diagnostic**: Sanitized operator-visible evidence for denied external-service access, rejected tool loading, direct publish attempts, or publish reconciliation outcomes.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Maps To |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | MM-680 User Story | MoonMind-launched agent runtimes must be structurally unable to reach external services outside MoonMind-contracted paths. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-002 | MM-680 User Story | Workflows must remain deterministic and attributable across current and future agent runtimes. | In scope | FR-009, FR-010, FR-012 |
| DESIGN-REQ-003 | MM-680 Problem Statement | Account-level Atlassian or similar connectors must not be visible inside managed agent sessions. | In scope | FR-001, FR-003 |
| DESIGN-REQ-004 | MM-680 Problem Statement | In-workspace pull request creation and other unmanaged publish paths must not compete with MoonMind-owned publishing. | In scope | FR-005, FR-006 |
| DESIGN-REQ-005 | MM-680 Goal | The only viable path from an agent session to external services must be the MoonMind-contracted tool path. | In scope | FR-002, FR-003, FR-004 |
| DESIGN-REQ-006 | MM-680 Scope In #1 | Agent sessions must launch under MoonMind-managed service identity rather than operator user OAuth. | In scope | FR-001 |
| DESIGN-REQ-007 | MM-680 Scope In #2 | Runtime launch must enforce a strict declared tool and MCP scope for each skill. | In scope | FR-002, FR-003 |
| DESIGN-REQ-008 | MM-680 Scope In #3 | External-service egress must fail closed for non-allowlisted destinations regardless of agent tool naming. | In scope | FR-004, FR-006 |
| DESIGN-REQ-009 | MM-680 Scope In #4 | Agent containers must not own publish credentials or writable repository authority; MoonMind owns push and pull request creation. | In scope | FR-005, FR-006 |
| DESIGN-REQ-010 | MM-680 Scope In #5 | Publish-side activity must adopt an existing pull request for the intended head and base instead of duplicate-failing. | In scope | FR-007 |
| DESIGN-REQ-011 | MM-680 Scope In #5 | Branch publishing must handle remote lease misses as structured retryable conflicts. | In scope | FR-008 |
| DESIGN-REQ-012 | MM-680 Scope In #6 | Skill manifests must declare required MoonMind tools and MCPs and launcher enforcement must be runtime-neutral. | In scope | FR-002, FR-003, FR-010 |
| DESIGN-REQ-013 | MM-680 Functional Requirements | Boundary-level tests must prove launch enforcement, external-service denial, direct publish denial, pull request adoption, and lease-miss handling. | In scope | FR-011 |
| DESIGN-REQ-014 | MM-680 Functional Requirements and Success Criteria | Telemetry must record blocked external-service, rejected tool load, and pull request reconciliation events. | In scope | FR-009 |
| DESIGN-REQ-015 | MM-680 Out of Scope | Per-runtime denylist rules, non-MoonMind user session migration, external allowlist management UI, and commit attribution policy changes are excluded from this story. | Out of scope: constrains the selected story boundary. | FR-010 |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Session-bootstrap audit for representative managed-agent launches shows 0 sessions exposing account-level external-service connectors or operator user OAuth grants.
- **SC-002**: In boundary tests, 100% of non-contract external-service and direct publish attempts are denied without mutating external state.
- **SC-003**: A pre-existing pull request for the intended head and base is adopted successfully in 100% of reconciliation test runs.
- **SC-004**: Remote branch lease-miss scenarios produce a structured retryable conflict outcome in 100% of targeted test runs and produce 0 non-retryable terminal publish failures.
- **SC-005**: At least one boundary test covers each required isolation and reconciliation case: launch contract enforcement, external-service denial, direct publish denial, pull request adoption, and lease-miss conflict handling.
- **SC-006**: Traceability review confirms `MM-680` and the original Jira preset brief remain preserved in MoonSpec artifacts and final verification evidence.
