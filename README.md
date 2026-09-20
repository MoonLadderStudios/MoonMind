# 🌙 MoonMind — Security, resilience, and observability for AI coding agents

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="210">
    </picture>
</p>

MoonMind is an open-source framework that gives AI coding agents stronger **security**, more **resilient** execution, and more **observable** operations through Temporal-based durable workflows, explicit Provider Profiles and policies, controlled runtime and container boundaries, and an operational dashboard.

For now, MoonMind is focused on software engineering use cases, but it can be used for other use cases as well. Support for workflows that do not require a Git repository will become easier over time.

## Start here: the supported first path

MoonMind coordinates provider-maintained coding agents with security, durable execution, and inspectable results. It is built for engineers who want to direct an agent (Codex, Claude Code, OpenCode, or a future approved harness) without handing it ambient credentials, the host Docker socket, or an unscoped network.

The supported first path is local-first: `docker compose up -d`, open the dashboard at `http://localhost:7000`, add a provider credential to a Provider Profile, then create a workflow and submit it. The first result appears as outputs and artifacts on the Workflow Detail page (`/workflows/{workflowId}`), with logs and diagnostics alongside it. The full steps are in [Quick Start](#quick-start), and the combined MoonMind plus Omnigent check is in [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md).

Main controls per run: one Provider Profile (runtime, credential reference, model and policy), an authorized workspace, mounted Skills and tools, an enforced egress profile, and immutable artifacts carrying step evidence. See [Authentication Contracts](docs/Security/AuthenticationContracts.md), [Restricted egress](docs/Security/RestrictedEgress.md), and the [Secrets System](docs/Security/SecretsSystem.md).

Important limitations up front: suspending or closing the laptop that hosts the MoonMind stack suspends execution with it — Temporal resumes the workflow when the infrastructure returns, it does not keep running while the host sleeps. High-security outbound secret scanning is opt-in (default off); see the [Secrets System](docs/Security/SecretsSystem.md). A binary or image being present does not make an exact runtime combination supported; support is per exact combination in the [Runtime-Provider Rollout Policy](docs/Omnigent/RuntimeProviderRollout.md). Each load-bearing claim below links its owner, evidence, and limits; the editorial checklist is in [README claim/evidence checklist](docs/READMEClaimEvidence.md).

## Runtime direction

**Omnigent is to become MoonMind's primary runtime provider over time.** Codex, Claude Code, OpenCode, and future approved harnesses should converge on one generic Omnigent execution plane rather than accumulating separate MoonMind runtime architectures.

MoonMind will continue to own Temporal orchestration, Provider Profiles, OAuth enrollment, secret references, workspaces, Skills, model and policy selection, publication, checkpoints, remediation, evidence, and cleanup. Omnigent will increasingly provide the host, runner, harness, provider-session, and live interaction substrate beneath those controls.

The migration is deliberately evidence-gated. OpenCode is the first generic-host integration. Codex currently has both direct and profile-bound Omnigent compatibility paths. Claude Code has direct support and Omnigent substrate. These older paths are governed by the [Runtime-Provider Rollout Policy](docs/Omnigent/RuntimeProviderRollout.md): direct rows are `direct_compatibility_only` (explicit, labeled compatibility choice, never a new-work default), and the legacy profile-bound Codex row becomes `retired_for_new_work` once the generic Codex row is qualified — recorded work stays executable, new work follows the qualified default. Codex-specific cutover phases and support rows are owned by [Codex Support and Cutover](docs/Omnigent/CodexSupportAndCutover.md).

The intended host direction is one digest-pinned MoonMind Omnigent image reused by Codex, Claude Code, and OpenCode wherever practical. Separate Host Classes, runtime-pack adapters, credential materializers, and support rows preserve strict runtime and credential isolation even when they share the same image digest.

See the [Omnigent module entrypoint](docs/Omnigent/README.md), the canonical [Omnigent Primary Runtime Provider Strategy](docs/Omnigent/PrimaryRuntimeProviderStrategy.md), the [Omnigent Harness Platform Design](docs/Omnigent/OmnigentHarnessPlatformDesign.md), and the [MoonMind Roadmap](docs/MoonMindRoadmap.md).

## Quick Start

1. [Install Docker Desktop](https://docs.docker.com/get-started/get-docker/) (includes Compose V2)
2. Install git
3. `git clone https://github.com/MoonLadderStudios/MoonMind.git`
4. `cd MoonMind && git submodule update --init --checkout -- moonspec omnigent`. This checks out the recorded `moonspec` and `omnigent` commits, and it is required: the stack references submodule content at startup. It does not advance dependencies to upstream.
5. Run `docker compose up -d` to start the services (infrastructure startup; no mandatory `.env` on a fresh install). Images are pulled from their configured registries (GHCR by default); nothing is built locally on this path. To prefetch without starting, run `docker compose pull` first.
6. Wait until the control plane is ready: the dashboard loads at [http://localhost:7000](http://localhost:7000) **and** `curl -fsS http://localhost:7000/healthz` succeeds. For combined MoonMind plus Omnigent validation, see [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md). Control-plane health means the API and dashboard are up; it does not mean a run is ready — model eligibility and source access below are separate checks.
7. Complete protected operator setup: a zero-config `docker compose up` ships
   the explicit local default (`AUTH_PROVIDER=${AUTH_PROVIDER:-disabled}` in
   `docker-compose.yaml`), which seeds the stable default user for the
   restricted `disabled` quick-start journey (loopback-only bind or documented
   trusted ingress) — it does not select `accounts`, so there is no protected
   first-owner setup or login to complete on that path. Opt in to `accounts`
   explicitly for the production journey with protected first-owner setup and
   invite-only enrollment (no unauthenticated administrator is granted). On an
   existing database with an omitted `AUTH_PROVIDER`, startup stops actionably
   pending an explicit migration decision (`AUTH_PROVIDER` or
   `MOONMIND_AUTH_MIGRATION_DECISION='<mode>:v1'` after migration preflight)
   instead of silently changing owners. Retired selectors (`keycloak`,
   `default`, `google`, `local`) fail at startup with migration guidance. See
   [Authentication Contracts](docs/Security/AuthenticationContracts.md).
8. Authenticate to the application with the configured `AUTH_PROVIDER` mode
   (`accounts` | `oidc` | `header` | explicitly restricted local `disabled`).
   Application login is separate from the source credentials and model
   eligibility below.
9. Make one Provider Profile launch-ready for the work itself (model eligibility, separate from application login and source access above):
     - Add a provider API key to the profile, or use OAuth: in Settings click OAuth next to the profile, follow the instructions on the new tab, then return to Settings and click Finalize.
     - If no profile is launch-ready, the run fails closed with an actionable error instead of silently switching to another profile or model.
10. For repository-backed work, add source credentials for the work itself:
     - Add a GitHub personal access token.
     - Configure any other secrets or settings needed for the first workflow.
11. Check model eligibility, then click Create, select the Runtime and Profile combination the Create page marks as the default for new work (see [Create Page](docs/UI/CreatePage.md)), enter task instructions or select an explicit Skill/Preset (plus repository and branch inputs for repository-backed work), and submit a workflow.
12. Open the resulting Workflow Detail page (`/workflows/{workflowId}`) and inspect its outputs and artifacts, with logs and diagnostics alongside. That inspectable terminal evidence is the end of the first path: a loading dashboard or an accepted submission is not the result.

`.env` is optional for normal local startup. Use `.env-template` only when you want to override defaults or preconfigure advanced settings before launch. The template's `AUTH_PROVIDER` comments describe the same selector, fresh-install, existing-database, and retired-selector behavior summarized above; the canonical contract remains [Authentication Contracts](docs/Security/AuthenticationContracts.md).

### Source access, publication, and repository-free work

Infrastructure startup (steps 1–8), model eligibility (step 9), source access (step 10), and optional publication (this section) are independent concerns. Having no `.env` does not mean every workload is credential-free: repository-backed work currently requires the source authority in step 10.

A PAT-free scratch/anonymous save-only path is [proposed, not shipped](docs/RepositoryAccessAndWorkspaceDesign.md): that design's Status is Proposed, and it becomes the documented default only when its admission, workspace, terminal evidence, and durability handoff pass. Until then, repository work requires the source credentials above. An immediate PR result additionally requires an explicit publication selection that authorizes repository mutation; saving outputs as artifacts without publishing needs no such authorization (see [Workflow Publishing](docs/Workflows/WorkflowPublishing.md)).

### If the model step cannot run

Free or anonymous model availability depends on third-party providers and is not a product guarantee. Eligibility is decided per model from observed catalog data: candidates must have known zero cost across the relevant billing dimensions, the required capabilities, availability, and acceptable data-use terms — unknown price or terms are ineligible. When no model satisfies the policy, the run reports `no_eligible_free_model` with separate reason axes (availability, pricing, privacy) and points at the configured-provider path in step 9 instead.

MoonMind never silently substitutes a paid or keyed profile when the free path is unavailable, and it never infers contributor or training-data consent from a convenience default: data-use terms require explicit acceptance. See [Repository Access and Workspace Design §11](docs/RepositoryAccessAndWorkspaceDesign.md) for the owning contract.

### Troubleshooting the first path

Start with non-destructive checks: `docker compose ps`, `docker compose logs <service>`, and the health endpoints in step 6. The [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md) guide owns the full startup, validation, rollback, and troubleshooting behavior: normal rollback preserves PostgreSQL, MoonMind, Omnigent, OAuth, and artifact data, and destructive cleanup (removing volumes) is a separate opt-in step for confirmed-disposable data or a tested backup — never the first response to a failed startup. Do not disable safety gates to make startup pass; an unhealthy dependency must be fixed, not skipped.

### Access through a host name, LAN, or VPN

Fresh Compose installations publish the dashboard on `127.0.0.1:7000`.
`http://localhost:7000` works on that host; a machine name that resolves to its
LAN or VPN address needs an explicit publish binding.

For an operator-approved private network with restricted ingress, persist the
following in the deployment's existing `.env`, replacing the example address
with the host's approved LAN or VPN interface address:

```dotenv
MOONMIND_API_PUBLISH_HOST=192.0.2.10
MOONMIND_API_HOST_PORT=7000
MOONMIND_TRUSTED_INGRESS=1
```

`MOONMIND_TRUSTED_INGRESS=1` declares that the ingress boundary is already trusted;
it does not configure a firewall or add authentication. The `disabled` auth mode
relies on that boundary. An explicit interface binding serves that interface;
it does not also publish on localhost or other interfaces. Use an authenticated,
restricted ingress for access outside the approved network. See
[Authentication Contracts](docs/Security/AuthenticationContracts.md).

Apply a binding change with `docker compose up -d --no-deps api`, then check
`http://<operator-host>:7000/healthz`, the dashboard, and
`python tools/verify_deployed_ui_assets.py --base-url http://<operator-host>:7000`.
Use the actual operator URL when verifying an update. Preserve these `.env`
settings across upgrades; changing a fresh-install default must not remove an
existing installation's access.

The host update scripts require Python 3.10+ and Docker Compose V2. Run
`bash tools/update-moonmind.sh --branch main` to fetch a source snapshot and
qualify its newest published `sha-<commit>` image without modifying the checkout.
When the fetched tip has no published image yet, the script waits a bounded
interval for that commit's publish, then uses the newest published ancestor
automatically.
The selected image owns application code, migrations, Compose and the release
controller. Its durable updater qualifies all worker queues and the candidate
API, promotes Temporal routing, and replaces the normal fleet. Previous workers
remain available until Temporal confirms their version has drained.

Use this updater for upgrades, including after a direct Compose replacement.
`docker compose pull` and `docker compose up -d` can install new code while
Temporal continues sending workflows to retained workers from the previous
release. Check the current and candidate versions in
`deploy/state/release-jobs/availability.json`; an available old route does not
mean the installed fix is active. Resume an interrupted submission with the
updater's `--resume <submission-id>` option.

The controller compares installed API bindings, networks and access settings
with the candidate before replacement, preserving deployment-owned `.env` and
Compose overrides. It records a resumable submission instead of resetting the
budget when the caller disappears. See [the release contract](docs/Steps/DockerComposeUpdateSystem.md).
Direct `docker compose up` does not run this preflight; intentional access
migrations still require verification through the operator URL afterward.

### OAuth Workflow

When you already have a subscription with a model provider:

1. Go to Settings
2. Click OAuth next to the profile
3. Follow the instructions on the new tab
4. Return to Settings and click Finalize

After Finalize, MoonMind owns a launch-ready OAuth Provider Profile and credential generation. An Omnigent-backed runtime reuses that generation without another login ceremony. The host starts non-interactively and receives only the selected runtime's credential material.

Dedicated static hosts remain an optional advanced deployment choice during migration. To use one, enable the matching host profile in `.env`, such as `COMPOSE_PROFILES="omnigent-host-codex"`, rerun `docker compose up -d`, and explicitly select its static host policy. The long-term direction is a shared digest-pinned host image and generic startup path, not one permanent image architecture per runtime.

## Why MoonMind?

AI coding agents are remarkable, but long-running autonomous work needs more than a terminal process:

- Can I submit work and pick it up from another device while MoonMind keeps it durable?
- Can I inspect logs, diagnostics, artifacts, and step evidence after the fact?
- Can I run build and test containers without handing the agent the host Docker socket?
- Can I intervene, clear context, retry, or recover without losing the audit trail?
- What credentials did the agent receive, and what provider and model policy was used?
- What happened before the run failed, stalled, or hit a rate limit?

MoonMind exists to answer those questions. Progress against each promise below is tracked milestone by milestone in the [MoonMind Roadmap](docs/MoonMindRoadmap.md).

### 🛡️ Security — policy-enforced boundaries around each run

An autonomous agent with your credentials and a shell creates a privileged attack surface unless something constrains it. MoonMind constrains each run in the execution substrate rather than depending on the agent to police its own authority. What each boundary does and does not cover is stated below; the per-claim owner and evidence table is in the [README claim/evidence checklist](docs/READMEClaimEvidence.md):

- **Provider Profiles as policy.** A profile binds runtime, provider, credential source, materialization, concurrency slots, cooldowns, and routing into one declared contract, so model and credential policy is explicit per run rather than ambient environment state.
- **Sandboxed execution.** Managed runtime sessions and specialized workloads run in isolated Docker boundaries with strict capability routing. Containerized build and test jobs are submitted through MoonMind's API-owned Docker Backend Service. Agent runtimes never receive the host Docker socket. File allowlists restrict what a run may modify. Network confinement holds only where the workload's immutable egress profile is attested — see [Restricted egress](docs/Security/RestrictedEgress.md); plain Docker `bridge` and local allowlists are non-enforcing development mechanisms, not enforced boundaries.
- **Secrets stay out of durable state.** Durable contracts carry secret references, never raw values. Credentials are resolved only at controlled launch boundaries for the narrow scope the runtime or MoonMind-owned call path requires; a shared runtime image never receives every runtime's credentials. This does not guarantee that no raw secret ever exists in process memory, and generated runtime files with materialized credentials are ephemeral, not durable artifacts — see the [Secrets System](docs/Security/SecretsSystem.md).
- **Outbound scanning (opt-in high-security mode).** When `MOONMIND_HIGH_SECURITY_MODE` is enabled (default `false`), MoonMind-owned outbound callers scan text payloads and push bundles before a PR comment, message, commit push, or artifact publication, and block on a secret-like finding. Binary attachments, terminal input, and browser automation are outside the native text-scan contract and are never claimed as scanned. With the mode disabled, the scan contract allows the payload through unchanged. Owner: [Secrets System §1.1](docs/Security/SecretsSystem.md).
- **Fail fast, not fallback.** Missing or revoked credentials produce explicit, actionable failures. MoonMind never silently substitutes an alternate credential source, runtime, harness, realizer, or billing-relevant model value.

Where this is headed (planned): typed policy envelopes that declare per run what an agent may touch, governance telemetry that records every privileged action an agent took and why, and a complete audit trail for the secret lifecycle, including creation, rotation, reference, and every launch that resolved one. The goal is that granting an agent autonomy never means granting it trust.

### 🔁 Resilience — durable orchestration that resumes when infrastructure returns

Submit a refactoring job from the dashboard, and Temporal-backed orchestration keeps its durable step ledger across container crashes, worker restarts, and host reboots — resuming when the infrastructure that runs the stack returns. Suspending or powering off the machine hosting the MoonMind stack suspends execution with it; submitting from another device while the stack runs is supported, continuing while the hosting laptop sleeps is not. Each guarantee below states its actual handoff; details are in [Managed and External Agent Execution Model §16](docs/Temporal/ManagedAndExternalAgentExecutionModel.md):

- **Durable step ledger and step-boundary checkpoints.** Long workflows are decomposed into steps whose state, attempts, and outputs are persisted as immutable artifacts. When compatible workspace capture and restore evidence exists, a failed step can resume from the last good step boundary. A verified objective is preserved across reporting failures without re-buying implementation (see `separate_objective_from_execution` in `moonmind/workflows/temporal/github_issue_finalization.py`); that is objective preservation, not a universal exactly-once guarantee for arbitrary external effects.
- **Stuck detection and escalating intervention.** MoonMind detects looping or silently stalled agents and applies escalating responses before they burn through the API budget.
- **Rate limits as a first-class citizen.** Runtime strategies recognize provider rate-limit signals in live output and respond with slot-based concurrency control and cooldowns instead of blind retry storms.
- **Idempotent by design, reconciled on ambiguity.** Start-like side effects (starting runs, publishing results, posting to GitHub or Jira) carry an `idempotencyKey` or deterministic execution tuple, and a retry reuses the same key while inspecting durable run/provider/session/host state before creating side effects — so a crash mid-operation reuses rather than duplicates the recorded work. Ambiguous provider responses still require lane-specific reconciliation; no lane silently creates replacement authority while the original may still be active.
- **Scheduled and recurring workflows.** Run heavy jobs overnight when tokens are cheaper, or put issue triage on a schedule and get alerted on failure.

Where this is headed (planned): self-healing remediation workflows where a dedicated supervisor can target a failed run, read its durable evidence, and execute typed recovery actions with privilege separation and a full audit trail. The aspiration is a system where a failed run at 3 a.m. is diagnosed, repaired, and resumed before you wake up.

### 🔭 Observability — know what your agent actually did

"It finished" is not an answer. MoonMind treats every run as an evidence-producing process. These views exist today:

- **The dashboard.** Track run status in real time, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI.
- **Live logs as a session-aware timeline.** Merged stdout, stderr, system, and session events stream over SSE into one ordered, run-global sequence with durable artifact-backed replay after the run ends. Session boundaries, resets, and epochs are explicit, observable events.
- **Artifact-first outputs.** Prompts, transcripts, diffs, and diagnostics are stored as immutable, content-addressed artifacts rather than buried in process logs, so every run's evidence outlives the container that produced it.
- **Correlated structured logs.** Every log line carries correlation IDs tying it to its workflow, run, activity, and trace. Questions about what happened can be answered without reading raw worker internals.
- **Exact runtime provenance.** Omnigent-backed evidence identifies the host image, harness implementation, runtime pack, credential materializer, Host Class, launch policy, model configuration, and execution realizer that governed the run.

The consolidated per-run governance report (one inspectable report per run built from existing authoritative evidence) is planned, not shipped: see [Per-Run Governance Reports](docs/Governance/RunGovernanceReports.md) (MoonMind#3969). Missing or partial evidence in any view must not be read as a complete audit of every privileged action. Optional model review of generated content is a quality aid, not a security guarantee.

Where this is headed (planned): end-to-end OpenTelemetry tracing from API request through workflow, activity, and provider call, with token and cost attribution per step. The aspiration is that any question about a run, including what it changed, what it spent, why it failed, and which runtime authority it used, has a durable, queryable answer.

### 🛰️ Run CLI agents in MoonMind

Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, placing provider-maintained CLI agents and Omnigent harnesses inside a durable operational envelope:

- **Omnigent-backed agents.** The long-term normal path resolves an Omnigent Agent Profile, Provider Profile, runtime pack, Host Class, materializer, model, and policy into one immutable execution plan. Which exact combinations are a new-work default, an explicit-only choice, a labeled compatibility path, or unavailable is decided per combination by the [Runtime-Provider Rollout Policy](docs/Omnigent/RuntimeProviderRollout.md); a binary, plugin, or image being present never makes a combination supported on its own.
- **Compatibility paths during migration.** Direct Codex and Claude Code rows are `direct_compatibility_only` and the legacy profile-bound Codex row retires for new work once the generic row is qualified (recorded work stays executable). They remain explicit, labeled paths with in-flight Temporal-history and historical-read compatibility until retirement criteria pass — see the [Runtime-Provider Rollout Policy](docs/Omnigent/RuntimeProviderRollout.md) and [Codex Support and Cutover](docs/Omnigent/CodexSupportAndCutover.md).
- **Step-based context management.** Agents perform better on small, focused tasks. MoonMind injects the right context into each step and clears it between steps to prevent context-window pollution.
- **Personal-use friendly defaults.** A fresh local install boots with `docker compose up -d`. Enter a few secrets in the dashboard and begin without requiring enterprise secret infrastructure.

## Architecture

MoonMind's architecture is a set of conceptual components; several Compose services can implement one component. The table below is that conceptual map, not a one-row-per-container inventory:

| Component | Role |
| --- | --- |
| **API Service** | FastAPI control plane for the dashboard, `/api/executions`, artifacts, templates, MCP tools, and the API-owned Docker Backend Service contract. |
| **Temporal Server** | Durable execution engine with PostgreSQL persistence. |
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, runtime supervision, external integrations, and durable container-job execution. |
| **Omnigent Runtime Plane** | Target primary runtime-provider plane for Codex, Claude Code, OpenCode, and future approved harnesses. It uses immutable Agent Profiles, plans, runtime bindings, Host Classes, runtime packs, materializers, exact-host attestation, canonical sessions, and native Workflow Chat. |
| **Managed Compatibility Plane** | Direct Codex and Claude Code (`direct_compatibility_only`) and the legacy Codex profile-bound realizer (`retired_for_new_work` once generic Codex is qualified). Explicit, labeled paths with in-flight and historical-read compatibility until retirement criteria pass. |
| **Docker Backend Service** | Authenticated MCP and HTTP container-job surface that resolves workspaces, applies policy, dispatches bounded jobs through Temporal, and uses one deployment-selected Docker daemon whose image cache is reusable across workflows. |
| **Dashboard** | Operational dashboard for managing workflows, reviewing per-step progress, and inspecting logs, diagnostics, artifacts, runtime provenance, and recovery state. |
| **MinIO** | S3-compatible storage for immutable artifacts and large evidence. |
| **Docker Proxy** | Restricted system-Docker access for trusted MoonMind backend execution. It is not exposed to managed sessions or Omnigent runners. |

MoonMind is vector-free by design: ordinary workflows and chat need no vector
database, embedding credentials, or retrieval-index configuration. PostgreSQL
persists relational records, Temporal persists durable execution state, and
MinIO persists artifacts. There is no MoonMind-managed vector service, profile,
extension, or index, and no implicit external replacement.

The operational counterpart of the conceptual table — every Compose service,
its published ports and profiles, and whether it is steady-state, init/one-shot,
optional, or worker-created on-demand — is derived in the [First-run service
inventory](docs/FirstRunServiceInventory.md), validated against the rendered
`docker-compose.yaml`.

## Contributing

Contributions are welcome, including high-quality AI-assisted pull requests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, validation commands, testing expectations, and pull request guidelines. When using an AI coding agent, also read [AGENTS.md](AGENTS.md) before making changes.

## License

MoonMind is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for copyright and attribution notices.

MoonMind includes Omnigent as a Git submodule at `omnigent/`. Omnigent is separately licensed by the Omnigent project under Apache License 2.0. After running `git submodule update --init --checkout -- moonspec omnigent`, see `omnigent/LICENSE` and `omnigent/NOTICE` for its license and attribution notices. Other submodules retain their own upstream licenses.
