# Tasks: Create Page Composed Preset Drafts

**Input**: `specs/197-create-page-composed-drafts/spec.md`  
**Plan**: `specs/197-create-page-composed-drafts/plan.md`  
**Canonical Jira Input**: `docs/tmp/jira-orchestration-inputs/MM-384-moonspec-orchestration-input.md`  
**Unit test command**: `rg -n "AppliedPresetBinding|StepDraft.source|preset-bound|grouped composition|flat reconstruction|Reapply preset|save-as-preset|flatten" docs/UI/CreatePage.md`  
**Integration test command**: `! rg -n "template-bound|appliedTemplates|AppliedTemplateState" docs/UI/CreatePage.md`  
**Final verification**: `/moonspec-verify`

## Source Traceability

The original MM-384 Jira preset brief is preserved in `docs/tmp/jira-orchestration-inputs/MM-384-moonspec-orchestration-input.md` and in `specs/197-create-page-composed-drafts/spec.md`. Tasks cover FR-001 through FR-013, SC-001 through SC-004, and DESIGN-REQ-010 through DESIGN-REQ-016, DESIGN-REQ-025, and DESIGN-REQ-026.

## Phase 1: Setup

- [X] T001 Confirm MM-384 input classification and active feature directory in `specs/197-create-page-composed-drafts/spec.md` and `.specify/feature.json` (FR-013, SC-001)
- [X] T002 Confirm source context availability for `docs/UI/CreatePage.md`, `docs/Tasks/TaskPresetsSystem.md`, and missing `docs/Tasks/PresetComposability.md` assumption in `specs/197-create-page-composed-drafts/research.md` (DESIGN-REQ-010, DESIGN-REQ-011)

## Phase 2: Foundational

- [X] T003 Verify the existing Create page terminology baseline for required composed preset terms and legacy template-bound terms in `docs/UI/CreatePage.md` (FR-002, FR-003, FR-010)
- [X] T004 Verify Task Presets source context for includes, expansion tree, flattened plan, provenance, detachment, and save-as-preset behavior in `docs/Tasks/TaskPresetsSystem.md` (DESIGN-REQ-010, DESIGN-REQ-016)

## Phase 3: Story - Composed Preset Draft Authoring

**Story Summary**: The Create page contract preserves composed preset authoring state while runtime execution remains flattened.

**Independent Test**: Review the Create page contract and validation evidence for composed preset draft behavior covering preview, apply, detachment, reapply, save-as-preset, edit/rerun reconstruction, and degraded fallback.

**Traceability IDs**: FR-001 through FR-013, DESIGN-REQ-010 through DESIGN-REQ-016, DESIGN-REQ-025, DESIGN-REQ-026, SC-001 through SC-004.

### Tests First

- [X] T005 Add red-first documentation-contract check for required composed preset terms against `docs/UI/CreatePage.md` using `rg -n "AppliedPresetBinding|StepDraft.source|preset-bound|grouped composition|flat reconstruction|Reapply preset|save-as-preset|flatten" docs/UI/CreatePage.md` (FR-002, FR-003, FR-006, FR-008, SC-002)
- [X] T006 Add red-first terminology cleanup check against `docs/UI/CreatePage.md` using `! rg -n "template-bound|appliedTemplates|AppliedTemplateState" docs/UI/CreatePage.md` (FR-010, SC-003)

### Implementation

- [X] T007 Update the Create page draft model in `docs/UI/CreatePage.md` to define `AppliedPresetBinding` and `StepDraft.source` with binding metadata, include path, blueprint slug, detachment, expansion digest, and flat reconstruction state (FR-002, FR-003, DESIGN-REQ-012)
- [X] T008 Update the Create page preset contract in `docs/UI/CreatePage.md` to describe composed preset authoring objects, grouped preview, server-expanded apply, binding metadata, flat steps, per-step provenance, and non-mutating selection (FR-001, FR-004, FR-005, DESIGN-REQ-011, DESIGN-REQ-014)
- [X] T009 Update reapply and detachment behavior in `docs/UI/CreatePage.md` so still-bound steps update by default, detached steps remain untouched, and the user sees the exact effect before proceeding (FR-006, FR-007, DESIGN-REQ-015)
- [X] T010 Update save-as-preset behavior in `docs/UI/CreatePage.md` to preserve intact composition by default and require explicit advanced flattening before save (FR-008, DESIGN-REQ-016)
- [X] T011 Update edit/rerun reconstruction and failure behavior in `docs/UI/CreatePage.md` to preserve binding state when possible and clearly warn for flat-only reconstruction or unavailable metadata (FR-011, DESIGN-REQ-025)
- [X] T012 Replace legacy template-bound terminology for composed preset draft state in `docs/UI/CreatePage.md` with preset-bound terminology (FR-010, DESIGN-REQ-013)
- [X] T013 Update Create page testing requirements in `docs/UI/CreatePage.md` to cover preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback (FR-012, DESIGN-REQ-026)

### Story Validation

- [X] T014 Run required-term documentation-contract check for `docs/UI/CreatePage.md` and record PASS/FAIL in final verification evidence (FR-002, FR-003, SC-002)
- [X] T015 Run legacy terminology cleanup check for `docs/UI/CreatePage.md` and record PASS/FAIL in final verification evidence (FR-010, SC-003)
- [X] T016 Confirm MM-384 traceability across `docs/tmp/jira-orchestration-inputs/MM-384-moonspec-orchestration-input.md`, `specs/197-create-page-composed-drafts/spec.md`, and `specs/197-create-page-composed-drafts/tasks.md` (FR-013, SC-001)

## Phase 4: Polish

- [X] T017 Run MoonSpec artifact alignment review and write `specs/197-create-page-composed-drafts/speckit_analyze_report.md` (SC-001)
- [X] T018 Review `git diff -- docs/UI/CreatePage.md specs/197-create-page-composed-drafts docs/tmp/jira-orchestration-inputs/MM-384-moonspec-orchestration-input.md .specify/feature.json` for unrelated changes (SC-004)

## Phase 5: Final Verification

- [X] T019 Run `/moonspec-verify` equivalent against `specs/197-create-page-composed-drafts/spec.md` and record verification in `specs/197-create-page-composed-drafts/verification.md` (FR-001 through FR-013)

## Dependencies and Execution Order

1. Complete setup and source verification tasks T001-T004.
2. Run red-first validation checks T005-T006 before editing `docs/UI/CreatePage.md`.
3. Complete implementation tasks T007-T013.
4. Run validation tasks T014-T016.
5. Complete alignment, diff review, and final verification tasks T017-T019.

## Parallel Examples

- T001 and T002 can run in parallel because they inspect different artifacts.
- T003 and T004 can run in parallel after setup because they inspect different docs.
- Implementation tasks T007-T013 all touch `docs/UI/CreatePage.md` and must run sequentially.

## Implementation Strategy

Keep the implementation focused on the single Create page composed preset draft contract. Preserve flattened runtime execution semantics, MM-384 traceability, and canonical desired-state documentation style. Do not introduce executable UI code changes unless the documentation contract cannot be satisfied without them.
