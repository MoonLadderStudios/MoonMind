# Implementation Plan: DooD Executable Tool Exposure

**Branch**: `152-dood-executable-tools` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)

## Summary

Expose Phase 2 Docker workload launching through MoonMind's existing executable tool contract. The implementation keeps Docker-backed workloads on `tool.type = "skill"`, routes `docker_workload` capabilities to the existing `agent_runtime` fleet, and adds curated handlers for `container.run_workload` and `unreal.run_tests` that validate runner-profile requests before invoking `DockerWorkloadLauncher`.

## Technical Context

**Language/Version**: Python 3.10+  
**Primary Dependencies**: Existing tool registry/dispatcher, Temporal activity catalog, Phase 1 workload schemas, Phase 2 Docker launcher  
**Testing**: `./tools/test_unit.sh` plus focused workload/tool/Temporal unit tests  
**Constraints**: Do not expose raw image, mount, device, or arbitrary Docker parameters; do not overload `tool.type = "agent_runtime"` or managed-session verbs

## Constitution Check

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses MoonMind's existing tool orchestration and Docker launcher boundary. |
| II. One-Click Agent Deployment | PASS | Reuses the existing `agent_runtime` fleet and Docker proxy wiring. |
| III. Avoid Vendor Lock-In | PASS | Generic profile-backed container tool plus one curated Unreal wrapper. |
| IV. Own Your Data | PASS | Results remain bounded local metadata; artifact expansion is Phase 4. |
| V. Skills Are First-Class | PASS | Workloads are exposed as executable tools (`tool.type = "skill"`). |
| VI. Bittersweet Lesson | PASS | The bridge is thin and replaceable behind tests. |
| VII. Runtime Configurability | PASS | Runner profiles remain deployment-owned registry entries. |
| VIII. Modular Architecture | PASS | Adds a workload tool bridge without changing managed-session controllers. |
| IX. Resilient by Default | PASS | Uses validated workload requests and existing timeout/cleanup launcher behavior. |
| X. Continuous Improvement | PASS | Tool results include structured workload status metadata. |
| XI. Spec-Driven Development | PASS | Spec, plan, and tasks trace the implementation. |
| XII. Canonical Docs vs tmp | PASS | Completion notes live in `docs/tmp/remaining-work/`. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility alias or old workload path is introduced. |

## Project Structure

```text
moonmind/workloads/tool_bridge.py
moonmind/workflows/temporal/activity_catalog.py
moonmind/workflows/temporal/activity_runtime.py
moonmind/workflows/temporal/worker_runtime.py
tests/unit/workloads/test_workload_tool_bridge.py
tests/unit/workflows/temporal/test_activity_catalog.py
tests/unit/workflows/temporal/test_activity_runtime.py
tests/unit/workflows/temporal/workflows/test_run_integration.py
```

## Complexity Tracking

No constitution violations require complexity waivers.
