# Contract: Capability Input Metadata and Create-Page Rendering

## Capability Detail Response

A selectable capability that supports schema-driven inputs exposes this contract to the Create page.

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "label": "Jira Orchestrate",
  "description": "Build and execute an implementation workflow from a Jira issue.",
  "inputSchema": {
    "type": "object",
    "required": ["jira_issue"],
    "properties": {
      "jira_issue": {
        "type": "object",
        "title": "Jira issue",
        "required": ["key"],
        "properties": {
          "key": { "type": "string", "title": "Issue key" },
          "summary": { "type": "string", "title": "Summary" },
          "url": { "type": "string", "format": "uri", "title": "URL" }
        }
      }
    }
  },
  "uiSchema": {
    "jira_issue": {
      "widget": "jira.issue-picker",
      "allowManualKeyEntry": true,
      "searchPlaceholder": "Search Jira issues"
    }
  },
  "defaults": {}
}
```

Pre-release replacement rule: when `inputSchema`/`uiSchema` is present, the schema-driven renderer is authoritative for that capability. Do not add compatibility aliases, duplicate capability-specific branches, or fallback paths that silently change schema semantics; existing task-template `inputs` remain only for unrelated surfaces until they are explicitly converted.

## Draft Input Value

```json
{
  "jira_issue": {
    "key": "MM-593",
    "summary": "Implement schema-driven preset and skill inputs on Create page",
    "url": "https://example.atlassian.net/browse/MM-593"
  }
}
```

Only safe issue identifiers and sanitized context are allowed. Raw credentials, auth headers, cookies, and resolved secret values are forbidden.

## Validation Error

```json
{
  "path": "preset.inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required",
  "recoverable": true
}
```

The Create page must map `path` to the generated field and preserve existing draft values.

## Widget Resolution Rules

1. If `uiSchema[field].widget` is present, resolve it through the allowlisted widget registry.
2. Else if the schema field has a supported `x-moonmind-widget`, resolve that key through the same registry.
3. Else select a standard widget from schema type and format.
4. Unknown widgets either fall back safely when the schema permits a standard input or produce a field-addressable `unsupported_widget` error.
5. Widget resolution must not branch on capability ID.

## Required Test Contract

- Preset detail with `inputSchema` and `uiSchema` renders generated fields.
- Skill detail with the same shape renders through the same schema renderer.
- `jira.issue-picker` selection is driven by metadata only.
- Missing `jira_issue.key` blocks dependent actions with a field-addressable error.
- Manual key entry remains in draft state when Jira lookup is unavailable and manual entry is allowed.
- New capability fixture with supported schema renders without capability-specific Create-page code.
- Secret-like schema defaults are rejected or redacted and never appear in submitted safe input values.
