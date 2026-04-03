# Feature Specification: Jira Tools for Managed Agents

**Feature Branch**: `125-jira-managed-agents`  
**Created**: 2026-04-03  
**Status**: Draft  
**Input**: User description: "Implement docs/Tools/JiraIntegration.md trusted Jira tools for managed agents"

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Tools/JiraIntegration.md` §Recommendation, §Security model | Managed agents must work with Jira through trusted MoonMind-side tools, and raw Jira credentials must not be injected into the managed agent shell or workspace. |
| DOC-REQ-002 | `docs/Tools/JiraIntegration.md` §Authentication modes | The trusted Jira integration must support both service-account scoped auth and compatibility basic auth. |
| DOC-REQ-003 | `docs/Tools/JiraIntegration.md` §Security model, §Production | Jira credentials must be stored as SecretRef-backed bindings for production use, resolved just in time inside the trusted tool path, and never persisted in plaintext to workflow payloads, logs, artifacts, or diagnostics. |
| DOC-REQ-004 | `docs/Tools/JiraIntegration.md` §Tool surface, §What not to do | MoonMind must expose a narrow, explicit Jira tool surface and must not expose arbitrary raw Jira HTTP mutation to normal agents. |
| DOC-REQ-005 | `docs/Tools/JiraIntegration.md` §Dockerfile changes, §Why not install Forge MCP or a Jira CLI? | The Jira mutation path must be implemented as MoonMind-owned server-side code using Jira REST APIs rather than Forge MCP or a Jira CLI dependency. |
| DOC-REQ-006 | `docs/Tools/JiraIntegration.md` §Jira client behavior | The client must select the correct base URL and auth header per auth mode, use bounded timeouts and retries, and log operational metadata without credential leakage. |
| DOC-REQ-007 | `docs/Tools/JiraIntegration.md` §Operation details, §Metadata helpers | MoonMind must expose issue, sub-task, transition, comment, search, and metadata helper actions with the documented behavior and newer metadata endpoints. |
| DOC-REQ-008 | `docs/Tools/JiraIntegration.md` §Input validation rules, §Permissions and policy | The tool layer must validate structured inputs, support MoonMind-side allowlists/policy controls, and reject unsafe or malformed calls before Jira requests are sent. |
| DOC-REQ-009 | `docs/Tools/JiraIntegration.md` §Operation details | Create/comment flows must support plain-text to Atlassian Document Format conversion where needed, and edit/transition behavior must remain separate and explicit. |
| DOC-REQ-010 | `docs/Tools/JiraIntegration.md` §Error handling, §Rate limiting | Jira failures must map to structured MoonMind errors, bounded retries must honor `Retry-After` on rate limits/transient failures, and exhausted rate limits must surface a structured rate-limited outcome. |
| DOC-REQ-011 | `docs/Tools/JiraIntegration.md` §Operational notes | MoonMind must support a safe connection-verification path and keep credential rotation independent from tool-call payloads or historical workflow data. |
| DOC-REQ-012 | `docs/Tools/JiraIntegration.md` §Testing checklist | Delivery must include unit, integration-style, and security regression coverage for auth resolution, validation, retries, ADF conversion, and secret-redaction boundaries. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use Jira Actions Without Exposing Credentials (Priority: P1)

As a managed-agent operator, I can let an agent create, search, read, and update Jira issues through MoonMind tools without ever giving the agent the raw Jira token.

**Why this priority**: This is the core capability described by the source document and the main security boundary the feature exists to enforce.

**Independent Test**: Call the Jira MCP tool surface with valid structured inputs for issue creation, lookup, search, and edit operations, and verify the backend performs Jira requests while the agent-visible request/result path contains no credentials.

**Acceptance Scenarios**:

1. **Given** Jira tool bindings are configured in MoonMind, **When** an agent calls `jira.create_issue`, **Then** MoonMind resolves the Jira credentials inside the trusted tool handler and creates the issue without placing the raw token in the agent runtime environment.
2. **Given** an agent needs to inspect or search work items, **When** it calls `jira.get_issue` or `jira.search_issues`, **Then** MoonMind returns a sanitized Jira result payload through the tool surface.
3. **Given** an agent needs to change summary or fields on an issue, **When** it calls `jira.edit_issue`, **Then** MoonMind performs only the edit operation and does not silently attempt a workflow transition.

---

### User Story 2 - Safely Drive Transitions and Metadata-Dependent Work (Priority: P1)

As a managed-agent operator, I can let an agent discover valid Jira metadata, comments, and transitions through explicit tools so it does not guess issue types, required fields, or status transitions.

**Why this priority**: Transition safety and metadata discovery are the main correctness safeguards that keep agents from issuing invalid or ambiguous Jira updates.

**Independent Test**: Use metadata helper tools and transition/comment tools against mocked Jira responses, including field metadata and transition lookups, and verify invalid or disallowed requests are rejected before mutation.

**Acceptance Scenarios**:

1. **Given** an agent must create a Jira issue or sub-task with valid field shapes, **When** it calls `jira.list_create_issue_types` and `jira.get_create_fields`, **Then** MoonMind returns project-specific issue-type and field metadata from the newer Jira endpoints.
2. **Given** an agent needs to move an issue through workflow state, **When** it calls `jira.get_transitions` and then `jira.transition_issue`, **Then** MoonMind validates the transition against Jira metadata and applies only the requested transition.
3. **Given** an agent submits multiline descriptions or comments, **When** it uses `jira.create_issue`, `jira.create_subtask`, or `jira.add_comment`, **Then** MoonMind converts plain text to Atlassian Document Format before sending the request where needed.

---

### User Story 3 - Operate Jira Tools with Policy, Retry, and Redaction Guarantees (Priority: P2)

As a platform operator, I can enforce project/action policy, inspect connectivity, and trust that retries, structured errors, and logging behavior remain bounded and sanitized.

**Why this priority**: The feature is not production-ready unless policy controls, rate-limit behavior, and secret-redaction boundaries are all enforceable and testable.

**Independent Test**: Exercise allowlist failures, malformed inputs, `429` retry behavior, connection verification, and HTTP failures that echo secret material, and verify the resulting MoonMind errors and logs stay structured and redacted.

**Acceptance Scenarios**:

1. **Given** MoonMind is configured with allowed Jira projects or actions, **When** an agent requests a disallowed project or action, **Then** the tool call is rejected before Jira is contacted.
2. **Given** Jira responds with `429` and a `Retry-After` header, **When** MoonMind retries the request, **Then** it performs bounded retries and returns a structured rate-limited failure if the limit persists.
3. **Given** operators need to verify Jira connectivity or rotate the credential binding, **When** they use the verification path or replace the secret value, **Then** new tool calls use the new secret without changing historical request payloads or leaking plaintext secrets.

### Edge Cases

- Jira tool bindings are enabled but the configured auth mode and bound fields do not match.
- Jira returns a response body that contains echoed credential-like values or an Authorization header leak.
- The caller passes an issue key for a project that is outside MoonMind’s configured allowlist.
- An agent tries to transition an issue using an invalid or stale transition identifier.
- A create or comment payload includes multiline text that must be transformed to Atlassian Document Format.
- Jira returns `429`, `502`, `503`, or `504` repeatedly and `Retry-After` is present or absent.
- SecretRef resolution succeeds for some Jira binding values but fails for another required field.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose Jira capability to managed agents through trusted MoonMind-side tool execution rather than by injecting raw Jira credentials into the managed agent shell, workspace, or durable execution payloads. (DOC-REQ-001)
- **FR-002**: The system MUST support Jira service-account scoped authentication using a bound cloud identifier, service-account identity, and API token. (DOC-REQ-002)
- **FR-003**: The system MUST support Jira compatibility basic-auth mode using a bound site URL, account email, and API token. (DOC-REQ-002)
- **FR-004**: Production Jira tool execution MUST support SecretRef-backed credential bindings and MUST resolve those bindings only inside the trusted Jira tool path immediately before the Jira request is made. (DOC-REQ-003)
- **FR-005**: The system MUST NOT persist plaintext Jira credentials in workflow payloads, run metadata, artifacts, diagnostics, or structured logs, and MUST redact credential material from errors and log messages. (DOC-REQ-003, DOC-REQ-010)
- **FR-006**: The managed-agent Jira surface MUST expose explicit actions for `jira.create_issue`, `jira.create_subtask`, `jira.edit_issue`, `jira.get_issue`, `jira.search_issues`, `jira.get_transitions`, `jira.transition_issue`, and `jira.add_comment`. (DOC-REQ-004, DOC-REQ-007)
- **FR-007**: The system MUST expose or internally support metadata helper actions for `jira.list_create_issue_types`, `jira.get_create_fields`, and `jira.get_edit_metadata`, and MUST use newer project-specific metadata endpoints instead of the deprecated catch-all `createmeta` endpoint. (DOC-REQ-007)
- **FR-008**: The Jira mutation path MUST be implemented as MoonMind-owned server-side REST client code and MUST NOT depend on Forge MCP or a Jira CLI runtime for managed-agent issue mutations. (DOC-REQ-005)
- **FR-009**: Jira client behavior MUST choose the correct base URL and authorization header based on auth mode, use bounded connect/read timeouts, and retry only `429`, `502`, `503`, and `504` responses. (DOC-REQ-006, DOC-REQ-010)
- **FR-010**: Retry behavior MUST honor Jira `Retry-After` guidance when present and MUST surface a structured rate-limited result when retries are exhausted. (DOC-REQ-006, DOC-REQ-010)
- **FR-011**: The system MUST validate Jira tool inputs before sending requests, including non-empty issue/project identifiers, transition identifiers, required create/sub-task fields, and strict rejection of unknown top-level keys. (DOC-REQ-008)
- **FR-012**: MoonMind MUST support optional allowlists for Jira projects and Jira actions, and MUST reject requests that violate configured policy before Jira is contacted. (DOC-REQ-008)
- **FR-013**: `jira.create_issue` and `jira.create_subtask` MUST accept structured create inputs and MUST convert plain-text multiline descriptions into Atlassian Document Format when needed. (DOC-REQ-007, DOC-REQ-009)
- **FR-014**: `jira.edit_issue` MUST update editable fields only and MUST NOT implicitly perform workflow transitions. (DOC-REQ-009)
- **FR-015**: `jira.transition_issue` MUST validate or fetch available transitions and MUST apply only the explicitly requested transition, including any transition-specific fields or updates that are required. (DOC-REQ-007, DOC-REQ-009)
- **FR-016**: `jira.add_comment` MUST support plain-text comment input and convert multiline text to Atlassian Document Format when needed. (DOC-REQ-009)
- **FR-017**: Jira request failures MUST map to clean MoonMind error categories for invalid credentials/auth mode, missing permission, missing issue/project, rate limiting, and field-validation failures without returning raw HTML or unsanitized exception traces to the model. (DOC-REQ-010)
- **FR-018**: The system MUST provide a `jira.verify_connection` path that resolves Jira bindings, checks a safe Jira endpoint, and returns a sanitized success or failure result. (DOC-REQ-011)
- **FR-019**: Jira credential rotation MUST be independent from tool-call payloads so that replacing the bound secret changes future Jira requests without mutating historical durable request data. (DOC-REQ-011)
- **FR-020**: Delivery MUST include production runtime code changes and automated validation tests that cover auth-mode resolution, SecretRef wiring, retry behavior, ADF conversion, input validation, tool dispatch, and secret-redaction guarantees. (DOC-REQ-012)

### Key Entities *(include if feature involves data)*

- **JiraCredentialBinding**: Trusted MoonMind-side binding that identifies the Jira auth mode and the SecretRef or local-development value for each required Jira credential field.
- **ResolvedJiraConnection**: In-memory Jira connection configuration built just in time for one tool call, including auth mode, base URL, headers, timeouts, and retry policy.
- **JiraToolRequest**: Strict, structured agent-facing payload for one Jira action, validated before any Jira request is sent.
- **JiraPolicy**: MoonMind-side project/action allowlists and transition-safety rules that constrain what managed agents may do through the Jira tools.
- **JiraToolResult**: Sanitized result envelope returned to the model after a Jira action completes or fails.

### Assumptions & Dependencies

- Managed agents consume MoonMind’s existing MCP tool surface, so Jira tools should be added to the same discovery and call endpoints.
- MoonMind’s existing SecretRef resolution stack (`env://`, `db://`, `exec://`, `vault://`) remains the canonical way to resolve Jira bindings.
- Existing Atlassian indexing and planning code paths may continue to exist, but this feature’s scope is the trusted managed-agent Jira tool path described by `docs/Tools/JiraIntegration.md`.
- The runtime environment already includes outbound HTTPS capability and trust roots, so the feature can call Jira directly without adding a new external runtime dependency.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Managed-agent Jira tool discovery exposes the configured Jira actions and metadata helpers through the existing MCP surface without exposing raw Jira credentials to the caller.
- **SC-002**: Unit and router-level validation shows 100% rejection of malformed Jira requests with missing required identifiers or disallowed project/action usage before any Jira HTTP request is sent.
- **SC-003**: Auth-mode tests show 100% correct base-URL and authorization-header selection for both service-account scoped and compatibility basic-auth modes.
- **SC-004**: Retry tests show bounded retries for `429`, `502`, `503`, and `504`, including honoring `Retry-After` when present and returning a structured rate-limited outcome when retries are exhausted.
- **SC-005**: Security regression coverage shows zero instances of Jira credential material appearing in tool errors, structured logs, or returned result payloads across the covered failure cases.
- **SC-006**: Multiline description/comment tests show 100% conversion of plain text into valid Atlassian Document Format for the covered Jira mutation actions.
- **SC-007**: Feature completion requires production runtime code changes under `moonmind/` or `api_service/` plus automated tests under `tests/`; spec/docs-only output does not satisfy completion.
