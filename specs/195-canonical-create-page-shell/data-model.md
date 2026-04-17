# Data Model: Canonical Create Page Shell

## Create Page Shell

Represents the browser-visible task authoring surface served at `/tasks/new`.

Fields:
- `route`: canonical page path. Must be `/tasks/new`.
- `page`: boot payload page id. Must be `task-create`.
- `runtimeConfig`: server-generated endpoint and feature configuration.
- `sections`: ordered collection of canonical section identifiers.

Validation rules:
- `route` must remain `/tasks/new`.
- Compatibility routes may redirect to `/tasks/new` but must not produce their own shell state.
- `runtimeConfig` must come from the server boot payload.

## Canonical Section

Represents a stable Create page form region.

Fields:
- `name`: one of Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
- `order`: one-based section position.
- `available`: whether the section is currently present for the page mode/runtime configuration.

Validation rules:
- Create mode must expose Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit in that order when task presets are enabled.
- Optional integration content inside a section must not prevent the base manual authoring surface from rendering.
- Edit and rerun mode may hide creation-only schedule controls while preserving the same composition surface.

## Runtime Boot Payload

Represents server-provided page configuration consumed by the React entrypoint.

Fields:
- `page`: Mission Control page id.
- `initialData.dashboardConfig.sources`: MoonMind REST endpoints.
- `initialData.dashboardConfig.system`: runtime defaults and optional feature config.
- `initialData.dashboardConfig.features`: enabled product capabilities.

Validation rules:
- The Create page boot payload must identify the `task-create` page.
- Browser code must use configured MoonMind REST endpoints for page actions.
- Missing optional Jira or attachment settings must not block manual task authoring.
