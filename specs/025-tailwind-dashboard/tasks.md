# Tasks: Tailwind Style System Phase 2

**Input**: Design documents from `/specs/025-tailwind-dashboard/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the Tailwind/PostCSS toolchain required to compile the dashboard stylesheet deterministically.

- [X] T001 Update `package.json` to add Tailwind/PostCSS devDependencies and scripts (`dashboard:css`, `dashboard:css:min`, `dashboard:css:watch`).
- [ ] T002 Run `npm install` to produce `package-lock.json` capturing Tailwind/PostCSS versions (blocked until npm CLI is available in the sandbox).
- [X] T003 Add `tailwind.config.cjs` configured for `task_dashboard.html` and `dashboard.js` content scanning with `darkMode: "class"` and tokenized color extensions.
- [X] T004 Add `postcss.config.cjs` wiring Tailwind and autoprefixer for the dashboard build.
- [X] T005 [P] Add optional helper script `tools/build-dashboard-css.sh` (bash) that runs the minified build to aid CI and WSL workflows.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the editable Tailwind source file and migrate existing CSS so user stories can focus on brand/token changes.

- [X] T006 Create `api_service/static/task_dashboard/dashboard.tailwind.css` with `@tailwind` directives and copy the current `dashboard.css` content as the starting point.
- [X] T007 Update `.gitignore` / contributor docs if necessary to flag `dashboard.css` as generated (do not hand-edit) while keeping it committed for serving.
- [ ] T008 Ensure build scripts (`npm run dashboard:css`) regenerate `dashboard.css` identically before any palette edits (blocked until npm CLI exists).

---

## Phase 3: User Story 1 - Operators see branded UI (Priority: P1) 🎯 MVP

**Goal**: Replace the dashboard’s blue/amber palette with the MoonMind purple/cyan gradients and ensure views render identically aside from color changes.

**Independent Test**: Run `npm run dashboard:css:min`, load `/tasks`, `/tasks/queue`, `/tasks/orchestrator`, and confirm panels/nav/status chips show the updated palette with no layout regressions.

### Implementation for User Story 1

- [X] T009 [US1] Replace the `:root` section in `dashboard.tailwind.css` with the documented `--mm-*` RGB tokens (bg, panel, ink, muted, border, accent family, status, shadow).
- [X] T010 [US1] Update `body` background gradients in `dashboard.tailwind.css` to the purple/cyan/pink radial stack described in `docs/TailwindStyleSystem.md`.
- [ ] T011 [US1] Regenerate `api_service/static/task_dashboard/dashboard.css` via `npm run dashboard:css:min` and verify diffs only reflect token + gradient changes (blocked until npm CLI exists; CSS updated manually in the interim).
- [ ] T012 [US1] Store before/after screenshots under `docs/assets/task_dashboard/phase2/` and link them from `docs/TailwindStyleSystem.md` to prove readability post-migration.

---

## Phase 4: User Story 2 - Developers manage tokens centrally (Priority: P1)

**Goal**: Ensure every semantic component references the new tokens so future Tailwind utilities and dark mode inherit consistent colors.

**Independent Test**: Searching for legacy variables (`--bg`, `--accent`, etc.) in `dashboard.tailwind.css` returns none; toggling a token via DevTools updates all dependent components.

### Implementation for User Story 2

- [X] T013 [US2] Update panels, cards, masthead, nav pills, and buttons in `dashboard.tailwind.css` to use `rgb(var(--mm-*) / alpha)` plus the shared `--mm-shadow` and radius tokens.
- [X] T014 [US2] Convert status chip classes (`.status-*`) to the new status tokens with translucent fills and borders that match the spec’s brand mapping.
- [X] T015 [US2] Document the token usage in `contracts/theme-tokens.md` and ensure any new tokens or values are reflected there.
- [X] T016 [US2] Smoke-test `dashboard.js` semantic class usage in the browser to confirm no class renames are required (only CSS variable changes).

---

## Phase 5: User Story 3 - Build + docs alignment (Priority: P2)

**Goal**: Keep the canonical documentation and validation steps in sync with the implemented palette to support future Tailwind phases.

**Independent Test**: Comparing `docs/TailwindStyleSystem.md` values with the final CSS tokens shows exact matches; quickstart instructions describe the actual commands used.

### Implementation for User Story 3

- [X] T017 [US3] Update `docs/TailwindStyleSystem.md` with the final token table, gradient settings, and links to the new screenshot assets.
- [X] T018 [US3] Refresh `quickstart.md` within the spec folder to include the precise build/test steps executed after tokenization (done in Phase 1/2, verify nothing drifted).
- [X] T019 [US3] Add a Phase 2 release note section to `docs/TaskUiArchitecture.md` (or the repo changelog) summarizing the purple rebrand for stakeholders.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation tasks ensuring ready-to-merge quality.

- [ ] T020 Run `npm run dashboard:css:min` followed by `git diff --stat api_service/static/task_dashboard/dashboard.css` to ensure deterministic output (blocked until npm CLI exists).
- [ ] T021 Execute `./tools/test_unit.sh` to protect FastAPI router/template integrations (in-progress; command currently running in CI-less sandbox).
- [ ] T022 [P] Perform manual visual QA across Chromium + Firefox and append contrast notes to `docs/TailwindStyleSystem.md#Validation Checklist`.
- [X] T023 Add Phase 2 troubleshooting insights to `docs/TailwindStyleSystem.md#15` based on any issues hit during implementation.
- [X] T024 Draft the final PR summary + checklist in `specs/025-tailwind-dashboard/pr-summary.md`, referencing screenshots, tests, and doc alignment.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → User Stories → Polish. Tailwind tooling must exist before migrating CSS, and CSS must be tokenized before docs/tests finalize.

### User Story Dependencies

- US1 is the MVP (visual palette) and must complete before US2 token refactors (since US2 builds on tokens).
- US2 must complete before US3 documentation updates to avoid stale instructions.

### Parallel Opportunities

- T001–T005 can be split across contributors (package.json vs config vs helper script).
- Once `dashboard.tailwind.css` exists (T006), US1 and US2 CSS edits can proceed in parallel so long as merge conflicts are managed.
- Validation tasks T020–T024 can run concurrently with documentation polish once the CSS diff stabilizes.

## Implementation Strategy

1. Land Phase 1 + Phase 2 to ensure deterministic builds.
2. Deliver US1 palette updates and validate readability (screenshots/tests) — this is the MVP slice.
3. Immediately follow with US2 token sweeps to guarantee maintainability.
4. Close with US3 documentation + release notes before running the final validation suite.
