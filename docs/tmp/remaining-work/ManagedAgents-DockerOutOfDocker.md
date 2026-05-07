# ManagedAgents-DockerOutOfDocker Remaining Work

Temporary rollout tracker for [`docs/ManagedAgents/DockerOutOfDocker.md`](../../ManagedAgents/DockerOutOfDocker.md).

## Status

- Phase 0 documentation boundary: complete.
- Phase 1 workload contract and registry validation: complete.
- Phase 2 workload launcher routing: complete.
- Phase 3 bounded helper containers: complete.
- Phase 4 unrestricted container and Docker CLI policy: pending.
- Phase 5 artifact and observability hardening: pending.
- Phase 6 curated Unreal pilot: complete.
- Phase 7 operator rollout validation: pending.

## Open Items

- Validate unrestricted mode against the deployment policy before exposing it in operator defaults.
- Keep workload containers outside managed-session identity and `MoonMind.AgentRun` records.
- Retire this tracker once the remaining DooD rollout phases are implemented and verified.
