# Implementation Plan: DooD Bounded Helper Containers

**Branch**: `163-dood-bounded-helper-containers` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/163-dood-bounded-helper-containers/spec.md`

**Mode**: Runtime implementation. Required deliverables include production runtime code changes plus validation tests; docs/spec-only completion is invalid.

## Summary

Add Phase 7 bounded helper containers to the Docker-out-of-Docker workload plane. The implementation extends the existing workload profile/request/result contracts with a bounded helper workload kind, TTL/readiness policy, explicit ownership, detached helper start, readiness observation, explicit teardown, and expired-helper cleanup. The helper lifecycle remains an executable workload capability on control-plane-owned Docker-capable workers and must not become managed-session identity or a `MoonMind.AgentRun` substitute.

## Technical Context

**Language/Version**: Python 3.12 backend/runtime, TypeScript only if UI projection changes become necessary  
**Primary Dependencies**: Pydantic v2 contracts, Temporal worker/activity infrastructure, Docker CLI via existing Docker proxy path, pytest, existing workload tool bridge and artifact helpers  
**Storage**: Durable artifacts and bounded workflow metadata; no container state as durable truth  
**Testing**: `./tools/test_unit.sh` for final verification; focused pytest coverage under `tests/unit/workloads/` and Temporal/tool boundary tests when execution routing changes  
**Target Platform**: Linux Docker Compose deployment with Docker-capable `agent_runtime` worker fleet  
**Project Type**: Backend/runtime feature inside the existing MoonMind monorepo  
**Performance Goals**: Helper readiness and teardown must remain bounded by profile TTL/probe/cleanup policy; captured logs and diagnostics must stay bounded and artifact-backed  
**Constraints**: No raw Docker authority in Codex session containers; no arbitrary image strings; no indefinite helper lifetimes; no secrets/prompts/transcripts/raw scrollback in metadata; helper state is not durable truth  
**Scale/Scope**: Phase 7 optional helper lifecycle for short-lived service dependencies inside one bounded execution window; one-shot workload behavior must remain unchanged

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Helpers stay behind MoonMind orchestration and do not introduce a new agent runtime model.
- **II. One-Click Agent Deployment**: PASS. Uses existing Docker Compose/Docker proxy assumptions and runner-profile policy.
- **III. Avoid Vendor Lock-In**: PASS. The helper contract is generic to curated workload profiles, not a vendor-specific runtime.
- **IV. Own Your Data**: PASS. Helper diagnostics and outputs remain local artifacts/bounded metadata.
- **V. Skills Are First-Class and Easy to Add**: PASS. Helper launch remains executable-tool/workload capability, separate from agent instruction bundles.
- **VI. Replaceable Scaffolding, Scientific Method**: PASS. Contract and tests define the helper lifecycle before implementation details.
- **VII. Runtime Configurability**: PASS. TTL, readiness, resource, and cleanup policy come from runner profiles and request payloads.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay in workload schema/registry/launcher/tool boundary modules.
- **IX. Resilient by Default**: PASS. TTL, cancellation, readiness failure, and cleanup paths are explicit and testable.
- **X. Facilitate Continuous Improvement**: PASS. Helper outcomes produce structured summaries/diagnostics for operators.
- **XI. Spec-Driven Development**: PASS. This plan follows the ready spec and generates design artifacts before tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This planning work remains under `specs/`; no canonical doc migration narrative is added.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan introduces no compatibility aliases; superseded internal helper shapes must be removed if discovered.

No constitution violations are expected.

## Project Structure

### Documentation (this feature)

```text
specs/163-dood-bounded-helper-containers/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── bounded-helper-workload-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── workload_models.py
├── workloads/
│   ├── registry.py
│   ├── docker_launcher.py
│   └── tool_bridge.py
└── workflows/
    └── temporal/
        ├── activity_runtime.py
        ├── worker_runtime.py
        └── workers.py

tests/
└── unit/
    ├── workloads/
    │   ├── test_workload_contract.py
    │   ├── test_docker_workload_launcher.py
    │   └── test_workload_tool_bridge.py
    └── workflows/temporal/
        └── test_workload_run_activity.py
```

**Structure Decision**: Implement the helper lifecycle in the existing Docker workload modules. Add Temporal/tool-boundary tests only when helper launch/teardown is exposed through executable tools or activities. Keep session-plane code out of scope except for assertions that helper metadata does not become session identity.

## Complexity Tracking

No constitution violations or structural complexity exceptions are planned.

## Phase 0: Research & Decisions

Research decisions are captured in [research.md](./research.md). All planning unknowns are resolved with existing repo patterns: extend workload contracts, use profile-backed policy validation, publish bounded artifacts, and keep helper execution on Docker-capable control-plane workers.

## Phase 1: Design & Contracts

Design artifacts generated:

- [data-model.md](./data-model.md)
- [contracts/bounded-helper-workload-contract.md](./contracts/bounded-helper-workload-contract.md)
- [quickstart.md](./quickstart.md)

No `DOC-REQ-*` identifiers exist in `spec.md`, so no requirements traceability contract is required.

## Post-Design Constitution Check

- **I** PASS: helper containers remain orchestrated workloads, not new agent implementations.
- **II** PASS: no new deployment prerequisite beyond existing Docker-capable worker path.
- **III** PASS: contract remains profile-based and generic.
- **IV** PASS: artifacts and bounded metadata remain authoritative.
- **V** PASS: executable tooling remains separate from agent instruction skills.
- **VI** PASS: testable contracts and quickstart preserve verifiability.
- **VII** PASS: profile/request policy controls runtime behavior.
- **VIII** PASS: module boundaries align with existing workload architecture.
- **IX** PASS: TTL, readiness, cancellation, and teardown paths are covered.
- **X** PASS: diagnostics support operator review.
- **XI** PASS: spec/plan/design artifacts are complete for task generation.
- **XII** PASS: no canonical-doc construction diary added.
- **XIII** PASS: no compatibility aliases or deprecated helper paths planned.

## Validation Strategy

Run focused tests first, then full unit verification:

```bash
./tools/test_unit.sh --python-only \
  tests/unit/workloads/test_workload_contract.py \
  tests/unit/workloads/test_docker_workload_launcher.py \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py

./tools/test_unit.sh
```

If helper exposure touches Temporal activity routing or worker topology, include the relevant activity catalog and worker runtime tests in the focused command.
