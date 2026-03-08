# Implementation Plan: Wire Temporal Artifacts

**Branch**: `001-wire-temporal-artifacts` | **Date**: 2026-03-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-wire-temporal-artifacts/spec.md`

## Summary

This feature will implement section 5.8 of the Temporal Migration Plan: Wiring activities to the artifact store. Specifically, it involves ensuring that all large payloads generated during the `MoonMind.Run` and `ManifestIngest` Temporal workflows are stored in the artifact store, with only their references (`plan_ref`, `logs_ref`, etc.) returned and saved in the workflow history. This is crucial for avoiding Temporal history bloat and adhering to Temporal best practices.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Temporalio Python SDK, FastAPI (API layer)
**Storage**: Artifact Store (S3/MinIO via existing MoonMind artifact APIs)
**Testing**: pytest
**Target Platform**: Linux server (Docker Compose)
**Project Type**: backend
**Performance Goals**: Support typical large outputs (MBs of logs/plans) without hitting Temporal limits.
**Constraints**: Keep workflow history payloads < 2MB per event.
**Scale/Scope**: Impacts all MoonMind execution workflows and their respective activities.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. One-Click Agent Deployment**: PASS. Relies on existing Docker Compose artifacts service.
- **II. Avoid Vendor Lock-In**: PASS. Artifacts store is accessed via abstract adapter.
- **III. Own Your Data**: PASS. Data is extracted from Temporal's opaque history and placed in accessible artifact storage.
- **IV. Skills Are First-Class**: N/A.
- **V. The Bittersweet Lesson**: PASS. Uses standard activities to store artifacts.
- **VI. Powerful Runtime Configurability**: PASS. Uses existing configured artifact store.
- **VII. Modular and Extensible Architecture**: PASS. Keeps workflow logic separated from large blob storage.
- **VIII. Self-Healing by Default**: PASS. Uses standard Temporal retry policies for artifact storage.
- **IX. Facilitate Continuous Improvement**: PASS. Artifacts become easier to inspect.
- **X. Spec-Driven Development**: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/001-wire-temporal-artifacts/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── requirements-traceability.md # Phase 1 output
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── workflows/
│   ├── run.py                 # Update/Verify MoonMind.Run
│   └── manifest_ingest.py     # Create new ManifestIngest Workflow
├── activity_runtime.py        # Update sandbox, plan activities to return refs
├── manifest_ingest.py         # Migrate old logic to new Temporal Workflow
└── artifacts.py               # Ensure artifact API returns proper refs

tests/unit/workflows/temporal/
├── test_run_artifacts.py               # Test MoonMind.Run references
└── test_manifest_ingest_artifacts.py   # Test ManifestIngest references
```

**Structure Decision**: A single project backend update focusing on `moonmind/workflows/temporal/` module. The `ManifestIngest` will be formalized as a proper workflow and activities modified to handle artifact refs exclusively for large payloads.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
