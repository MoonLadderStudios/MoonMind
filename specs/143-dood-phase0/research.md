# Research: Docker-Out-of-Docker Phase 0 Contract Lock

## Context

Phase 0 of the DooD rollout is a contract-locking step. The main question is not how to build the launcher yet, but whether the canonical docs already agree on the architecture boundary and what minimal executable validation should guard that agreement.

## Findings

### Decision: Keep `docs/ManagedAgents/DockerOutOfDocker.md` as the canonical desired-state source

- **What was chosen**: Treat `docs/ManagedAgents/DockerOutOfDocker.md` as the canonical desired-state DooD document and adjust nearby docs to align with it.
- **Rationale**: The DooD doc already defines the glossary, the tool-path default, the one-shot workload-container emphasis, and the separation between session identity and workload identity.
- **Alternatives considered**: Split the contract across multiple canonical docs equally. Rejected because Phase 0 is meant to lock one boundary source before broader implementation work.

### Decision: Add short cross-reference wording instead of duplicating the full DooD design

- **What was chosen**: Add concise references in `docs/ManagedAgents/CodexCliManagedSessions.md` and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.
- **Rationale**: Constitution Principle XII requires canonical docs to stay declarative and avoid duplicating volatile rollout detail. The DooD doc should remain the detailed source while adjacent docs state only the boundary relevant to their scope.
- **Alternatives considered**: Copy large DooD sections into the session-plane and execution-model docs. Rejected because duplication would make drift more likely.

### Decision: Treat Docker-backed workloads as ordinary executable tools unless they launch a true managed runtime

- **What was chosen**: Preserve `tool.type = "skill"` as the initial execution primitive for DooD-backed workload launches and reserve `tool.type = "agent_runtime"` for true managed runtimes only.
- **Rationale**: This matches the existing `SkillAndPlanContracts` split and prevents specialized containers from being mislabeled as new `MoonMind.AgentRun` instances.
- **Alternatives considered**: Model specialized workload containers as child `MoonMind.AgentRun` workflows immediately. Rejected because Phase 0 explicitly locks the boundary on the tool path first.

### Decision: Add a focused unit test for documentation drift

- **What was chosen**: Add a dedicated pytest file that reads the canonical docs and the DooD tracker path and asserts the required phrases and references exist.
- **Rationale**: The repo does not already have a doc-contract test for this boundary. A small test gives Phase 0 executable validation without inventing production code outside the phase scope.
- **Alternatives considered**: Rely on manual review only. Rejected because the user asked for TDD and Phase 0 should still leave a durable guard.

## Audit Summary

| Area | Current state | Phase 0 action |
|------|---------------|----------------|
| `docs/ManagedAgents/DockerOutOfDocker.md` | Already defines glossary and one-shot workload emphasis | Tighten only where tracker/Phase 0 wording needs to stay explicit |
| `docs/ManagedAgents/CodexCliManagedSessions.md` | Links to DooD doc but does not explicitly describe session-assisted workload tools | Add short cross-reference paragraph |
| `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` | Defines true agent-runtime boundary but does not name Docker-backed workload tools explicitly | Add short execution-model note |
| `docs/tmp/remaining-work/` | No DooD tracker exists yet | Add tracker and register it in README |
| `tests/unit/` | No DooD Phase 0 documentation contract test exists | Add focused pytest coverage |
