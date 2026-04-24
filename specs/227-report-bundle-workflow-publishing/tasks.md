# Tasks: Report Bundle Workflow Publishing

**Input**: `specs/227-report-bundle-workflow-publishing/spec.md`  
**Plan**: `specs/227-report-bundle-workflow-publishing/plan.md`  
**Mode**: runtime  
**Source Traceability**: MM-461, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-018

## Phase 1: Setup

- [X] T001 Confirm active feature context points to `specs/227-report-bundle-workflow-publishing` in `.specify/feature.json`
- [X] T002 Confirm existing report artifact contract tests from MM-460 are present in `tests/unit/workflows/temporal/test_artifacts.py`

## Phase 2: Foundational

- [X] T003 Inspect existing artifact service and activity facade boundaries in `moonmind/workflows/temporal/artifacts.py`
- [X] T004 Inspect existing report artifact metadata/link helpers in `moonmind/workflows/temporal/report_artifacts.py`

## Phase 3: Publish Report Bundles From Activities

**Summary**: Activities publish report component artifacts, link them to executions and optional step context, and return compact `report_bundle_v = 1` refs to workflow code.

**Independent Test**: Run a report-producing activity path that creates primary, summary, structured, and evidence artifacts, then verify the produced bundle contains only compact artifact refs and bounded metadata; execution and step linkage is present; exactly one final report is identifiable; and no report body, evidence blob, log content, raw URL, screenshot, transcript, or large finding detail is embedded in workflow state or return values.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-018

- [X] T005 [P] Add failing unit tests for compact report bundle result serialization and unsafe inline payload rejection in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-002, FR-006, FR-007, DESIGN-REQ-008, and DESIGN-REQ-010
- [X] T006 [P] Add failing unit tests for report bundle publication links, final marker cardinality, step metadata, and evidence refs in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-001, FR-003, FR-004, FR-005, FR-008, DESIGN-REQ-006, DESIGN-REQ-014, DESIGN-REQ-017, and DESIGN-REQ-018
- [X] T007 [P] Add failing activity-boundary test for the report bundle publication facade in `tests/unit/workflows/temporal/test_artifacts_activities.py` covering acceptance scenarios 1-5
- [X] T008 Run targeted tests and confirm the new MM-461 tests fail before production code using `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py`
- [X] T009 Implement compact report bundle value objects and validation helpers in `moonmind/workflows/temporal/report_artifacts.py` covering FR-002, FR-005, FR-006, FR-007, DESIGN-REQ-008, and DESIGN-REQ-010
- [X] T010 Implement report bundle publication service method in `moonmind/workflows/temporal/artifacts.py` covering FR-001, FR-003, FR-004, FR-008, DESIGN-REQ-006, DESIGN-REQ-014, DESIGN-REQ-017, and DESIGN-REQ-018
- [X] T011 Implement activity facade method for report bundle publication in `moonmind/workflows/temporal/artifacts.py` covering FR-001 and DESIGN-REQ-018
- [X] T012 Run targeted unit tests using `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_activity_runtime.py`
- [X] T013 Run traceability check for MM-461 and report bundle terms across specs, docs, runtime code, and tests using `rg -n "MM-461|report_bundle_v|ReportBundle|DESIGN-REQ-006|DESIGN-REQ-008|DESIGN-REQ-010|DESIGN-REQ-014|DESIGN-REQ-017|DESIGN-REQ-018" specs/227-report-bundle-workflow-publishing moonmind/workflows/temporal tests/unit/workflows/temporal`

## Final Phase: Polish And Verification

- [X] T014 Run full unit suite with `./tools/test_unit.sh`
- [ ] T015 Run hermetic integration suite with `./tools/test_integration.sh` when Docker is available
- [X] T016 Run `/moonspec-verify` equivalent for `specs/227-report-bundle-workflow-publishing/spec.md` and write verification evidence in `specs/227-report-bundle-workflow-publishing/verification.md`

## Verification Notes

- T015 blocked in this managed container: `./tools/test_integration.sh` failed because `/var/run/docker.sock` is unavailable.

## Dependencies And Execution Order

1. T001-T004 establish context.
2. T005-T007 write tests before production code.
3. T008 confirms red-first behavior.
4. T009-T011 implement runtime behavior.
5. T012-T016 validate and verify.

## Parallel Opportunities

- T005, T006, and T007 can be prepared in parallel because they cover different test concerns.
- No production implementation tasks are parallel-safe because `report_artifacts.py` and `artifacts.py` changes are coupled.

## Implementation Strategy

Start with the smallest compact result validation API in `report_artifacts.py`, then wire service-level artifact creation and activity facade publication through `artifacts.py`. Keep workflow-facing return values to refs and bounded metadata only. Do not create new database tables or storage backends.
