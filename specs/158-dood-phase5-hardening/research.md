# Research: DooD Phase 5 Hardening

## Decision: Enforce workload policy before Docker launch

**Rationale**: Policy failures such as unknown profile, disallowed environment key, unsafe mount, excessive resource request, host networking, privileged posture, implicit device access, and missing workload capability must stop before a workload container exists. This protects the managed session plane and keeps denial behavior deterministic.

**Alternatives considered**:

- Launch then inspect/stop unsafe containers: rejected because it creates an avoidable unsafe interval and complicates cleanup.
- Let Docker CLI validation be the primary policy engine: rejected because Docker errors are not stable operator-facing MoonMind policy reasons.

## Decision: Keep runner-profile registry policy deployment-owned and fail-closed

**Rationale**: Phase 5 requires runner profile allowlists and image provenance controls. The safest default is an operator-owned registry with explicit approved registries/images and no repo-authored overrides in normal execution.

**Alternatives considered**:

- Permit repo-authored profile overrides: rejected for this phase because it needs approval/policy workflow not included in Phase 5.
- Permit arbitrary image strings for advanced use: rejected for normal execution because runner profiles replace free-form image input.

## Decision: Use explicit default no-privileged launch posture

**Rationale**: The launcher should make the security posture visible in constructed launch arguments, including no privileged execution, no broad Linux capabilities, no host networking by default, and no implicit device access.

**Alternatives considered**:

- Rely only on Docker defaults: rejected because Phase 5 calls for explicit safe defaults and audit-friendly launch decisions.
- Support GPU/device defaults now: rejected because implicit device access is forbidden; approved device policy can be planned later.

## Decision: Prevent managed-runtime auth material from reaching workload containers

**Rationale**: Workload containers are not Codex, Claude, Gemini, or other managed session containers. They must not inherit session auth volumes or credentials automatically because that would collapse the workload/session boundary and increase secret exposure.

**Alternatives considered**:

- Allow auth volumes when a profile requests them: rejected for Phase 5 because secret injection must be explicit MoonMind policy, not inherited volume reuse.
- Copy broad worker environment into workloads: rejected because it risks leaking credentials and violates least privilege.

## Decision: Apply per-profile and per-fleet workload concurrency guards

**Rationale**: Heavy workloads such as Unreal jobs can consume enough CPU, memory, disk, and Docker capacity to starve normal managed-runtime work. Per-profile limits protect hot profiles; a fleet-level limit bounds aggregate pressure.

**Alternatives considered**:

- Use only Temporal worker concurrency: rejected because it does not distinguish heavy Docker workload profiles from other agent-runtime activities.
- Use a global external scheduler now: rejected as unnecessary for Phase 5 one-shot workloads and higher operational complexity.

## Decision: Sweep expired workload containers by MoonMind ownership labels and TTL

**Rationale**: Container state is not durable truth. Workload containers need deterministic ownership and expiration metadata so cleanup can remove abandoned MoonMind-owned containers without touching unrelated containers.

**Alternatives considered**:

- Sweep by container name prefix only: rejected because labels are more precise and support ownership filtering.
- Sweep all stopped containers: rejected because it can remove containers not owned by MoonMind.

## Decision: Expose stable non-secret denial and cleanup diagnostics

**Rationale**: Operators need to know whether a workload was denied by policy, capacity, missing capability, or malformed input without reading raw worker internals or exposing secrets.

**Alternatives considered**:

- Return raw exception strings only: rejected because they are unstable and hard to aggregate.
- Include full request/environment details: rejected because diagnostics must avoid secrets and broad config dumps.
