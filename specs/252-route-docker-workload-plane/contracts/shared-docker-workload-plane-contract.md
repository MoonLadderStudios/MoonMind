# Contract: Shared Docker Workload Execution Plane

## Purpose

Define the runtime contract for MM-503: all Docker-backed MoonMind tools execute through one trusted workload plane with shared routing, deterministic metadata, bounded timeout/cancellation semantics, and explicit cleanup ownership boundaries.

## In-Scope Execution Surface

This story governs Docker-backed workload execution through the control-plane-owned workload path, including:

- profile-backed workload tools such as `container.run_workload`
- bounded helper tools such as `container.start_helper` and `container.stop_helper`
- curated workload-backed tools such as `moonmind.integration_ci` and `unreal.run_tests`
- unrestricted runtime containers through `container.run_container` when deployment mode allows them
- unrestricted Docker CLI execution through `container.run_docker` when deployment mode allows it

It does not redefine managed session lifecycle or create a second execution surface outside the MoonMind tool path.

## Routing Rules

Contract rules:

- all Docker-backed tools execute through `mm.tool.execute`
- all Docker-backed tools require the logical `docker_workload` capability
- the logical capability, not current physical fleet placement, defines the observable execution contract
- deployment mode determines which tool classes are exposed, but allowed tools still use the same shared workload plane

Expected outcome:

- supported DooD tools do not bypass the shared execution path
- moving `docker_workload` to a different worker fleet does not change the workload-facing contract

## Metadata Rules

Contract rules:

- each workload launch must carry deterministic task/run identity metadata
- execution metadata must identify task run, step, attempt, tool, and workload access class
- session association metadata, when present, remains bounded traceability metadata inside `sessionContext`
- the shared execution plane must expose equivalent workload result metadata across supported launch classes

Expected outcome:

- workload launches remain observable and attributable across curated and unrestricted tool classes
- session linkage does not change workload identity

## Timeout And Cancellation Rules

Contract rules:

- timeout policy remains control-plane owned and is enforced at the launcher boundary
- cancellation attempts bounded shutdown before forced termination when needed
- terminal workload metadata records timeout or cancellation context when available
- these semantics apply consistently across structured and unrestricted launch classes

Expected outcome:

- workload termination stays bounded and explainable
- timeout/cancellation behavior does not depend on which Docker-backed tool invoked the workload

## Cleanup Rules

Contract rules:

- MoonMind owns cleanup for structured workload and helper launches it starts directly
- helper lifecycle remains explicitly bounded and owned
- arbitrary Docker-created resources are not removed unless MoonMind can reliably establish ownership
- label-based cleanup must remain conservative for unrestricted Docker CLI side effects

Expected outcome:

- owned workload resources are cleaned deterministically
- MoonMind does not overreach into arbitrary operator-managed Docker resources

## Testing Requirements

Unit coverage must verify:

- `docker_workload` capability routing for curated and unrestricted tool definitions
- deterministic workload metadata and labels for structured and unrestricted requests
- launcher timeout, cancellation, and cleanup behavior
- mode-aware routing and activity-runtime enforcement

Hermetic integration coverage must verify:

- dispatcher/runtime execution for profile-backed and helper workloads
- shared execution-plane behavior for unrestricted container and Docker CLI paths when enabled
- bounded `sessionContext` metadata and workload result shaping across launch classes
- cleanup ownership boundaries remain conservative and class-appropriate
