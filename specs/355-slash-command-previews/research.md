# Phase 0 Research: Provider-Neutral Slash Command Previews

## Planning Setup

Decision: Use `.specify/feature.json` and local templates instead of the setup script.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch `run-jira-orchestrate-for-mm-685-show-pro-4537c34f` is not named like a numeric MoonSpec feature branch; `.specify/feature.json` points to `specs/355-slash-command-previews`.
Rationale: The active feature directory and valid single-story spec already exist, so planning can continue without creating a branch or another feature directory.
Alternatives considered: Renaming or switching branches was rejected because this managed step is limited to planning artifacts.
Test implications: None beyond final artifact verification.

## Runtime Command Backend Baseline

Decision: Treat backend runtime command normalization as partial existing support and keep it authoritative at submit time.
Evidence: `moonmind/workflows/tasks/task_contract.py` builds `runtimeCommand` metadata for objective and step instructions, supports escaped literals, path-like malformed literals, hinted commands, opaque unknown commands, and unsupported runtime recognition modes. `tests/unit/workflows/tasks/test_task_contract.py` covers task-level `/review`, step-level `/simplify`, unknown `/future-command`, escaped `\/review`, path-like `/src/app.ts`, unsupported runtime, and leading whitespace.
Rationale: The Create page preview should align with backend semantics, but backend coverage does not satisfy the user-facing preview story by itself.
Alternatives considered: Moving all preview decisions to the backend on every keystroke was rejected because previews must update immediately without network calls.
Test implications: Unit tests should continue to cover backend normalization; new frontend tests should prove preview parity for user-visible states.

## Browser Capability And Hint Metadata

Decision: Add browser-safe runtime command preview metadata to the existing dashboard boot payload.
Evidence: `api_service/api/routers/task_dashboard_view_model.py` currently exposes `supportedTaskRuntimes`, default runtime/model/effort, provider profiles, and task template routes, but no slash-command capability or hint catalog. `frontend/src/entrypoints/task-create.tsx` reads dashboard config for runtime behavior.
Rationale: The spec requires declarative runtime capability and hint data, and the Create page must not hard-code provider-specific command markup. The boot payload is the established path for Create page runtime metadata.
Alternatives considered: Hard-coding slash-capable runtimes in `task-create.tsx` was rejected because it violates provider-neutral declarative behavior. Adding a new per-keystroke endpoint was rejected because preview should be local and responsive.
Test implications: Add Python unit or integration coverage for boot payload shape, plus Vitest coverage proving the Create page consumes the metadata.

## Create Page Preview Surface

Decision: Add local preview derivation and rendering to objective and step instruction controls in `frontend/src/entrypoints/task-create.tsx`.
Evidence: The Create page stores `runtime`, `steps`, objective text, and runtime-derived defaults, and submits task/step instructions. Repository search found no frontend `runtimeCommand`, `recognitionMode`, slash-command preview, `/review`, or escaped-slash preview implementation.
Rationale: The story is explicitly about the Create page before submission. Existing backend normalization cannot provide immediate user feedback while composing.
Alternatives considered: Waiting until submit validation was rejected because the acceptance criteria require preview before submission.
Test implications: Add Vitest/Testing Library tests for objective and step previews, runtime switching, and exact instruction preservation.

## Edit And Rerun Preview Restoration

Decision: Extend Temporal task editing draft reconstruction to preserve stored `runtimeCommand` metadata for preview-only use.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` reconstructs instructions, runtime, steps, attachments, templates, and related task state, but the draft types and `draftStepFrom()` do not expose `runtimeCommand`. Edit tests cover instruction reconstruction but not runtime command metadata.
Rationale: The spec requires edit mode to restore stored metadata when present and re-detect only for preview when absent. Carrying metadata through the draft avoids losing authoritative historical interpretation.
Alternatives considered: Always re-detecting in edit mode was rejected because runtime capabilities and hints may have changed since the original run.
Test implications: Add helper tests for draft reconstruction with objective and step `runtimeCommand` metadata, plus Create page edit-mode rendering tests.

## Requirement FR-001

Decision: Status `missing`; add objective and step preview state.
Evidence: `task-create.tsx` contains objective/step instruction controls and runtime state, but no user-visible runtime command preview implementation.
Rationale: Users cannot currently see whether slash-leading text will execute as a runtime command.
Alternatives considered: Relying on backend task snapshot metadata was rejected because it is submit-time only.
Test implications: Unit and integration tests.

## Requirement FR-002

Decision: Status `missing`; expose and consume runtime capability and hint data.
Evidence: Dashboard boot config lacks slash command preview metadata; backend has private capability/hint constants.
Rationale: Preview labels must be declarative and provider-neutral.
Alternatives considered: UI-local runtime allowlist rejected as hard-coded provider logic.
Test implications: Unit and integration tests.

## Requirement FR-003

Decision: Status `missing`; add unknown valid command pass-through preview.
Evidence: Backend tests prove unknown commands are opaque pass-through, but frontend has no preview.
Rationale: Missing hints must not become warning or error states.
Alternatives considered: Requiring known hints for preview rejected by source design.
Test implications: Unit tests.

## Requirement FR-004

Decision: Status `missing`; recompute previews on runtime changes without text mutation.
Evidence: Runtime changes currently update model/profile defaults, but no runtime command preview depends on runtime.
Rationale: Unsupported-runtime warnings and pass-through status depend on selected runtime.
Alternatives considered: Recomputing only on submit rejected by acceptance criteria.
Test implications: Unit tests.

## Requirement FR-005

Decision: Status `missing`; add escaped literal preview.
Evidence: Backend has escaped metadata; frontend has no escaped-literal UI.
Rationale: Users need a visible distinction between command execution and literal text.
Alternatives considered: Showing no preview for escaped text was rejected because the spec requires literal text intent representation.
Test implications: Unit tests.

## Requirement FR-006

Decision: Status `missing`; distinguish whitespace-prefixed and inline slash text.
Evidence: Backend preserves leading-whitespace slash as ordinary instructions; frontend has no preview classifier.
Rationale: Preview must avoid misleading users into thinking non-leading slash text is executable.
Alternatives considered: Trimming before preview rejected because it would change semantics.
Test implications: Unit tests.

## Requirement FR-007

Decision: Status `missing`; distinguish path-like or malformed slash input.
Evidence: Backend marks path-like input as malformed literal; frontend has no equivalent display.
Rationale: File paths should not look like runtime commands.
Alternatives considered: Treating all slash-leading text as command rejected by source design.
Test implications: Unit tests.

## Requirement FR-008

Decision: Status `partial`; preserve provider-neutral UI while adding declarative preview behavior.
Evidence: No provider-specific command markup appears in Create page slash preview code because preview code does not exist; runtime-specific logic is absent rather than satisfied.
Rationale: The implementation must add behavior without introducing provider-specific rendering decisions.
Alternatives considered: Direct Codex/Claude preview branches rejected.
Test implications: Unit and integration tests.

## Requirement FR-009

Decision: Status `partial`; edit mode restores instructions but drops runtime command metadata.
Evidence: `buildTemporalSubmissionDraftFromExecution()` reconstructs instructions and steps from snapshots, but draft types do not carry `runtimeCommand`.
Rationale: Edit preview should prefer stored metadata to avoid drift from changed capability or hint catalogs.
Alternatives considered: Always re-detecting from restored instructions rejected by source design.
Test implications: Unit and integration tests.

## Requirement FR-010

Decision: Status `missing`; add frontend preview test matrix.
Evidence: Existing tests cover backend normalization, not Create page preview states.
Rationale: The spec requires explicit frontend coverage for the user-facing matrix.
Alternatives considered: Counting backend tests as sufficient rejected because UI behavior is the story.
Test implications: Unit tests.

## Requirement FR-011

Decision: Status `implemented_unverified`; preserve traceability through downstream work.
Evidence: `specs/355-slash-command-previews/spec.md` preserves `MM-685` and the original Jira preset brief. This plan also references `MM-685`.
Rationale: Final verification has not happened yet, so traceability remains a planned verification item.
Alternatives considered: Marking verified now rejected because final implementation artifacts do not exist.
Test implications: Final verify.
