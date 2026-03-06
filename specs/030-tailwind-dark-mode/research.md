# Research: Tailwind Style System Phase 3 Dark Mode

## Decision 1: Theme preference precedence and persistence
- **Decision**: Use a three-state preference model (`light`, `dark`, `unset`) where explicit user choice in local storage always overrides system preference.
- **Rationale**: Matches source contract requirements for user-first precedence and predictable behavior across reloads and routes.
- **Alternatives Considered**:
  - System preference always wins: violates explicit user-control requirement.
  - Server-side preference storage: out of scope and unnecessary for dashboard-only Phase 3 behavior.

## Decision 2: No-flash boot strategy
- **Decision**: Add a small inline bootstrap script in `task_dashboard.html` `<head>` to resolve and apply theme class before dashboard stylesheet rendering.
- **Rationale**: Prevents first-paint light/dark flash and directly satisfies Phase 3 no-flash requirement.
- **Alternatives Considered**:
  - Applying theme only in deferred `dashboard.js`: too late; causes visible flash.
  - Server-rendered theme class: adds backend coupling and user-state transport that are not currently needed.

## Decision 3: Runtime sync with system preference
- **Decision**: Subscribe to `matchMedia("(prefers-color-scheme: dark)")` change events only when user preference is unset.
- **Rationale**: Preserves expected system-follow behavior while respecting user override once selected.
- **Alternatives Considered**:
  - Ignore runtime system changes: fails source requirement for no-preference sessions.
  - Always mirror system changes: breaks user override precedence.

## Decision 4: Dark token design and accent hierarchy
- **Decision**: Implement `.dark` token overrides in `dashboard.tailwind.css` using purple as primary accent, cyan for live/runtime signals, and restrained yellow/orange for warning/high-attention emphasis.
- **Rationale**: Aligns with the updated style-system contract and keeps the dashboard futuristic but operationally readable.
- **Alternatives Considered**:
  - Reuse light tokens with a dark background only: insufficient contrast and weak hierarchy.
  - Heavy neon glow across all components: degrades readability and adds visual noise.

## Decision 5: Readability preservation strategy
- **Decision**: Audit and tune dark-mode styles for tables, forms, and live output surfaces using token-driven colors instead of introducing utility-class rewrites.
- **Rationale**: Token-driven updates minimize churn and preserve semantic class contracts used by dashboard JS rendering.
- **Alternatives Considered**:
  - Rewrite markup to utility-heavy class output: large scope increase with higher regression risk.
  - Darken backgrounds without foreground/border updates: likely readability regressions.

## Decision 6: Validation approach
- **Decision**: Validate with deterministic CSS rebuild + `./tools/test_unit.sh` + manual route-level theme checks covering persistence, no-flash first paint, system-follow behavior (unset mode), and accent/readability gates.
- **Rationale**: Existing unit tests cover shell routing while manual checks are necessary for visual/theme behavior not presently automated.
- **Alternatives Considered**:
  - Skip manual visual QA: cannot verify no-flash/readability/accent-hierarchy requirements.
  - Add comprehensive screenshot automation in this phase: valuable later but out of current implementation scope.
