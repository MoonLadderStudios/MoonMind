# Feature Specification: Shared Docker Workload Execution Plane

**Feature Branch**: `252-route-docker-workload-plane`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-503 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-503` from `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-503 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-503 MoonSpec Orchestration Input

## Source

- Jira issue: MM-503
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Route DooD tools through the shared docker_workload execution plane
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-503 from MM project
Summary: Route DooD tools through the shared docker_workload execution plane
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-503 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-503: Route DooD tools through the shared docker_workload execution plane

Source Reference
- Source document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source sections:
  - 5.4 Logical execution capability
  - 8. Ownership and routing model
  - 13. Execution model and launch semantics
  - 16. Timeout, cancellation, and cleanup
  - 17. Compose and runtime shape
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a workflow runtime owner, I can execute all DooD-backed tools through one trusted `docker_workload` capability and a shared launcher pipeline that applies labels, timeouts, cancellation, and cleanup consistently.

Acceptance Criteria
- Given any DooD-backed tool, when it is executed, then the request routes through `mm.tool.execute` with required capability `docker_workload`.
- Given a workload starts, then MoonMind applies deterministic labels including `task_run_id`, `step_id`, `attempt`, `tool_name`, `docker_mode`, and workload access class.
- Given timeout or cancellation occurs, then MoonMind attempts graceful stop, escalates to kill after the grace period when needed, captures remaining diagnostics where available, and records bounded terminal metadata.
- Given structured containers are created through `container.run_workload`, `container.start_helper`, or `container.run_container`, then MoonMind owns their cleanup; given arbitrary resources are created by `container.run_docker`, then MoonMind only performs cleanup when ownership can be reliably identified.

Requirements
- Keep `docker_workload` as the stable logical capability independent of the current physical fleet assignment.
- Share one launcher pipeline across profile-backed, unrestricted container, and unrestricted Docker CLI launch classes.
- Apply explicit helper lifecycle ownership instead of relying on long-running container side effects.
- Allow a future dedicated Docker workload fleet without changing the contract exposed to workflows.

Relevant Implementation Notes
- Preserve MM-503 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for logical execution capability, ownership and routing, launch semantics, timeout/cancellation/cleanup behavior, and compose/runtime shape.
- Keep all DooD-backed tools on one trusted `docker_workload` execution path rather than splitting execution semantics across multiple capability classes.
- Ensure deterministic label application, bounded cancellation handling, and cleanup ownership remain part of the shared launcher behavior.
- Limit cleanup for arbitrary Docker-created resources to cases where MoonMind can reliably determine ownership.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-503 blocks MM-502, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-503 is blocked by MM-504, whose embedded status is Backlog.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story; breakdown is reserved for broad technical or declarative designs that contain multiple independently testable stories.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-503 were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for MM-503 because this spec remains isolated to one story; if future Jira input requires multiple generated specs, they must remain isolated and be processed in dependency order.

## User Story - Route DooD Workloads Through One Execution Plane

**Summary**: As a workflow runtime owner, I want all Docker-backed MoonMind tools to execute through one trusted workload execution plane so workload routing, labels, timeout handling, cancellation, and cleanup stay consistent across tool types.

**Goal**: MoonMind can run Docker-backed workloads through one stable logical capability and one shared launcher behavior regardless of the current worker-fleet placement or the specific structured container contract used by the step.

**Independent Test**: Trigger representative Docker-backed workload executions across the supported workload classes, then verify they all route through the same trusted execution capability, emit the same required ownership labels and bounded terminal metadata, and follow the same timeout, cancellation, and cleanup rules regardless of current runtime placement.

**Acceptance Scenarios**:

1. **Given** any Docker-backed MoonMind tool is invoked, **When** MoonMind routes the execution, **Then** it uses the same trusted workload execution capability rather than tool-specific or fleet-specific routing semantics.
2. **Given** Docker-backed workloads run through different launch classes, **When** MoonMind launches them, **Then** each execution receives deterministic ownership and audit labels that identify the task run, step, attempt, tool, runtime mode, and workload access class.
3. **Given** a Docker-backed workload times out or is cancelled, **When** MoonMind enforces terminal handling, **Then** it attempts bounded shutdown behavior, captures remaining diagnostics when available, and records consistent terminal metadata.
4. **Given** MoonMind launches a structured workload or helper container, **When** that execution completes or is cancelled, **Then** MoonMind owns cleanup of the launched resources.
5. **Given** a Docker-backed execution creates arbitrary Docker resources outside the structured workload contracts, **When** MoonMind performs cleanup, **Then** it only cleans those resources when ownership can be determined reliably.
6. **Given** deployment topology changes and the logical workload capability is satisfied by a different physical fleet, **When** Docker-backed tools are executed, **Then** the observable workload contract remains unchanged.

### Edge Cases

- A tool path tries to bypass the shared workload plane because the current deployment happens to satisfy the logical capability on the `agent_runtime` fleet.
- Different workload launch classes emit inconsistent ownership labels or omit workload access-class information.
- Cancellation occurs after a container has started but before normal artifact publication completes.
- Cleanup for arbitrary Docker-created resources mistakenly removes resources that MoonMind does not own.
- A future dedicated Docker workload fleet changes physical placement while unintentionally changing the observable execution contract.

## Assumptions

- MM-503 is limited to establishing one shared execution-plane contract for Docker-backed MoonMind tools, not to introducing new independent workload features outside the routing, label, timeout, cancellation, and cleanup concerns named in the Jira brief.
- Existing deployment-owned Docker workflow modes continue to gate which tool classes are available; this story standardizes the shared execution plane for the classes that are allowed.
- The blocker relationship to MM-504 is tracked operationally outside this specification and does not change the single-story runtime scope captured here.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/ManagedAgents/DockerOutOfDocker.md` §5.4, §8.2-8.3 | Docker-backed MoonMind tools must route through the stable logical `docker_workload` capability on the tool execution path rather than through tool-specific execution semantics. | In scope | FR-001, FR-006 |
| DESIGN-REQ-019 | `docs/ManagedAgents/DockerOutOfDocker.md` §13.1, §13.6 | Docker-backed workload launches must share one execution plane that applies deterministic workload ownership and audit labels. | In scope | FR-002, FR-003 |
| DESIGN-REQ-020 | `docs/ManagedAgents/DockerOutOfDocker.md` §13.1-13.5, §17.2-17.4 | Physical worker-fleet placement may change, but the logical workload execution contract exposed to workflows must remain stable. | In scope | FR-001, FR-006 |
| DESIGN-REQ-023 | `docs/ManagedAgents/DockerOutOfDocker.md` §16.1-16.4 | Timeout, cancellation, and cleanup behavior for Docker-backed workloads must remain control-plane-owned, bounded, and consistent across structured and unrestricted workload classes. | In scope | FR-004, FR-005 |
| DESIGN-REQ-024 | `docs/ManagedAgents/DockerOutOfDocker.md` §13.3-13.5, §16.3-16.4 | MoonMind owns cleanup for structured workload and helper launches, while cleanup for arbitrary Docker-created resources is limited to cases with reliable ownership evidence. | In scope | FR-004, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST route all Docker-backed MoonMind tool executions through one stable logical workload execution capability that remains consistent even when the backing worker fleet changes.
- **FR-002**: The system MUST apply deterministic execution metadata to every Docker-backed workload launch that identifies the task run, step, attempt, tool, workflow mode, and workload access class.
- **FR-003**: The system MUST publish a consistent execution outcome contract for Docker-backed workloads so launch classes share the same audit and observability expectations.
- **FR-004**: The system MUST enforce consistent timeout and cancellation handling for Docker-backed workloads, including bounded shutdown attempts and terminal diagnostic capture when available.
- **FR-005**: The system MUST own cleanup for structured workload and helper executions, and MUST limit cleanup of arbitrary Docker-created resources to cases where ownership can be determined reliably.
- **FR-006**: The system MUST preserve the same observable routing and lifecycle contract for Docker-backed workloads regardless of the physical worker fleet currently satisfying the logical workload capability.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-503.

### Key Entities

- **Logical Docker Workload Capability**: The stable MoonMind capability that represents the trusted execution plane for Docker-backed workload tools independent of physical fleet placement.
- **Docker-Backed Tool Execution**: A workload launched by MoonMind through a Docker-backed tool path, including profile-backed workloads, helper launches, unrestricted runtime containers, and Docker CLI executions when deployment policy allows them.
- **Deterministic Ownership Labels**: Execution metadata attached to workload launches so audit, observability, and cleanup can identify the owning task run, step, attempt, tool, runtime mode, and workload access class.
- **Structured Workload Resource**: A workload container or helper lifecycle that MoonMind launches through a structured contract and therefore owns for cleanup.
- **Arbitrary Docker-Created Resource**: A Docker-created resource outside the structured workload contracts whose cleanup is limited to cases with reliable ownership evidence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves representative Docker-backed MoonMind tools all route through the same logical workload capability regardless of launch class.
- **SC-002**: Validation proves each Docker-backed workload launch records deterministic execution metadata for task run, step, attempt, tool, runtime mode, and workload access class.
- **SC-003**: Validation proves timeout and cancellation handling produces bounded shutdown behavior and terminal diagnostic capture consistently across workload classes.
- **SC-004**: Validation proves MoonMind cleans up structured workload and helper resources it owns while avoiding cleanup of arbitrary Docker-created resources without reliable ownership evidence.
- **SC-005**: Validation proves changing the physical fleet that satisfies the logical workload capability does not change the observable workload routing and lifecycle contract.
- **SC-006**: Traceability review confirms MM-503 and DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, and DESIGN-REQ-024 remain preserved in MoonSpec artifacts and downstream verification evidence.
