# Contract: Mission Control Styling Source and Build Invariants

This contract defines the observable runtime and build-source invariants required by MM-430. It does not change backend APIs, task submission payloads, Temporal workflows, or Jira integration behavior.

## Semantic Shell Stability

- Existing shared Mission Control shell classes remain stable where their surfaces still exist:
  - `dashboard-root`
  - `masthead`
  - `route-nav`
  - `panel`
  - `card`
  - `toolbar`
  - `status-*`
  - compatible `queue-*` classes
- New shared surface variants extend the shell through additive semantic modifiers when useful.
- Additive modifier examples include `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, and equivalent table width modifiers such as `table-wrap--wide` or existing data-wide patterns.

## Token-First Theming

- Semantic Mission Control role styling uses `--mm-*` tokens for color, surface, border, shadow, control, and atmosphere roles.
- Light and dark themes preserve matching behavior by changing token values, not by scattering unrelated one-off overrides for the same surface role.
- Fixed or raw colors may exist only when they are not tokenized semantic roles, such as code/log contrast, transparency effects, or narrowly scoped status accents with explicit source-backed rationale.

## Source Scanning

Tailwind content scanning includes all source inputs that can contain Mission Control utility classes before Vite output exists:

- `./api_service/templates/react_dashboard.html`
- `./api_service/templates/_navigation.html`
- `./frontend/src/**/*.{js,jsx,ts,tsx}`

## Source Boundary

- Canonical Mission Control styling source remains `frontend/src/styles/mission-control.css`.
- Source templates/components remain under their existing source paths.
- Generated assets under `api_service/static/task_dashboard/dist/` are build outputs and are not hand-edited as the source of truth.
- Vite may regenerate dist assets through the build pipeline, but implementation work for this story must not directly patch dist files.

## Completion Criteria

The story is complete when tests and final verification show:

- MM-430 and the trusted Jira preset brief are preserved in artifacts.
- Semantic shell and modifier invariants are covered.
- Token-first role styling and light/dark token parity are covered.
- Tailwind scan paths are covered.
- Canonical source and generated dist boundaries are covered.
- Existing Mission Control workflows continue to pass focused regression tests.
