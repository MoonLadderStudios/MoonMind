# Research: Docker-Out-of-Docker Workload Launcher

## Decision 1: Use the Existing `agent_runtime` Fleet for Phase 2

**Decision**: Route one-shot Docker workload launches to the existing Docker-capable `agent_runtime` worker fleet with a distinct workload capability.

**Rationale**: The fleet already owns the Docker proxy wiring and workspace volume mount needed to launch containers safely from the control plane. This avoids granting Docker authority to managed session containers and avoids creating a new worker deployment before one-shot workload behavior is proven.

**Alternatives considered**:

- New dedicated workload fleet: rejected for Phase 2 because it increases deployment surface before the launcher contract is validated.
- Managed-session controller verbs: rejected because workload containers are not managed sessions and must not inherit session identity.

## Decision 2: Build a Dedicated Launcher Module

**Decision**: Add a dedicated Docker workload launcher and cleanup helper under the workload module boundary.

**Rationale**: Phase 1 already established workload schemas and runner-profile validation under `moonmind/workloads/`. Keeping launch construction and cleanup there gives later tool integration a single service surface without coupling the logic to Temporal workflow code or managed-session controllers.

**Alternatives considered**:

- Add Docker launch logic directly to activity handlers: rejected because it would make activity runtime code responsible for container argument construction and cleanup policy.
- Extend managed runtime launcher: rejected because managed agent runs and workload containers have different lifecycles and result contracts.

## Decision 3: Return Bounded Result Metadata in Phase 2

**Decision**: Capture stdout, stderr, exit status, timing, timeout reason, selected profile, and selected image as bounded workload result metadata.

**Rationale**: Phase 4 will add durable artifact publication and richer live-log linkage. Phase 2 still needs enough metadata for tests and operator diagnostics while avoiding large workflow payloads.

**Alternatives considered**:

- Publish artifacts immediately: deferred to Phase 4 to keep this phase focused on launching and cleanup.
- Embed full logs in results: rejected because large logs should not become workflow payload truth.

## Decision 4: Use Explicit Stop, Terminate, Remove Cleanup

**Decision**: Timeout and cancellation cleanup should attempt bounded stop, then terminate, then remove when the profile cleanup policy requires container removal.

**Rationale**: This sequence gives cooperative process shutdown a chance while ensuring routine timed-out workload containers are not left running. Label-based orphan lookup provides a follow-up cleanup surface for abnormal worker crashes.

**Alternatives considered**:

- Rely on Docker auto-remove only: rejected because it gives less explicit cleanup control and makes timeout diagnostics harder.
- Terminate immediately on timeout: rejected because some workloads may need a short grace interval to flush reports or logs.

## Decision 5: Keep Phase 2 Independent of Generic Tool Exposure

**Decision**: Add the workload activity/capability now, but leave `container.run_workload` and domain tools such as `unreal.run_tests` to Phase 3.

**Rationale**: The launcher must be validated before exposing it through plan/tool contracts. This preserves the DooD sequencing and keeps the Phase 2 blast radius limited to the Docker-capable worker fleet.

**Alternatives considered**:

- Implement tool definitions in the same phase: rejected because tool input contracts and artifact behavior need separate acceptance tests.
