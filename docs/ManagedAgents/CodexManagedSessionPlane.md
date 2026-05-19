# Codex Managed Session Plane

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-04-14
Related:
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/DockerSidecarRuntime.md`](./DockerSidecarRuntime.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## Purpose

This document preserves the canonical Codex managed session plane entrypoint used
by managed-agent architecture references. The detailed Codex CLI session
contract lives in [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md).

The Codex managed session plane is the task-scoped managed runtime environment
for Codex continuity. It owns the session container, thread and turn lifecycle,
session reset boundaries, and continuity artifacts for one MoonMind task.

Ordinary repository Docker work that originates from the Codex session uses the
per-session sidecar runtime described in
[`DockerSidecarRuntime.md`](./DockerSidecarRuntime.md). The session container
gets a Docker CLI pointed at its own private daemon; it never receives the host
socket or MoonMind deployment credentials.

## Contract

The bounded session identity remains:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

Control-plane Docker workload containers remain available through
[`DockerOutOfDocker.md`](./DockerOutOfDocker.md) for MoonMind admin/update,
helper, and deliberately gated exceptional workloads. Those workload containers
remain outside session identity: they do not become `session_id`,
`session_epoch`, `container_id`, `thread_id`, or `active_turn_id`, and they are
not `MoonMind.AgentRun` executions unless the launched runtime is itself an
agent runtime.
