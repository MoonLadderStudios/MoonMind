# Task Presets System

Status: Draft  
Owners: MoonMind Engineering (Task Platform + UI)  
Last Updated: 2026-02-18

## 1. Purpose

Define the MoonMind "Task Presets" system: a server-hosted catalog of step templates with compile-time expansion into concrete `task.steps[]`. The design keeps the worker contract unchanged while giving UI, CLI, and MCP users reusable orchestrations, convenient editing affordances, and the ability to save real task steps back into the catalog.

## 2. Goals and Non-Goals

### Goals

- Provide a single authoritative task step template catalog with versioning, ownership, and scopes (global/team/personal).
- Offer deterministic server-side expansion, validation, and audit tracking before tasks are enqueued.
- Deliver UI conveniences (preview, append/replace, collapse-as-group, favorites) without changing the task payload schema.
- Allow users to promote existing steps into reusable templates safely, including secret scrubbing and input parameterization.
- Support CLI/MCP flows via REST endpoints identical to the UI.

### Non-Goals

- Changing worker runtime behavior or allowing step-level runtime/git/publish overrides (remains forbidden).
- Executing templates lazily at runtime; all expansion happens before enqueue.
- Replacing the SpecKit skills or orchestrator workflows (templates are complementary).

## 3. System Overview

```
             +---------------------+
             | Template Catalog DB |
             +----------+----------+
                        ^
                        | CRUD + version seed
+-------------+   REST  |                      +----------------+
| Task UI /   +-------->+  Task Template API  <-+ MCP / CLI / CI |
| Automations |         |                      +----------------+
+------+------+         v
       |          +-----+------------------+
       | expand   | Step Expansion Service |
       +--------->+ (validation + hydrate) |
                  +-----+------------------+
                        v
                  +-----+------------------+
                  | Task Payload Compiler |
                  | merges steps + audit  |
                  +-----+------------------+
                        v
                  +-----+------------------+
                  | Queue Job Creation     |
                  +------------------------+
```

Key properties:

- Templates are stored centrally and exposed via FastAPI routers under `/api/task-step-templates`.
- The expansion service applies inputs, generates stable step IDs, validates schema compliance, and emits derived metadata (required capabilities, referenced skills).
- The compiler merges expanded steps into the task payload, updates `task.appliedStepTemplates`, and never stores template definitions inline with the task.

## 4. Template Model

### 4.1 Storage Schema

Add table `task_step_templates` (and SQLModel/ORM) with fields:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key.
| `slug` | text | Human-friendly identifier (`pr-code-change`). Unique per scope.
| `version` | text | SemVer string (`1.2.0`).
| `scope_type` | enum | `global`, `team`, `personal`.
| `scope_ref` | text | `null` for global, team id, or user id owner.
| `title` | text | Display name.
| `description` | text | Markdown/short description.
| `inputs_schema` | jsonb | Array of `{name,label,type,required,default,options}` definitions.
| `steps` | jsonb | Ordered array of step blueprints (instructions, skill, title, annotations).
| `tags` | text[] | Search facets, e.g., `plan`, `qa`.
| `required_capabilities` | text[] | Additional capabilities derived from referenced skills.
| `created_by` | uuid | User id.
| `created_at` | timestamptz | Audit.
| `updated_at` | timestamptz | Audit.
| `is_active` | bool | Soft delete / hide.

Seed data (default presets) live in `api_service/data/task_step_templates/*.yaml` and hydrate table via Alembic migrations.

### 4.2 Step Blueprint Schema

Each `steps[]` entry matches the task step contract subset:

```yaml
- slug: assess
  title: "Assess repo + objective"
  instructions: "Review the repo and restate the objective."
  skill: { id: auto, args: {} }
  annotations:
    hideInPreview: false
    collapseGroup: null
```

Only `instructions`, `title`, and `skill` are allowed. Optional `annotations` drive UI-only hints (group labels, recommended icons) but are stripped from expanded steps before enqueue.

### 4.3 Input Typing

Support primitive types with minimal validation:

- `text`, `textarea`, `markdown`
- `enum` / select (server enforces allowed values)
- `boolean`
- `user` / `team` selectors (resolve to ids)
- `repo_path` (validated against repo tree cache)

Inputs feed template-level variable substitution plus optional built-in variables (Section 6.2).

## 5. API Surface

Routes live under `api_service/api/routers/task_step_templates.py` with dependency guards (auth scopes, RBAC, rate-limit).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/task-step-templates` | List templates (filter by scope, tags, text, favorites, last-used).
| `GET` | `/api/task-step-templates/{slug}` | Return metadata + blueprint + input schema + latest version.
| `GET` | `/api/task-step-templates/{slug}/versions/{version}` | Fetch specific version (immutable).
| `POST` | `/api/task-step-templates` | Create template (team/personal scopes only).
| `PUT` | `/api/task-step-templates/{slug}/versions/{version}` | Update metadata for draft versions (not allowed for released versions; create new version instead).
| `POST` | `/api/task-step-templates/{slug}:expand` | Expand template with provided `inputs` + `context` (alias for `/expand`).
| `POST` | `/api/task-step-templates/save-from-task` | Save selected task steps as a new template (Section 7.3).
| `DELETE` | `/api/task-step-templates/{slug}` | Soft-delete template (team/personal owners).

CLI + MCP usage: same endpoints with service tokens; they use `expand` and merge steps client-side similar to UI.

## 6. Expansion and Validation Pipeline

### 6.1 Flow

1. UI retrieves template detail (slug + version) to render inputs and preview.
2. User fills inputs; UI sends `POST /expand` with `{slug, version, inputs, options}`.
3. Expansion service resolves template -> blueprint -> steps.
4. For each step blueprint:
   - Perform Jinja-like substitution for `{{inputs.name}}` and built-ins.
   - Validate required fields (`instructions` or `skill` must exist).
   - Copy `skill` definitions verbatim; no runtime/git/publish overrides allowed.
   - Generate deterministic `stepId` using `tpl:{slug}:{version}:{index}` (optionally hashed with inputs for uniqueness when repeating segments).
5. Aggregate derived `requiredCapabilities` (union of template metadata + referenced skills).
6. Return payload:

```json
{
  "steps": [ ... concrete steps ... ],
  "appliedTemplate": {
    "slug": "pr-code-change",
    "version": "1.0.0",
    "inputs": {"change_summary": "..."},
    "stepIds": ["tpl:pr-code-change:01", ...]
  },
  "capabilities": ["git", "codex"],
  "warnings": []
}
```

### 6.2 Variable Sources

- `inputs.<name>` from user-provided values.
- `context.repo`, `context.defaultBranch`, `context.taskObjective`, `context.requester` (server populates from submission form values to avoid spoofing).
- Date/time helpers: `{{now}}`, `{{iso_today}}`.
- Sequence helper `{{counter}}` for enumerated instructions.

### 6.3 Validation Rules

- Reject templates referencing forbidden step keys.
- Reject expansions whose final `steps[]` is empty.
- Reject if substitution yields unresolved placeholders.
- Limit step count per expansion (defaults to 25) with configurable override per template.
- Run schema validation using `pydantic` models reused from task submission path.

### 6.4 Audit Trail

`task.appliedStepTemplates` stores `[ { slug, version, inputs, appliedAt } ]` when the UI merges steps. Queue job creation also emits a `task_template_applied` event for monitoring.

## 7. UI Experience

### 7.1 Template Browser

- **Surface**: `/tasks/queue/new` sidebar drawer titled "Add preset".
- **Filters**: search by text, tag chips, scope tabs (My templates / Team / Global), "Recently used" quick list.
- **Favorites**: star icon stored per user; pinned templates appear first.
- **Preview**: selecting a template shows a read-only step preview with the number of steps, referenced skills, and derived capabilities.

### 7.2 Apply Flow

- Modal collects input fields rendered from `inputs_schema` (supports validation + placeholders).
- Users choose "Append" (default) or "Replace all steps"; UI explains the impact.
- UI can display steps collapsed as groups by reading `annotations.collapseGroup` for presentation only.
- After expansion response, UI merges steps into the editor, enabling inline edits while keeping `stepId`s unless the user manually changes instructions.
- Recents list records last 5 template applications per user to accelerate repeated workflows.

### 7.3 Save as Template (from existing steps)

- Within the step editor, a "Save as template" button becomes active when one or more steps are selected.
- Flow:
  1. User selects contiguous or non-contiguous steps.
  2. UI opens the save modal, showing detected repeated phrases; user may click to convert them into parameter placeholders (e.g., highlight text → "Make variable" → choose variable name and field type).
  3. UI scrubs potential secrets by redacting values matching heuristics (`token=`, `ghp_`, etc.) before sending to the server.
  4. UI submits to `POST /api/task-step-templates/save-from-task` with sanitized blueprint, suggested inputs, and desired scope (personal by default).
- Server validates as in Section 6, assigns `version=1.0.0`, and persists template. Success response returns template slug so it appears immediately in the browser.
- Optionally, UI offers "Share with team" toggle (requires permission) that sets scope to team and notifies collaborators via existing notification system.

### 7.4 Convenience Enhancements

- **Collapse as group**: purely client-side; groups remain collapsed across sessions via local storage.
- **Draft templates**: UI indicates when a template version is marked `is_active=false`; applying prompts confirmation if version is not yet released.
- **Diff view**: before applying, users may open a diff comparing current steps to the template output.
- **Keyboard shortcuts**: `⌘+P` opens preset drawer, `Shift+Enter` applies last used template with default inputs (when allowed).

## 8. Integration with Task Payloads

- Expanded steps are inserted into `task.steps[]` exactly as workers expect; no template metadata is included per step.
- `task.requiredCapabilities` becomes `union(task-level, template-derived)` before job creation.
- `task.skill` precedence remains unchanged; steps with explicit `skill` override take priority, others inherit.
- Task payload includes `appliedStepTemplates` array for observability but workers ignore it.

## 9. Template Lifecycle and Governance

- **Versioning**: released versions are immutable. To edit, create a new version via `POST /{slug}` with `baseVersion`. UI shows latest stable by default, with manual selection for older versions.
- **Publishing**: Team/global templates require review—UI supports "Submit for review" which notifies maintainers; API enforces reviewer approval before `is_active=true`.
- **Deactivation**: Soft-delete hides template from listing but keeps history for audits; tasks referencing historic versions continue to show metadata in audit logs.
- **Telemetry**: each expansion increments counters (StatsD) labeled by slug/version to see usage.

## 10. Security and Compliance

- Authorization enforced with RBAC policies: personal templates accessible to owner; team templates require team membership; global templates restricted to platform admins.
- Input substitution occurs server-side only; UI cannot inject arbitrary context fields.
- Secret scrubbing pipeline runs for both creation and "save as template" flows, dropping `skill.args` keys flagged as secrets unless explicitly whitelisted by template owner.
- Templates referencing restricted skills inherit their `requiredCapabilities`; queue creation fails fast if the submitting user lacks required approvals.
- Audit logs include who created, updated, and applied each template (user id + timestamp) for compliance reviews.

## 11. Deployment & Migration Plan

1. **Phase 0** – schema + seed: add DB table, migrations, and load a handful of core presets (Plan→Implement→Validate→PR, Refactor Safely, Security Sweep).
2. **Phase 1** – API read-only + UI apply: expose listing + expand endpoints; UI consume them with append/replace + preview.
3. **Phase 2** – Save-as-template: enable creation endpoints, UI selection flow, and RBAC gating; release to limited beta.
4. **Phase 3** – Team governance + reviews; add notifications and analytics.
5. **Phase 4** – CLI/MCP parity plus export/import CLI commands for offline editing.

Rollouts include feature flags (`taskTemplateCatalog.enabled`, `templateSave.enabled`) to gate functionality per environment.

## 12. Open Questions

- Should template inputs support validation expressions (regex/min length) beyond current primitives?
- How should we handle templates that need conditional step inclusion (e.g., optional verification step)? Proposal: add `when` expressions referencing inputs but postpone to v2.
- Do we allow auto-upgrading tasks when a template version is deprecated? Current stance: no auto-mutation; rely on UI warnings.

