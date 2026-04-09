# DooD Phase 0 Documentation Contract

## Purpose

Define the exact documentation assertions that Phase 0 must lock before Phase 1 implementation begins.

## Required canonical surfaces

1. `docs/ManagedAgents/DockerOutOfDocker.md`
2. `docs/ManagedAgents/CodexManagedSessionPlane.md`
3. `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
4. `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`

## Required assertions

### Glossary consistency

- The canonical docs must use the terms:
  - `session container`
  - `workload container`
  - `runner profile`
  - `session-assisted workload`

### Session-plane boundary

- The session-plane doc must say that session-plane steps may invoke control-plane tools that launch separate workload containers.
- The session-plane doc must say those workload containers are outside session identity.

### Execution-model boundary

- The execution-model doc must say Docker-backed workload launches are ordinary executable tools.
- The execution-model doc must say those launches are not new `MoonMind.AgentRun` instances unless the launched workload is itself a true managed runtime.

### Lifecycle scope

- The canonical docs must preserve one-shot workload containers as the initial DooD implementation scope.
- The canonical docs must preserve bounded helper containers as a later phase rather than the MVP.

### Tracker presence

- The canonical DooD doc must link to `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`.
- `docs/tmp/remaining-work/README.md` must list the same tracker.

## Validation surface

- A focused pytest test reads the required files and fails if any assertion above is no longer true.
