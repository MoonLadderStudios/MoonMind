# Jira Integration for Managed Agents

Status: Recommended design
Owners: MoonMind Engineering
Last Updated: 2026-04-02

## Purpose

This document describes the recommended way to let MoonMind managed agents work with Jira Cloud without exposing the raw `ATLASSIAN_API_KEY` value inside the agent runtime.

The design covers:

- how to install the necessary runtime dependencies in the worker image
- how to store and resolve Jira credentials securely
- how to expose Jira operations as MoonMind tools
- how to support issue creation, sub-task creation, edits, and workflow transitions
- how to keep credentials out of workflow payloads, logs, artifacts, and general-purpose agent shells

## Recommendation

**Do not inject `ATLASSIAN_API_KEY` into the managed agent container as a normal environment variable.**

Instead:

1. Store Jira credentials as a SecretRef-backed secret.
2. Resolve the secret only inside a trusted MoonMind-side Jira tool handler.
3. Let the agent call a MoonMind Jira tool such as `jira.create_issue` or `jira.transition_issue`.
4. Have the trusted tool handler call Jira Cloud REST APIs on the agent's behalf.

This gives the agent the *capability* to work with Jira without giving it direct possession of the raw credential.

## Why this is the right fit for MoonMind

MoonMind is already moving toward a **Provider Profiles + SecretRefs + launch-time materialization** model rather than the older `auth_mode` / `api_key_ref` contract. The recent direction is:

- Provider Profiles are the durable contract for managed runtime credentials.
- SecretRefs are the mechanism for binding secrets.
- Secret resolution should happen only at controlled execution boundaries.
- Raw secrets should not appear in workflow payloads, profile rows, artifacts, logs, or test fixtures.
- Generated files containing secrets should be ephemeral and not durably published.

For Jira, the safest controlled boundary is **the Jira tool invocation itself**, not the general agent runtime.

That means the recommended pattern is:

- keep Jira credentials in the MoonMind secrets system
- resolve them in a trusted backend or worker process only when a Jira tool is called
- send the HTTPS request to Jira from that trusted code path
- return a sanitized result to the agent

This is better than placing the key in the agent environment, because managed agents often have bash, file-system, and code-execution capabilities.

## Authentication modes

Support two modes.

### 1. Preferred production mode: service account + scoped API token

Use an Atlassian **service account** and store its token in `ATLASSIAN_API_KEY`.

Recommended supporting values:

- `ATLASSIAN_AUTH_MODE=service_account_scoped`
- `ATLASSIAN_API_KEY=<service-account-token>`
- `ATLASSIAN_SERVICE_ACCOUNT_EMAIL=<service-account-email>`
- `ATLASSIAN_CLOUD_ID=<atlassian-cloud-id>`

Use the Platform API Gateway base URL:

```text
https://api.atlassian.com/ex/jira/${ATLASSIAN_CLOUD_ID}/rest/api/3
```

Why this mode is preferred:

- separate non-human identity
- explicit scopes
- cleaner revocation and rotation
- easier auditing
- easier least-privilege setup

Recommended scopes for standard issue work:

- `read:jira-work`
- `write:jira-work`

Only add broader scopes such as `manage:jira-project` if the tool truly needs project-administration capabilities.

### 2. Compatibility / local-dev mode: account email + API token

If you already have an existing Atlassian API token and email address, support a compatibility mode.

Suggested values:

- `ATLASSIAN_AUTH_MODE=basic`
- `ATLASSIAN_API_KEY=<api-token>`
- `ATLASSIAN_EMAIL=<atlassian-account-email>`
- `ATLASSIAN_SITE_URL=https://your-domain.atlassian.net`

Use the site-local Jira base URL:

```text
${ATLASSIAN_SITE_URL}/rest/api/3
```

This mode is useful for simple local development and existing bot-style automation, but production should prefer a service account and scoped token when possible.

## Security model

### Never expose the raw secret to the managed agent shell

The managed agent should **not** receive:

- `ATLASSIAN_API_KEY`
- a mounted `.env` file containing the Jira token
- a config file containing the Jira token
- a serialized workflow payload containing the Jira token

The agent should only receive a tool capability such as:

- `jira.create_issue`
- `jira.create_subtask`
- `jira.edit_issue`
- `jira.get_transitions`
- `jira.transition_issue`
- `jira.add_comment`
- `jira.search_issues`

### Resolve secrets just in time

When a Jira tool call is made:

1. validate the tool input
2. resolve SecretRefs into plaintext credentials in memory
3. construct the Jira client
4. make the request
5. discard the plaintext secret
6. return a sanitized result

### Never persist plaintext secrets

The Jira tool path must never write plaintext credentials into:

- Temporal workflow payloads
- run metadata
- Provider Profile rows
- artifacts
- diagnostics
- structured logs
- exception text returned to the model

### Redaction

At minimum, redact:

- `Authorization`
- `ATLASSIAN_API_KEY`
- any `email:token` basic-auth material
- request dumps that accidentally include headers
- serialized SecretRef resolution results

## Dockerfile changes

The Jira integration does **not** require a Jira CLI.

The recommended implementation is a small Python client that calls Jira's REST API directly from trusted MoonMind code.

### System packages

Install HTTPS trust roots if they are not already present.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
 && rm -rf /var/lib/apt/lists/*
```

Notes:

- `ca-certificates` is required for reliable TLS to Atlassian Cloud.
- `curl` is optional but useful for manual health checks and debugging.
- Do **not** rely on `curl` as the agent-facing integration mechanism.

### Python dependencies

Prefer adding dependencies to the project's normal dependency file, then rebuilding the image.

A minimal dependency set is:

```dockerfile
RUN pip install --no-cache-dir \
    httpx \
    tenacity
```

Recommended usage:

- `httpx` for async HTTPS calls to Jira
- `tenacity` for bounded retries on `429` and transient `5xx` responses

### Why not install Forge MCP or a Jira CLI?

Do **not** use the Forge MCP Server as the runtime mutation tool for this feature.

The Forge MCP Server is intended to expose Atlassian and Forge **knowledge/context** to coding agents and IDEs. It is not the right primitive for a MoonMind-managed backend integration that needs to create or edit Jira issues from a trusted tool path.

For MoonMind, the correct primitive is a **trusted server-side Jira tool** implemented in MoonMind itself.

## Suggested code layout

One clean layout would be:

```text
moonmind/
  integrations/
    jira/
      __init__.py
      auth.py
      client.py
      models.py
      tool.py
      adf.py
      errors.py
  workflows/
    temporal/
      activities/
        jira_activities.py
```

Suggested responsibilities:

- `auth.py` — convert resolved SecretRefs into auth headers and base URLs
- `client.py` — low-level REST wrapper
- `models.py` — request / response schemas
- `tool.py` — tool dispatcher and policy checks
- `adf.py` — convert plain text descriptions/comments into Atlassian Document Format when needed
- `jira_activities.py` — optional Temporal activity wrapper if Jira calls should run through activity boundaries

## Secret configuration

### Local development

For local development, it is acceptable to point SecretRefs at environment variables on the trusted API or worker process.

#### Compatibility/basic-auth mode

```bash
ATLASSIAN_AUTH_MODE=basic
ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
ATLASSIAN_EMAIL=bot@example.com
ATLASSIAN_API_KEY=your-api-token
```

#### Preferred/service-account mode

```bash
ATLASSIAN_AUTH_MODE=service_account_scoped
ATLASSIAN_CLOUD_ID=11111111-2222-3333-4444-555555555555
ATLASSIAN_SERVICE_ACCOUNT_EMAIL=bot@serviceaccount.atlassian.com
ATLASSIAN_API_KEY=your-service-account-token
```

### Production

For production, store credentials in MoonMind's managed secrets system and bind them by SecretRef.

Illustrative pseudo-configuration:

```yaml
jira_credentials:
  auth_mode:
    source: db_encrypted
    secret_id: jira-auth-mode
  api_key:
    source: db_encrypted
    secret_id: jira-api-key
  email:
    source: db_encrypted
    secret_id: jira-email
  cloud_id:
    source: db_encrypted
    secret_id: jira-cloud-id
  site_url:
    source: db_encrypted
    secret_id: jira-site-url
```

Important:

- this is **not** intended to be materialized into the agent's general environment
- resolve these values inside the Jira tool handler only
- if Mission Control surfaces this binding, show only presence/health, never the secret value

## Tool surface

Expose a narrow, explicit set of actions rather than a generic "execute arbitrary Jira HTTP request" tool.

Recommended actions:

- `jira.create_issue`
- `jira.create_subtask`
- `jira.edit_issue`
- `jira.get_issue`
- `jira.search_issues`
- `jira.get_transitions`
- `jira.transition_issue`
- `jira.add_comment`

Avoid a catch-all raw-request tool unless it is restricted to operators.

## Jira client behavior

### Base URL selection

```python
if auth_mode == "service_account_scoped":
    base_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3"
else:
    base_url = f"{site_url.rstrip('/')}/rest/api/3"
```

### Authorization header selection

Service-account scoped token mode:

```python
headers = {
    "Authorization": f"Bearer {api_key}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
```

Compatibility/basic-auth mode:

```python
basic = base64.b64encode(f"{email}:{api_key}".encode("utf-8")).decode("ascii")
headers = {
    "Authorization": f"Basic {basic}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
```

### Timeouts and retries

Recommended defaults:

- connect timeout: 10 seconds
- read timeout: 30 seconds
- max retry attempts: 3
- retry on: `429`, `502`, `503`, `504`
- respect `Retry-After` when present

### Logging

Log:

- action name
- Jira issue key/id where available
- project key where available
- HTTP status code
- Atlassian request ID headers if useful
- retry count

Do **not** log:

- authorization headers
- resolved secrets
- raw request dumps containing credentials

## Operation details

### 1. Create issue

Tool name:

```text
jira.create_issue
```

Suggested input:

```json
{
  "projectKey": "ENG",
  "issueTypeId": "10001",
  "summary": "Add Jira integration",
  "description": "Implement server-side Jira tool",
  "fields": {
    "priority": { "name": "High" }
  }
}
```

Implementation notes:

- use `POST /issue`
- support either a ready-made `fields` payload or a higher-level simplified input that MoonMind converts to Jira JSON
- if a plain-text description is provided, convert it to Atlassian Document Format for multiline fields
- use create-metadata endpoints to discover valid issue types and required fields before submission when needed

### 2. Create sub-task

Tool name:

```text
jira.create_subtask
```

Suggested input:

```json
{
  "projectKey": "ENG",
  "parentIssueKey": "ENG-123",
  "issueTypeId": "10002",
  "summary": "Wire SecretRef resolution",
  "description": "Resolve Jira token at tool execution time"
}
```

Implementation notes:

- also use `POST /issue`
- ensure `issueTypeId` refers to a sub-task issue type
- ensure the request includes the parent issue key or ID

### 3. Edit issue

Tool name:

```text
jira.edit_issue
```

Suggested input:

```json
{
  "issueKey": "ENG-123",
  "fields": {
    "summary": "Finalize Jira integration design"
  },
  "update": {}
}
```

Implementation notes:

- use `PUT /issue/{issueIdOrKey}`
- **do not** attempt transitions here
- retrieve edit metadata when field mutability is unclear

### 4. Get transitions

Tool name:

```text
jira.get_transitions
```

Suggested input:

```json
{
  "issueKey": "ENG-123",
  "expandFields": true
}
```

Implementation notes:

- use `GET /issue/{issueIdOrKey}/transitions`
- if `expandFields` is true, request `expand=transitions.fields`
- return transition IDs and names so the model can choose a valid transition

### 5. Transition issue

Tool name:

```text
jira.transition_issue
```

Suggested input:

```json
{
  "issueKey": "ENG-123",
  "transitionId": "31",
  "fields": {
    "resolution": { "name": "Done" }
  }
}
```

Implementation notes:

- use `POST /issue/{issueIdOrKey}/transitions`
- fetch available transitions first unless a validated transition ID is already supplied
- if the transition screen has required fields, include them in `fields` or `update`

### 6. Add comment

Tool name:

```text
jira.add_comment
```

Suggested input:

```json
{
  "issueKey": "ENG-123",
  "body": "Implementation PR is ready for review"
}
```

Implementation notes:

- use the Jira comment endpoint
- convert multiline text to Atlassian Document Format when needed

## Metadata helpers

The tool layer should expose or internally use metadata helpers so the agent does not guess issue types or required fields.

Recommended helpers:

- `jira.list_create_issue_types(projectKey)`
- `jira.get_create_fields(projectKey, issueTypeId)`
- `jira.get_edit_metadata(issueKey)`
- `jira.get_transitions(issueKey)`

Important:

- do **not** build new logic on the deprecated `GET /issue/createmeta` endpoint
- prefer the newer project-specific issue-type and field-metadata endpoints

## Input validation rules

The Jira tool wrapper should validate all inputs before making an API call.

Examples:

- `projectKey` must match an allowed project if MoonMind enforces project allowlists
- `issueKey` must be non-empty and match a simple Jira key pattern
- `transitionId` must be a string or integer-like value
- `summary` must be present for issue creation
- `parentIssueKey` must be present for sub-task creation
- reject unknown top-level keys if strict mode is enabled

## Example internal tool contract

Illustrative shape:

```json
{
  "tool": "jira.create_issue",
  "parameters": {
    "projectKey": "ENG",
    "issueTypeId": "10001",
    "summary": "Add Jira integration",
    "description": "Implement MoonMind-side Jira tool",
    "fields": {
      "labels": ["automation", "moonmind"]
    }
  }
}
```

The model should never send raw credentials. The backend already knows which Jira credential binding to use.

## Example execution flow

1. The agent decides it needs to create or modify a Jira issue.
2. It invokes a Jira tool by name with structured parameters.
3. MoonMind validates the call against the tool schema and policy.
4. The trusted Jira tool handler resolves the bound SecretRefs.
5. The handler constructs the correct base URL and auth header.
6. The handler calls Jira REST.
7. The handler redacts sensitive data from logs and returns a sanitized result.
8. The managed agent sees only the result, never the raw secret.

## Permissions and policy

Use least privilege.

Recommended policy defaults:

- service account limited to the relevant Jira projects
- minimal required scopes only
- no project-admin scopes unless truly needed
- optional allowlist of project keys inside MoonMind
- optional allowlist of tool actions per agent/runtime

Potential MoonMind-side policy knobs:

```yaml
jira_policy:
  allowed_projects:
    - ENG
    - OPS
  allowed_actions:
    - create_issue
    - create_subtask
    - edit_issue
    - get_transitions
    - transition_issue
    - add_comment
  require_explicit_transition_lookup: true
  deny_raw_http_mode: true
```

## Error handling

Map Jira failures into clean, structured MoonMind errors.

Examples:

- `401` -> credential invalid / expired / wrong auth mode
- `403` -> valid credential but missing permission or scope
- `404` -> issue or project not found, or wrong Cloud ID / endpoint format
- `429` -> rate limited; retry with backoff
- `400` / `422` -> field validation or workflow/transition mismatch

Avoid returning giant raw HTML or raw exception traces to the model.

## Rate limiting

The client should:

- retry boundedly on `429`
- honor `Retry-After`
- surface a structured "rate_limited" result to orchestration when retries are exhausted
- optionally mark the associated profile / tool credential as cooling down if MoonMind later unifies tool credentials with Provider Profile cooldown semantics

## Operational notes

### Token rotation

Because Atlassian tokens now expire by default, track expiration metadata in MoonMind and rotate before expiry.

Recommended operational behavior:

- store token expiry alongside secret metadata when known
- warn operators before expiration
- allow replacing the secret without changing the tool contract
- ensure new tool invocations use the new secret without mutating historical durable payloads

### Health check

Add a lightweight verification path such as:

```text
jira.verify_connection
```

This should:

- resolve credentials
- call a small safe endpoint such as project or user lookup
- verify both auth and permissions
- return a sanitized success/failure result

## Testing checklist

### Unit tests

- auth header construction for both auth modes
- base URL selection
- SecretRef resolution wiring
- redaction of secrets from logs and exceptions
- plain-text-to-ADF conversion
- input validation for each tool action

### Integration tests

- create issue
- create sub-task
- edit issue
- get transitions
- transition issue
- add comment
- `429` retry behavior
- permission-denied behavior

### Security regression tests

- secret never appears in workflow payloads
- secret never appears in run metadata
- secret never appears in artifacts
- secret never appears in structured logs
- secret never appears in agent-visible stderr/stdout

## What not to do

Do **not**:

- inject `ATLASSIAN_API_KEY` into the agent runtime's general environment
- write a `.jira` or `.env` file with the token into the workspace
- expose a raw "make arbitrary Jira HTTP call" tool to normal agents
- use the deprecated `GET /issue/createmeta` endpoint for new work
- try to change issue status through `Edit issue`
- assume a transition exists by status name without checking the available transitions
- rely on Forge MCP as the mutation path for managed agents

## Bottom line

The best MoonMind design is:

- **SecretRef-backed Jira credentials**
- **trusted MoonMind-side Jira tool execution**
- **no raw Jira token in the managed agent shell**
- **strongly typed Jira actions instead of arbitrary HTTP**
- **bounded retries, redaction, and least-privilege credentials**

This approach lines up with MoonMind's current move toward Provider Profiles, launch-only secret resolution, and strict avoidance of raw secrets in durable contracts.
