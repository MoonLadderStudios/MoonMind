# Research: DooD Bounded Helper Containers

## Decision: Model helpers as a workload kind, not an agent runtime

**Rationale**: The DooD architecture already distinguishes session containers, one-shot workload containers, and bounded helper workload containers. A helper supports a step's execution but does not own agent continuity, model interaction, or session identity. Keeping helpers as workload containers preserves the boundary that `MoonMind.AgentRun` is only for true agent execution.

**Alternatives considered**:
- **Agent runtime child workflow**: Rejected because helpers are non-agent services and would confuse session/task identity.
- **Managed session sidecar**: Rejected because it would grant the session plane broader container ownership than intended.

## Decision: Enforce helper lifetime through both request TTL and profile maximum TTL

**Rationale**: The feature's core safety property is bounded lifetime. Request TTL gives each launch an explicit window, while profile maximum TTL lets operators cap classes of helper workloads according to risk and resource cost.

**Alternatives considered**:
- **Only profile default TTL**: Rejected because every helper launch should make the bounded window explicit.
- **Unbounded helper until task completion**: Rejected because task completion is not a sufficient lifecycle control after cancellation or worker interruption.

## Decision: Require readiness contracts for helper profiles

**Rationale**: A helper service must be usable across dependent sub-steps. Readiness avoids ambiguous downstream failures and gives operators direct evidence of whether the helper was usable.

**Alternatives considered**:
- **No readiness requirement**: Rejected because startup success is not service readiness.
- **Log-string readiness only**: Rejected as brittle and likely to leak unbounded logs.

## Decision: Publish helper diagnostics through bounded artifacts and metadata

**Rationale**: Existing DooD phases made artifacts and bounded workflow metadata authoritative. Helper start, readiness, unhealthy, cancellation, teardown, and expired cleanup should be diagnosable without relying on container state or raw worker logs.

**Alternatives considered**:
- **Container inspection as source of truth**: Rejected because containers are disposable operational state.
- **Embedding full logs in workflow metadata**: Rejected because logs can be large or sensitive.

## Decision: Use label-based expired-helper cleanup separate from one-shot cleanup

**Rationale**: Helpers survive longer than one-shot containers and need their own ownership kind so the janitor can target expired helpers without touching fresh helpers, one-shot workloads, session containers, or unrelated containers.

**Alternatives considered**:
- **Reuse one-shot `moonmind.kind=workload` cleanup only**: Rejected because helpers have different normal lifetime semantics.
- **Single broad MoonMind container sweep**: Rejected because it raises risk of deleting active or unrelated MoonMind containers.

## Decision: Preserve executable-tool path for helper exposure

**Rationale**: DooD workloads enter through executable tools or curated workload activities. Helper start/stop should follow that same policy-routed path when exposed to plans, keeping Docker authority on control-plane workers.

**Alternatives considered**:
- **Allow direct Docker from Codex sessions**: Rejected by DooD guardrails.
- **Expose arbitrary image/mount inputs**: Rejected because runner profiles replace arbitrary image strings in normal execution.
