# Research: Compile Recursive Task Presets

## FR-001 / Preset Compilation Before Finalization

Decision: partial; implement missing authoritative submission/snapshot metadata after existing recursive expansion.
Evidence: `api_service/services/task_templates/catalog.py` recursively expands include steps in `_expand_version_steps()` and returns flattened `steps`; `frontend/src/entrypoints/task-create.tsx` auto-expands unresolved preset steps before create submission.
Rationale: Expansion exists, but the submitted task contract needs to clearly carry compiled include-tree metadata as the final execution contract, not just live expansion output.
Alternatives considered: Rely only on `appliedStepTemplates` was rejected because it currently records root template metadata and step IDs, not the recursive include tree needed for reconstruction.
Test implications: unit plus hermetic integration.

## FR-002 / Invalid Include Trees

Decision: implemented_unverified; current catalog behavior appears strong, but final planning keeps verification coverage explicit.
Evidence: `tests/unit/api/test_task_step_templates_service.py` covers global-to-personal rejection, include cycles, inactive includes, incompatible inputs, and flattened step limits.
Rationale: Existing validation likely satisfies most invalid-tree cases, but MM-630 should add or confirm missing/unauthorized target coverage if gaps remain.
Alternatives considered: Reimplement include validation at execution time was rejected because the spec requires control-plane compile-time validation.
Test implications: focused unit tests.

## FR-003 / Deterministic Flattened Order

Decision: implemented_unverified; verify deterministic order through submission boundary.
Evidence: `TaskTemplateCatalogService._expand_version_steps()` appends executable steps to one `resolved_steps` list; `test_expand_template_flattens_pinned_include_with_provenance` asserts child step order.
Rationale: Catalog order appears deterministic, but the story needs proof that submitted task payload order matches compiled order and remains stable across repeated equivalent submissions.
Alternatives considered: Trust catalog unit coverage only; rejected because Create/API payload serialization can still reorder or drop steps.
Test implications: unit and integration.

## FR-004 / Authored Preset And Step Source Provenance

Decision: partial; implement provenance derivation/preservation for recursive authored bindings and include-tree summary.
Evidence: `moonmind/workflows/tasks/task_contract.py` models `TaskStepSource` and `AuthoredPresetBinding`; `frontend/src/entrypoints/task-create.tsx` preserves `source` when present; `api_service/api/routers/executions.py` preserves supplied `authoredPresets` and `appliedStepTemplates`.
Rationale: Step source is already supported, but recursive include-tree and authored preset binding details are not consistently synthesized from catalog expansion output.
Alternatives considered: Store only per-step source; rejected because the spec requires authored preset bindings, include-tree summaries, mappings, alias, detachment state, and reconstruction support.
Test implications: unit and integration.

## FR-005 / Worker-Facing Resolved Steps

Decision: implemented_unverified; add a boundary test proving no live catalog dependency after submission.
Evidence: `moonmind/workflows/temporal/worker_runtime.py` expands top-level task templates before child execution and sets `task_payload["steps"]`; docs and existing tests expect workers to consume flattened steps.
Rationale: The execution-facing behavior appears present, but MM-630 requires proof that workers do not expand presets or read the live catalog after the task has been compiled.
Alternatives considered: Add worker-side catalog fallback; rejected because it would violate the source design and live-catalog independence.
Test implications: hermetic integration_ci.

## FR-006 / Reconstruction After Catalog Changes

Decision: partial; preserve recursive composition summary in submitted snapshots.
Evidence: Existing snapshots and `appliedStepTemplates` are preserved in API and integration tests, but `TaskTemplateCatalogService.expand_template()` returns `composition` separately and `appliedTemplate` omits it.
Rationale: A submitted task can only be safely reconstructed after catalog changes when the snapshot includes both flattened steps and enough composition/provenance metadata.
Alternatives considered: Re-expand from live catalog during edit/rerun; rejected because live catalog definitions may drift.
Test implications: unit and integration.

## FR-007 / Existing Behavior Preservation

Decision: implemented_unverified; protect with regression assertions while changing preset metadata.
Evidence: Existing API tests preserve runtime, publish, dependencies, attachments, Jira provenance, `authoredPresets`, and `appliedStepTemplates`; integration test `test_task_shaped_submission_normalization.py` covers normalized task submission.
Rationale: MM-630 should not alter attachment, runtime, publish, Jira, edit, rerun, or resume semantics except where compiled preset provenance is needed.
Alternatives considered: Broaden the story into attachment/recovery changes; rejected as out of scope.
Test implications: unit and integration regression coverage.

## FR-008 / MM-630 Traceability

Decision: missing until final verification; preserve throughout artifacts and downstream metadata.
Evidence: `specs/324-compile-recursive-presets/spec.md` preserves MM-630 and the original Jira preset brief.
Rationale: Final verification and PR metadata need the issue key and original brief available for comparison.
Alternatives considered: Store only the Jira key; rejected because final verification compares against the original brief.
Test implications: final verify.

## Test Strategy

Decision: use both unit and hermetic integration testing.
Evidence: Unit tests already exist for catalog expansion, task contract models, API normalization, and Create page payload behavior; integration tests cover task-shaped submission normalization.
Rationale: Catalog behavior can be tested hermetically in unit tests, while the no-live-catalog worker/submission boundary requires integration-level evidence.
Alternatives considered: Provider verification tests; rejected because MM-630 uses local task/preset contracts and does not require external credentials.
Test implications: `./tools/test_unit.sh` and `./tools/test_integration.sh`.
