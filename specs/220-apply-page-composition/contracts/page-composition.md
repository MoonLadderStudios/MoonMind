# UI Contract: Page-Specific Task Workflow Composition

## Scope

This contract describes the observable UI composition required by MM-428 for task workflow pages. It does not change backend APIs or submission payloads.

## `/tasks/list`

- The route exposes `.task-list-control-deck.panel--controls` above `.task-list-data-slab.panel--data`.
- Active filters render as compact chips in the control deck.
- Page-size controls and pagination are visually attached to the data slab.
- Desktop table headers remain sticky inside the data slab.

## `/tasks/new`

- The route exposes `[data-canonical-create-section="Steps"]` as the matte/satin guided workflow body.
- Step authoring sections remain inside the Steps section and do not become nested cards.
- The route exposes exactly one `.queue-floating-bar.queue-floating-bar--liquid-glass.queue-step-submit-actions` in `[data-canonical-create-section="Submit"]`.
- The floating bar contains repository, branch, publish, and primary launch/commit action controls.
- Large task instruction textareas remain matte/readable and outside the floating glass treatment.

## Task Detail / Evidence Pages

- The route exposes a `.task-detail-page` root.
- Summary appears in `.td-summary-block`.
- Fact rails are grouped in `.td-facts-region`.
- Step/evidence, artifacts, timeline, observation/log, session continuity, and action surfaces use explicit task-detail composition classes.
- Dense evidence/log/table regions use matte/data styling and do not use `.panel--floating`, `.queue-floating-bar`, or liquid glass hero classes.

## Non-Regression

- Task-list fetch/query behavior, task submission payloads, task detail actions, evidence/log visibility, and navigation remain unchanged.
