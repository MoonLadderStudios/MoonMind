# Implementation Plan: Canonical Workflow Surface Naming

**Branch**: `040-spec-removal` | **Date**: 2026-02-24 | **Spec**: `specs/040-spec-removal/spec.md`  
**Input**: Feature specification from `/specs/040-spec-removal/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Execute a controlled canonicalization pass over the docs/spec migration surfaces defined in `docs/SpecRemovalPlan.md`, replacing legacy `SPEC`-style workflow naming with canonical `workflow` naming while keeping one explicit historical appendix in `docs/SpecRemovalPlan.md`. Runtime behavior work is deferred to this feature’s US4 follow-up (`T040`/`T041`) while docs/spec migration remains in-band.

## Technical Context

**Language/Version**: Markdown artifacts with Python/FastAPI/Celery ecosystem context from the MoonMind repository  
**Primary Dependencies**: `.specify` planning scripts and templates, markdown documentation files in `docs/` and `specs/`, command-line verification utilities  
**Storage**: Filesystem documentation artifacts under `docs/`, `specs/`, and `specs/040-spec-removal/contracts/`  
**Testing**: `rg`/`grep` verification scans, review checklist checks, and scope-alignment review; runtime regression checks are captured in US4 tasks (`T041`/`tests/test_workflow_renaming.py`).
**Target Platform**: Repository documentation and spec artifacts (planning and verification runbook scope)  
**Project Type**: Documentation/spec migration planning  
**Performance Goals**: Complete migration of documented legacy tokens in targeted files with zero unapproved legacy tokens outside intended appendix sections  
**Constraints**: Preserve bounded scope; no unrelated code, deployment, or DB/runtime changes; runtime behavior unchanged in this docs/spec pass with explicit follow-up runtime tasks.
**Scale/Scope**: Full docs/spec migration list from the plan document (66 spec files + 10 docs files)  

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Core Principles**: `NEEDS CLARIFICATION` marker in the constitution template means no concrete enforceable principle text is available in `.specify/memory/constitution.md`; no hard gates can be derived from missing governance content.
- **Additional Constraints / Workflow Rules**: also unresolved in the template; no further formal checks can be derived.

**Gate Status**: PASS WITH NOTE — proceed with explicit constraints captured in this feature (`docs` scope, runtime in-band in US4, verification only in this slice).

## Project Structure

### Documentation (this feature)

```text
specs/040-spec-removal/
├── plan.md                  # This file (/speckit.plan command output)
├── research.md              # Phase 0 output (/speckit.plan command)
├── data-model.md            # Phase 1 output (/speckit.plan command)
├── quickstart.md            # Phase 1 output (/speckit.plan command)
├── contracts/
│   └── requirements-traceability.md
└── tasks.md                 # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
docs/                     # Canonical naming reference and execution checklist surface
specs/                    # Migrated docs/spec artifacts (planning scope only)
```

**Structure Decision**: Runtime code directories are explicitly excluded from this slice’s direct edits. US4 tracks production runtime follow-up work for canonical naming alignment.

## Phase 0 – Research Summary

See `specs/040-spec-removal/research.md` for the resolved research decisions and rationale:

1. Lock legacy-to-canonical token mapping from the user plan and treat it as the authoritative source of truth.
2. Keep migration confined to listed `docs/` and `specs/` files to preserve bounded operational impact.
3. Preserve legacy vocabulary only in `docs/SpecRemovalPlan.md` appendices and explicit historical notes.
4. Keep runtime mode behavior aligned by confirming this feature tracks runtime naming follow-up via US4 (`T040`, `T041`) and avoids direct runtime behavior changes in this pass.

## Phase 1 – Design Outputs

- **Data Model** (`data-model.md`): Define token mapping entities, migration boundaries, and verification artifacts used for deterministic legacy-token reporting.
- **Contracts**:
  - `contracts/requirements-traceability.md`: maps all `DOC-REQ-*` IDs to FRs and validation strategies for this migration feature.
- **Quickstart** (`quickstart.md`): provides verification commands and acceptance checks for a successful migration pass.

## Implementation Strategy

### US1 – Canonical token mapping and scope lock

- Use the exact mapping in `docs/SpecRemovalPlan.md` as the canonical reference.
- Replace `SPEC_*`/legacy tokens in the permitted file set with canonical `WORKFLOW_*`/`workflow` equivalents.
- Ensure all edits are limited to listed files and add or retain only the one historical trace context in `docs/SpecRemovalPlan.md`.

### US2 – Spec artifact and contract normalization

- Update `specs/040-spec-removal` planning collateral to use canonical terminology and remove aliasing statements except where historical traceability requires an explicit note.
- Align existing `DOC-REQ-*` references, FR mappings, and acceptance criteria with canonical naming and this plan’s bounded scope.

### US3 – Verification and residual handling

- Produce verification outputs that prove token removal is complete except allowed historical references.
- Record unresolved follow-up references and route them to follow-on workflow execution, not this docs-only pass.

### US4 – Scope-aligned execution note

- For this feature, runtime semantics remain unchanged in docs/spec pass scope; production runtime behavior alignment is tracked in US4 (`T040`/`T041`) with explicit implementation and validation evidence.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
