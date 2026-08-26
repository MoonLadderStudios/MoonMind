# Omnigent Host Mounted Runtime Tools

**Document Class:** Canonical declarative
**Viewpoint:** System / Feature Design View
**Status:** Desired-state design  
**Owners:** MoonMind Platform  
**Last updated:** 2026-08-18

**Implementation tracking:** rollout notes, spikes, migration checklists, and temporary handoffs belong under `docs/tmp/` or in issue/PR tracking. This document defines the durable target-state contract.

## Related documents

- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Omnigent/CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md)
- [`docs/Workflows/RequiredCapabilities.md`](../Workflows/RequiredCapabilities.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

---

## 1. Purpose

MoonMind needs a simple way to add small command-line capabilities to an unchanged upstream `omnigent-host` image when a workflow or resolved Skill requires a tool that the stock image does not contain.

The canonical solution is a **MoonMind-managed, read-only mounted tool bundle**:

```text
MoonMind startup or deployment reconciliation
  -> prepare one versioned tool bundle
  -> mount the bundle read-only into stock omnigent-host containers
  -> expose the bundle's bin directory to host and runner shells
  -> verify required tools before an Omnigent session starts
  -> let the agent invoke the real tools through their normal CLI interfaces
```

The first supported example is GitHub CLI, `gh`, for GitHub-aware Skills such as `pr-resolver`. The same mechanism may carry other small, self-contained CLI tools later when mounting a binary is sufficient and simpler than maintaining a custom host image.

This design preserves the upstream image, keeps portable Skills on their ordinary CLI interfaces, and avoids per-run package installation.

---

## 2. Scope

This document covers:

- adding small runtime CLI tools to stock `omnigent-host` containers;
- one standard mounted tool-bundle layout;
- static Docker Compose hosts and MoonMind-launched on-demand hosts;
- tool versioning and initialization;
- runtime `PATH` projection, including login shells;
- required-capability readiness checks;
- the initial `gh` use case;
- the boundary between tool availability, credentials, Git workspace preparation, and resolved Skill materialization;
- criteria for using the same approach for future tools.

This document does not define:

- a custom MoonMind fork of `omnigent-host`;
- a general-purpose package manager or plugin marketplace for hosts;
- downloading or installing tools from inside an active agent run;
- an RPC service that imitates a third-party CLI;
- a new GitHub credential broker;
- provider OAuth materialization, which remains defined by `OmnigentHostOAuth.md`;
- resolved Skill storage or selection, which remains defined by `SkillSystem.md`;
- tools that require kernel modules, privileged host changes, background daemons, or broad system-package installation.

The baseline intentionally does not require a token broker, sidecar command service, custom container image, or new persistent database model.

---

## 3. Architectural decision summary

The target design is governed by these decisions:

1. **The upstream host image remains unchanged.** MoonMind extends the runtime through mounts and launch configuration rather than maintaining a fork for isolated CLI additions.
2. **One standard tool bundle is the extension boundary.** Tools are mounted under `/opt/moonmind-tools`; MoonMind does not invent a separate mount layout for every executable.
3. **The bundle is read-only to hosts and runners.** A trusted initializer prepares it before the host starts. Agent processes never update the bundle.
4. **Tool versions are explicit.** The initializer uses pinned versions and verifies the expected bytes before publishing a bundle as ready. `latest` is not a durable tool identity.
5. **Both ordinary and login-shell paths are supported.** The host receives a `PATH` value at launch and a small `/etc/profile.d` snippet so `bash -lc` sessions retain `/opt/moonmind-tools/bin`.
6. **Required capabilities determine readiness.** A mounted executable may be present on every compatible host, but a run receives readiness guarantees and any required credentials only when its normalized `requiredCapabilities` demand them. The Run workflow carries that authored list into the canonical `AgentExecutionRequest`; host preflight and the provider adapter consume the same list rather than re-deriving it.
7. **Tool availability does not grant authorization.** Credentials are resolved separately through MoonMind's existing settings and secret-reference boundaries.
8. **Portable Skills receive the real CLI they declare.** MoonMind does not replace `gh` with an incomplete host-native emulator when the resolved Skill implementation calls `gh` directly.
9. **Missing required tools fail before session creation or mutation.** MoonMind must not start an Omnigent runner and let the Skill discover the missing executable after reasoning has begun.
10. **Mounted tools remain a small-tool solution.** When a capability needs extensive system changes, MoonMind should prefer an upstream host-image addition or an explicitly justified derived image rather than stretching this mechanism into a package distribution system.

---

## 4. Canonical tool bundle

### 4.1 Runtime path

Every mounted tool bundle uses this runtime root:

```text
/opt/moonmind-tools
```

The canonical layout is:

```text
/opt/moonmind-tools/
  manifest.json
  bin/
    gh
    <future-tool>
```

Executable names under `bin/` are the ordinary command names expected by Skills and agents. A Skill that invokes `gh` must find an executable named `gh`; it must not need a MoonMind-specific alias.

### 4.2 Bundle identity

The initial implementation does not require a new database-backed `ToolBundle` resource. Bundle identity is deployment-owned and consists of:

- the mounted volume or daemon-visible source reference;
- an explicit bundle version;
- the manifest stored inside the bundle.

A minimal manifest has this shape:

```json
{
  "schemaVersion": 1,
  "bundleVersion": "<deployment-selected-version>",
  "tools": [
    {
      "name": "gh",
      "version": "<pinned-gh-version>",
      "platform": "linux/amd64",
      "sha256": "<expected-sha256>",
      "path": "bin/gh",
      "versionProbe": ["--version"]
    }
  ]
}
```

The manifest is readiness evidence and diagnostics metadata. It contains no credentials.

### 4.3 Storage

For the local Compose path, the preferred storage is one versioned Docker named volume populated by an initializer service. The initializer is a default one-shot dependency of the agent-runtime worker, so on-demand hosts are operational without enabling a static-host Compose profile and no tool container remains running at steady state.

For on-demand host launches, MoonMind mounts the same named volume into each compatible host. A daemon-visible read-only bind source is also valid when a deployment already manages immutable tool directories outside Docker volumes.

Hosts bind to one completed bundle version for their lifetime. Tool updates publish a new completed bundle version and use it for later hosts; an active host does not observe an in-place tool replacement.

---

## 5. Tool initialization

A trusted initializer is the only writer to the bundle.

For each declared tool, the initializer must:

1. select the correct operating-system and CPU-architecture artifact;
2. obtain one pinned release artifact from an approved source or copy it from a trusted build image;
3. verify the expected SHA-256;
4. install the executable into an isolated staging directory with executable permissions;
5. execute the tool's bounded, non-interactive `versionProbe` arguments from the trusted manifest;
6. atomically publish the completed staging directory, including `manifest.json`, only after every tool passes validation.

The initializer is idempotent. A completed bundle whose manifest does not match the expected manifest fails rather than being modified in place. Incomplete staging state without a completed manifest is not a bundle: the initializer may safely remove that private staging state and retry. Staging paths are unique to an initializer attempt, so a failed attempt cannot poison the versioned published volume or expose partial files to hosts.

Tool downloads do not occur inside an ordinary Omnigent workflow session. An agent cannot add arbitrary executables to the shared bundle.

Before an on-demand host is created, the trusted runtime adapter probes the
completed named volume through the system Docker boundary and verifies the
pinned tool version. It never invokes deployment Compose from the worker: the
worker's project path is not Docker-daemon path authority. A missing or stale
bundle fails with `tool_bundle_unavailable` and deployment-initialization
evidence before session creation.

---

## 6. Host mounts and shell visibility

### 6.1 Required mounts

A compatible host receives:

```text
<tool bundle> -> /opt/moonmind-tools                read-only
<profile file> -> /etc/profile.d/moonmind-tools.sh  read-only
```

The profile file is a small deployment-owned script:

```sh
case ":${PATH}:" in
  *:/opt/moonmind-tools/bin:*) ;;
  *)
    if [ -n "${PATH:-}" ]; then
      export PATH="/opt/moonmind-tools/bin:${PATH}"
    else
      export PATH="/opt/moonmind-tools/bin"
    fi
    ;;
esac
```

The host launch also sets `PATH` directly so non-login processes see the tools without relying on shell startup files:

```text
PATH=/opt/moonmind-tools/bin:<upstream-image-PATH>
```

Both mechanisms are required. Omnigent native harnesses may execute through login shells, and login-shell initialization may rebuild `PATH` after container environment values are applied.

MoonMind must not mount a tool volume over all of `/usr/local/bin`, because that would hide executables already supplied by the upstream host image.

### 6.2 Illustrative Compose shape

The exact service and volume names are deployment details, but the canonical shape is:

```yaml
services:
  omnigent-tools-init:
    image: ${MOONMIND_IMAGE}
    command: ["/opt/moonmind/init-omnigent-tools.sh"]
    volumes:
      - omnigent-tools:/output

  omnigent-host-codex:
    image: ${OMNIGENT_HOST_IMAGE}:${OMNIGENT_HOST_IMAGE_TAG}
    environment:
      PATH: /opt/moonmind-tools/bin:${OMNIGENT_HOST_BASE_PATH:-/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin}
    volumes:
      - omnigent-tools:/opt/moonmind-tools:ro
      - ./services/omnigent/profile/moonmind-tools.sh:/etc/profile.d/moonmind-tools.sh:ro
    depends_on:
      omnigent-tools-init:
        condition: service_completed_successfully

volumes:
  omnigent-tools:
    name: moonmind-omnigent-tools-${OMNIGENT_TOOL_BUNDLE_VERSION}
```

The deployment sets `OMNIGENT_HOST_BASE_PATH` from the selected upstream image's default `PATH`; the fallback shown above preserves the conventional executable and system-administration directories. It must not replace a discovered upstream path with a smaller static list. The volume identity includes the pinned bundle version, so an upgrade publishes a distinct volume and existing hosts remain bound to their original completed bundle. The on-demand Docker launch uses the same target paths and readiness rules.

### 6.3 Docker-daemon visibility

Every bind source used by an on-demand host must be visible to the Docker daemon, not merely to the Temporal worker container issuing `docker run`.

Named volumes are preferred for tool binaries because they avoid worker-path translation. Deployment-owned profile files may use an existing daemon-visible MoonMind project path.

---

## 7. Initial `gh` capability

### 7.1 Why `git` is not enough

`git` provides repository transport and local history operations: clone, fetch, checkout, diff, commit, merge, and push.

GitHub pull requests, reviews, review threads, checks, branch protection, comments, merge queues, and authoritative PR merge state are GitHub service concepts. They require GitHub API access through `gh` or another explicitly supported GitHub client.

The current `pr-resolver` Skill declares both `git` and `gh` because it must modify the repository and inspect or mutate GitHub pull-request state.

### 7.2 Baseline credential mapping

The simple baseline uses GitHub CLI's standard configuration on an on-demand host or a host dedicated to exactly one run:

```text
XDG_CONFIG_HOME=<lease-owned-cache>/moonmind-xdg
GH_PROMPT_DISABLED=1
GH_NO_UPDATE_NOTIFIER=1
GH_NO_EXTENSION_UPDATE_NOTIFIER=1
```

The trusted host entrypoint receives the narrowly scoped credential only long enough to write `gh/hosts.yml` with owner-only permissions under the lease-owned cache volume. It removes the raw credential before Omnigent starts, while the non-secret `XDG_CONFIG_HOME` selector reaches the runner and native Codex app-server. The cache volume is outside the repository workspace and is removed with the host lease, so it is neither shared between sessions nor eligible for workspace capture. A reusable static host is not eligible for credential-bearing GitHub runs because its host-wide configuration could expose credentials to unrelated runners. Such a deployment must route the run to an on-demand or run-dedicated host.

The tool bundle never contains token values. MoonMind resolves the credential at the trusted launch boundary and must keep it out of workflow payloads, Temporal history, logs, artifacts, and durable host metadata.

### 7.3 Runner environment

On an on-demand or run-dedicated host, GitHub CLI settings that are not part of the stock runner allowlist must be explicitly forwarded to that run's spawned runner:

```text
OMNIGENT_RUNNER_ENV_PASSTHROUGH=MOONMIND_STEP_EXECUTION_ID,XDG_CONFIG_HOME,GH_PROMPT_DISABLED,GH_NO_UPDATE_NOTIFIER,GH_NO_EXTENSION_UPDATE_NOTIFIER
```

A deployment may append other non-secret runtime selectors to the same comma-separated setting when another canonical MoonMind contract requires them.

For the stock Codex native wrapper, no token-bearing environment variable or
terminal argument is required. Native Codex preserves `XDG_CONFIG_HOME`, and
model-authored `gh` commands therefore use the same lease-private standard
configuration that both preflight boundaries prove before session creation.

Terminal-evidence Skills also receive the exact, non-secret
`MOONMIND_STEP_EXECUTION_ID` for the current attempt. The on-demand host forwards
that identity to its runner and mounts a run-private
`/etc/profile.d/moonmind-execution.sh` so a native Codex login shell restores it
after Codex applies its own subprocess environment filter. The generated profile
is outside the repository workspace, is bound to the same execution-specific
runtime-script snapshot as the host lease, and is removed with that run-owned
materialization. This lets the portable Skill stamp its normal `executionRef`;
MoonMind does not synthesize or rewrite the Skill's terminal evidence.

For a terminal `merged` or `already_merged` resolver disposition, AgentRun
validates both the Skill's execution-bound `var/pr_resolver/result.json` and its
canonical `artifacts/publish_result.json` companion. It publishes each exact
file as a durable artifact and passes the publish-evidence ref to the parent
workflow. Auto-publication finalization reads and validates that artifact; it
does not require a second publisher, infer success from the disposition alone,
or copy merge semantics into the Omnigent integration.

### 7.4 Private workspace preparation

Mounting `gh` into the host does not authenticate a repository clone that MoonMind performs before the host starts.

When MoonMind prepares a private repository workspace outside the Omnigent container, that clone must use MoonMind's canonical GitHub credential resolver and the existing in-memory Git credential-helper environment. Repository URLs remain token-free.

The pre-host clone and the in-host Git/`gh` commands may use the same resolved credential, but they are distinct execution boundaries and must each receive their required authentication.

### 7.5 Relationship to resolved Skills

A mounted executable satisfies an executable capability; it does not install or replace an Agent Skill.

For `pr-resolver`, MoonMind must separately materialize the resolved immutable Skill closure, including its required sibling Skills, through the canonical Skill projection boundary. The runner must see both:

```text
MOONMIND_ACTIVE_SKILLS_DIR=<read-only resolved Skill snapshot>
PATH contains /opt/moonmind-tools/bin
```

The mounted `gh` binary lets the resolved Skill execute its existing helper scripts. It does not move GitHub snapshot collection, comment classification, CI interpretation, retry policy, or merge decisions into the Omnigent adapter.

---

## 8. Capability readiness

### 8.1 General rule

A run that declares a tool-backed required capability must not start until MoonMind proves that the tool is visible through the same shell path the agent will use.

For a CLI named `<tool>`, minimum readiness uses its trusted manifest probe:

```sh
bash -lc 'command -v <tool>'
bash -lc '<tool> <validated-versionProbe-arguments>'
```

The check must run in the actual stock host environment with the mounted bundle and profile file applied. When the harness creates a distinct runner environment, the same probe must also execute through the exact runner construction path before session creation. Runner-bound verification is mandatory in that case; host-shell success alone is insufficient evidence.

### 8.2 `gh` readiness

When normalized `requiredCapabilities` includes `gh`, readiness includes:

```sh
bash -lc 'command -v gh'
bash -lc 'gh --version'
bash -lc 'gh auth status'
bash -lc 'gh repo view owner/repo --json nameWithOwner'
```

Mutation-capable workflows must also verify that the selected GitHub credential has the repository permissions required by the Skill or publish policy.

If `gh` is missing, unauthenticated, or unauthorized for the target repository, MoonMind blocks before creating the Omnigent session and returns an actionable capability diagnostic.

A host may contain `gh` even when a run does not require it. Mere executable presence does not cause MoonMind to inject GitHub credentials or imply permission to perform GitHub mutations.

### 8.4 MoonMind runtime capabilities

MoonMind-internal runtime capabilities are distinct from mounted CLIs. The
transport route may exist for every compatible host, but the runtime adapter
materializes operation-specific authority only when normalized
`requiredCapabilities` and policy demand it.

For `execution.fanout`, the bearer is stored in a lease-owned read-only file
under `/opt/moonmind/capabilities`. The login-shell profile exports only
`MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE`; it never contains the bearer
value. The portable queue helper reads that file and fails closed when the
selector exists but the file is missing or empty. This preserves unchanged
upstream host images and avoids a token broker, sidecar, broad API token, or
operator-managed permission switch.

The container-job bearer uses the same lease-owned file pattern. Both the
projected dependency-free `moonmind` CLI and the shared Python CLI prefer
`MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE`, fail closed when its selected file
is missing or empty, and use the legacy inline value only when no selector is
present. Same-lease retries atomically refresh file contents before launch.

---

## 9. Use for future tools

MoonMind may add another CLI to the standard bundle when all of the following are true:

- a workflow, Tool, or resolved Skill has a real runtime dependency on the CLI;
- the CLI has a stable non-interactive interface suitable for agent execution;
- a prebuilt artifact exists for the supported Linux architectures;
- the executable runs against the libraries already present in the stock host;
- the CLI does not require a background daemon, privileged installation, kernel changes, or an entrypoint replacement;
- mounting the executable and adding it to `PATH` is materially simpler than changing the host image;
- a required-capability readiness check can prove the tool is usable before session creation.

Future tools should normally join the same standard bundle rather than create one named volume and path convention per executable. Keep the bundle intentionally small and tied to demonstrated workflow requirements.

The mounted-binary approach is not appropriate when a capability requires:

- many tightly coupled system packages;
- shared libraries absent from the host image;
- privileged device, network, or kernel configuration;
- a long-running system service;
- changes to the host entrypoint or base user model;
- a large toolchain whose lifecycle is better managed as an image.

In those cases, prefer an upstream addition to `omnigent-host`. Use a MoonMind-derived image only when the requirement is necessary, upstream inclusion is unavailable, and the broader image ownership is explicitly accepted.

When upstream `omnigent-host` begins supplying a tool that MoonMind mounted only to fill that gap, MoonMind should remove the duplicate from the bundle and use the upstream executable after compatibility and readiness checks pass.

---

## 10. Failure and observability contract

Safe diagnostics may include:

- tool bundle version;
- tool name, reported version, architecture, and expected digest;
- whether the tool volume and profile file were mounted;
- host-shell and runner-shell readiness status;
- required capability and target repository identifier;
- bounded, redacted command failures;
- whether the failure occurred during initialization, host launch, runner visibility, authentication, or authorization.

Diagnostics must not include:

- token values;
- complete environment dumps;
- authenticated URLs;
- credential-helper output;
- arbitrary tool-bundle directory contents beyond the declared manifest.

Representative blockers are:

```text
tool_bundle_unavailable
tool_manifest_mismatch
tool_not_executable
tool_not_visible_in_login_shell
github_auth_unavailable
github_repository_unauthorized
resolved_skill_bundle_unavailable
```

A missing optional tool does not make the host globally unhealthy. It blocks only runs whose required capabilities need that tool.

---

## 11. Conformance requirements

An implementation conforms to this design when all of the following are true:

1. It uses an unchanged upstream `omnigent-host` image for the mounted-tools path.
2. A trusted initializer prepares a pinned, verified tool bundle before the host starts.
3. The host mounts the bundle read-only at `/opt/moonmind-tools`.
4. Both ordinary processes and `bash -lc` shells resolve tools from `/opt/moonmind-tools/bin`.
5. Required-capability preflight blocks before Omnigent session creation when a required tool is missing or unusable.
6. Credentials are resolved separately from the tool bundle and are not stored in durable workflow or host records.
7. The `gh` path supports authenticated GitHub repository and pull-request operations for a target repository while Git transport continues to use normal Git credentials.
8. Private repository preparation succeeds before host launch through the canonical MoonMind Git authentication path.
9. Resolved Skills remain the semantic authority; the mounted binary only supplies the declared executable capability.
10. No ordinary agent run downloads, installs, or mutates shared runtime tools.

The baseline success test for the initial example is an Omnigent Codex runner executing:

```sh
bash -lc 'command -v gh && gh --version && gh auth status'
```

followed by a repository-scoped `gh` operation required by the selected Skill.
