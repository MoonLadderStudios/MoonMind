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
