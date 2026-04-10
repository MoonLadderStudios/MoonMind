# Data Model: Docker-Out-of-Docker Phase 0 Contract Lock

## Entities

### SessionContainerTerm

- **Purpose**: Represents the canonical term for the task-scoped managed Codex container used for continuity within one task.
- **Required attributes**:
  - `name`: `session container`
  - `identity_scope`: task-scoped managed session only
  - `durable_truth_role`: none; continuity cache only

### WorkloadContainerTerm

- **Purpose**: Represents the canonical term for the specialized non-agent container launched by the control plane for a bounded workload.
- **Required attributes**:
  - `name`: `workload container`
  - `launch_path`: ordinary executable tool on the control plane
  - `identity_scope`: separate from session identity
  - `default_lifecycle`: one-shot container for Phases 1 through 4

### RunnerProfileTerm

- **Purpose**: Represents the curated MoonMind definition of an allowed workload-container shape.
- **Required attributes**:
  - `name`: `runner profile`
  - `authority`: MoonMind policy / control plane
  - `shape`: image, mount, environment, resource, network, and cleanup rules

### SessionAssistedWorkloadTerm

- **Purpose**: Represents a managed-session step that requests a specialized workload through the control plane.
- **Required attributes**:
  - `name`: `session-assisted workload`
  - `session_identity_transfer`: forbidden
  - `docker_authority`: remains with the Docker-capable worker fleet

### DoodRemainingWorkTracker

- **Purpose**: Represents the temporary implementation tracker linked from the canonical DooD doc.
- **Required attributes**:
  - `path`: `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`
  - `role`: phased rollout checklist after Phase 0
  - `canonical_relationship`: linked from the canonical DooD doc, not duplicated inside it

### DoodPhase0DocContractTest

- **Purpose**: Represents the automated validation surface that guards the Phase 0 boundary.
- **Required attributes**:
  - `path`: `tests/unit/docs/test_dood_phase0_contract.py`
  - `assertions`: glossary consistency, execution primitive wording, tracker presence
  - `failure_mode`: fail fast if any required phrase or file path disappears
