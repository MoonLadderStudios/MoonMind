# Contract: Unrestricted Container And Docker CLI Workloads

## Purpose

Define the runtime contract for MM-501: unrestricted Docker-backed execution is available only through explicit MoonMind tools, remains deployment-gated, and does not weaken the normal profile-backed workload path.

## In-Scope Tool Surface

Unrestricted tools:

- `container.run_container`
- `container.run_docker`

Related normal-path tools whose meaning must remain unchanged:

- `container.run_workload`
- `container.start_helper`
- `container.stop_helper`

## `container.run_container`

Contract rules:

- available only when workflow Docker mode is `unrestricted`
- accepts a runtime-selected image with an explicit tag or digest
- requires workspace-rooted `repoDir`, `artifactsDir`, and `scratchDir`
- accepts command, workdir, declared outputs, bounded resource overrides, named cache mounts, and explicit network mode within the unrestricted schema
- must not expose arbitrary host-path mounts, unrestricted privilege flags, implicit credential inheritance, or generic shell authority

Expected outcome:

- validated unrestricted container requests launch through the Docker workload launcher without requiring a runner profile
- unrestricted launches remain explicitly labeled and auditable
- forbidden unrestricted shapes fail before launch with deterministic invalid-input or policy outcomes

## `container.run_docker`

Contract rules:

- available only when workflow Docker mode is `unrestricted`
- command must be a Docker CLI invocation where `command[0]` equals `docker`
- runs through the trusted Docker-capable worker plane rather than through generic shell access
- declared outputs and bounded resource overrides remain explicit in the request

Expected outcome:

- MoonMind executes Docker CLI requests as Docker-specific workloads rather than widening them into arbitrary shell commands
- unrestricted Docker CLI usage is explicit in metadata and result labeling

## Mode Matrix

| Workflow Docker Mode | `container.run_workload` | `container.run_container` | `container.run_docker` |
| --- | --- | --- | --- |
| `disabled` | denied | denied | denied |
| `profiles` | allowed | denied | denied |
| `unrestricted` | allowed | allowed | allowed |

## Preservation Of The Profile-Backed Path

When workflow Docker mode is `unrestricted`:

- `container.run_workload` remains a profile-backed contract
- unrestricted container execution does not turn `container.run_workload` into an alias for runtime-selected images
- unrestricted execution expands the control-plane tool surface, not session-side Docker authority

## Testing Requirements

Unit coverage must verify:

- unrestricted request schema validation
- Docker CLI prefix enforcement
- unrestricted tool registration only in `unrestricted` mode
- runtime denial in `profiles` mode
- bounded unrestricted launcher args and metadata

Hermetic integration coverage must verify:

- dispatcher-boundary omission or denial of unrestricted tools outside `unrestricted` mode
- dispatcher-boundary execution of `container.run_container` in `unrestricted` mode
- alignment between registry exposure and runtime enforcement for unrestricted tools
