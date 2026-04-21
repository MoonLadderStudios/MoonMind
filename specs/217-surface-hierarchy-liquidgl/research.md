# Research: Surface Hierarchy and liquidGL Fallback Contract

## FR-001 / DESIGN-REQ-005 / DESIGN-REQ-015

Decision: Add stable semantic selectors for the full surface hierarchy: matte data, satin form, glass control, floating/utility glass, liquidGL hero, and accent/live.
Evidence: `frontend/src/styles/mission-control.css` already has `.panel--controls`, `.panel--data`, `.data-table-slab`, `.queue-floating-bar--liquid-glass`; `docs/UI/MissionControlDesignSystem.md` names the desired stable classes.
Rationale: Existing selectors partially express the hierarchy, but MM-425 requires the roles to be clearly distinguishable and reusable.
Alternatives considered: Per-route Tailwind-only styling was rejected because it would not create a shared contract.
Test implications: Unit CSS tests in `frontend/src/entrypoints/mission-control.test.tsx`.

## FR-002 / FR-003 / DESIGN-REQ-007

Decision: Use existing glass tokens as the default glass foundation and add shared fallback rules for glass/floating/liquid roles when backdrop filtering is unavailable.
Evidence: `:root` and `.dark` already define `--mm-glass-fill`, `--mm-glass-border`, `--mm-glass-edge`, and elevation tokens.
Rationale: Token-driven CSS glass must exist independently of liquidGL and must degrade to near-opaque surfaces.
Alternatives considered: JavaScript-based fallback detection was rejected because CSS feature queries are simpler and safer.
Test implications: CSS regex assertions for token use, blur/saturation, 1px border, and `@supports not` fallback.

## FR-004 / FR-007 / FR-008 / DESIGN-REQ-018

Decision: Preserve `.queue-floating-bar--liquid-glass` as the explicit bounded liquidGL target and strengthen its CSS fallback shell.
Evidence: `frontend/src/entrypoints/task-create.tsx` initializes liquidGL against `.queue-floating-bar--liquid-glass`; existing tests assert controls remain accessible.
Rationale: liquidGL should enhance one explicit elevated surface, not become the default styling system.
Alternatives considered: Applying liquidGL to `.panel` or `.card` was rejected because the source design forbids default/dense usage.
Test implications: Create page tests continue to cover accessible controls; Mission Control CSS tests assert liquidGL is opt-in.

## FR-005 / FR-006 / DESIGN-REQ-003 / DESIGN-REQ-008 / DESIGN-REQ-027

Decision: Keep dense data and editing surfaces near-opaque and add quieter nested dense-surface modifiers.
Evidence: `.panel--data`, `.data-table-slab`, `.queue-table-wrapper`, table styles, and input wells already favor opacity.
Rationale: Dense task data, logs, and form inputs should prioritize readability over translucency.
Alternatives considered: A single glass card style everywhere was rejected because it reduces scanability and violates the hierarchy.
Test implications: Existing task-list composition tests plus new CSS assertions for matte and satin/nested dense surfaces.

## Test Strategy

Decision: Use focused Vitest suites as the unit/integration-style evidence for this UI contract.
Evidence: `mission-control.test.tsx`, `task-create.test.tsx`, and `tasks-list.test.tsx` already read CSS and render representative UI surfaces.
Rationale: The story is a shared CSS/UI contract with no backend or persistence boundary.
Alternatives considered: Compose-backed integration tests were rejected as unnecessary for a frontend styling contract.
Test implications: Run focused UI tests first, then full `./tools/test_unit.sh` before completion when feasible.
