# 🌙 MoonMind — Security, resilience, and observability for AI coding agents

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="210">
    </picture>
</p>

MoonMind is an open-source framework that gives AI coding agents stronger **security**, more **resilient** execution, and more **observable** operations through Temporal-based durable workflows, explicit Provider Profiles and policies, controlled runtime and container boundaries, and an operational dashboard.

For now, MoonMind is focused on software engineering use cases, but it can be used for other use cases as well. Support for workflows that do not require a Git repository will become easier over time.

## Runtime direction

**Omnigent is to become MoonMind's primary runtime provider over time.** Codex, Claude Code, OpenCode, and future approved harnesses should converge on one generic Omnigent execution plane rather than accumulating separate MoonMind runtime architectures.

MoonMind will continue to own Temporal orchestration, Provider Profiles, OAuth enrollment, secret references, workspaces, Skills, model and policy selection, publication, checkpoints, remediation, evidence, and cleanup. Omnigent will increasingly provide the host, runner, harness, provider-session, and live interaction substrate beneath those controls.

The migration is deliberately evidence-gated. OpenCode is the first generic-host integration. Codex currently has both direct and profile-bound Omnigent compatibility paths. Claude Code has direct support and Omnigent substrate. These older paths remain truthfully labeled replay, rollback, migration, and historical-read compatibility until their exact generic Omnigent replacements pass conformance and retirement gates.

The intended host direction is one digest-pinned MoonMind Omnigent image reused by Codex, Claude Code, and OpenCode wherever practical. Separate Host Classes, runtime-pack adapters, credential materializers, and support rows preserve strict runtime and credential isolation even when they share the same image digest.

See the canonical [Omnigent Primary Runtime Provider Strategy](docs/Omnigent/PrimaryRuntimeProviderStrategy.md), the [Omnigent Harness Platform Design](docs/Omnigent/OmnigentHarnessPlatformDesign.md), and the [MoonMind Roadmap](docs/MoonMindRoadmap.md).

## Quick Start

1. [Install Docker Desktop](https://docs.docker.com/get-started/get-docker/)
2. Install git
3. `git clone https://github.com/MoonLadderStudios/MoonMind.git`
4. `cd MoonMind && git submodule update --init --recursive`. This initializes submodules such as Omnigent.
5. Run `docker compose up -d` to start the service
6. Open [http://localhost:7000](http://localhost:7000). For combined MoonMind plus Omnigent validation, see [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md).
7. In Settings:
    - Add a GitHub personal access token
    - Add an API key or use OAuth to authenticate a Provider Profile
    - Configure any other secrets or settings needed for the first workflow
8. Click Create and submit a workflow. Select only a runtime target whose readiness entry is available. Default migration to Omnigent occurs independently for each qualified combination.

`.env` is optional for normal local startup. Use `.env-template` only when you want to override defaults or preconfigure advanced settings before launch.

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

- Can I close my laptop and trust the workflow to continue?
- Can I inspect logs, diagnostics, artifacts, and step evidence after the fact?
- Can I run build and test containers without handing the agent the host Docker socket?
- Can I intervene, clear context, retry, or recover without losing the audit trail?
- What credentials did the agent receive, and what provider and model policy was used?
- What happened before the run failed, stalled, or hit a rate limit?

MoonMind exists to answer those questions. Progress against each promise below is tracked milestone by milestone in the [MoonMind Roadmap](docs/MoonMindRoadmap.md).

### 🛡️ Security — boundaries the agent can't cross

An autonomous agent with your credentials and a shell creates a privileged attack surface unless something constrains it. MoonMind enforces those constraints in the execution substrate rather than depending on the agent to police its own authority:

- **Provider Profiles as policy.** A profile binds runtime, provider, credential source, materialization, concurrency slots, cooldowns, and routing into one declared contract, so model and credential policy is explicit per run rather than ambient environment state.
- **Sandboxed execution.** Managed runtime sessions and specialized workloads run in isolated Docker boundaries with strict capability routing. Containerized build and test jobs are submitted through MoonMind's API-owned Docker Backend Service. Agent runtimes never receive the host Docker socket. File allowlists restrict what a run may modify.
- **Secrets stay out of the blast radius.** Durable contracts carry secret references, never raw values. Credentials are resolved only at controlled launch boundaries and are automatically redacted from logs, artifacts, and outbound text. A shared runtime image never receives every runtime's credentials.
- **Outbound scanning.** A high-security mode adds deterministic secret scans at outbound boundaries before an agent posts a pull request comment, sends a message, pushes a commit, or publishes an artifact.
- **Fail fast, not fallback.** Missing or revoked credentials produce explicit, actionable failures. MoonMind never silently substitutes an alternate credential source, runtime, harness, realizer, or billing-relevant model value.

Where this is headed: typed policy envelopes that declare per run what an agent may touch, governance telemetry that records every privileged action an agent took and why, and a complete audit trail for the secret lifecycle, including creation, rotation, reference, and every launch that resolved one. The goal is that granting an agent autonomy never means granting it trust.

### 🔁 Resilience — fire and forget, literally

Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Every run is backed by [Temporal](https://temporal.io/), so workflows survive container crashes, worker restarts, and host reboots:

- **Durable step ledger and step-boundary checkpoints.** Long workflows are decomposed into steps whose state, attempts, and outputs are persisted as immutable artifacts. When compatible workspace capture and restore evidence exists, a failed step can resume from the last good step boundary. Completed work is never re-bought.
- **Stuck detection and escalating intervention.** MoonMind detects looping or silently stalled agents and applies escalating responses before they burn through the API budget.
- **Rate limits as a first-class citizen.** Runtime strategies recognize provider rate-limit signals in live output and respond with slot-based concurrency control and cooldowns instead of blind retry storms.
- **Idempotent by design.** Externally visible side effects such as starting runs, publishing results, and posting to GitHub or Jira are retry-safe, so a crash mid-operation does not produce duplicates.
- **Scheduled and recurring workflows.** Run heavy jobs overnight when tokens are cheaper, or put issue triage on a schedule and get alerted on failure.

Where this is headed: self-healing remediation workflows where a dedicated supervisor can target a failed run, read its durable evidence, and execute typed recovery actions with privilege separation and a full audit trail. The aspiration is a system where a failed run at 3 a.m. is diagnosed, repaired, and resumed before you wake up.

### 🔭 Observability — know what your agent actually did

"It finished" is not an answer. MoonMind treats every run as an evidence-producing process:

- **The dashboard.** Track run status in real time, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI.
- **Live logs as a session-aware timeline.** Merged stdout, stderr, system, and session events stream over SSE into one ordered, run-global sequence with durable artifact-backed replay after the run ends. Session boundaries, resets, and epochs are explicit, observable events.
- **Artifact-first outputs.** Prompts, transcripts, diffs, and diagnostics are stored as immutable, content-addressed artifacts rather than buried in process logs, so every run's evidence outlives the container that produced it.
- **Correlated structured logs.** Every log line carries correlation IDs tying it to its workflow, run, activity, and trace. Questions about what happened can be answered without reading raw worker internals.
- **Exact runtime provenance.** Omnigent-backed evidence identifies the host image, harness implementation, runtime pack, credential materializer, Host Class, launch policy, model configuration, and execution realizer that governed the run.

Where this is headed: end-to-end OpenTelemetry tracing from API request through workflow, activity, and provider call, with token and cost attribution per step. The aspiration is that any question about a run, including what it changed, what it spent, why it failed, and which runtime authority it used, has a durable, queryable answer.

### 🛰️ Run CLI agents in MoonMind

Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, placing provider-maintained CLI agents and Omnigent harnesses inside a durable operational envelope:

- **Omnigent-backed agents.** The long-term normal path resolves an Omnigent Agent Profile, Provider Profile, runtime pack, Host Class, materializer, model, and policy into one immutable execution plan.
- **Managed compatibility paths.** Direct Codex and Claude Code paths remain available during migration where policy and support status permit them.
- **Step-based context management.** Agents perform better on small, focused tasks. MoonMind injects the right context into each step and clears it between steps to prevent context-window pollution.
- **Personal-use friendly defaults.** A fresh local install boots with `docker compose up -d`. Enter a few secrets in the dashboard and begin without requiring enterprise secret infrastructure.

## Architecture

MoonMind runs as a set of decoupled containers from a single `docker-compose.yaml`:

| Component | Role |
| --- | --- |
| **API Service** | FastAPI control plane for the dashboard, `/api/executions`, artifacts, templates, proposals, MCP tools, and the API-owned Docker Backend Service contract. |
| **Temporal Server** | Durable execution engine with PostgreSQL persistence. |
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, runtime supervision, external integrations, and durable container-job execution. |
| **Omnigent Runtime Plane** | Target primary runtime-provider plane for Codex, Claude Code, OpenCode, and future approved harnesses. It uses immutable Agent Profiles, plans, runtime bindings, Host Classes, runtime packs, materializers, exact-host attestation, canonical sessions, and native Workflow Chat. |
| **Managed Compatibility Plane** | Direct Codex and Claude Code and the retained legacy Codex profile-bound realizer. These remain explicit migration, rollback, replay, and historical-read paths until retirement criteria pass. |
| **Docker Backend Service** | Authenticated MCP and HTTP container-job surface that resolves workspaces, applies policy, dispatches bounded jobs through Temporal, and uses one deployment-selected Docker daemon whose image cache is reusable across workflows. |
| **Dashboard** | Operational dashboard for managing workflows, reviewing per-step progress, and inspecting logs, diagnostics, artifacts, runtime provenance, and recovery state. |
| **Qdrant and MinIO** | Vector database for RAG and memory, and S3-compatible artifact storage. |
| **Docker Proxy** | Restricted system-Docker access for trusted MoonMind backend execution. It is not exposed to managed sessions or Omnigent runners. |

## Contributing

Contributions are welcome, including high-quality AI-assisted pull requests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, validation commands, testing expectations, and pull request guidelines. When using an AI coding agent, also read [AGENTS.md](AGENTS.md) before making changes.

## License

MoonMind is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for copyright and attribution notices.

MoonMind includes Omnigent as a Git submodule at `omnigent/`. Omnigent is separately licensed by the Omnigent project under Apache License 2.0. After running `git submodule update --init --recursive`, see `omnigent/LICENSE` and `omnigent/NOTICE` for its license and attribution notices. Other submodules retain their own upstream licenses.