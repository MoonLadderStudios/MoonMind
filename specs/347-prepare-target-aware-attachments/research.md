# Research: Prepare-Time Target-Aware Attachment Materialization

## Decision: Build on existing prepared context contracts

Rationale: `moonmind/workflows/tasks/prepared_context.py` already models objective and step prepared refs, workflow metadata, bounded failure payloads, and no-inline-content guardrails from prior target-aware input work. Extending that contract with stable materialization metadata and stricter step identity checks is smaller and safer than introducing a parallel attachment model.

Alternatives considered: A new preparation contract was rejected because it would duplicate target-binding semantics and increase risk of inconsistent workflow metadata.

## Decision: Fail fast when step attachments lack stable step identity

Rationale: `MM-648` specifically forbids silent retargeting after reorder, preset apply, or text edits. Falling back to `step-<index>` for step attachments can change meaning when order changes. Step-scoped attachments must have a stable authored or normalized step reference.

Alternatives considered: Preserving index fallback was rejected because it encodes the silent retargeting risk identified by DESIGN-REQ-029.

## Decision: Keep materialized files workspace-local and referenced by metadata

Rationale: Existing Codex worker preparation writes `.moonmind/inputs/...` files and `.moonmind/attachments_manifest.json`. The workflow-visible contract should carry refs and metadata only, while the worker-local manifest records stable workspace paths and status.

Alternatives considered: Storing binary bytes or full file content in workflow history was rejected by DESIGN-REQ-002 and Constitution secret/data hygiene rules.

## Decision: Validate retargeting through pure unit tests plus worker materialization tests

Rationale: Reorder, preset apply, and text edit risks are normalization/contract risks. Unit tests can deterministically show attachments remain bound by stable step IDs rather than array positions; worker tests can show final materialized paths and manifest entries preserve the same target refs.

Alternatives considered: A full Temporal integration test for every edit/preset path was rejected as unnecessary for this narrow contract when the target-binding function is pure and existing workflow-boundary tests already cover dispatch filtering.

## Requirement Gap Analysis

Decision: All in-scope `MM-648` requirements are implemented and verified by current code plus focused tests; no additional production work is planned from this planning pass.
Evidence: `moonmind/workflows/tasks/prepared_context.py`, `moonmind/agents/codex_worker/worker.py`, `tests/unit/workflows/tasks/test_prepared_context.py`, `tests/unit/agents/codex_worker/test_attachment_materialization.py`, `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py`, and `specs/347-prepare-target-aware-attachments/verification.md`.
Rationale: The previous implementation removed step-index fallback, added stable `workspacePath` and `status` manifest metadata, preserved refs-only workflow payloads, and verified the target-aware workflow boundary. The full unit suite passed; the compose-backed integration runner was blocked by Docker administrative policy, with the focused target-aware integration test passing locally.
Alternatives considered: Regenerating tasks or broadening implementation was rejected because the current evidence already satisfies the single `MM-648` story and additional scope would duplicate previous target-aware input work.
Test implications: Keep unit and focused integration evidence as the primary verification path; rerun `./tools/test_integration.sh` in a Docker-enabled environment before merge if required by CI policy.

## FR-001 through FR-004 Prepared Manifest Metadata

Decision: implemented_verified.
Evidence: `PreparedInputEntry` carries refs, `workspacePath`, and `status`; worker materialization writes `.moonmind/attachments_manifest.json`; unit tests assert manifest metadata and no inline binary content.
Rationale: These requirements concern bounded metadata shape and stable materialization paths, which are directly covered by pure unit tests and worker materialization tests.
Alternatives considered: Adding a new manifest model was rejected because existing prepared-context and worker manifest surfaces already own this contract.
Test implications: Unit tests are sufficient for the metadata contract; final verification checks traceability.

## FR-005 and SC-004 Target-Aware Image Context

Decision: implemented_verified.
Evidence: Existing vision/target-aware workflow tests plus focused integration test `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` passed locally.
Rationale: `MM-648` depends on preserving per-target context refs rather than changing the vision provider implementation.
Alternatives considered: New end-to-end provider tests were rejected because provider credentials are outside hermetic CI and the target-aware boundary is covered locally.
Test implications: Focused integration and existing vision tests cover the contract; provider verification is not required.

## FR-006, FR-009, and DESIGN-REQ-029 No Silent Retargeting

Decision: implemented_verified.
Evidence: `prepared_context.py` and `worker.py` now require stable `id`, `stepRef`, or `ref` for step-scoped attachments; unit tests cover missing stable refs and binding stability across reorder/text edits.
Rationale: Step index fallback was the remaining retargeting hazard. Failing fast on ambiguous step attachments matches the pre-release compatibility policy and the source invariant.
Alternatives considered: Keeping `step-<index>` fallback was rejected because it can silently retarget attachments after step reorder.
Test implications: Unit tests directly cover the normalization/contract risk.

## FR-007 and SC-003 Explicit Preparation Failure

Decision: FR-007 is implemented_verified; SC-003 is implemented_unverified pending integration-level failure coverage.
Evidence: Worker tests cover failed artifact download diagnostics and stable-ref validation. Repository search found unit coverage for target-specific preparation failure, but no integration test that proves preparation failure surfaces target-specific diagnostics.
Rationale: Preparation must stop before downstream execution sees incomplete target state, and SC-003 specifically requires integration evidence.
Alternatives considered: Treating unit worker failure coverage as sufficient for SC-003 was rejected because the success criterion explicitly names integration coverage.
Test implications: Add or run integration verification for target-specific preparation failure first; repair the preparation boundary only if that integration evidence exposes a real behavior gap.

## FR-010 and SC-005 Traceability

Decision: implemented_verified.
Evidence: `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve `MM-648`, the canonical Jira preset brief, and source mappings for DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029.
Rationale: Final verification needs a durable source trail from Jira brief to implementation evidence.
Alternatives considered: Relying only on local handoff artifacts was rejected because MoonSpec verification uses committed feature artifacts as the alignment source.
Test implications: Final verification is the relevant validation path.
