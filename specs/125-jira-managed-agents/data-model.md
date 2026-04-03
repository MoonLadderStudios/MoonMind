# Data Model: Jira Tools for Managed Agents

## JiraCredentialBinding

- **Purpose**: Represents the configured Jira binding that the trusted tool path resolves for one call.
- **Fields**:
  - `auth_mode`: `service_account_scoped` or `basic`
  - `api_key_ref` or local-development raw value
  - `service_account_email_ref` or raw value
  - `cloud_id_ref` or raw value
  - `email_ref` or raw value
  - `site_url_ref` or raw value
  - `tool_enabled`
  - `allowed_projects`
  - `allowed_actions`
  - `connect_timeout_seconds`
  - `read_timeout_seconds`
  - `retry_attempts`
- **Rules**:
  - Service-account mode requires API key, cloud ID, and service-account email.
  - Basic mode requires API key, site URL, and email.
  - Values may come from SecretRefs or explicit local-development raw settings, but resolved secrets never leave process memory.

## ResolvedJiraConnection

- **Purpose**: In-memory connection configuration built just in time for one Jira tool call.
- **Fields**:
  - `auth_mode`
  - `base_url`
  - `headers`
  - `redaction_values`
  - `connect_timeout_seconds`
  - `read_timeout_seconds`
  - `retry_attempts`
- **Rules**:
  - `base_url` is derived from `cloud_id` for service-account mode and from `site_url` for basic mode.
  - `headers` contain only the auth material required for the selected mode.
  - `redaction_values` include all resolved secret values and any derived basic-auth token string.

## JiraToolRequest

- **Purpose**: Strict structured input for a managed-agent Jira action.
- **Representative variants**:
  - `CreateIssueRequest`
  - `CreateSubtaskRequest`
  - `EditIssueRequest`
  - `GetIssueRequest`
  - `SearchIssuesRequest`
  - `GetTransitionsRequest`
  - `TransitionIssueRequest`
  - `AddCommentRequest`
  - `ListCreateIssueTypesRequest`
  - `GetCreateFieldsRequest`
  - `GetEditMetadataRequest`
  - `VerifyConnectionRequest`
- **Rules**:
  - Request models reject unknown top-level keys.
  - `issueKey` values must be non-empty and Jira-key shaped.
  - `projectKey` values must be non-empty and policy-allowed when allowlists are configured.

## JiraPolicy

- **Purpose**: MoonMind-side guardrails applied before Jira mutation.
- **Fields**:
  - `allowed_projects`
  - `allowed_actions`
  - `require_explicit_transition_lookup`
- **Rules**:
  - Requests violating project/action allowlists fail before transport.
  - Transition actions validate against Jira-provided transitions when strict transition lookup is enabled.

## JiraToolResult

- **Purpose**: Sanitized result envelope returned to the model.
- **Fields**:
  - `action`
  - `ok`
  - `status_code` when applicable
  - `result` payload from Jira, reduced to safe structured content
  - `summary`
  - `request_id` or retry metadata when available
- **Rules**:
  - No raw credential material appears in any result field.
  - Rate-limited and permission/auth failures return stable summaries that do not expose raw HTML or unsanitized exception text.
