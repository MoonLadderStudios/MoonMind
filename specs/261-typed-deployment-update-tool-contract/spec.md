# Feature Specification: Typed Deployment Update Tool Contract

**Feature Branch**: `261-typed-deployment-update-tool-contract`
**Created**: 2026-04-25
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-519 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-519 MoonSpec Orchestration Input

## Source

- Jira issue: MM-519
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Typed deployment update tool contract
- Labels: `moonmind-workflow-mm-d22f5e68-8c97-4885-b9c4-cc74b6576885`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-519 from MM project
Summary: Typed deployment update tool contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-519 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-519: Typed deployment update tool contract

Source Reference
Source Document: docs/Tools/DockerComposeUpdateSystem.md
Source Title: Docker Compose Deployment Update System
Source Sections:
- 8. Executable tool contract
- 16. Interaction with task execution
- 20. Locked decisions
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-016
- DESIGN-REQ-002
- DESIGN-REQ-004
As an operator of MoonMind workflows, I need deployment.update_compose_stack registered as a typed privileged executable tool, so deployment updates can be orchestrated through the plan/tool system rather than ad hoc shell execution.

Acceptance Criteria
- The tool registry exposes deployment.update_compose_stack version 1.0.0 with the documented required inputs and outputs.
- The tool requires deployment_control and docker_admin capabilities and admin authorization.
- The tool schema accepts stack, image.repository, image.reference, optional resolvedDigest, mode, options, and reason.
- The tool output schema includes status, stack, requestedImage, resolvedDigest, updatedServices, runningServices, and artifact refs.
- Retry policy uses max_attempts 1 with documented non-retryable error codes.
- Plan-node validation supports the representative skill tool invocation and rejects arbitrary shell snippets or runner image overrides.

Requirements
- Keep deployment update as executable operational work in the tool/plan system.
- Avoid treating deployment update as an agent instruction bundle.
- Preserve exact target image input semantics without hidden transformations.

Relevant Implementation Notes
- Preserve MM-519 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tools/DockerComposeUpdateSystem.md` as the source design reference for the typed deployment update tool contract, task execution interaction, and locked decisions.
- Register `deployment.update_compose_stack` as a typed privileged executable tool, not as an agent instruction bundle or ad hoc shell workflow.
- Keep exact target image input semantics for repository, reference, and optional resolved digest without hidden transformations.
- Validate representative skill tool invocations through the plan/tool system and reject arbitrary shell snippets or runner image overrides.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Typed Deployment Update Tool Contract

**Summary**: As an operator of MoonMind workflows, I need `deployment.update_compose_stack` registered as a typed privileged executable tool, so deployment updates can be orchestrated through the plan/tool system rather than ad hoc shell execution.

**Goal**: Deployment update runs can reference a versioned executable tool contract whose schema, capabilities, authorization, retry policy, and plan-node validation rules are deterministic before any privileged Compose operation is dispatched.

**Independent Test**: Can be fully tested by loading the deployment update tool definition into the existing tool registry and validating representative plan nodes, confirming valid typed deployment inputs pass and arbitrary shell, runner-image, and path fields fail schema validation.

**Acceptance Scenarios**:

1. **Given** the deployment tool registry is loaded, **When** the `deployment.update_compose_stack` definition is inspected, **Then** it exposes version `1.0.0`, required input and output schemas, `deployment_control` and `docker_admin` capabilities, admin-only security, and a non-retryable privileged-operation retry policy.
2. **Given** a representative deployment update plan node using the typed tool and documented inputs, **When** the plan is validated against the pinned registry snapshot, **Then** validation succeeds with `tool.type = skill`, name `deployment.update_compose_stack`, and version `1.0.0`.
3. **Given** a deployment update plan node that includes arbitrary shell commands, caller-provided Compose paths, or an updater runner image override, **When** the plan is validated against the pinned registry snapshot, **Then** validation fails before tool execution.
4. **Given** a queued deployment update is produced from the policy-gated API, **When** its initial parameters are inspected, **Then** the plan node uses the canonical deployment tool name and version instead of a raw shell command or ad hoc runner contract.

### Edge Cases

- Missing `stack`, `image`, or `reason` fields fail schema validation.
- Unsupported update modes fail schema validation.
- Unknown root-level plan inputs such as `command`, `composeFile`, `hostPath`, or `updaterRunnerImage` fail schema validation.
- Optional `resolvedDigest` remains accepted without changing requested repository/reference semantics.

## Assumptions

- MM-518 owns the policy-gated API and request authorization slice; MM-519 owns the executable tool definition and plan-validation contract slice.
- The first implementation registers the contract payload and validates plan-node shape; the privileged Docker execution handler remains a later deployment-control worker concern unless already present.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a canonical tool definition for `deployment.update_compose_stack` version `1.0.0`.
- **FR-002**: The tool definition MUST require `stack`, `image.repository`, `image.reference`, and `reason` inputs while accepting optional `image.resolvedDigest`, `mode`, `removeOrphans`, `wait`, `runSmokeCheck`, `pauseWork`, and `pruneOldImages` inputs.
- **FR-003**: The tool definition MUST declare output schema fields for `status`, `stack`, `requestedImage`, optional `resolvedDigest`, `updatedServices`, `runningServices`, and artifact refs for before state, after state, command log, and verification.
- **FR-004**: The tool definition MUST require `deployment_control` and `docker_admin` capabilities and admin authorization.
- **FR-005**: The tool definition MUST use the `mm.tool.execute` activity binding with selector mode `by_capability`.
- **FR-006**: The tool definition MUST use `max_attempts` 1 and identify non-retryable error codes for invalid input, permission denial, policy violation, and deployment locking.
- **FR-007**: Plan validation MUST accept a representative typed deployment update plan node that uses the canonical tool definition and documented inputs.
- **FR-008**: Plan validation MUST reject arbitrary shell snippets, caller-provided Compose paths, host paths, runner image overrides, and unrecognized flags before tool execution.
- **FR-009**: The policy-gated API queued-run payload MUST reference the canonical tool name and version from a shared contract source, not hardcoded divergent strings.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-519` and the canonical Jira preset brief.

### Key Entities

- **Deployment Update Tool Definition**: Versioned executable tool contract containing schemas, capability requirements, executor binding, retry policy, and admin security.
- **Deployment Update Plan Node**: A plan entry that invokes the typed deployment update tool with bounded deployment inputs.
- **Deployment Update Tool Result**: Structured output describing status, requested image, resolved digest when known, changed/running services, and durable artifact refs.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 8.1 requires deployment update to be represented as typed executable tool `deployment.update_compose_stack`. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002**: Source section 8.2 requires input schema fields for stack, image repository/reference/resolved digest, mode, options, and reason. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-003**: Source section 8.2 requires output schema fields for status, stack, requested image, resolved digest, updated services, running services, and artifact refs. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-004**: Source section 8.2 requires `deployment_control` and `docker_admin` capabilities plus admin-only security. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-005**: Source section 8.2 requires executor activity type `mm.tool.execute` with `by_capability` selector. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-006**: Source section 8.2 requires retry policy `max_attempts` 1 with non-retryable invalid input, permission, policy, and lock failures. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-007**: Source section 8.3 requires a representative plan node using `tool.type = skill`, canonical name/version, and typed inputs. Scope: in scope. Maps to FR-007 and FR-009.
- **DESIGN-REQ-008**: Source sections 4.2, 5, and 20 forbid arbitrary shell input, user-selectable runner images, arbitrary host paths, arbitrary Compose files, and hidden target image transformations. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-009**: Source section 16 requires deployment updates to interact with task execution through executable operational work rather than agent instruction bundles. Scope: in scope. Maps to FR-001, FR-005, and FR-007.
- **DESIGN-REQ-016**: Source section 16 requires task execution to resolve `deployment.update_compose_stack` through the executable tool registry path used by queued deployment runs. Scope: in scope. Maps to FR-001, FR-007, and FR-009.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Registry parsing exposes exactly one `deployment.update_compose_stack` v1.0.0 contract with the required schemas, capabilities, security, executor, and retry policy.
- **SC-002**: A representative valid deployment update plan node validates successfully against a pinned registry snapshot.
- **SC-003**: Representative invalid deployment update plan nodes containing shell/path/runner override inputs fail validation before execution.
- **SC-004**: Existing policy-gated API tests confirm queued deployment update runs use the canonical shared tool name and version.
- **SC-005**: Verification evidence preserves `MM-519`, the canonical Jira preset brief, DESIGN-REQ-001 through DESIGN-REQ-009, and DESIGN-REQ-016 in MoonSpec artifacts.
