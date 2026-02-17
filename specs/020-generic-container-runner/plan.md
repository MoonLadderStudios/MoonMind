# Implementation Plan: Generic Task Container Runner

**Branch**: `020-generic-container-runner` | **Date**: 2026-02-17 | **Spec**: `specs/020-generic-container-runner/spec.md`  
**Input**: Feature specification from `/specs/020-generic-container-runner/spec.md`

## Summary

Add a generic `task.container` execution path so queue tasks can run arbitrary Docker images/commands against the prepared repository workspace. Keep worker logic toolchain-agnostic (Unity/.NET/Unreal all data-driven from payload), preserve existing prepare/execute/publish lifecycle semantics, and add unit tests validating normalization, capability behavior, command construction, and execution outcomes.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Existing worker async command execution (`CodexWorker` + `CodexExecHandler`), canonical task payload normalization (`task_contract.py`), Docker CLI via worker runtime image  
**Storage**: Existing per-job filesystem workspace/artifact layout under `MOONMIND_WORKDIR`  
**Testing**: Unit tests via `./tools/test_unit.sh`  
**Target Platform**: MoonMind worker container deployed with Docker host access via `DOCKER_HOST`/`docker-proxy`  
**Project Type**: Backend service modules (worker + workflow payload normalization)  
**Performance Goals**: No regression for non-container tasks; container stage event emission and artifact capture stay within current worker lifecycle latency expectations  
**Constraints**: Preserve backward compatibility for existing task payloads and runtimes; keep security policy controls out of scope for this change; avoid introducing credential leakage in command logs  
**Scale/Scope**: Task payload contract + codex-worker execution path + compose worker env wiring + unit tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholders without enforceable MUST-level policy.
- Repository governance from `AGENTS.md` is enforced:
  - Global spec numbering uses `020`.
  - Unit tests run with `./tools/test_unit.sh`.
  - No secret material persisted in code/tests/docs.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/020-generic-container-runner/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-container-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/agent_queue/task_contract.py               # canonical payload support for task.container
└── agents/codex_worker/worker.py                        # container execution path in execute stage

docker-compose.yaml                                      # codex-worker DOCKER_HOST/capability defaults for container execution

tests/unit/
├── workflows/agent_queue/test_task_contract.py          # task.container normalization/validation tests
└── agents/codex_worker/test_worker.py                   # container execution path + config tests
```

**Structure Decision**: Extend existing task normalization and worker execute-stage logic to keep container behavior inside the canonical queue path and avoid introducing a separate runner subsystem.

## Phase 0: Research Plan

1. Confirm current task canonicalization behavior for additive task fields and required capability merging.
2. Confirm current execute-stage flow in `CodexWorker` and identify insertion point for container execution before runtime adapter branching.
3. Validate worker command execution utilities and determine how to safely handle timeout + cleanup for long-running `docker run` commands.
4. Confirm compose-level worker defaults for Docker host/proxy integration and minimal runtime config deltas needed.

## Phase 1: Design Outputs

- `research.md`: selected execution strategy and rejected alternatives.
- `data-model.md`: payload + execution request/result model for container tasks.
- `contracts/task-container-contract.md`: container payload contract and worker event/artifact contract.
- `quickstart.md`: submission example, expected worker behavior, and validation steps.

## Post-Design Constitution Re-check

- Design contains production runtime file changes and explicit validation tests.
- No additional constitution conflicts detected beyond placeholder constitution baseline.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
