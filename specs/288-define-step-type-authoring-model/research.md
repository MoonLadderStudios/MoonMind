# Research: Define Step Type Authoring Model

## Request Classification

Decision: Classify MM-575 as one independently testable runtime story and do not run `moonspec-breakdown`.
Evidence: `artifacts/moonspec/MM-575-orchestration-input.md` contains one user story, one acceptance set, and one requirement cluster for explicit Step Type authoring; `specs/288-define-step-type-authoring-model/spec.md` preserves that brief.
Rationale: The Jira preset brief asks for one cohesive authoring model: explicit selected Step Type, matching visible controls, explicit incompatible-data handling, and consistent terminology.
Alternatives considered: Treating `docs/Steps/StepTypes.md` as a broad design was rejected because the Jira issue selects specific sections and coverage IDs for one story.
Test implications: No breakdown tests; proceed with one-spec UI integration and runtime contract validation.

## FR-001 Explicit Step Type State

Decision: implemented_verified.
Evidence: `frontend/src/entrypoints/task-create.tsx` defines `StepState.stepType`, initializes new steps with `stepType: "skill"`, and renders one `Step Type` radio group; `frontend/src/entrypoints/task-create-step-type.test.tsx` asserts Skill, Tool, and Preset options.
Rationale: Current code and tests together prove each authored step has exactly one selected Step Type in draft state.
Alternatives considered: Adding new state was rejected because it would duplicate the existing model.
Test implications: Focused UI integration and TypeScript type validation are sufficient.

## FR-002 Type-Specific Controls

Decision: implemented_verified.
Evidence: `frontend/src/entrypoints/task-create.tsx` conditionally renders Tool, Skill, and Preset panels based on `step.stepType`; the focused UI test switches Step Type and verifies visible controls.
Rationale: The UI already presents the correct configuration surface for the selected Step Type.
Alternatives considered: Broader Create page regression coverage was considered, but the focused Step Type test is the more direct integration boundary.
Test implications: Focused UI integration plus dashboard final verification.

## FR-003 Incompatible Data Handling

Decision: implemented_verified.
Evidence: `handleStepTypeChange` preserves shared step fields, clears incompatible Skill, Tool, and Preset fields, and sets `stepTypeMessage`; the focused UI test verifies preserved instructions and visible Skill discard feedback.
Rationale: The current behavior meets the explicit handling requirement without adding confirmation complexity.
Alternatives considered: Confirmation prompts were unnecessary because visible discard feedback satisfies the spec and keeps authoring flow simple.
Test implications: Focused UI integration.

## FR-004 Runtime Payload Validation

Decision: implemented_verified.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects non-executable Step Types (`preset`, `activity`, `Activity`) and mixed Tool/Skill payloads.
Rationale: Runtime payload validation prevents unresolved Preset steps and invalid mixed executable shapes from reaching execution.
Alternatives considered: Adding compatibility aliases was rejected by the project compatibility policy.
Test implications: Runtime contract unit test and final `./tools/test_unit.sh`.

## FR-005 Terminology Consistency

Decision: implemented_verified.
Evidence: `docs/Steps/StepTypes.md` defines Task, Step, Step Type, Tool, Skill, Preset, Expansion, Plan, and Activity; `spec.md`, `contracts/step-type-authoring-model.md`, and the Create page selector preserve those terms.
Rationale: The behavior and artifacts align to the canonical terminology.
Alternatives considered: No terminology rewrite is needed because canonical docs remain the desired-state source.
Test implications: Final MoonSpec verification plus focused UI label checks.

## FR-006 Umbrella Label Guardrail

Decision: implemented_verified.
Evidence: The Create page selector legend is `Step Type`; the focused UI test locates the group by `Step Type` and asserts Skill, Tool, and Preset options.
Rationale: Capability, Activity, invocation, command, and script are not used as the umbrella authoring label.
Alternatives considered: No code change is needed; adding new label indirection would increase drift risk.
Test implications: Focused UI integration and final verification.

## SC and Acceptance Coverage

Decision: implemented_verified for SC-001 through SC-005 and acceptance scenarios 1-5.
Evidence: `frontend/src/entrypoints/task-create-step-type.test.tsx` covers selector choices, control switching, instruction preservation, and visible discard feedback; `tests/unit/workflows/tasks/test_task_contract.py` covers executable payload rejection; `spec.md` and `verification.md` preserve MM-575 traceability.
Rationale: The direct UI and contract tests cover the observable story and runtime boundary.
Alternatives considered: Adding duplicate tests was rejected because current focused tests already exercise the required behavior.
Test implications: Run focused UI, runtime contract, TypeScript, and final unit verification.

## Design Artifact Strategy

Decision: Keep `data-model.md`, `contracts/step-type-authoring-model.md`, and `quickstart.md` because the story has draft-state data, a public authoring UI contract, and a runtime payload contract.
Evidence: The feature involves Step Draft, Executable Step Payload, and Preset Expansion concepts, plus UI and runtime validation surfaces.
Rationale: These artifacts are required by the active plan workflow for data, contracts, and executable validation guidance.
Alternatives considered: Skipping contracts was rejected because the story exposes user-facing UI behavior and execution payload validation.
Test implications: Quickstart includes focused UI, runtime contract, and type validation commands.
