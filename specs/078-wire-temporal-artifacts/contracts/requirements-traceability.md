# Requirements Traceability

| DOC-REQ ID | FR ID(s) | Implementation Surfaces | Validation Strategy |
|------------|----------|-------------------------|---------------------|
| DOC-REQ-001 | FR-001, FR-002 | `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/workflows/manifest_ingest.py`, `activity_runtime.py` | Unit tests verify activity returns only dict with string refs. E2E test to inspect Temporal history size. |
| DOC-REQ-002 | FR-003 | `plan.generate` activity in `activity_runtime.py` | Unit test checking `plan_ref` returned type and payload. |
| DOC-REQ-003 | FR-004 | `artifacts.py`, `activity_runtime.py` | Integration test confirming artifact can be fetched from artifact service using returned ref. |
| DOC-REQ-004 | FR-005 | `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/workflows/manifest_ingest.py` | Pytest fixture using `temporalio.testing.WorkflowEnvironment` asserts histories lack large values. |
| DOC-REQ-005 | FR-006 | Entire feature branch codebase | CI/CD pipeline validates test passes. PR review confirms runtime vs mock changes. |
