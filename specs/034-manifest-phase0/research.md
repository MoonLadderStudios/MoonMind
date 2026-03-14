# Phase 0 Research: Manifest Phase 0 Rebaseline

**Feature**: `031-manifest-phase0`  
**Branch**: `031-manifest-phase0`  
**Date**: 2026-03-02

## Runtime Mode vs Docs Mode

- **Decision**: Treat this feature as runtime implementation mode, not docs-only mode.
- **Rationale**: `spec.md` requires production runtime code changes plus validation tests whenever requirements and as-built behavior diverge (FR-002, FR-010, FR-012).
- **Alternatives considered**: Closing with spec/doc edits only was rejected because it would violate explicit completion criteria and allow behavioral drift.

## Manifest Queue Normalization Contract

- **Decision**: Keep `moonmind/workflows/agent_queue/manifest_contract.py::normalize_manifest_job_payload` as the single normalization entrypoint for `type="manifest"` submissions.
- **Rationale**: The contract enforces `manifest.name == metadata.name`, `action in {plan, run}`, `version == v0`, supported source kinds, option allowlists, deterministic hash/version metadata, derived capabilities, and effective run config in one place (FR-003, FR-005, FR-007).
- **Alternatives considered**: Reusing task-job normalization was rejected because task semantics differ and would create hidden coupling with non-manifest fields.

## Source-Kind Strategy for Phase 0

- **Decision**: Model supported kinds as `inline` and `registry` by default, with `path` gated by `workflow.allow_manifest_path_source` (default `false`).
- **Rationale**: This matches current runtime behavior and preserves a fail-fast posture for unsupported modes while retaining a guarded dev/test path option (FR-007).
- **Alternatives considered**: Enabling `path` by default or adding `repo` support in this phase was rejected to avoid widening the trust/runtime surface before dedicated controls exist.

## Capability Derivation and Claim Eligibility

- **Decision**: Continue server-side derivation of `requiredCapabilities` from manifest content plus configurable base labels (`manifest_required_capabilities`), and require worker capability supersets during claim.
- **Rationale**: `derive_required_capabilities()` + repository claim checks enforce deterministic routing and prevent worker/job mismatches (DOC-REQ-001, DOC-REQ-003; FR-005).
- **Alternatives considered**: Accepting client-supplied capability hints was rejected because it weakens routing trust and violates server-authoritative contract expectations.

## Secret-Safety Enforcement

- **Decision**: Keep recursive secret-leak detection during normalization and preserve only sanitized secret references (`profile://`, `vault://`) in persisted/serialized metadata.
- **Rationale**: `detect_manifest_secret_leaks()` blocks raw token patterns before persistence; `collect_manifest_secret_refs()` and payload sanitization retain only safe reference metadata (FR-004, FR-007).
- **Alternatives considered**: Redaction after persistence was rejected because it still risks secret exposure in database rows and API responses.

## Queue and Registry Serialization Boundaries

- **Decision**: Preserve split between persisted payload details and API-safe payload details: API serializers remove inline content and expose only safe metadata (`manifestHash`, `manifestVersion`, `requiredCapabilities`, optional `manifestSecretRefs`, and sanitized manifest source metadata).
- **Rationale**: `sanitize_manifest_payload()` is already integrated in queue response models, and manifest registry routes return structured run linkage/state metadata needed for operations (FR-004, FR-006, FR-011).
- **Alternatives considered**: Dual payload storage (raw + sanitized columns) was rejected as unnecessary complexity for Phase 0.

## Validation Strategy

- **Decision**: Keep unit-test verification centered on manifest contract, queue routing/serialization, repository claim gating, and manifest registry CRUD/run submission, executed exclusively via `./tools/test_unit.sh`.
- **Rationale**: This aligns with repository policy and ensures deterministic CI-equivalent validation for rebaseline changes (FR-010).
- **Alternatives considered**: Running raw `pytest` or relying only on manual/API checks was rejected due policy mismatch and weaker regression guarantees.
