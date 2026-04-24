# Implementation Plan: Publish Durable DooD Observability Outputs

**Branch**: `253-publish-dood-observability` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/253-publish-dood-observability/spec.md`

## Summary

MM-504 is a runtime verification-first planning story focused on proving and, if needed, tightening the durable observability contract for Docker-backed workloads. The current repository already publishes workload artifacts and metadata in `moonmind/workloads/docker_launcher.py`, carries declared output and report-path contracts through `moonmind/workloads/tool_bridge.py`, exposes artifact link semantics in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/report_artifacts.py`, and contains unit plus hermetic integration coverage for workload artifact publication and routing. The plan is therefore to preserve MM-504 and the original Jira preset brief in feature-local artifacts, make unit and integration strategies explicit, and proceed verification-first with an implementation contingency only if focused tests expose drift in artifact classes, report publication semantics, or redaction/audit metadata behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | verify minimum durable outputs across representative Docker-backed launch types; implement only if output classes or failure-path capture drift from the spec | unit + integration |
| FR-002 | partial | `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/temporal/report_artifacts.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | confirm declared primary reports publish through the shared artifact/report contract for Docker-backed workloads and add missing verification or small runtime fixes if report linkage is incomplete | unit + integration |
| FR-003 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/artifacts.py`, `tests/unit/api/routers/test_task_runs.py`, `tests/integration/temporal/test_temporal_artifact_lifecycle.py` | verify stored artifacts and bounded metadata are sufficient for operator inspection without daemon-local state; implement only if API/read-model evidence is missing or inconsistent | unit + integration |
| FR-004 | partial | `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/registry.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | strengthen verification that workload mode, workload access, and unrestricted indicators are present and consistent across run and helper paths; add runtime metadata only if verification shows gaps | unit + integration |
| FR-005 | partial | `moonmind/workloads/docker_launcher.py`, `moonmind/utils/logging.py`, existing launcher redaction calls, limited launcher-focused tests | add focused verification for docker-host normalization and secret-like value redaction in stdout, stderr, diagnostics, and metadata; harden publication only if leakage is exposed | unit + integration |
| FR-006 | implemented_unverified | `moonmind/workflows/temporal/report_artifacts.py`, `moonmind/workflows/temporal/workflows/run.py`, `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | verify supported Docker-backed launch types emit the expected artifact classes and publication semantics consistently; implement contingency only if class drift appears | unit + integration |
| FR-007 | implemented_verified | `spec.md` (Input), `specs/253-publish-dood-observability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/dood-observability-publication-contract.md`, `quickstart.md` | preserve MM-504 through tasks and final verification output | traceability review |
| DESIGN-REQ-021 | partial | `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/temporal/report_artifacts.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | verify durable artifact publication, report publication, and bounded observability records remain shared across Docker-backed workload paths | unit + integration |
| DESIGN-REQ-022 | partial | `moonmind/workloads/docker_launcher.py`, `moonmind/utils/logging.py`, `docs/ManagedAgents/DockerOutOfDocker.md` §14.3 and §15.6 | verify explicit unrestricted markers plus normalized or redacted docker host and secret-like values in published outputs and metadata | unit + integration |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher and artifact helpers, pytest
**Storage**: Existing temporal artifact metadata/content store and workload output directories only; no new persistent storage
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_task_runs.py`
**Integration Testing**: `./tools/test_integration.sh`
**Target Platform**: MoonMind worker runtime, Temporal artifact publication path, and execution/task-run inspection surfaces
**Project Type**: Backend runtime and verification story for Docker-backed workload observability, artifact publication, and audit metadata
**Performance Goals**: Preserve bounded artifact publication and metadata serialization with no new workflow-history payload bloat or new runtime services
**Constraints**: Keep artifacts and bounded metadata authoritative; preserve shared report publication semantics; redact secret-like values before publication; make unrestricted usage explicit in metadata; preserve MM-504 traceability; do not add compatibility wrappers
**Scale/Scope**: One story covering durable summary/log/diagnostics/output publication, report publication, redacted audit metadata, operator-visible inspection, and artifact-class consistency across supported Docker-backed workload launch types

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - stays on the existing workload launcher, artifact service, and report helper boundaries instead of inventing a new observability subsystem.
- II. One-Click Agent Deployment: PASS - introduces no new service, credential, or operator prerequisite.
- III. Avoid Vendor Lock-In: PASS - the story is about MoonMind-owned artifact and metadata contracts, not vendor-specific runtime behavior.
- IV. Own Your Data: PASS - durable evidence remains in MoonMind-managed artifact storage and task workspaces.
- V. Skills Are First-Class and Easy to Add: PASS - Docker-backed workload tools remain executable skill contracts on the existing tool registry path.
- VI. Replaceable AI Scaffolding: PASS - work focuses on durable runtime contracts and verification evidence rather than agent-side scaffolding.
- VII. Runtime Configurability: PASS - existing deployment-owned Docker mode and artifact/report publication settings remain in force.
- VIII. Modular and Extensible Architecture: PASS - changes stay localized to workload publication helpers, artifact/report contracts, and verification surfaces.
- IX. Resilient by Default: PASS - durable outputs, bounded metadata, and explicit failure publication remain the core resiliency mechanism for unattended inspection.
- X. Facilitate Continuous Improvement: PASS - downstream verification can report concrete evidence and remaining drift for MM-504.
- XI. Spec-Driven Development: PASS - MM-504 and the preserved Jira preset brief remain the source of truth for planning.
- XII. Canonical Documentation Separation: PASS - desired-state runtime requirements remain in `docs/ManagedAgents/DockerOutOfDocker.md`; implementation planning remains feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases, translation layers, or fallback behaviors are proposed.

## Project Structure

### Documentation (this feature)

```text
specs/253-publish-dood-observability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── dood-observability-publication-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workloads/
├── docker_launcher.py
├── registry.py
└── tool_bridge.py

moonmind/workflows/temporal/
├── activity_runtime.py
├── artifacts.py
├── report_artifacts.py
└── workflows/run.py

moonmind/schemas/
└── workload_models.py

tests/unit/workloads/
├── test_docker_workload_launcher.py
└── test_workload_tool_bridge.py

tests/unit/workflows/temporal/
├── test_activity_runtime.py
└── test_report_workflow_rollout.py

tests/unit/api/routers/
└── test_task_runs.py

tests/integration/temporal/
├── test_integration_ci_tool_contract.py
├── test_profile_backed_workload_contract.py
└── test_temporal_artifact_lifecycle.py
```

**Structure Decision**: MM-504 stays entirely on the existing Docker-backed workload launcher, artifact publication, report helper, and execution inspection path. No new API surface or persistent data model is required; the likely work is targeted verification plus small runtime hardening only if focused tests expose gaps in report publication, metadata redaction, or artifact-class consistency.

## Complexity Tracking

No constitution violations.
