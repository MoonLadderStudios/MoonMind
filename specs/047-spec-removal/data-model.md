# Data Model: Canonical Workflow Surface Naming

## Entity: LegacyCanonicalTokenMapping

- **Description**: Deterministic mapping from each legacy workflow token to its canonical token.
- **Fields**:
  - `mapping_id` (string): stable identifier for audit references.
  - `legacy_term` (string): source token (example: `SPEC_WORKFLOW_CODEX_QUEUE`).
  - `canonical_term` (string): target token (example: `WORKFLOW_CODEX_QUEUE`).
  - `surface_type` (enum): `config`, `route`, `schema`, `metric`, `artifact_path`, `documentation`.
  - `scope` (enum): `runtime`, `docs`, `both`.
  - `exception_allowed` (bool): true only for historical appendix retention.
- **Rules**:
  - One `legacy_term` maps to exactly one canonical replacement in this feature.
  - `exception_allowed=true` is valid only in explicit historical sections.

## Entity: RuntimeNamingSurface

- **Description**: Runtime-facing naming surfaces that must move to canonical terms without semantic changes.
- **Fields**:
  - `surface_id` (string): stable key for route/env/schema/metric/artifact domains.
  - `component_path` (path): implementation location (API/router/service/config/test).
  - `legacy_pattern` (string): legacy naming pattern currently used.
  - `canonical_pattern` (string): canonical naming pattern to enforce.
  - `behavioral_invariant` (string): behavior that must remain unchanged.
- **Rules**:
  - All runtime updates must preserve queue semantics, model/effort pass-through, and billing-relevant behavior.
  - Unsupported legacy runtime inputs must fail deterministically (no hidden fallback aliases).

## Entity: ModeAlignmentRecord

- **Description**: Captures runtime-mode and docs-mode alignment expectations.
- **Fields**:
  - `selected_mode` (enum): `runtime` or `docs`.
  - `authoritative_requirement` (string): source requirement controlling mode behavior.
  - `shared_token_map_version` (string): canonical map revision used by both modes.
  - `docs_gate_command` (string): docs/spec verification command.
  - `runtime_gate_command` (string): runtime verification command.
- **Rules**:
  - When `selected_mode=runtime`, docs-mode guidance cannot reduce runtime acceptance criteria.
  - Both gates must be executable and documented in `quickstart.md`.

## Entity: HistoricalReferenceException

- **Description**: Approved legacy reference retained for traceability.
- **Fields**:
  - `location` (path): file/section containing retained legacy token.
  - `legacy_term` (string): retained token.
  - `justification` (string): explicit reason for retention.
  - `review_owner` (string): approver for exception.
- **Rules**:
  - Exceptions are forbidden in active operational instructions.
  - Each exception must be linked to a migration follow-up action if unresolved.

## Entity: VerificationFinding

- **Description**: Result record for token scans and test executions.
- **Fields**:
  - `finding_id` (string): stable ID.
  - `check_type` (enum): `docs_scan`, `runtime_scan`, `unit_tests`.
  - `status` (enum): `pass`, `fail`, `needs_review`.
  - `evidence_ref` (string): command output artifact or summary reference.
  - `remediation` (string): required fix when not `pass`.
- **Rules**:
  - Feature completion requires all mandatory checks in `pass` state.
  - `needs_review` requires explicit reviewer disposition before handoff.

## Entity: RequirementsTraceabilityRecord

- **Description**: One-to-one mapping from each `DOC-REQ-*` to implementation and validation surfaces.
- **Fields**:
  - `doc_req_id` (string): `DOC-REQ-001` ... `DOC-REQ-011`.
  - `mapped_frs` (list[string]): FR identifiers in `spec.md`.
  - `implementation_surfaces` (list[path]): planned files/components.
  - `validation_strategy` (string): deterministic verification approach.
- **Rules**:
  - Every `DOC-REQ-*` in spec must exist exactly once in traceability output.
  - `DOC-REQ-011` must include runtime code + validation test surfaces.
