# 🌙 MoonMind — Safety, resiliency, and observability for Claude Code and Codex CLI

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="210">
    </picture>
</p>

MoonMind is an open-source framework that makes Claude Code and Codex CLI **safer**, more **resilient**, and more **observable** by wrapping agent CLI runs in Docker containers, using Temporal-based durable workflows, and providing a powerful UI dashboard.

For now, MoonMind is focused on software engineering use cases, but can be used for other use cases as well and this will be made easier in the future (e.g. not requiring a git repo).

UPDATE: MoonMind is in the process of incorporating [Omnigent-host](https://github.com/omnigent-ai/omnigent) as a supported managed agent, which will make MoonMind compatible with Claude Code, Codex, Antigravity, Cursor, OpenCode, Hermes, Pi, and other agents. This should be finished by the end of July 2026.

## Quick Start

1. [Install Docker Desktop](https://docs.docker.com/get-started/get-docker/)
2. Install git
3. `git clone https://github.com/MoonLadderStudios/MoonMind.git`
4. `cd MoonMind && git submodule update --init --recursive`. This initializes the submodules like Omnigent.
5. Run `docker compose up -d` to start the service
6. Open [http://localhost:7000](http://localhost:7000). For combined MoonMind plus Omnigent validation, see [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md).
7. In Settings:
    - Add a GitHub personal access token
    - Add an API key or click OAuth to authenticate a provider profile
    - Configure any other secrets or settings you want to adjust for your first workflow
8. Click Create and submit a workflow!

`.env` is optional for normal local startup. Use `.env-template` only when you want to override defaults or preconfigure advanced settings before launch.

### OAuth Workflow
If you already have a subscription with a model provider:

1. Go to Settings
2. Click OAuth next to the profile
3. Follow the instructions on the new tab
4. Go back to Settings and click Finalize

To expose a Settings-authenticated OAuth profile to Omnigent, enable the matching dedicated host profile in `.env`, for example `COMPOSE_PROFILES="omnigent-host-codex"`, and rerun `docker compose up -d`. The dedicated hosts live in the canonical Compose file, so no platform-specific `COMPOSE_FILE` list is required. See [Combined Stack Validation and Rollback](docs/Omnigent/CombinedStackValidationAndRollback.md#dedicated-oauth-hosts).

## Why MoonMind?

Claude Code and Codex CLI are remarkable agents, but long-running autonomous work needs more than a terminal process:

- Can I close my laptop and trust the workflow to continue?
- Can I inspect logs, diagnostics, artifacts, and step evidence after the fact?
- Can I run build and test containers without handing the agent the host Docker socket?
- Can I intervene, clear context, retry, or recover without losing the audit trail?
- What credentials did the agent receive, and what provider and model policy was used?
- What happened before the run failed, stalled, or hit a rate limit?

MoonMind exists to answer those questions. Progress against each promise below is tracked milestone-by-milestone in the [MoonMind Roadmap](docs/MoonMindRoadmap.md).

### 🛡️ Safety — boundaries the agent can't cross

An autonomous agent with your credentials and a shell is a liability unless something constrains it. MoonMind builds the constraints into the execution substrate rather than trusting the agent to behave:

- **Provider Profiles as policy.** A profile binds runtime, provider, credential source, materialization, concurrency slots, cooldowns, and routing into one declared contract — so model and credential policy is explicit per run, never ambient environment state.
- **Sandboxed execution.** Managed runtime sessions and specialized workloads run in isolated Docker boundaries with strict capability routing. Containerized build and test jobs are submitted through MoonMind's API-owned Docker Backend Service; agent runtimes never receive the host Docker socket. File allowlists restrict what a run may modify.
- **Secrets stay out of the blast radius.** Durable contracts carry secret *references*, never raw values; credentials are resolved only at controlled launch boundaries and automatically redacted from logs, artifacts, and outbound text. OAuth credentials live in isolated per-runtime volumes, so one runtime cannot read another's auth state.
- **Outbound scanning.** A high-security mode adds deterministic secret scans at outbound boundaries — before an agent posts a PR comment, sends a message, pushes a commit, or publishes an artifact.
- **Fail-fast, not fall-back.** Missing or revoked credentials produce explicit, actionable failures. MoonMind never silently substitutes an alternate credential source or rewrites billing-relevant values like model identifiers.

Where this is headed: typed policy envelopes that declare per-run what an agent may touch, governance telemetry that records every privileged action an agent took and why, and a complete audit trail for the secret lifecycle — creation, rotation, reference, and every launch that resolved one. The goal is that granting an agent autonomy never means granting it trust.

### 🔁 Resiliency — fire and forget, literally

Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Every run is backed by [Temporal](https://temporal.io/), so workflows survive container crashes, worker restarts, and host reboots:

- **Durable step ledger and step-boundary checkpoints.** Long workflows are decomposed into steps whose state, attempts, and outputs are persisted as immutable artifacts. When compatible workspace capture and restore evidence exists, a failed step can resume from the last good step boundary — completed work is never re-bought.
- **Stuck detection and escalating intervention.** MoonMind detects looping or silently stalled agents and applies escalating responses — soft reset, hard reset, termination — before they burn through your API budget.
- **Rate limits as a first-class citizen.** Runtime strategies recognize provider rate-limit signals in live output and respond with slot-based concurrency control and cooldowns instead of blind retry storms.
- **Idempotent by design.** Externally visible side effects — starting runs, publishing results, posting to GitHub or Jira — are retry-safe, so a crash mid-operation never produces duplicates.
- **Scheduled and recurring workflows.** Run heavy jobs overnight when tokens are cheaper, or put issue triage on a cron schedule and get alerted on failure.

Where this is headed: self-healing remediation workflows — already taking shape in the codebase — where a dedicated supervisor can target a failed run, read its durable evidence, and execute typed recovery actions (resume, retry, interrupt, clear) with privilege separation and a full audit trail. The aspiration is a system where a failed run at 3 a.m. is diagnosed, repaired, and resumed before you wake up.

### 🔭 Observability — know what your agent actually did

"It finished" is not an answer. MoonMind treats every run as an evidence-producing process:

- **The dashboard.** Track run status in real time, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI.
- **Live logs as a session-aware timeline.** Merged stdout/stderr/system/session events stream over SSE into one ordered, run-global sequence — with durable artifact-backed replay after the run ends. Session boundaries, resets, and epochs are explicit, observable events.
- **Artifact-first outputs.** Prompts, transcripts, diffs, and diagnostics are stored as immutable, content-addressed artifacts rather than buried in process logs, so every run's evidence outlives the container that produced it.
- **Correlated structured logs.** Every log line carries correlation IDs tying it to its workflow, run, activity, and trace — "what happened?" is answerable without reading raw worker internals.

Where this is headed: end-to-end OpenTelemetry tracing from API request through workflow, activity, and provider call — with token and cost attribution per step, so you can see not just what an agent did but what it cost. The aspiration is that any question about a run — what it changed, what it spent, why it failed — has a durable, queryable answer.

### 🛰️ Run CLI agents in MoonMind

Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, running state-of-the-art CLI agents inside a durable operational envelope:

- **Managed sessions and managed runs.** MoonMind runs owned CLI runtimes on your own infrastructure using your existing subscriptions or API keys.
- **Step-based context management.** Agents perform better on small, focused tasks. MoonMind injects the right context into each step and clears it between steps to prevent context-window pollution.
- **Personal-use friendly defaults.** A fresh local install boots with `docker compose up -d`; enter a few secrets in the dashboard and go — no enterprise secret infrastructure required up front.

## Architecture

MoonMind runs as a set of decoupled containers from a single `docker-compose.yaml`:

| Component | Role |
| --- | --- |
| **API Service** | FastAPI control plane for the dashboard, `/api/executions`, artifacts, templates, proposals, MCP/context surfaces, and the API-owned Docker Backend Service contract. |
| **Temporal Server** | Durable execution engine with PostgreSQL persistence. |
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, managed runtime supervision, external integrations, and durable container-job execution. |
| **Managed Session Plane** | Workflow-scoped owned runtime sessions for Codex CLI and future Omnigent-backed runtimes. Container jobs remain separate from session identity and are requested through MoonMind tools. |
| **Docker Backend Service** | Authenticated MCP/HTTP container-job surface that resolves workspaces, applies policy, dispatches bounded jobs through Temporal, and uses one deployment-selected Docker daemon whose image cache is reusable across workflows. |
| **Dashboard** | Operational dashboard for managing workflows, reviewing per-step progress, and inspecting logs, diagnostics, and artifacts. |
| **Qdrant & MinIO** | Vector database for RAG/memory, and S3-compatible artifact storage. |
| **Docker Proxy** | Restricted system-Docker access for trusted MoonMind backend execution; it is not exposed to managed sessions or Omnigent runners. |

## Contributing

Contributions are welcome, including high-quality AI-assisted pull requests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, validation commands, testing expectations, and pull request guidelines. If you are using an AI coding agent, also read [AGENTS.md](AGENTS.md) before making changes.

## License

MoonMind is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for the copyright and attribution notice.

MoonMind includes Omnigent as a Git submodule at `omnigent/`. Omnigent is separately licensed by the Omnigent project under Apache License 2.0; after running `git submodule update --init --recursive`, see `omnigent/LICENSE` and `omnigent/NOTICE` for its license and attribution notices. Other submodules retain their own upstream licenses.
