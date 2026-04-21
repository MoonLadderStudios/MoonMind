# Contract: Mission Control Shared Interaction Language

This contract defines the MM-427 shared CSS interaction behavior for Mission Control routine controls.

## Interaction Tokens

The shared stylesheet exposes:

- `--mm-control-hover-scale`
- `--mm-control-press-scale`
- `--mm-control-transition`
- `--mm-control-focus-ring`
- `--mm-control-disabled-opacity`
- `--mm-control-shell`
- `--mm-control-shell-hover`
- `--mm-control-border`

## Routine Control Rules

Routine controls include:

- `button`
- `.button`
- `.queue-action`
- `.queue-submit-primary`
- `.queue-step-icon-button`
- `.queue-step-extension-button`
- `.queue-inline-toggle`
- `.queue-inline-filter`
- `.task-list-filter-chip`

Expected behavior:

- Hover uses `scale(var(--mm-control-hover-scale))`.
- Press/active uses `scale(var(--mm-control-press-scale))`.
- Routine hover and press rules do not use `translateY`.
- Focus-visible uses `var(--mm-control-focus-ring)`.
- Disabled rules use `var(--mm-control-disabled-opacity)` and suppress transform, filter, and glow.
- Compact controls and filter chips use the shared control shell and border tokens.

## Non-Goals

- No backend contract change.
- No new JavaScript interaction runtime.
- No route, fetch, pagination, submission, or Jira Orchestrate behavior change.
