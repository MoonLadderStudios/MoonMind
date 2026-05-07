# Jira Integration

Status: Desired-state architecture
Owners: MoonMind Engineering
Last Updated: 2026-05-05
Canonical for: Jira issue context, Jira issue picker widget, Jira tool execution, Jira credential handling, schema-driven Jira inputs
Related: `docs/UI/CreatePage.md`, `docs/Tasks/TaskPresetsSystem.md`, `docs/Steps/StepTypes.md`, `docs/Steps/SkillSystem.md`, `docs/Tasks/TaskPublishing.md`, `docs/Tasks/PrMergeAutomation.md`

---

## 1. Purpose

This document defines the desired-state Jira integration for MoonMind.

It covers:

1. schema-driven Jira issue inputs on the Create page,
2. the reusable `jira.issue-picker` widget,
3. how presets and skills request Jira issue context without Create page hard-coding,
4. trusted MoonMind-side Jira tools,
5. secure credential handling through SecretRefs and Provider Profiles,
6. Jira issue creation, sub-task creation, edits, comments, searches, and workflow transitions,
7. post-merge Jira completion,
8. validation, error handling, redaction, and testing.

The central rule is:

> Jira is an integration and field widget, not a special Create page mode.

A preset such as `jira-orchestrate` should declare that it needs a Jira issue through `input_schema` / `ui_schema`. The Create page should render a Jira issue input automatically because the schema requests the reusable `jira.issue-picker` widget, not because the page has hard-coded knowledge of the preset ID.

---

## 2. Desired-State Summary

MoonMind should support Jira in three complementary ways:

1. **Schema-driven input context**
   - Presets, skills, and tools can request Jira issue values through JSON Schema-compatible input schemas.
   - The Create page renders the correct fields from schema metadata.
   - Jira issue selection uses the reusable `jira.issue-picker` widget.

2. **Trusted Jira tools**
   - Agents call structured MoonMind Jira tools such as `jira.get_issue`, `jira.search_issues`, `jira.transition_issue`, or `jira.add_comment`.
   - The trusted backend/worker handler resolves credentials and calls Jira REST APIs.
   - The managed agent never receives raw Jira credentials.

3. **Workflow automation**
   - Presets may bind Jira issue input into generated Skill and Tool steps.
   - Merge automation may transition or comment on a Jira issue through trusted Jira activities.
   - Publishing and post-merge completion use exact issue references, not fuzzy issue guessing.

---

## 3. Non-Negotiable Security Rule

**Do not inject `ATLASSIAN_API_KEY` into the managed agent container as a normal environment variable.**

The managed agent should not receive:

- `ATLASSIAN_API_KEY`
- a mounted `.env` file containing the Jira token
- a config file containing the Jira token
- a serialized workflow payload containing the Jira token
- auth headers or cookies
- raw SecretRef resolution results

Instead:

1. Store Jira credentials as SecretRef-backed secrets.
2. Bind those secrets through Provider Profiles or the Jira integration configuration.
3. Resolve the secret only inside trusted MoonMind-side Jira tool handlers or trusted Temporal activities.
4. Return sanitized Jira results to the agent.

This gives the agent the capability to work with Jira without giving it possession of raw credentials.

---

## 4. Schema-Driven Jira Inputs

Jira issue context should be declared by capabilities, not hard-coded into the Create page.

A Jira-oriented preset should request a Jira issue like this:

```yaml
input_schema:
  type: object
  required:
    - jira_issue
  properties:
    jira_issue:
      type: object
      title: Jira issue
      description: Issue that will seed the task instructions and orchestration context.
      required:
        - key
      properties:
        key:
          type: string
          title: Issue key
        summary:
          type: string
          title: Summary
        description:
          type: string
          title: Description
        url:
          type: string
          title: URL
          format: uri
        status:
          type: string
          title: Status
        assignee:
          type: string
          title: Assignee

ui_schema:
  jira_issue:
    widget: jira.issue-picker
    data_source: jira.issues
    search_placeholder: Search Jira issues
    allow_manual_key_entry: true
    display_template: "{{ key }} — {{ summary }}"
```

The Create page renders a Jira issue picker because the schema requests `jira.issue-picker`.

The Create page must not do this:

```tsx
if (preset.id === "jira-orchestrate") {
  return <JiraOrchestrateSpecialForm />
}
```

The page may do this:

```tsx
widgetRegistry["jira.issue-picker"] = JiraIssuePickerInput
```

That is generic widget registration, not preset-specific logic.

---

## 5. Jira Issue Value Shape

The minimum durable Jira issue value is:

```json
{
  "key": "MOON-123"
}
```

An enriched value may include additional fields:

```json
{
  "key": "MOON-123",
  "summary": "Add schema-driven preset inputs",
  "description": "Create page should render preset inputs from schema.",
  "url": "https://example.atlassian.net/browse/MOON-123",
  "status": "Ready for Dev",
  "assignee": "Nathaniel Sticco"
}
```

Rules:

1. `key` is the minimum durable reference.
2. Optional fields are cached/enriched context, not the source of truth.
3. Backend validation and expansion should tolerate missing optional enrichment fields.
4. The backend may fetch fresh issue details during validation or expansion when needed.
5. Durable task records should store only safe issue identifiers and sanitized context.
6. Secrets, auth headers, account tokens, and raw Jira client responses must never be stored in the input value.

---

## 6. Jira Issue Picker Widget

The `jira.issue-picker` widget is reusable across presets, skills, and tools.

It should support:

- searching issues by key, summary, status, project, board, sprint, or assignee when the Jira integration is available
- manual issue key entry when allowed by schema/UI metadata
- displaying key, summary, status, assignee, and URL when available
- storing a durable value containing at least `key`
- enriching optional fields such as `summary`, `description`, `url`, `status`, and `assignee`
- preserving manually entered values when integration lookup fails
- showing integration setup errors without losing entered values
- reporting validation errors at the field path that requested the widget

Example field-addressable validation error:

```json
{
  "path": "steps[0].preset.inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required"
}
```

Unknown or unavailable Jira integrations should degrade gracefully:

1. allow manual key entry if `allow_manual_key_entry` is true;
2. otherwise show a blocking setup/connection error;
3. never discard the draft input value.

---

## 7. Preset Integration

A Jira preset is a preset with a schema-declared Jira input.

Example configured preset step before expansion:

```json
{
  "type": "preset",
  "preset_id": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {
    "jira_issue": {
      "key": "MOON-123",
      "summary": "Add schema-driven preset inputs",
      "url": "https://example.atlassian.net/browse/MOON-123"
    }
  },
  "expansion_state": "not_expanded"
}
```

Preset expansion binds the Jira issue into generated steps:

```yaml
steps:
  - type: skill
    skill_id: code.implementation
    inputs:
      repository: "{{ context.repository.full_name }}"
      branch: "{{ context.branch.name }}"
      jira_issue: "{{ inputs.jira_issue }}"
      instructions: "Implement {{ inputs.jira_issue.key }} and prepare a pull request."

  - type: tool
    tool_id: jira.add_comment
    inputs:
      issueKey: "{{ inputs.jira_issue.key }}"
      body: "MoonMind task started."
```

Rules:

1. The preset declares `jira_issue` in its `input_schema`.
2. The Create page renders the input from schema.
3. The backend validates the selected issue.
4. Expansion binds input values through explicit expressions.
5. Generated steps validate under their own Skill or Tool schemas.
6. Generated steps preserve preset provenance.

Example provenance:

```json
{
  "sourceType": "preset",
  "presetId": "jira-orchestrate",
  "presetVersion": "1",
  "inputSnapshot": {
    "jira_issue": {
      "key": "MOON-123"
    }
  }
}
```

---

## 8. Skill Integration

A Skill may request Jira context directly in its own input schema.

Example Skill manifest fragment:

```yaml
id: code.implementation
kind: skill
version: 1
title: Code Implementation
entrypoint: SKILL.md

input_schema:
  type: object
  required:
    - instructions
  properties:
    instructions:
      type: string
      title: Instructions
    jira_issue:
      type: object
      title: Jira issue
      required:
        - key
      properties:
        key:
          type: string
        summary:
          type: string
        description:
          type: string
        url:
          type: string
          format: uri

ui_schema:
  instructions:
    widget: textarea
  jira_issue:
    widget: jira.issue-picker
    allow_manual_key_entry: true
```

The same Jira picker renders whether the field is requested by a Skill or a Preset.

Rules:

1. Skills declare Jira inputs through their Skill manifest/input schema.
2. Presets can generate Skill steps and bind Jira inputs into them.
3. Skill inputs are validated against the Skill input schema before runtime launch.
4. Runtime adapters receive only sanitized issue context, not Jira credentials.

---

## 9. Tool Integration

Expose a narrow, explicit set of Jira actions rather than a generic "execute arbitrary Jira HTTP request" tool.

Recommended Tool surface:

- `jira.verify_connection`
- `jira.get_issue`
- `jira.search_issues`
- `jira.create_issue`
- `jira.create_subtask`
- `jira.edit_issue`
- `jira.get_transitions`
- `jira.transition_issue`
- `jira.add_comment`

Avoid a catch-all raw-request tool unless it is restricted to trusted operators.

A Tool step may be authored directly:

```json
{
  "type": "tool",
  "tool": {
    "id": "jira.transition_issue",
    "version": "1.0.0",
    "inputs": {
      "issueKey": "MOON-123",
      "transitionId": "31"
    }
  }
}
```

Or generated by a preset:

```yaml
steps:
  - type: tool
    tool_id: jira.transition_issue
    inputs:
      issueKey: "{{ inputs.jira_issue.key }}"
      transitionId: "{{ inputs.start_transition_id }}"
```

The model should never send raw credentials. The backend already knows which Jira credential binding to use.

---

## 10. Credential Model

MoonMind is moving toward a Provider Profiles + SecretRefs + controlled materialization model.

For Jira, the safest controlled boundary is the Jira tool invocation itself, not the general agent runtime.

Recommended pattern:

1. Keep Jira credentials in the MoonMind secrets system.
2. Bind credentials to a Jira integration profile or provider profile.
3. Resolve credentials inside trusted backend/worker code only when a Jira tool is called.
4. Send the HTTPS request to Jira from that trusted code path.
5. Return sanitized results to the agent.

### Local development

For local development, it is acceptable to point SecretRefs at environment variables on the trusted API or worker process.

Compatibility/basic-auth mode:

```bash
ATLASSIAN_AUTH_MODE=basic
ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
ATLASSIAN_EMAIL=bot@example.com
ATLASSIAN_API_KEY=your-api-token
```

Preferred/service-account mode:

```bash
ATLASSIAN_AUTH_MODE=service_account_scoped
ATLASSIAN_CLOUD_ID=11111111-2222-3333-4444-555555555555
ATLASSIAN_SERVICE_ACCOUNT_EMAIL=bot@serviceaccount.atlassian.com
ATLASSIAN_API_KEY=your-service-account-token
```

### Production

For production, store credentials in MoonMind's managed secrets system and bind them by SecretRef.

Example:

```yaml
jira_credentials:
  auth_mode: db://jira-auth-mode
  api_key: db://jira-api-key
  email: db://jira-email
  cloud_id: db://jira-cloud-id
  site_url: db://jira-site-url
```

Important:

- do not materialize these values into the agent's general environment
- resolve these values inside the Jira tool handler only
- if Mission Control surfaces the binding, show only presence/health, never secret values

---

## 11. Authentication Modes

Support two modes.

### 11.1 Preferred production mode: service account + scoped API token

Use an Atlassian service account and store its token in `ATLASSIAN_API_KEY`.

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

### 11.2 Compatibility/local-dev mode: account email + API token

Suggested values:

- `ATLASSIAN_AUTH_MODE=basic`
- `ATLASSIAN_API_KEY=<api-token>`
- `ATLASSIAN_EMAIL=<atlassian-account-email>`
- `ATLASSIAN_SITE_URL=https://your-domain.atlassian.net`

Use the site-local Jira base URL:

```text
${ATLASSIAN_SITE_URL}/rest/api/3
```

This mode is useful for local development and existing bot-style automation, but production should prefer a service account and scoped token when possible.

---

## 12. Trusted Jira Tool Execution Flow

When a Jira tool call is made:

1. Validate the tool input against the tool schema.
2. Validate policy, project allowlists, and action allowlists.
3. Resolve SecretRefs into plaintext credentials in memory.
4. Construct the Jira client.
5. Make the Jira REST request.
6. Discard plaintext secrets.
7. Redact logs and exception messages.
8. Return a sanitized result.

The trusted Jira path must never write plaintext credentials into:

- Temporal workflow payloads
- run metadata
- Provider Profile rows
- artifacts
- diagnostics
- structured logs
- exception text returned to the model

---

## 13. Jira Client Behavior

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

Do not log:

- authorization headers
- resolved secrets
- raw request dumps containing credentials

---

## 14. Operation Details

### 14.1 Verify connection

Tool name:

```text
jira.verify_connection
```

Purpose:

- verify credentials
- verify base URL/cloud ID
- verify project permissions where configured
- return sanitized health status

### 14.2 Get issue

Tool name:

```text
jira.get_issue
```

Suggested input:

```json
{
  "issueKey": "ENG-123",
  "fields": ["summary", "description", "status", "assignee"]
}
```

Use this to enrich schema-driven `jira_issue` values and to validate exact issue references.

### 14.3 Search issues

Tool name:

```text
jira.search_issues
```

Suggested input:

```json
{
  "query": "schema-driven preset inputs",
  "projectKey": "ENG",
  "maxResults": 20
}
```

The issue picker should use a constrained search helper rather than exposing arbitrary JQL to normal users by default.

### 14.4 Create issue

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
- use project-specific issue-type and field-metadata endpoints to discover valid issue types and required fields

### 14.5 Create sub-task

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

- use `POST /issue`
- ensure `issueTypeId` refers to a sub-task issue type
- include the parent issue key or ID

### 14.6 Edit issue

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
- do not attempt transitions here
- retrieve edit metadata when field mutability is unclear

### 14.7 Get transitions

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

### 14.8 Transition issue

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

### 14.9 Add comment

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

---

## 15. Metadata Helpers

The tool layer should expose or internally use metadata helpers so the agent does not guess issue types or required fields.

Recommended helpers:

- `jira.list_create_issue_types(projectKey)`
- `jira.get_create_fields(projectKey, issueTypeId)`
- `jira.get_edit_metadata(issueKey)`
- `jira.get_transitions(issueKey)`

Do not build new logic on the deprecated `GET /issue/createmeta` endpoint. Prefer newer project-specific issue-type and field-metadata endpoints.

---

## 16. Input Validation Rules

The Jira tool wrapper should validate all inputs before making an API call.

Examples:

- `projectKey` must match an allowed project if MoonMind enforces project allowlists
- `issueKey` must be non-empty and match a simple Jira key pattern
- `transitionId` must be a string or integer-like value
- `summary` must be present for issue creation
- `parentIssueKey` must be present for sub-task creation
- reject unknown top-level keys if strict mode is enabled

Schema-driven Jira inputs should also validate:

- required field paths such as `jira_issue.key`
- supported object shape
- URI format for `jira_issue.url`
- widget-specific constraints such as project allowlists or manual-entry policy

---

## 17. Permissions and Policy

Use least privilege.

Recommended policy defaults:

- service account limited to relevant Jira projects
- minimal required scopes only
- no project-admin scopes unless truly needed
- optional allowlist of project keys inside MoonMind
- optional repository-to-project defaults for Jira Breakdown / Jira Orchestrate presets
- optional allowlist of tool actions per agent/runtime
- no raw HTTP mode for normal agents

Potential MoonMind-side policy knobs:

```yaml
jira_policy:
  allowed_projects:
    - ENG
    - OPS
  project_defaults_by_repository:
    ExampleOrg/Platform: ENG
    ExampleOrg/Game: OPS
  allowed_actions:
    - verify_connection
    - get_issue
    - search_issues
    - create_issue
    - create_subtask
    - edit_issue
    - get_transitions
    - transition_issue
    - add_comment
  require_explicit_transition_lookup: true
  deny_raw_http_mode: true
```

---

## 18. Error Handling

Map Jira failures into clean, structured MoonMind errors.

Examples:

- `401` -> credential invalid, expired, or wrong auth mode
- `403` -> valid credential but missing permission or scope
- `404` -> issue or project not found, or wrong Cloud ID / endpoint format
- `429` -> rate limited; retry with backoff
- `400` / `422` -> field validation or workflow/transition mismatch

Avoid returning raw HTML, auth headers, raw exception traces, or full Jira account payloads to the model.

Widget and schema errors should return field-addressable paths:

```json
{
  "path": "inputs.jira_issue.key",
  "message": "Issue MOON-123 was not found or is not visible to the configured Jira credential.",
  "code": "jira_issue_not_found"
}
```

---

## 19. Post-Merge Jira Completion

Post-merge Jira completion follows the same trusted-tool rule.

The `MoonMind.MergeAutomation` workflow invokes a trusted activity, such as `merge_automation.complete_post_merge_jira`, rather than asking `pr-resolver` or an agent shell to call Jira.

The trusted Jira path must:

1. fetch the selected issue by exact key through `get_issue`;
2. fetch transitions through `get_transitions` with field expansion enabled;
3. treat an existing done-category issue status as a successful no-op;
4. validate any explicit transition ID or exact transition name against currently available transitions;
5. otherwise select a transition only when exactly one available transition targets Jira's done status category;
6. fail closed when zero or multiple done transitions are available, or when required transition fields have no configured defaults;
7. apply the selected transition through `transition_issue`.

The trusted Jira path must not use fuzzy summary search to infer a target issue for post-merge completion, and it must not transition more than one Jira issue for one merge automation run.

Post-merge completion artifacts and summaries may include the selected issue key, candidate sources, transition ID/name, done/no-op status, and sanitized failure reason. They must not include auth headers, cookies, tokens, raw SecretRef resolution results, or full Jira account payloads.

---

## 20. Docker and Runtime Dependencies

The Jira integration does not require a Jira CLI.

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
- Do not rely on `curl` as the agent-facing integration mechanism.

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

---

## 21. Suggested Code Layout

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
      widgets.py
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
- `widgets.py` — backend helpers for picker search/enrichment if kept near integration code
- `jira_activities.py` — Temporal activity wrapper when Jira calls run through activity boundaries

---

## 22. What Not To Do

Do not:

- inject `ATLASSIAN_API_KEY` into the agent runtime's general environment
- write a `.jira` or `.env` file with the token into the workspace
- expose a raw "make arbitrary Jira HTTP call" tool to normal agents
- use the deprecated `GET /issue/createmeta` endpoint for new work
- try to change issue status through `Edit issue`
- assume a transition exists by status name without checking available transitions
- rely on Forge MCP as the mutation path for managed agents
- hard-code Jira preset forms in the Create page
- make `jira-orchestrate` a special Create page mode
- store raw Jira API responses containing account/private data as preset inputs

---

## 23. Testing Checklist

### Schema/UI tests

- a preset with `jira_issue` input renders the Jira issue picker from schema
- a Skill with `jira_issue` input renders the same picker
- widget selection is based on `ui_schema` / `x-moonmind-widget`, not preset or skill ID
- manual key entry is preserved when allowed
- missing issue key returns a field-addressable validation error
- unsupported Jira widget configuration fails safely

### Unit tests

- auth header construction for both auth modes
- base URL selection
- SecretRef resolution wiring
- redaction of secrets from logs and exceptions
- plain-text-to-ADF conversion
- input validation for each tool action
- Jira issue object validation and enrichment

### Integration tests

- verify connection
- search issues
- get issue
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
- schema defaults never expose secret values

---

## 24. Acceptance Criteria

The key acceptance criteria are:

1. A preset can request a Jira issue by declaring an `input_schema` and `ui_schema`.
2. The Create page renders the Jira issue input automatically without preset-specific code.
3. A Skill can request the same Jira issue shape and use the same widget.
4. A user can select or manually enter a Jira issue key.
5. The configured issue value is stored as preset/skill input without raw credentials.
6. Backend validation can enrich or verify the issue through trusted Jira tooling.
7. Preset expansion can bind the Jira issue into generated Skill and Tool steps.
8. Jira tools execute through trusted backend/worker handlers.
9. Managed agents never receive raw Jira credentials.
10. Post-merge Jira completion transitions only the exact selected issue through trusted code.

---

## 25. Bottom Line

The best MoonMind Jira design is:

- schema-driven Jira issue inputs
- reusable `jira.issue-picker` widget
- no preset-specific Create page hard-coding
- SecretRef-backed Jira credentials
- trusted MoonMind-side Jira tool execution
- no raw Jira token in managed agent shells
- strongly typed Jira actions instead of arbitrary HTTP
- bounded retries, redaction, and least-privilege credentials

This aligns Jira with the broader MoonMind direction: presets and skills declare typed inputs; the Create page renders inputs dynamically; the backend validates and executes trusted integration actions.
