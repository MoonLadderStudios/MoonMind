# Research: Submit Preset Auto-Expansion

## FR-001 / FR-002 / DESIGN-REQ-002 / DESIGN-REQ-009

Decision: Missing. Add submit-time expansion inside the explicit Create/Update/Rerun submit attempt before final task payload construction.
Evidence: `frontend/src/entrypoints/task-create.tsx` `handleSubmit` calls `validatePrimaryStepSubmission` before upload/submission, and `validatePrimaryStepSubmission` rejects a primary `preset` step with "Expand Preset steps before submitting." Additional steps with `stepType === "preset"` are also rejected during additional-step parsing.
Rationale: The current flow enforces executable-only submission but does not provide the new convenience path. The submit flow already has the correct location for guarded validation, attachment handling, payload shaping, artifact fallback, and create/update/rerun dispatch.
Alternatives considered: Expanding Presets on selection or descriptor load was rejected because submit must remain explicit and non-submit interactions must not create tasks.
Test implications: Unit and integration coverage for create/update/rerun submit attempts with unresolved Presets.

## FR-003 / FR-007

Decision: Implemented unverified. Existing interactions appear key-driven and non-submitting, but need explicit regression coverage for the new path.
Evidence: `loadPresetDetail` and `expandPresetForDraft` operate from selected `TemplateOption` values; current tests in `frontend/src/entrypoints/task-create.test.tsx` cover manual Preset selection and manual Expand without create side effects.
Rationale: Submit-time expansion must not infer Presets from objective text, hidden data, or non-submit interactions. Existing code structure supports this but tests should lock it.
Alternatives considered: A global "auto-detect Preset" submit helper was rejected because it would violate the source design.
Test implications: Unit tests should assert no create call is made by selection/detail/preview alone and that submit expansion uses the selected Preset key.

## FR-004 / FR-012 / FR-017 / DESIGN-REQ-008 / DESIGN-REQ-010

Decision: Partial overall. Authoritative task validation already rejects unresolved Presets and accepts executable Tool/Skill steps with provenance; submit-time frontend shaping is missing.
Evidence: `moonmind/workflows/tasks/task_contract.py` rejects `task.steps[].type` values outside `tool` and `skill`. `tests/unit/workflows/tasks/test_task_contract.py` includes `test_task_steps_reject_non_executable_step_types`, `test_task_steps_accept_explicit_tool_and_skill_discriminators`, and `test_task_steps_validate_without_resolving_source_provenance`.
Rationale: Runtime correctness is already protected at the task contract boundary. The implementation should preserve this backend gate and make the Create page submit an executable-only copy.
Alternatives considered: Allowing `preset` steps through backend validation was rejected because it would create linked live Preset execution semantics.
Test implications: Keep existing task-contract tests and add or update tests only if new payload fields are introduced.

## FR-005 / FR-011 / DESIGN-REQ-004

Decision: Partial. Manual Preview/Apply exists and is tested; submit-time path should reuse the same expansion and mapping semantics.
Evidence: `expandPresetForDraft`, `applyPresetExpansionToDraft`, and `mapExpandedStepToState` already support manual expansion into editable executable steps. `frontend/src/entrypoints/task-create.test.tsx` has "expands generated preset steps into editable executable steps" and "submits applied preset-generated Tool and Skill steps with executable binding and provenance".
Rationale: Reusing the same semantics reduces drift between manual and submit-time behavior. Manual Preview/Apply should remain unchanged.
Alternatives considered: Creating a separate submit-only generated-step compiler was rejected unless existing mapping cannot support frozen-copy use.
Test implications: Unit tests comparing submit-time generated payload shape with manual Apply output.

## FR-006 / DESIGN-REQ-005

Decision: Partial. Existing manual expansion sends selected slug/version, inputs, and context, but submit intent and full current task context are not represented.
Evidence: `expandPresetForDraft` posts to the task-step-template expansion endpoint with version, resolved inputs, repository/repo, and target runtime. The API endpoint `api_service/api/routers/task_step_templates.py` resolves scope and user permissions before calling `expand_template`.
Rationale: Submit-time expansion should use the same catalog authorization path, with explicit submit intent/options when needed by the expansion contract.
Alternatives considered: Browser-side version guessing was rejected. System resolution should occur through the existing catalog service.
Test implications: Unit tests should assert selected key/version/input/context are used and unavailable expansion blocks submission.

## FR-008 / DESIGN-REQ-006

Decision: Missing. Current manual Apply replaces a selected Preset step, but submit-time multi-Preset ordered replacement does not exist.
Evidence: `applyPresetExpansionToDraft` can replace a single local step in the visible draft. The submit path builds `normalizedSteps` from the current `steps` array after rejecting `preset` steps.
Rationale: The new story needs a frozen submission copy so multiple Presets can be expanded in authored order without mutating visible draft state.
Alternatives considered: Mutating the visible draft before submit was rejected because source design requires non-silent draft preservation.
Test implications: Unit test with three Presets, asserting generated steps are spliced in relative order.

## FR-009 / FR-010 / FR-018

Decision: Missing. No frozen submission-copy model or transient submit-expansion state exists.
Evidence: `StepState` has `presetMessage` but no submit expansion status. Manual Apply mutates `steps` directly. Edit/rerun reconstruction in `frontend/src/lib/temporalTaskEditing.ts` preserves explicit Preset draft state where snapshots contain it.
Rationale: Submit-time status needs to be local UI state and must not be serialized into task snapshots.
Alternatives considered: Reusing `presetMessage` only was rejected because duplicate/stale status and per-request feedback need clearer state.
Test implications: Unit tests for draft preservation after successful expansion plus failed final submission.

## FR-013 / FR-016 / DESIGN-REQ-007

Decision: Partial. Attachments, capabilities, and publish constraints exist, but submit-time Preset attachment retargeting and warning treatment need explicit handling.
Evidence: `createInputAttachmentArtifact` and `_validate_and_collect_task_input_attachments` handle structured attachment refs. `resolveEffectiveSkillId`, `deriveRequiredCapabilities`, and self-managed publish helpers handle existing applied-template and Skill constraints. Manual expansion surfaces warnings in tests.
Rationale: Submit-time expansion can only proceed automatically when generated attachment mappings and constraints are unambiguous.
Alternatives considered: Best-effort attachment retargeting was rejected because ambiguous attachment ownership could alter task inputs silently.
Test implications: Unit and integration tests for ambiguous attachment retargeting block and publish/merge constraint behavior.

## FR-014 / DESIGN-REQ-011

Decision: Partial. Manual expansion failure is non-mutating, but submit-time expansion failure currently falls back to the old unresolved-Preset block.
Evidence: Existing test "keeps drafts unchanged on expansion failure and blocks unresolved Preset submission" covers manual Expand failure and old submit block text.
Rationale: The new behavior should attempt expansion during submit and show returned validation/authorization/ambiguity errors on the relevant Preset step while preventing any create/update/rerun side effect.
Alternatives considered: Submitting remaining executable steps while skipping failed Presets was rejected because it would silently alter authored work.
Test implications: Integration-style frontend test should assert no `/api/executions` or edit/rerun update call is made after expansion failure.

## FR-015 / SC-004

Decision: Partial. `isSubmitting` guards the submit handler, but expansion-specific stale response handling is missing.
Evidence: `handleSubmit` exits early when `isSubmitting` is true and sets `isSubmitting` after validation. There is no submit expansion request id in current state.
Rationale: Expansion responses can arrive after cancellation/navigation or a newer submit attempt; stale responses must be ignored.
Alternatives considered: Relying only on button disabled state was rejected because asynchronous requests can still race.
Test implications: Unit tests for duplicate clicks during expansion and stale response ignored.

## FR-019 / SC-001 through SC-005

Decision: Partial. Existing coverage handles manual Apply and authoritative rejection. Missing coverage targets submit-time expansion.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` includes manual expansion, applied executable submission, and manual expansion failure. `tests/unit/workflows/tasks/test_task_contract.py` includes executable-step and non-executable-step validation.
Rationale: The feature changes a high-risk submission path and needs both frontend scenario tests and task-contract regression.
Alternatives considered: Relying only on manual Apply tests was rejected because submit-time expansion has distinct ordering, draft preservation, and duplicate/stale risks.
Test implications: Add focused Vitest tests first; keep pytest contract tests in the final unit suite.
