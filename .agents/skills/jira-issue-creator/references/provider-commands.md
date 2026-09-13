# Jira Issue Creator — provider commands (portable reference)

Selected-provider invocation shapes for this skill. Normative draft/write
intent, secret hygiene, authority boundaries, and success evidence stay in
`SKILL.md`; this file only shows how the selected provider is invoked.

## Trusted Jira tool surface (preferred)

- Inspect projects, issue types, and create fields through the trusted
  surface (`jira.list_create_issue_types`, `jira.get_create_fields` or
  equivalent), then create with `jira.create_issue` / `jira.create_subtask`.
- Create dependency links only through `jira.create_issue_link` when
  available.
- Search before an uncertain retry with `jira.search_issues` by a stable
  summary/project/reporter marker.

## Standalone adapter (explicitly authorized only)

A standalone adapter is permitted only when explicitly authorized for the
environment, and must preserve equivalent task semantics: metadata-driven
field resolution, ADF description handling, append/replacement preservation,
receipt-bound retries with unknown-outcome reconciliation, and no duplicate
writes. It must never use raw user-supplied credentials scraped from the
shell, logs, or environment dumps.
