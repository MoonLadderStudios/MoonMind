# Contract: Profile-Backed Workload And Helper Tools

## Purpose

Define the runtime contract for MM-500: approved Docker-backed workloads and helpers stay profile-backed rather than widening into arbitrary raw container requests.

## In-Scope Tool Surface

Profile-backed normal-path tools:

- `container.run_workload`
- `container.start_helper`
- `container.stop_helper`

Curated tools that must remain aligned with the same runner-profile model:

- `unreal.run_tests`
- `moonmind.integration_ci`

## `container.run_workload`

Contract rules:

- requires an approved `profileId`
- resolves through the runner profile registry before launch
- accepts workspace-rooted `repoDir` and `artifactsDir`
- accepts command and bounded overrides that stay within the selected runner profile
- must not accept raw image selection, arbitrary host-path mounts, unrestricted device requests, or unrestricted privilege fields

Expected outcome:

- validated profile-backed requests launch through the workload launcher with runner-profile-defined mounts, env policy, resources, timeout, and cleanup
- invalid raw-container fields fail before launch with a deterministic invalid-input outcome

## `container.start_helper` And `container.stop_helper`

Contract rules:

- require an approved helper profile
- preserve bounded-service lifecycle semantics rather than detached-service semantics
- helper start returns readiness metadata
- helper stop returns teardown metadata and preserves the explicit stop reason when provided

Expected outcome:

- helper lifecycle remains explicitly owned by the workload plane
- helper readiness and teardown metadata stay artifact/result-backed

## Disabled-Mode Behavior

When workflow Docker mode is `disabled`:

- profile-backed workload and helper tools must not execute
- the runtime returns a deterministic denial outcome instead of launching the request

## Curated Tool Alignment

Curated domain tools must stay on the same runner-profile-backed path:

- `unreal.run_tests` selects a curated approved profile and command shape
- `moonmind.integration_ci` selects a curated approved profile and command shape

These tools must not drift into raw runtime-selected container behavior.

## Testing Requirements

Unit coverage must verify:

- profile-backed request validation
- raw-container input rejection
- bounded helper lifecycle behavior
- curated-tool runner-profile alignment
- disabled-mode denial

Hermetic integration coverage must verify:

- dispatcher-boundary execution of an approved profile-backed workload
- dispatcher-boundary helper lifecycle behavior
- dispatcher-boundary rejection of raw container fields
- dispatcher-boundary denial when disabled mode forbids the profile-backed path
