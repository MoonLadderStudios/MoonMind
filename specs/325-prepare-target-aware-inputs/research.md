# Research: Prepare Target-Aware Inputs

## Existing Task Input Contract

Decision: Treat `task.inputAttachments` as objective-scoped input refs and `task.steps[].inputAttachments` as step-scoped input refs; status is implemented_verified for source contract validation.
Evidence: `moonmind/workflows/tasks/task_contract.py` defines `TaskInputAttachmentRef`, task-level `inputAttachments`, and step-level `inputAttachments`; API and integration tests assert task and step refs are preserved.
Rationale: The source of target binding is already explicit in the canonical task payload, so the new work should consume this shape rather than inventing a parallel target field.
Alternatives considered: Inferring target binding from filenames, artifact metadata, or storage paths was rejected because the spec requires authored target binding to remain authoritative.
Test implications: Unit regression for contract validation; integration tests for workflow delivery.

## Prepared Manifest and Materialization

Decision: Reuse the existing manifest entry shape as the planned canonical prepared-input manifest, but move/extend the behavior to the canonical runtime workflow boundary; status is partial.
Evidence: `moonmind/agents/codex_worker/worker.py` collects targets, materializes files, and writes `.moonmind/attachments_manifest.json`; `tests/unit/agents/codex_worker/test_attachment_materialization.py` verifies objective and step entries, stable paths, diagnostics, and failure on download errors.
Rationale: The current behavior satisfies many file/manifest details but lives in the Codex worker prepare path. MM-631 requires `MoonMind.Run` and delegated `MoonMind.AgentRun` children to observe target-aware prepared context.
Alternatives considered: Keeping this as Codex-worker-only behavior was rejected because runtime adapters and child runs must not redefine target binding.
Test implications: Unit tests for manifest/result models and integration-style workflow tests proving prepare occurs before affected step dispatch.

## Derived Image Context

Decision: Reuse `VisionService.render_target_contexts` for target-aware derived context and store only refs/paths in runtime state; status is implemented_verified for the service and partial for workflow integration.
Evidence: `moonmind/vision/service.py` renders objective and step target contexts with diagnostics; `tests/unit/moonmind/vision/test_service.py` verifies target preservation, disabled/provider-failed diagnostics, safe step-ref paths, and collision handling.
Rationale: Derived context already has the correct target-aware concept; the missing work is linking those outputs into the runtime prepare result and adapter-visible context.
Alternatives considered: Embedding generated markdown directly in step instructions was rejected because the spec requires derived context to remain secondary and large content to stay out of workflow history.
Test implications: Unit tests for prepare-result indexing; integration tests for manifest/context refs delivered to step execution.

## Step Runtime Delivery

Decision: Add workflow-level prepared context filtering so each step receives relevant objective context plus only its own step-scoped context; status is partial.
Evidence: `tests/unit/agents/codex_worker/test_worker.py` proves Codex instruction composition includes objective and current-step context while omitting non-current steps. `moonmind/workflows/temporal/workflows/run.py` currently builds `AgentExecutionRequest` with generic `inputRefs` but no prepared target-aware context.
Rationale: Existing Codex text-first filtering is valuable evidence, but MM-631 requires the canonical workflow boundary to enforce filtering before adapter realization.
Alternatives considered: Letting adapters filter from the full manifest was rejected because adapters must not invent or broaden target rules.
Test implications: Workflow boundary tests for `MoonMind.Run` step dispatch and adapter unit tests for preserving prepared target bindings.

## Child AgentRun Boundary

Decision: Extend child `AgentRun` request metadata/input refs to carry only the prepared context relevant to the represented step; status is missing.
Evidence: `moonmind/workflows/temporal/workflows/run.py` creates `AgentExecutionRequest` with `input_refs=node_inputs.get("inputRefs") or []`; searches found no `inputAttachments`, manifest, or image context handling in `run.py` or `agent_run.py`.
Rationale: The child boundary is where unrelated step attachments can leak unless the parent supplies a pre-filtered, bounded context set.
Alternatives considered: Passing the whole prepared manifest to every child was rejected because it would make every adapter responsible for target filtering.
Test implications: Workflow boundary integration test with one objective attachment and at least two step attachments, asserting child request refs/metadata omit unrelated step context.

## Failure Behavior

Decision: Add an explicit Temporal prepare failure path before affected step execution; status is partial.
Evidence: Codex worker materialization raises on download failures and records diagnostics. No equivalent `MoonMind.Run` prepare failure path exists for attachment preparation.
Rationale: The spec requires explicit failure before running with incomplete context.
Alternatives considered: Continuing with missing refs and warning diagnostics was rejected because silent context loss is a contract violation.
Test implications: Unit test for prepare activity error result and integration test that a failed prepare does not dispatch the affected step.

## Unit Test Strategy

Decision: Use focused pytest unit coverage through `./tools/test_unit.sh` for contract/model filtering, prepared manifest/result construction, adapter-visible context selection, and failure classification.
Evidence: Existing unit suites cover task contracts, vision service, Codex worker materialization, Codex prompt filtering, and workflow helper behavior.
Rationale: Unit tests should pin deterministic model and filtering behavior before workflow integration is updated.
Alternatives considered: Relying only on workflow tests was rejected because target filtering and manifest construction need faster, more local regression coverage.
Test implications: Add or update tests under `tests/unit/workflows/tasks/`, `tests/unit/moonmind/vision/`, `tests/unit/workflows/temporal/workflows/`, and adapter/worker tests as implementation touches those seams.

## Integration Test Strategy

Decision: Use hermetic Temporal/workflow boundary coverage and run it through `./tools/test_integration.sh` when marked `integration_ci`; use focused local workflow tests when Temporal time-skipping coverage is too slow for CI.
Evidence: Existing integration suites include `tests/integration/temporal/test_task_shaped_submission_normalization.py` and `tests/integration/workflows/temporal/workflows/`.
Rationale: MM-631 changes workflow/activity and child request boundaries, so integration evidence must prove real invocation shapes and no unrelated context leakage.
Alternatives considered: API-only submission tests were rejected because they do not prove runtime prepare or child dispatch behavior.
Test implications: Add an integration scenario with objective plus two step attachments, one delegated child step, and one prepare failure path.

## Traceability

Decision: Preserve `MM-631`, the original preset brief, and source design mappings in every generated artifact; status is implemented_unverified until final verification.
Evidence: `spec.md` preserves MM-631 and the original preset brief; this research and plan preserve MM-631.
Rationale: Final verification compares the implementation and artifacts against the original Jira brief.
Alternatives considered: Keeping only the issue key was rejected because the final verifier needs the full source brief.
Test implications: Final `/speckit.verify` must include traceability review.
