---
name: jira-issue-creator
description: Create Jira issues such as tasks, stories, bugs, or subtasks from user intent. Use when a user asks to open, file, draft, or create a Jira ticket and needs fields validated, issue text composed, trusted Jira tool or connector calls made, and the created issue link returned.
metadata:
  required-capabilities:
    - jira
---

# Jira Issue Creator

Create a Jira task, story, bug, or subtask from the user's request. Describe the required operation by capability and scope, and prefer the current supported trusted Jira tool path for the selected environment (MoonMind trusted Jira tool surface or an available Jira connector). A standalone adapter may be used only when explicitly authorized for the selected environment, and it must preserve the same draft/write-intent, field-validation, and receipt semantics.

Missing configuration or a denied managed operation is a blocker: report it and stop. It must never trigger credential shopping, raw-secret scraping, or a less-constrained HTTP fallback.

## Modes

- `draft` (default when the request carries no explicit write intent; the dry-run path): strictly non-mutating. Compose and validate the issue payload, perform zero issue/comment/status writes, and return the exact payload that would be sent with status `draft`.
- `create`: mutating. Requires the request's existing explicit write intent (the user already asked to open, file, or create the ticket). When that intent is present, complete creation without adding a second routine confirmation step. Without explicit write intent, stay in `draft` and report `blocked_awaiting_write_intent` instead of creating anything.
- When `mode` is omitted, resolve it from the request: explicit open/file/create wording (for example "create a Jira story") selects `create`; requests without write intent stay in `draft`. An explicitly supplied `mode` always wins over this inference.

## Inputs

- Required: `mode` (`draft` default when the request carries no explicit write intent, or `create`). Omitted `mode` with explicit open/file/create wording selects `create`; otherwise it stays `draft`. Draft/proposal requests are strictly non-mutating and perform zero writes.
- Required: Jira project key or enough context to identify one.
- Required: issue type (`Task`, `Story`, `Bug`, or `Sub-task`). Use `jira.list_create_issue_types` to resolve the name to an `issueTypeId`. Default to `Task` only when the user does not specify.
- Required: summary/title.
- Required for creation: the request's existing explicit write intent plus authenticated Jira access through the trusted Jira tool surface.
- Optional for breakdown-driven creation: `storyBreakdownPath`, `stories`, or `storyOutput` from `moonspec-breakdown`.
- Optional for ordered Jira story exports: dependency mode `none` or `linear_blocker_chain`.
- Optional: description, acceptance criteria, priority, labels, assignee, reporter, parent issue key, sprint, component, due date, linked issues, attachments.
- Required when creating Jira issues from canonical `moonspec-breakdown` output: original source document traceability. Read the path from each story's `sourceReference.path` or from the breakdown-level `source.referencePath` / `source.path`. For trusted Jira, inline, or `imperative-input` breakdowns without a document path, preserve the source title/key and `coverageIds` instead of blocking solely because no canonical document path exists.

## Workflow

1. Resolve the Jira target.
- Identify the project key, issue type, and Jira site/base URL.
- If the issue type is a subtask, require a parent issue key.
- If multiple Jira connections or projects are possible, inspect available connector metadata first. If ambiguity remains, ask for the missing target instead of guessing.

2. Compose the issue fields.
- Use the user's wording as the source of truth.
- Keep the summary concise and action-oriented.
- For bugs, include sections for observed behavior, expected behavior, reproduction steps, impact, and environment when the information is available.
- For stories, include sections for user story, acceptance criteria, notes, and out-of-scope items when the information is available.
- For tasks, include sections for objective, requirements, implementation notes, and verification when the information is available.
- When creating from `moonspec-breakdown` output, every Jira issue description must include source traceability. Use a `Source Document` section when an original declarative document path exists; otherwise name the selected source title/key and any story-specific source sections or coverage IDs available.
- Do not invent business requirements, acceptance criteria, assignees, priorities, or deadlines.

3. Validate before creating.
- Confirm required Jira fields for the project and issue type using the trusted Jira tool surface when possible.
- Map requested fields to Jira field IDs through metadata (using `jira.get_create_fields`) instead of hardcoding custom field IDs.
- Fail fast if a requested field cannot be set through the available Jira schema.
- Never print credentials, authorization headers, cookies, or full environment dumps.

4. Create the issue, or return the draft.
- In `draft` mode, stop here: perform zero issue/comment/status writes and return the composed payload with status `draft`.
- In `create` mode, first confirm the request already carries explicit write intent. If it does not, stay non-mutating and return status `blocked_awaiting_write_intent`. When intent is present, create without adding a second routine confirmation step.
- Use the available Jira connector's `jira.create_issue` or `jira.create_subtask` operations when present.
- In MoonMind workflow plans, `jira-issue-creator` is an agent skill, not a deterministic executable tool. Use the trusted Jira tool surface to inspect projects, issue types, and create fields, then create the requested issues.
- When the task references a story breakdown directory, look for `stories.json` inside that directory unless an exact `storyBreakdownPath` is provided.
- Preserve story order and stable story IDs from MoonSpec breakdown when creating Jira issues.
- For dependency mode `none`, create only the Jira issues and report that no dependency links were requested.
- For dependency mode `linear_blocker_chain`, after all target Jira issues are created or reused, create trusted Jira dependency links so each story is blocked by the stories it depends on, ordering prerequisites before the stories that depend on them.
- Create dependency links only through MoonMind's trusted Jira tool surface, such as `jira.create_issue_link` when available. Do not call Jira directly from the shell and do not rely on issue descriptions or prompt text as the dependency mechanism.
- Return created/reused issue keys plus created/reused/failed dependency-link results. If issue creation succeeds but a dependency link fails, report partial success and do not claim the dependency chain is complete.
- Before creating any issue from `stories.json`, verify every story has source traceability. Canonical declarative breakdowns require an original source document path through `story.sourceReference.path`, `source.referencePath`, or `source.path`. Trusted Jira, inline, and `imperative-input` breakdowns without a document path must preserve source title/key and `coverageIds`; do not block solely because those sources lack a canonical path.
- Otherwise use only an explicitly authorized standalone adapter that preserves equivalent task semantics (same field validation, write-intent gating, and receipt handling). Never fall back to raw credentials, scraped secrets, or a broader-authority HTTP call.
- Send only the fields needed for the requested issue.
- Persist a stable operation identity before the create POST and use it as the creation receipt basis: generate a unique operation key (for example a UUID), persist it to the workflow ledger/artifacts, and include it verbatim in searchable issue data (a unique marker token in the description plus a label when the project schema permits). When the trusted tool offers an idempotent create keyed by that identity, use it.
- Treat retries carefully: after an uncertain create outcome (timeout or lost response where no receipt was returned), reconcile through that persisted operation identity first — re-fetch by the exact marker via `jira.search_issues`/`jira.get_issue` and check for the returned receipt — before repeating the write. Summary/project/reporter similarity alone is not a usable receipt and an incomplete search is not proof no prior issue exists: do not repeat the create until the receipt is reconciled or the operator explicitly accepts the duplicate risk.

## Breakdown Story Behavior

When invoked after `moonspec-breakdown` or when the request references story breakdown output:

- Read story candidates from the provided JSON breakdown, not from `spec.md`.
- Require source traceability before Jira creation. Each Jira issue must include `Source Document: <path>` in its description when a source path exists, using the story-level `sourceReference.path` when present and otherwise the breakdown-level `source.referencePath` or `source.path`. If no source path exists, include the selected source title/key instead.
- Include story-specific `sourceReference.sections` and `sourceReference.coverageIds` when present so the Jira issue points to the relevant part of the selected source.
- Preserve story order, stable story IDs, and dependency IDs so ordered Jira issue chains can be created after issue creation succeeds.
- Treat missing canonical source document references as a hard blocker only for canonical declarative breakdowns. Do not block trusted Jira, inline, or `imperative-input` breakdowns that preserve source title/key and coverage IDs.
- Do not create or rename `spec.md`.
- If Jira issue creation succeeds, return Jira keys/URLs and do not request another output format.
- If Jira is not configured, required Jira fields are missing, or issue creation fails, report the exact blocker and the existing `artifacts/story-breakdowns/...` output instead of fabricating Jira success.
- The fallback file must not be named `spec.md`.

5. Return the result.
- In `draft` mode, report status `draft` with the exact payload that would be sent. No issue key or URL exists.
- In `create` mode, report status `created` with the created issue key and URL.
- Summarize the issue type, project, summary, and any important fields that were set.
- If creation failed or was blocked, report the exact missing input, missing write intent, validation error, permission issue, or Jira API error without exposing secrets.

## Outputs

- Result status: `draft` (payload only, zero writes), `created` (issue created, include key/URL), or `blocked`/`blocked_awaiting_write_intent` with the reason.
- Draft payload that would be sent (draft mode), or created issue key and URL (create mode).
- Short summary of fields set.
- Failure reason and recommended operator action when creation is blocked.

## External Dependencies

- Trusted Jira tool surface, Jira connector, or an explicitly authorized standalone adapter with equivalent semantics.
- Jira access with permission to create issues in the target project.
- Network access to the Jira site.
- Project metadata access for issue types and required/custom fields.

## Failure Modes

- Missing or ambiguous project, issue type, parent issue, or Jira connection: ask for the specific missing value.
- Authentication or authorization failure: state that Jira access is unavailable or insufficient and identify the target project/operation.
- Required Jira field missing: list the field name Jira requires and ask for its value.
- Unsupported issue type or field: explain which value is unsupported for the selected project.
- Uncertain retry state: reconcile the persisted operation identity (exact marker search plus receipt check) before creating another issue; never treat an incomplete similarity search as proof of absence.
