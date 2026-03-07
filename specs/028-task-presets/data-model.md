# Data Model: Task Presets Catalog

## Overview

New relational tables live in the API service database; payload metadata lands inline with queued tasks to avoid worker changes.

## Tables

### `task_step_templates`
| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | UUID | PK | Stable template identity referenced by scopes + favorites. |
| `slug` | citext | Unique (per scope) | Human-readable template key (`pr-code-change`). |
| `scope_type` | enum(`global`,`team`,`personal`) | indexed | Drives RBAC enforcement + listing filters. |
| `scope_ref` | UUID/Text | nullable | Team ID or User ID depending on scope; null for global. |
| `title` | text | not null | Display name. |
| `description` | text | not null | Markdown summary. |
| `tags` | text[] | default `{}` | Search facets. |
| `latest_version_id` | UUID | FK → `task_step_template_versions.id` | Convenience pointer for listing. |
| `required_capabilities` | text[] | default `{}` | Capabilities aggregated across latest version. |
| `is_active` | bool | default true | Soft delete flag. |
| `created_by` | UUID | FK → `user.id` | Owner for audit. |
| `created_at` | timestamptz | default now | Audit. |
| `updated_at` | timestamptz | default now | Audit. |

### `task_step_template_versions`
| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | UUID | PK | Immutable version ID. |
| `template_id` | UUID | FK → `task_step_templates.id` | Parent relation. |
| `version` | text | unique per template | SemVer string. |
| `inputs_schema` | jsonb | not null | Array of `{name,label,type,required,default,options}`. |
| `steps` | jsonb | not null | Ordered blueprint of step definitions. |
| `annotations` | jsonb | optional | UI metadata (collapse groups, icons). |
| `required_capabilities` | text[] | default `{}` | Derived from embedded skills. |
| `max_step_count` | integer | default 25 | Guardrail per version. |
| `release_status` | enum(`draft`,`active`,`inactive`) | default `draft` | Controls availability. |
| `reviewed_by` | UUID | nullable | Reviewer for team/global scopes. |
| `reviewed_at` | timestamptz | nullable | Audit timestamp. |
| `notes` | text | nullable | Release notes / warnings. |
| `seed_source` | text | nullable | Path to YAML used during seeding (for defaults). |

### `task_step_template_favorites`
| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | bigserial | PK | |
| `user_id` | UUID | FK → `user.id` | Owner of favorite. |
| `template_id` | UUID | FK → `task_step_templates.id` | Favorited template. |
| `created_at` | timestamptz | default now | Sorting / analytics. |

### `task_step_template_recents`
| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | bigserial | PK | |
| `user_id` | UUID | FK → `user.id` | Author applying template. |
| `template_version_id` | UUID | FK → `task_step_template_versions.id` | Version used. |
| `applied_at` | timestamptz | default now | Rolling window (server prunes to last 5 per user). |

## JSON payload extensions

- `task.appliedStepTemplates`: JSON array stored alongside queue payloads. Each entry: `{ "slug": str, "version": str, "inputs": dict, "appliedAt": iso8601 }`. Populated by API before enqueueing.
- `task.requiredCapabilities`: union of existing task-level capabilities + `required_capabilities` on each expanded step. Compiler merges automatically.

## Seed files

- YAML definitions under `api_service/data/task_step_templates/*.yaml` convert into ORM objects on migration. Loader enforces `version="1.0.0"` for seeds and marks them `global` scope.

## Relationships

- `task_step_templates` 1→N `task_step_template_versions`.
- `task_step_template_versions` 1→N `task_step_template_recents`.
- `task_step_templates` 1→N `task_step_template_favorites`.
- Cascade deletes disabled; use soft-delete + `is_active` so history remains.
