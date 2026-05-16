# Story Breakdown: Managed Agent Docker Sidecar Runtime

- Source design: `docs/ManagedAgents/DockerSidecarRuntime.md`
- Original source reference path: `docs/ManagedAgents/DockerSidecarRuntime.md`
- Story extraction date: 2026-05-16T08:58:18Z
- Requested output mode: jira
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The design defines a desired-state managed-agent Docker sidecar runtime where normal repository tests and builds run through ordinary Docker commands from the agent container while a private per-session sibling sidecar owns the Docker daemon. It preserves host-socket isolation, same-path workspace mounts, per-session daemon state, explicit runtime modes, readiness and cleanup behavior, audit metadata, and a strict separation between ordinary agent workloads and MoonMind admin/update Docker access. It also documents future portability to Kubernetes sidecar Pods and Kubernetes Jobs while keeping artifacts and bounded session metadata as the source of truth rather than nested container state.

## Coverage Points

- **DESIGN-REQ-001 - Managed sessions run ordinary Docker commands through a sidecar runtime** (requirement, 1. Purpose; 2. Core decisions): Normal repo tests, builds, docker run, docker build, and docker compose run originate from the managed agent session without MoonMind-specific workload syntax.
- **DESIGN-REQ-002 - Docker daemon is a sibling sidecar, not embedded in the agent image** (constraint, 1. Purpose; 2. Core decisions; 8. Agent image contract): The agent image carries only client tooling; dockerd, containerd, runc, and graph storage remain outside the agent container.
- **DESIGN-REQ-003 - Host Docker socket is forbidden for managed sessions and sidecars** (security, 1. Purpose; 2. Core decisions; 9. Docker sidecar contract; 14. What the session sees): Neither the agent nor sidecar may mount or substitute the host /var/run/docker.sock.
- **DESIGN-REQ-004 - Workspace path must match exactly in agent and sidecar** (constraint, 5.1 Workspace path invariant; 10. Volume contract; 23. Validation rules): Bind mount sources are resolved by the daemon, so launch must reject mismatched absolute workspace paths.
- **DESIGN-REQ-005 - MoonMind workspace layout remains canonical** (state-model, 5.2 MoonMind workspace convention): The sidecar runtime keeps agent_workspaces mounted at /work/agent_jobs with repo and artifacts under the established per-run layout.
- **DESIGN-REQ-006 - Runtime profile declaratively describes agent, sidecar, volumes, resources, readiness, and policy** (artifact, 6. Session runtime profile): Launch behavior is materialized from existing launcher inputs and provider/runtime profile records, not ad hoc imperative setup.
- **DESIGN-REQ-007 - Runtime modes are explicit and cannot be raised by task instructions** (requirement, 7. Runtime modes): Profiles choose docker-sidecar, docker-sidecar-rootless, no-docker, or future kubernetes-job; tasks cannot grant themselves more Docker capability.
- **DESIGN-REQ-008 - Agent image contract stays lightweight and CLI-only for Docker** (constraint, 8. Agent image contract): The agent image must include shell, git, network basics, Docker CLI and optional Compose plugin, but no daemon or deployment credentials.
- **DESIGN-REQ-009 - Sidecar image is generic, prebuilt, pinned, and credential-free** (security, 9. Docker sidecar contract; 23. Validation rules): The sidecar needs only a daemon and session volumes; it must not receive MoonMind code, tokens, deployment credentials, or unpinned image tags.
- **DESIGN-REQ-010 - Volumes have per-session lifecycle and sharing rules** (state-model, 10. Volume contract; 20. Cleanup model): Workspace, docker-socket, docker-graph, and optional caches have declared mount paths, ownership, lifecycle, and cleanup semantics.
- **DESIGN-REQ-011 - Docker deployment materializes as two containers and three per-session volumes with ownership names and labels** (integration, 11. Materialized Docker shape; 22. Audit and observability): Current Docker deployments create session agent and sidecar containers plus workspace, socket, and graph volumes following MoonMind naming and labeling conventions.
- **DESIGN-REQ-012 - Kubernetes portability maps the same profile to a future two-container Pod** (integration, 12. Materialized Kubernetes shape (future); 25. Backend portability): The same logical contract should map to an agent plus sidecar Pod with shared PVC/emptyDir volumes.
- **DESIGN-REQ-013 - Kubernetes Job mode is a future fallback for clusters that disallow DinD** (integration, 7. Runtime modes; 12. Materialized Kubernetes shape (future); 25.3 Kubernetes-native fallback): The design keeps a future native workload mode that preserves isolation but gives up ordinary in-session Docker commands.
- **DESIGN-REQ-014 - Session request and status expose Docker capability, readiness, mode, checks, and degraded reasons** (public-contract, 13. Session request and status; 19. Readiness behavior): Temporal session workflows request runtime profiles and surface whether Docker is available, ready, and usable by skills/plans.
- **DESIGN-REQ-015 - Session-visible Docker scope is private to the current session** (security, 14. What the session sees; 17. Policy model): The agent sees its DOCKER_HOST and only its session containers, not host/app/other-session containers.
- **DESIGN-REQ-016 - Repository workloads use normal scripts and Docker commands** (requirement, 15. Repository workload examples; 16. Repository script convention; 24. Skill and plan guidance): Repo tests should run through scripts like test-container.sh or direct docker/compose commands rather than MoonMind workload bridge tools.
- **DESIGN-REQ-017 - Policy model enforces topology at MVP and leaves per-operation proxy enforcement for future hardening** (security, 17. Policy model): MVP must enforce private daemon/no host socket/no app control/no deployment credentials; blocking specific Docker API operations can come later via proxy.
- **DESIGN-REQ-018 - Resource limits are applied outside the nested daemon** (requirement, 18. Resource model): The sidecar and session receive outer CPU, memory, runtime, ephemeral storage, and nested-container limits that the daemon cannot exceed.
- **DESIGN-REQ-019 - Readiness behavior controls session startup and degraded reporting** (requirement, 19. Readiness behavior): Docker version/info probes run with required/optional behavior and explicit timeout-driven failure or degraded status.
- **DESIGN-REQ-020 - Cleanup stops nested containers and removes sidecar state according to retention policy** (requirement, 20. Cleanup model): Session end, sidecar failure, and agent failure have declared cleanup and preservation behavior.
- **DESIGN-REQ-021 - MoonMind admin/update Docker access remains isolated in a separate ops runtime** (integration, 21. Separation from the MoonMind admin/update path; 26. Stable design rules; 27. Final declarative contract): Ordinary managed sessions must not deploy, restart, roll back, inspect, or kill MoonMind app containers; admin Docker access is a separate runtime.
- **DESIGN-REQ-022 - Audit and observability label every sidecar deployment and keep artifacts authoritative** (observability, 22. Audit and observability; 26. Stable design rules): Session containers and volumes carry ownership labels; daemon readiness and versions are reported; durable work evidence remains in the artifact pipeline.
- **DESIGN-REQ-023 - Launcher validation fails closed for unsafe or inconsistent profiles** (requirement, 23. Validation rules): Before launch, the system rejects missing client support, daemon-in-agent, wrong DOCKER_HOST, mismatched mounts, host socket, credentials, API socket mounts, unpinned sidecars, and shared daemons.
- **DESIGN-REQ-024 - Canonical docs require follow-up alignment after adopting sidecar runtime** (migration, 1.1 Relationship to existing architecture; 28. Follow-up alignment work): Related architecture docs must be updated so DooD is no longer presented as the default repo workload path for managed sessions.
- **DESIGN-REQ-025 - Explicit out-of-scope boundaries constrain the sidecar runtime effort** (non-goal, 3. Scope and non-goals): The design excludes MoonMind Kubernetes orchestration, arbitrary control-plane shells, container marketplaces, runtime-specific session protocols, and provider-profile/secrets/OAuth subsystem changes.

## Ordered Story Candidates

### STORY-001: Add declarative Docker sidecar runtime profile validation

- Short name: `sidecar-profile-validation`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 6. Session runtime profile, 7. Runtime modes, 10. Volume contract, 23. Validation rules, 3. Scope and non-goals
- Description: As a MoonMind operator, I need managed-session Docker capability to be declared and validated before launch so unsafe or inconsistent runtime profiles fail closed instead of starting a broken or over-privileged session.
- Independent test: Unit and launcher-boundary tests submit valid and invalid runtime profiles and verify accepted profile metadata or deterministic validation errors without starting real Docker workloads.
- Dependencies: None
- Needs clarification: None

Acceptance criteria:
- A valid docker-sidecar profile with matching workspace paths, pinned sidecar image, Docker CLI enabled, and sidecar socket DOCKER_HOST passes validation.
- Profiles fail validation when daemonInAgent is true, DOCKER_HOST points outside the declared socket path, workspace mount paths differ, host Docker socket is mounted, credentials or unrelated session tokens are injected, the API container is configured for normal workload Docker socket access, the sidecar image is unpinned, or daemon scope is shared.
- Runtime mode is read from deployment/profile configuration and task instructions cannot raise Docker capability.
- Validation errors explain the broken invariant and operational consequence.

Requirements:
- Expose a typed profile model for workloadMode, workspace, agent Docker client settings, dockerSidecar settings, resources, readiness, and policy.
- Validate all minimum rules listed in the source document before session launch.
- Keep explicit non-goals outside the contract surface.

Source design coverage:
- DESIGN-REQ-004: This story explicitly owns enforcement or delivery of: Workspace path must match exactly in agent and sidecar
- DESIGN-REQ-006: This story explicitly owns enforcement or delivery of: Runtime profile declaratively describes agent, sidecar, volumes, resources, readiness, and policy
- DESIGN-REQ-007: This story explicitly owns enforcement or delivery of: Runtime modes are explicit and cannot be raised by task instructions
- DESIGN-REQ-010: This story explicitly owns enforcement or delivery of: Volumes have per-session lifecycle and sharing rules
- DESIGN-REQ-023: This story explicitly owns enforcement or delivery of: Launcher validation fails closed for unsafe or inconsistent profiles
- DESIGN-REQ-025: This story explicitly owns enforcement or delivery of: Explicit out-of-scope boundaries constrain the sidecar runtime effort

Out of scope:
- Implementing Kubernetes orchestration, arbitrary control-plane shell execution, provider OAuth/secrets subsystems, or runtime-specific Codex/Claude/Gemini protocols.

Assumptions:
- Existing launcher inputs and provider/runtime profile records can carry the new profile fields without a new persistent table.

### STORY-002: Launch managed sessions with a private Docker sidecar

- Short name: `private-sidecar-launch`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 1. Purpose, 2. Core decisions, 5. Runtime model, 5.2 MoonMind workspace convention, 8. Agent image contract, 9. Docker sidecar contract, 10. Volume contract, 11. Materialized Docker shape
- Description: As a managed agent user, I need a launched session to contain an agent container with Docker CLI and a sibling private Docker daemon sidecar so ordinary containerized test and build commands work without host socket access.
- Independent test: Launcher integration or adapter-boundary tests create a session launch plan and assert generated containers, volumes, environment, image contracts, mount paths, and forbidden mounts.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- The agent container includes Docker CLI/optional Compose support but not dockerd, containerd, runc, Docker graph storage, host socket, or deployment credentials.
- The sidecar uses a pinned generic Docker daemon image and receives no MoonMind codebase, deployment credentials, session tokens, or host Docker socket.
- The workspace is mounted at the same absolute path in both containers and follows the existing /work/agent_jobs layout.
- Session-scoped container and volume names follow the documented moonmind-session pattern.

Requirements:
- Create one private daemon sidecar per Docker-enabled session.
- Share socket and workspace volumes between the agent and sidecar only as declared.
- Keep graph storage session-scoped and sidecar-only.

Source design coverage:
- DESIGN-REQ-001: This story explicitly owns enforcement or delivery of: Managed sessions run ordinary Docker commands through a sidecar runtime
- DESIGN-REQ-002: This story explicitly owns enforcement or delivery of: Docker daemon is a sibling sidecar, not embedded in the agent image
- DESIGN-REQ-003: This story explicitly owns enforcement or delivery of: Host Docker socket is forbidden for managed sessions and sidecars
- DESIGN-REQ-005: This story explicitly owns enforcement or delivery of: MoonMind workspace layout remains canonical
- DESIGN-REQ-008: This story explicitly owns enforcement or delivery of: Agent image contract stays lightweight and CLI-only for Docker
- DESIGN-REQ-009: This story explicitly owns enforcement or delivery of: Sidecar image is generic, prebuilt, pinned, and credential-free
- DESIGN-REQ-010: This story explicitly owns enforcement or delivery of: Volumes have per-session lifecycle and sharing rules
- DESIGN-REQ-011: This story explicitly owns enforcement or delivery of: Docker deployment materializes as two containers and three per-session volumes with ownership names and labels

Out of scope:
- Running MoonMind admin/update flows through this sidecar.
- Adding a Docker API proxy for per-operation command filtering.

### STORY-003: Expose Docker capability readiness in session request and status

- Short name: `docker-capability-status`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 13. Session request and status, 19. Readiness behavior, 24. Skill and plan guidance
- Description: As a workflow or skill author, I need session status to report whether Docker sidecar capability is ready, degraded, or unavailable so plans can choose ordinary Docker commands only when the environment supports them.
- Independent test: Workflow/activity boundary tests simulate ready, timeout, and optional-degraded sidecar probe results and assert serialized session status.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None

Acceptance criteria:
- When Docker is required and probes pass, status reports docker.available=true with sidecar mode, socket DOCKER_HOST, daemon ready/version, and passed checks.
- When Docker is required and readiness times out, startup fails or is marked degraded per profile with reason sidecar_not_ready.
- When Docker is optional and readiness fails, the session starts with docker.available=false.
- Skill/plan guidance can consult capabilities.docker.available and use docker version as a probe.

Requirements:
- Represent Docker capability request fields for required mode and compose support.
- Record readiness probe results in compact status metadata.
- Preserve deterministic status values for unavailable Docker capability.

Source design coverage:
- DESIGN-REQ-014: This story explicitly owns enforcement or delivery of: Session request and status expose Docker capability, readiness, mode, checks, and degraded reasons
- DESIGN-REQ-019: This story explicitly owns enforcement or delivery of: Readiness behavior controls session startup and degraded reporting
- DESIGN-REQ-016: This story explicitly owns enforcement or delivery of: Repository workloads use normal scripts and Docker commands

Out of scope:
- Executing repository tests as part of readiness.
- Publishing provider-specific runtime protocol changes.

### STORY-004: Enforce private session Docker isolation and policy guardrails

- Short name: `session-docker-isolation`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 14. What the session sees, 17. Policy model, 21. Separation from the MoonMind admin/update path, 26. Stable design rules, 27. Final declarative contract
- Description: As a MoonMind operator, I need Docker-enabled managed sessions to be isolated from the host daemon, MoonMind application containers, deployment credentials, and other sessions so normal repo workloads cannot affect platform operations or other users.
- Independent test: Security-oriented launcher tests and runtime contract tests inspect generated specs and environment for forbidden sockets, credentials, app-control mounts, and shared daemons.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None

Acceptance criteria:
- Managed sessions never receive /var/run/docker.sock or a silent substitution to the host socket path.
- docker ps from a session is scoped to the private sidecar daemon.
- Admin operations such as deploy, restart, rollback, image refresh, and app logs are available only through the dedicated ops runtime.
- MVP documents and enforces topology guardrails while future Docker API proxy enforcement remains separate.

Requirements:
- Forbid hostDockerSocket, sharedDaemonAcrossUsers, moonmindDeploymentSecretsInSession, and appContainerControlFromSession in runtime policy.
- Reserve control-plane Docker access for a MoonMindOpsRuntime-style backend with exposedToManagedAgents=false.

Source design coverage:
- DESIGN-REQ-003: This story explicitly owns enforcement or delivery of: Host Docker socket is forbidden for managed sessions and sidecars
- DESIGN-REQ-015: This story explicitly owns enforcement or delivery of: Session-visible Docker scope is private to the current session
- DESIGN-REQ-017: This story explicitly owns enforcement or delivery of: Policy model enforces topology at MVP and leaves per-operation proxy enforcement for future hardening
- DESIGN-REQ-021: This story explicitly owns enforcement or delivery of: MoonMind admin/update Docker access remains isolated in a separate ops runtime

Out of scope:
- Implementing a full Docker API proxy for blocking every dangerous Docker operation.

### STORY-005: Apply sidecar resource limits, cleanup, and retention behavior

- Short name: `sidecar-lifecycle-cleanup`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 18. Resource model, 20. Cleanup model, 10. Volume contract
- Description: As a MoonMind operator, I need Docker sidecar resources and cleanup behavior to be explicit so nested containers cannot exceed allocated capacity and failed or completed sessions do not leave dangling Docker state.
- Independent test: Lifecycle tests simulate session end, sidecar failure, and agent failure and assert cleanup calls, retention/removal decisions, and status changes.
- Dependencies: STORY-002, STORY-003
- Needs clarification: None

Acceptance criteria:
- Sidecar CPU, memory, ephemeral storage, max runtime, default nested container resources, and max container counts are applied outside the nested daemon.
- On session end, nested containers stop and Docker graph/socket state is removed by default while workspace preservation follows retention policy.
- On sidecar failure, Docker capability becomes unavailable but the agent session can be preserved when policy says so.
- On agent failure, sidecar cleanup runs and workspace retention remains configurable.

Requirements:
- Represent resource limits in the runtime profile and launch plan.
- Run idempotent cleanup for sidecar, graph, socket, and nested containers.
- Keep optional caches deployment-approved and avoid arbitrary host path mounts.

Source design coverage:
- DESIGN-REQ-010: This story explicitly owns enforcement or delivery of: Volumes have per-session lifecycle and sharing rules
- DESIGN-REQ-018: This story explicitly owns enforcement or delivery of: Resource limits are applied outside the nested daemon
- DESIGN-REQ-020: This story explicitly owns enforcement or delivery of: Cleanup stops nested containers and removes sidecar state according to retention policy

Out of scope:
- Long-term cross-session Docker image caching or container marketplace behavior.

### STORY-006: Publish sidecar audit labels and runtime observability

- Short name: `sidecar-audit-observability`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 22. Audit and observability, 13. Session request and status, 26. Stable design rules
- Description: As a MoonMind operator, I need sidecar containers and volumes to be discoverable and session status to expose Docker runtime facts so I can trace ownership, diagnose readiness failures, and rely on artifacts as the durable evidence of work.
- Independent test: Adapter-boundary tests inspect generated labels and status payloads; log routing tests verify daemon logs are not incorrectly treated as task artifacts.
- Dependencies: STORY-002, STORY-003
- Needs clarification: None

Acceptance criteria:
- Agent and sidecar resources carry moonmind.kind, moonmind.session_id, moonmind.session_epoch, moonmind.task_run_id when bound, and moonmind.workload_mode labels.
- Session status exposes readiness, daemon version, mode, and probe results in a compact surface.
- Durable evidence for agent work continues through the artifact pipeline and nested container state is not the system of record.
- Sidecar daemon stdout/stderr is available through worker logs unless debug explicitly attaches it as an artifact.

Requirements:
- Label all launched sidecar runtime resources consistently with ownership conventions.
- Keep artifacts and bounded metadata authoritative for task outcomes.

Source design coverage:
- DESIGN-REQ-011: This story explicitly owns enforcement or delivery of: Docker deployment materializes as two containers and three per-session volumes with ownership names and labels
- DESIGN-REQ-014: This story explicitly owns enforcement or delivery of: Session request and status expose Docker capability, readiness, mode, checks, and degraded reasons
- DESIGN-REQ-022: This story explicitly owns enforcement or delivery of: Audit and observability label every sidecar deployment and keep artifacts authoritative

Out of scope:
- Making sidecar daemon logs a mandatory task artifact for every run.

### STORY-007: Guide repository workloads to ordinary Docker entrypoints

- Short name: `ordinary-docker-entrypoints`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 15. Repository workload examples, 16. Repository script convention, 24. Skill and plan guidance, 21. Separation from the MoonMind admin/update path
- Description: As an agent or skill author, I need managed-session containerized work to use ordinary repo-provided Docker scripts and commands so tests and builds run the way developers expect while MoonMind-specific workload bridge tools stay reserved for control-plane operations.
- Independent test: Skill guidance and contract tests verify representative repo workload commands run through DOCKER_HOST and planning does not route ordinary tests through admin workload tools.
- Dependencies: STORY-002, STORY-003, STORY-004
- Needs clarification: None

Acceptance criteria:
- A Docker-capable session can run the documented smoke, workspace visibility, docker build, and docker compose examples through the sidecar daemon.
- Skills and plans check Docker capability before attempting Docker work.
- MoonMind admin tools are not recommended or invoked for ordinary repo test/build workloads.

Requirements:
- Surface clear repository script conventions for outer Docker invocation and inner build/test commands.
- Keep the developer mental model as normal Docker use from the session.

Source design coverage:
- DESIGN-REQ-001: This story explicitly owns enforcement or delivery of: Managed sessions run ordinary Docker commands through a sidecar runtime
- DESIGN-REQ-016: This story explicitly owns enforcement or delivery of: Repository workloads use normal scripts and Docker commands
- DESIGN-REQ-021: This story explicitly owns enforcement or delivery of: MoonMind admin/update Docker access remains isolated in a separate ops runtime

Out of scope:
- Rewriting every repository to add test-container.sh scripts.
- Changing provider-specific managed session protocols.

### STORY-008: Preserve backend portability for Kubernetes sidecar and Job modes

- Short name: `backend-portability-targets`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 12. Materialized Kubernetes shape (future), 25. Backend portability, 7. Runtime modes, 26. Stable design rules
- Description: As a platform maintainer, I need the sidecar runtime profile to retain a clean future mapping to Kubernetes Pods and Kubernetes Jobs so Docker deployments today do not block hardened or cluster-native deployments later.
- Independent test: Contract tests serialize a profile and verify required fields can be projected into both current Docker launch specs and documented Kubernetes Pod/Job shapes.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- The profile model keeps workspace, socket, graph, resources, labels, and capability semantics separable from Docker-specific rendering.
- docker-sidecar remains the default for trusted Docker deployments, rootless remains a hardening target, and kubernetes-job remains future.
- Kubernetes Job mode cannot be selected unless deployment explicitly supports it.

Requirements:
- Do not encode Docker Compose-only assumptions into the durable runtime profile contract.
- Keep backend portability visible in docs and tests.

Source design coverage:
- DESIGN-REQ-012: This story explicitly owns enforcement or delivery of: Kubernetes portability maps the same profile to a future two-container Pod
- DESIGN-REQ-013: This story explicitly owns enforcement or delivery of: Kubernetes Job mode is a future fallback for clusters that disallow DinD
- DESIGN-REQ-007: This story explicitly owns enforcement or delivery of: Runtime modes are explicit and cannot be raised by task instructions

Out of scope:
- Implementing Kubernetes orchestration in this story.
- Making kubernetes-job the current default for Docker deployments.

Assumptions:
- No Kubernetes implementation is required for the initial Docker-sidecar adoption story set.

### STORY-009: Align canonical managed-agent Docker documentation

- Short name: `canonical-doc-alignment`
- Source reference: `docs/ManagedAgents/DockerSidecarRuntime.md`; sections: 1.1 Relationship to existing architecture, 21. Separation from the MoonMind admin/update path, 28. Follow-up alignment work
- Description: As a MoonMind maintainer, I need the related managed-agent architecture documents to reflect that Docker sidecar is the default ordinary repo workload path and DooD is reserved for MoonMind admin/update operations so canonical docs do not contradict the desired runtime model.
- Independent test: Documentation review and grep checks verify canonical docs no longer state ordinary repo Docker work must route through the control plane and preserve no-host-socket/no-app-control invariants.
- Dependencies: STORY-001, STORY-004
- Needs clarification: None

Acceptance criteria:
- ManagedAgentArchitecture.md points to sidecar runtime as the default Docker capability path while preserving security boundaries.
- DockerOutOfDocker.md is narrowed to MoonMind admin/update and exceptional control-plane workloads.
- Architecture overview references and diagrams no longer contradict the sidecar default.
- Migration or rollout details stay in MoonSpec artifacts or local handoffs.

Requirements:
- Keep canonical docs focused on desired state per Constitution principle XII.
- Preserve the source document as the reference for sidecar runtime semantics.

Source design coverage:
- DESIGN-REQ-021: This story explicitly owns enforcement or delivery of: MoonMind admin/update Docker access remains isolated in a separate ops runtime
- DESIGN-REQ-024: This story explicitly owns enforcement or delivery of: Canonical docs require follow-up alignment after adopting sidecar runtime

Out of scope:
- Creating migration checklists in canonical docs or implementing runtime code changes.

## Coverage Matrix

- DESIGN-REQ-001: STORY-002, STORY-007
- DESIGN-REQ-002: STORY-002
- DESIGN-REQ-003: STORY-002, STORY-004
- DESIGN-REQ-004: STORY-001
- DESIGN-REQ-005: STORY-002
- DESIGN-REQ-006: STORY-001
- DESIGN-REQ-007: STORY-001, STORY-008
- DESIGN-REQ-008: STORY-002
- DESIGN-REQ-009: STORY-002
- DESIGN-REQ-010: STORY-001, STORY-002, STORY-005
- DESIGN-REQ-011: STORY-002, STORY-006
- DESIGN-REQ-012: STORY-008
- DESIGN-REQ-013: STORY-008
- DESIGN-REQ-014: STORY-003, STORY-006
- DESIGN-REQ-015: STORY-004
- DESIGN-REQ-016: STORY-003, STORY-007
- DESIGN-REQ-017: STORY-004
- DESIGN-REQ-018: STORY-005
- DESIGN-REQ-019: STORY-003
- DESIGN-REQ-020: STORY-005
- DESIGN-REQ-021: STORY-004, STORY-007, STORY-009
- DESIGN-REQ-022: STORY-006
- DESIGN-REQ-023: STORY-001
- DESIGN-REQ-024: STORY-009
- DESIGN-REQ-025: STORY-001

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-001, STORY-002
- STORY-004: STORY-001, STORY-002
- STORY-005: STORY-002, STORY-003
- STORY-006: STORY-002, STORY-003
- STORY-007: STORY-002, STORY-003, STORY-004
- STORY-008: STORY-001
- STORY-009: STORY-001, STORY-004

## Out-of-Scope Items and Rationale

- Kubernetes orchestration of MoonMind itself: excluded by the source design; Kubernetes mappings are future portability targets only.
- General arbitrary-shell execution from the control plane: excluded to preserve managed-session and ops-runtime boundaries.
- Generic container marketplace or cross-session container reuse: excluded because the daemon, graph, and socket are session-scoped.
- Detailed Codex, Claude, or Gemini managed session protocols: owned by runtime-specific docs and not by this sidecar contract.
- Provider-profile, secrets, and OAuth subsystem changes: explicitly outside this Docker sidecar runtime design.
- Docker API proxy per-operation enforcement: identified as future hardening; MVP enforces topology at launch time.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
