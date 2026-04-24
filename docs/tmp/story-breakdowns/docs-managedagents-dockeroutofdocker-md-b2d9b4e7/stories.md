# DockerOutOfDocker Story Breakdown

Source design: `docs/ManagedAgents/DockerOutOfDocker.md`
Story extraction date: 2026-04-24T01:00:30Z
Requested story output mode: `jira`

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Design Summary

`DockerOutOfDocker.md` defines MoonMind's desired-state contract for Docker-backed specialized workload containers as a control-plane-owned workload plane adjacent to, but separate from, the managed Codex session plane. The design centers on explicit deployment modes, profile-backed normal execution, explicit unrestricted escape hatches, workspace-rooted path control, and durable artifact-first observability.

The extracted stories preserve that separation. They first lock down mode gating and profile-backed contracts, then add unrestricted execution behind deployment policy, enforce workspace and session boundaries, route all launches through a shared `docker_workload` execution plane, and finish with the durable artifact and audit contract operators rely on.

## Coverage Points

| ID | Type | Source Section | Design Point |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | requirement | 1. Purpose | DooD purpose and deployment modes |
| DESIGN-REQ-002 | architecture | 1. Purpose; 5. Architectural layering | Session and workload planes remain separate |
| DESIGN-REQ-003 | constraint | 2. Core decisions; 19. Stable design rules | Stable governing rules |
| DESIGN-REQ-004 | non-goal | 3. Scope and non-goals | Explicit scope and non-goals |
| DESIGN-REQ-005 | state-model | 4. Terminology | Terminology and identity model |
| DESIGN-REQ-006 | integration | 5.4 Logical execution capability; 8. Ownership and routing model | Logical docker_workload capability routing |
| DESIGN-REQ-007 | requirement | 6. Docker workflow permission modes | Mode configuration and normalization |
| DESIGN-REQ-008 | requirement | 6.2 disabled | Disabled mode behavior |
| DESIGN-REQ-009 | requirement | 6.3 profiles | Profiles mode behavior |
| DESIGN-REQ-010 | requirement | 6.4 unrestricted | Unrestricted mode behavior |
| DESIGN-REQ-011 | integration | 6.5 Tool exposure and runtime enforcement; 11.6 Planner and registry behavior | Mode-aware registry exposure with runtime enforcement |
| DESIGN-REQ-012 | requirement | 7. Supported container roles | Supported workload roles |
| DESIGN-REQ-013 | integration | 8. Ownership and routing model | Tool-path execution model |
| DESIGN-REQ-014 | constraint | 9. Session-plane interaction model | Session interaction boundary |
| DESIGN-REQ-015 | requirement | 10. Workspace and volume contract | Workspace path and mount rules |
| DESIGN-REQ-016 | security | 10.4 Auth volume rule | Auth volume isolation |
| DESIGN-REQ-017 | integration | 11. User-facing tool surface | Tool contracts for profile-backed and unrestricted execution |
| DESIGN-REQ-018 | artifact | 12. Runner profile model | Runner profile model |
| DESIGN-REQ-019 | integration | 13. Execution model and launch semantics | Shared execution plane and launch classes |
| DESIGN-REQ-020 | observability | 13.6 Deterministic ownership labels; 13.7 Exit contract; 13.9 Helper lifecycle | Deterministic ownership labels and exit contract |
| DESIGN-REQ-021 | observability | 14. Artifact, audit, and observability contract | Durable artifact and audit truth |
| DESIGN-REQ-022 | security | 15. Security and policy controls | Security and policy controls |
| DESIGN-REQ-023 | migration | 16. Timeout, cancellation, and cleanup | Timeout, cancellation, and cleanup semantics |
| DESIGN-REQ-024 | architecture | 17. Compose and runtime shape; 20. Future refinements | Compose/runtime shape and future fleet split |
| DESIGN-REQ-025 | artifact | 18. Example flows | Representative example flows |

## Ordered Stories

### Story 1: Enforce Docker workflow modes and registry gating

Short name: `docker-mode-gating`

Why:
As a deployment operator, I can set Docker workflow access to disabled, profiles, or unrestricted so MoonMind exposes and enforces only the workload tools allowed for that environment.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 1. Purpose, 2. Core decisions, 6. Docker workflow permission modes, 19. Stable design rules

Independent test:
- Load settings and the default tool registry under each mode and assert startup normalization, exposed tools, and runtime-denied invocations match the configured mode.

Acceptance criteria:
- Given MOONMIND_WORKFLOW_DOCKER_MODE is omitted, when settings load, then the effective mode is profiles.
- Given MOONMIND_WORKFLOW_DOCKER_MODE is an unsupported value, when the service starts, then startup fails with a deterministic configuration error.
- Given mode is disabled, when the registry snapshot is built or a DooD tool is invoked directly, then all Docker-backed tools are omitted or denied at runtime.
- Given mode is profiles, when the registry snapshot is built, then profile-backed and curated DooD tools are available while unrestricted tools are omitted and denied.
- Given mode is unrestricted, when the registry snapshot is built, then profile-backed and unrestricted DooD tools are available while session-side Docker authority remains unchanged.
Requirements:
- Normalize the deployment-owned Docker mode from MOONMIND_WORKFLOW_DOCKER_MODE at settings load time.
- Keep disabled, profiles, and unrestricted as the only supported modes.
- Make registry exposure mode-aware without relying on registration alone for enforcement.
- Return deterministic denial behavior for mode-forbidden tool invocations.
Dependencies: None.

Assumptions:
- Legacy boolean alias handling, if still present during migration, normalizes before any runtime decision.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011
### Story 2: Implement profile-backed workload and helper tool contracts

Short name: `profile-workload-tools`

Why:
As a workflow author, I can run curated and profile-backed workload containers and helpers through stable MoonMind tool contracts that validate against runner profiles instead of raw container inputs.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 7. Supported container roles, 11. User-facing tool surface, 12. Runner profile model, 18.1 Unreal test run in profiles mode

Independent test:
- Invoke container.run_workload and helper lifecycle tools with valid and invalid profile IDs and verify the request validation, runner-profile resolution, and bounded helper behavior without enabling unrestricted mode.

Acceptance criteria:
- Given mode is profiles or unrestricted, when container.run_workload is invoked with an approved profileId, then MoonMind resolves the runner profile and launches the workload with profile-defined mounts, env policy, resources, timeout, and cleanup.
- Given container.run_workload is invoked with raw image strings, arbitrary host-path mounts, or unrestricted privilege fields, then validation fails because the contract remains profile-backed.
- Given container.start_helper and container.stop_helper are invoked with an approved helper profile, then MoonMind treats the helper as an explicitly owned, bounded workload lifecycle rather than an arbitrary detached service.
- Given mode is disabled, when any profile-backed workload or helper tool is invoked, then the request is denied deterministically.
Requirements:
- Keep container.run_workload generic but strictly profile-validated.
- Preserve bounded helper semantics for container.start_helper and container.stop_helper.
- Model normal execution around runner profiles rather than unrestricted image strings.
- Keep curated domain tools such as unreal.run_tests aligned with the same DooD execution model.
Dependencies: STORY-001

Assumptions:
- None.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-025
### Story 3: Add unrestricted container and Docker CLI execution contracts

Short name: `unrestricted-docker-tools`

Why:
As a trusted deployment operator, I can allow arbitrary runtime containers and explicit Docker CLI workloads through separate unrestricted MoonMind tools without weakening the normal profile-backed contract.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 2. Core decisions, 7. Supported container roles, 11.4 container.run_container, 11.5 container.run_docker, 18.2-18.4 Example flows

Independent test:
- In unrestricted mode, invoke container.run_container and container.run_docker with valid inputs and assert successful execution; then verify the same requests are denied in profiles mode and invalid docker commands fail fast.

Acceptance criteria:
- Given mode is unrestricted, when container.run_container is invoked with a runtime-selected image plus workspace paths and declared outputs, then MoonMind launches the container without requiring a pre-registered runner profile.
- Given container.run_container includes arbitrary host-path mounts, privileged flags, host networking, or implicit auth inheritance, then validation rejects the request because those capabilities are outside the structured unrestricted contract.
- Given mode is unrestricted, when container.run_docker is invoked, then command[0] must equal docker and the command runs as a Docker CLI invocation rather than a general shell surface.
- Given mode is disabled or profiles, when unrestricted tools are invoked, then MoonMind returns deterministic denial codes such as unrestricted_container_disabled or unrestricted_docker_disabled.
Requirements:
- Expose container.run_container as the first-class unrestricted arbitrary-container contract.
- Expose container.run_docker as the explicit Docker CLI escape hatch and not as generic shell access.
- Keep unrestricted execution deployment-gated and auditable.
- Preserve the meaning of container.run_workload as profile-backed even when unrestricted mode is enabled.
Dependencies: STORY-001

Assumptions:
- None.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, DESIGN-REQ-025
### Story 4: Enforce workspace, mount, and session-boundary isolation

Short name: `workspace-boundaries`

Why:
As a platform maintainer, I can guarantee that workload containers operate only on MoonMind-owned task paths and remain isolated from managed-session identity and provider auth state unless policy explicitly allows otherwise.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 4. Terminology, 8. Ownership and routing model, 9. Session-plane interaction model, 10. Workspace and volume contract, 15.2-15.5 Security and policy controls

Independent test:
- Attempt valid and invalid launches that vary repoDir, artifactsDir, scratchDir, cache mounts, and session association metadata, then assert MoonMind accepts only workspace-rooted paths, keeps auth mounts explicit, and preserves session/workload identity separation.

Acceptance criteria:
- Given a DooD request, when repoDir, artifactsDir, scratchDir, or declared outputs resolve outside the workspace root, then the request is rejected before launch.
- Given a managed Codex session requests a DooD tool, when the workload runs, then the session may receive results and association metadata but does not gain raw Docker socket or unrestricted DOCKER_HOST access.
- Given a workload is launched from a session-assisted step, then any session_id or source_turn_id fields are treated as association metadata only and do not convert the workload container into a managed session.
- Given a workload request lacks an explicit credential policy, then provider auth volumes are not mounted into the workload container by default.
Requirements:
- Preserve the architectural separation between session containers and workload containers.
- Constrain structured DooD contracts to MoonMind-owned workspace paths and approved caches.
- Prevent automatic auth-volume inheritance into workload containers.
- Keep generic shell access and session-side Docker bypasses out of scope.
Dependencies: STORY-001

Assumptions:
- None.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022
### Story 5: Route DooD tools through the shared docker_workload execution plane

Short name: `docker-workload-executor`

Why:
As a workflow runtime owner, I can execute all DooD-backed tools through one trusted docker_workload capability and a shared launcher pipeline that applies labels, timeouts, cancellation, and cleanup consistently.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 5.4 Logical execution capability, 8. Ownership and routing model, 13. Execution model and launch semantics, 16. Timeout, cancellation, and cleanup, 17. Compose and runtime shape

Independent test:
- Run profile-backed, unrestricted-container, and unrestricted-docker executions through the same activity surface and assert launch-class selection, ownership labels, timeout/cancel behavior, cleanup behavior, and current fleet routing all match the request type.

Acceptance criteria:
- Given any DooD-backed tool, when it is executed, then the request routes through mm.tool.execute with required capability docker_workload.
- Given a workload starts, then MoonMind applies deterministic labels including task_run_id, step_id, attempt, tool_name, docker_mode, and workload access class.
- Given timeout or cancellation occurs, then MoonMind attempts graceful stop, escalates to kill after the grace period when needed, captures remaining diagnostics where available, and records bounded terminal metadata.
- Given structured containers are created through container.run_workload, container.start_helper, or container.run_container, then MoonMind owns their cleanup; given arbitrary resources are created by container.run_docker, then MoonMind only performs cleanup when ownership can be reliably identified.
Requirements:
- Keep docker_workload as the stable logical capability independent of the current physical fleet assignment.
- Share one launcher pipeline across profile-backed, unrestricted container, and unrestricted Docker CLI launch classes.
- Apply explicit helper lifecycle ownership instead of relying on long-running container side effects.
- Allow a future dedicated Docker workload fleet without changing the contract exposed to workflows.
Dependencies: STORY-002, STORY-003, STORY-004

Assumptions:
- None.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-024
### Story 6: Publish durable DooD artifacts, audit metadata, and observability outputs

Short name: `dood-artifact-audit`

Why:
As an operator, I can inspect durable logs, diagnostics, summaries, reports, and explicit audit metadata for every Docker-backed workload without relying on daemon state or terminal scrollback.

Source document reference:
- Path: `docs/ManagedAgents/DockerOutOfDocker.md`
- Sections: 13.8 Report publication, 14. Artifact, audit, and observability contract, 15.6 Secret handling and redaction

Independent test:
- Execute representative DooD tools and verify that each result publishes durable stdout, stderr, diagnostics, summaries, declared outputs, and redacted audit metadata that make unrestricted use obvious without leaking secrets.

Acceptance criteria:
- Given any DooD invocation, when it completes or fails, then MoonMind persists invocation summary, stdout, stderr, diagnostics, exit metadata, and declared outputs as durable artifacts.
- Given report publication is requested, when the run completes, then declared primary reports follow the shared artifact publication contract.
- Given unrestricted execution is used, when audit metadata is published, then dockerMode and workloadAccess clearly identify it while dockerHost and secret-looking values remain normalized or redacted.
- Given operators inspect results, then daemon state, container-local history, and terminal scrollback are not required as the source of truth.
Requirements:
- Treat artifacts and bounded metadata as authoritative for DooD observability.
- Preserve consistent artifact classes across all launch types.
- Redact secret-looking output and metadata before publication.
- Make unrestricted usage obvious in result metadata and audit surfaces.
Dependencies: STORY-005

Assumptions:
- None.

Needs clarification:
- None.

Owned coverage: DESIGN-REQ-021, DESIGN-REQ-022

## Coverage Matrix

- DESIGN-REQ-001: STORY-001
- DESIGN-REQ-002: STORY-004
- DESIGN-REQ-003: STORY-001, STORY-003
- DESIGN-REQ-004: STORY-004
- DESIGN-REQ-005: STORY-004
- DESIGN-REQ-006: STORY-005
- DESIGN-REQ-007: STORY-001
- DESIGN-REQ-008: STORY-001
- DESIGN-REQ-009: STORY-001
- DESIGN-REQ-010: STORY-001, STORY-003
- DESIGN-REQ-011: STORY-001
- DESIGN-REQ-012: STORY-002
- DESIGN-REQ-013: STORY-004
- DESIGN-REQ-014: STORY-004
- DESIGN-REQ-015: STORY-004
- DESIGN-REQ-016: STORY-004
- DESIGN-REQ-017: STORY-002, STORY-003
- DESIGN-REQ-018: STORY-002
- DESIGN-REQ-019: STORY-005
- DESIGN-REQ-020: STORY-005
- DESIGN-REQ-021: STORY-006
- DESIGN-REQ-022: STORY-003, STORY-004, STORY-006
- DESIGN-REQ-023: STORY-005
- DESIGN-REQ-024: STORY-005
- DESIGN-REQ-025: STORY-002, STORY-003

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-001
- STORY-004: STORY-001
- STORY-005: STORY-002, STORY-003, STORY-004
- STORY-006: STORY-005

## Out of Scope

- Creating or modifying any `spec.md` file is intentionally out of scope for breakdown.
- Creating directories under `specs/` is intentionally out of scope for breakdown.
- Kubernetes orchestration, a generic shell surface, raw Docker access from managed sessions, and a permanent detached arbitrary service framework remain excluded by the source design.

## Notes

- Every story preserves the original declarative source path in `sourceReference.path`: `docs/ManagedAgents/DockerOutOfDocker.md`.
- No `spec.md` files were created or modified during this breakdown.
- No directories under `specs/` were created during this breakdown.
- Downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement` should remain TDD-first.
- Run `/speckit.verify` after implementation to compare the final behavior against the original design preserved through specify.
