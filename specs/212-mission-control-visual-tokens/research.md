# Research: Mission Control Visual Tokens and Atmosphere

## Decision 1: Treat MM-424 as a runtime design-system foundation story

Decision: Implement token and atmosphere changes in `frontend/src/styles/mission-control.css`, not as docs-only work.

Rationale: The trusted Jira preset brief asks to establish visual tokens and atmosphere from `docs/UI/MissionControlDesignSystem.md`. The design document already describes the desired-state design direction. Before MM-424, the runtime stylesheet embedded several atmospheric and glass values directly; a CSS token contract makes the design system operational.

Alternatives considered: Updating only canonical docs was rejected because the design-system doc is already present and the story needs runtime evidence. Adding a new token build system was rejected because CSS custom properties already exist and are sufficient.

## Decision 2: Add semantic CSS custom properties for atmosphere and glass

Decision: Add named `--mm-atmosphere-*`, `--mm-glass-*`, `--mm-input-*`, and `--mm-elevation-*` tokens in both `:root` and `.dark`.

Rationale: Core tokens (`--mm-bg`, `--mm-panel`, `--mm-ink`, and status colors) are stable. Before MM-424, the body gradient and glass shells encoded alpha/elevation choices inline. Named tokens let surfaces consume the same visual language while preserving flexible alpha composition.

Alternatives considered: Creating route-specific variables was rejected because MM-424 is a shared Mission Control foundation story. Replacing core semantic tokens was rejected because existing components already depend on them.

## Decision 3: Keep atmosphere balanced across violet, cyan, and warm accents

Decision: Use one violet identity layer, one cyan energy layer, and one warm horizon layer for both light and dark themes.

Rationale: The canonical design system describes Mission Control as structured, glassy, and atmospheric. Balanced accent layers preserve the product identity without turning the UI into a single-hue palette.

Alternatives considered: A purple-only gradient was rejected because it reduces scan hierarchy and makes state colors less meaningful. A flat neutral background was rejected because it does not satisfy the atmosphere part of the story.

## Decision 4: Validate through CSS contract tests plus shared app-shell regression tests

Decision: Add focused assertions in `frontend/src/entrypoints/mission-control.test.tsx` that read the stylesheet and verify the token contract, body gradient usage, chrome consumption, and existing app shell behavior.

Rationale: This story has no new API or state behavior. CSS contract tests are precise for token presence and usage, while existing render tests prove route loading and dashboard alerts still work.

Alternatives considered: Browser screenshot tests were rejected for this foundation story because the repo's current Mission Control coverage is Vitest-based and the requested deliverable is the token contract. Compose-backed integration was rejected because no server/runtime behavior changes.
