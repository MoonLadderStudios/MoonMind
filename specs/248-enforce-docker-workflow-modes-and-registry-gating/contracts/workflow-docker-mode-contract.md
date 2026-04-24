# Contract: Workflow Docker Mode

## Purpose

Define the runtime contract for deployment-owned workflow Docker access under MM-499.

## Configuration Surface

- Canonical environment variable: `MOONMIND_WORKFLOW_DOCKER_MODE`
- Allowed values:
  - `disabled`
  - `profiles`
  - `unrestricted`
- Default when omitted: `profiles`
- Unsupported values: startup error at settings-load time
- Task prompts, plans, and runtime tool inputs cannot override the selected mode

## Mode Behavior Matrix

| Mode | Curated/profile-backed Docker workload tools | Unrestricted Docker workload tools | Direct invocation of forbidden Docker tools |
| --- | --- | --- | --- |
| `disabled` | not exposed | not exposed | deterministic non-retryable denial |
| `profiles` | exposed | not exposed | deterministic non-retryable denial |
| `unrestricted` | exposed | exposed | allowed when request passes validation |

## Curated/Profile-Backed Tool Set

The existing profile-backed workflow Docker path remains the normal execution path and includes curated tools such as:

- `container.run_workload`
- `container.start_helper`
- `container.stop_helper`
- `moonmind.integration_ci`
- `unreal.run_tests`

These tools validate requests through deployment-owned runner profiles.

## Unrestricted Tool Contract

Unrestricted-mode-only tools expose deployment-gated arbitrary runtime container or Docker CLI behavior through MoonMind-owned tool contracts.

Rules:
- they must not appear in `disabled` or `profiles` mode
- they must route through the same workload policy boundary rather than widening session-container Docker authority
- enabling unrestricted workflow mode must not grant raw Docker authority to the managed session container itself

## Registration And Runtime Alignment

The same normalized workflow Docker mode must drive both:

1. registry/tool exposure during worker setup
2. runtime denial or allow behavior inside tool handlers and Temporal activity helpers

Discovery and execution must not disagree about whether a tool is allowed for the selected mode.

## Validation And Errors

- Invalid `MOONMIND_WORKFLOW_DOCKER_MODE` values fail fast during settings load.
- Mode-forbidden direct invocation returns a deterministic non-retryable permission denial.
- Disabled-mode denial reasons remain machine-readable so tests and operators can distinguish policy failures from generic launcher failures.

## Testing Requirements

Unit coverage must verify:
- default `profiles` mode normalization
- invalid-mode rejection
- mode-aware registration matrix
- mode-aware tool-handler denial behavior
- Temporal activity/runtime denial behavior

Hermetic integration coverage must verify at least one dispatcher/runtime boundary where mode-aware registration and execution stay aligned.
