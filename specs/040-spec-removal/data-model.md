# Data Model: Canonical Workflow Surface Naming

## Entity: LegacyCanonicalTokenMapping

- **Description**: Defines the transformation from legacy workflow terms to canonical replacements.
- **Fields**:
  - `legacy_term` (string): source token to replace, e.g., `SPEC_WORKFLOW_CODEX_QUEUE`.
  - `canonical_term` (string): preferred target token, e.g., `WORKFLOW_CODEX_QUEUE`.
  - `surface_type` (enum): one of `config`, `route`, `schema`, `metric`, `artifact_path`, `documentation`.
  - `applies_to` (list[path]): files and/or directories under migration scope.
  - `exception_allowed` (bool): true only when retention is explicitly required for history.
- **Rules**:
  - Mapping must be deterministic and reviewed against `docs/SpecRemovalPlan.md`.
  - Any token with `exception_allowed=true` must only appear in a historical appendix section.

## Entity: MigrationBoundary

- **Description**: Represents the allowed edit scope for this feature.
- **Fields**:
  - `scope_name` (string): `docs/specs migration`.
  - `included_directories` (list[path]): `docs/`, `specs/`.
  - `allowed_file_types` (list[string]): `*.md`, `*.yaml`, `*.yml`.
  - `excluded_artifacts` (list[string]): runtime code, tests, deployment manifests.
- **Rules**:
  - Edits must stay within `docs/` and `specs/` and listed target files.
  - Excluded artifacts cannot be changed in this feature pass.

## Entity: HistoricalReferenceException

- **Description**: Controls intentional retention of legacy naming for traceability only.
- **Fields**:
  - `location` (path): file path containing the retained legacy reference.
  - `legacy_term` (string): preserved token.
  - `justification` (string): why retention is needed.
  - `reviewed_on` (date): review date for exception.
- **Rules**:
  - Each exception must have explicit justification.
  - No operational instruction may rely on retained legacy tokens.

## Entity: VerificationFinding

- **Description**: Captures results from the migration validation pass.
- **Fields**:
  - `token` (string): discovered legacy or canonical token.
  - `matches` (list[path]): discovered files/lines.
  - `status` (enum): `approved`, `unexpected`, `needs_review`.
  - `remediation` (string): required action or signoff note.
- **Rules**:
  - Validation report must clearly separate approved historical exceptions from unexpected matches.
  - Unexpected matches must generate migration follow-up tasks before completion claim.

## Entity: RequirementsTraceabilityRecord

- **Description**: Maps `DOC-REQ-*` identifiers to implementation and validation surfaces.
- **Fields**:
  - `doc_req_id` (string): e.g., `DOC-REQ-001`.
  - `source_reference` (string): spec section or source doc location.
  - `mapped_frs` (list[string]): linked functional requirements.
  - `implementation_surfaces` (list[string]): planned files/tasks to edit or verify.
  - `validation_strategy` (string): command or review expected to confirm completion.
- **Rules**:
  - Every `DOC-REQ-*` from `spec.md` must have exactly one mapping row.
  - `validation_strategy` cannot be empty.

