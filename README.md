WARNING: This project is still pre-release. A release should be ready in May 2026 after the full migration to Temporal.

# 🌙 MoonMind — Mission control for your AI agents

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="420">
    </picture>
</p>

MoonMind is an open-source platform that orchestrates leading AI agents out of the box — Claude Code, Codex, Gemini CLI, and more — adding safety, resiliency, and observability.

## Quick Start

1. [Install Docker Desktop](https://docs.docker.com/get-started/get-docker/)
2. Install git
3. `git clone https://github.com/MoonLadderStudios/MoonMind.git`
4. run `docker compose up -d` to start the service
5. open [http://localhost:5000/tasks](http://localhost:5000/tasks)
6. add the few secrets you need for your first workload, such as a model-provider API key and GitHub PAT, through Mission Control
7. submit a task

`.env` is optional for normal local startup. Use `.env-template` only when you want to override defaults or preconfigure advanced settings before launch.

### Authenticate a runtime with OAuth

```bash
./tools/auth-codex-volume.sh    # Codex CLI (OAuth)
./tools/auth-gemini-volume.sh   # Gemini CLI (OAuth)
```

## Why MoonMind?

### 🛰️ Bring Your Own Agent — or let MoonMind run one for you
Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, orchestrating state-of-the-art agents out of the box.
- **Managed Sessions and Managed Runs:** MoonMind can run owned CLI runtimes on your own infrastructure using your existing subscriptions or API keys. The current concrete task-scoped managed-session plane is Codex-first; Claude Code and Gemini CLI remain managed-runtime targets and future adopters of the same session pattern where their adapters support it.
- **External Delegated Agents:** Cloud-hosted agents like Jules and Codex Cloud are coordinated through external-agent adapters. MoonMind tracks status, injects context, and closes the feedback loop even when it does not own the provider's runtime envelope.
- **Sandboxed Execution:** Managed runtime sessions and specialized workload containers run through controlled Docker boundaries with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs.
- **Personal-use friendly defaults:** A fresh local install should boot successfully with `docker compose up -d`, then let you enter a small number of secrets in Mission Control instead of forcing enterprise-only secret infrastructure up front.

### 1️⃣ Orchestration Starts At One
You don't need ten agents to benefit from a task execution system. MoonMind supercharges the planning, resiliency, and context management of even a single agent.
- **Mission Control:** See what your agent is doing in real time. Track run status, inspect per-step progress, open step-scoped logs and diagnostics, browse generated artifacts, monitor intervention requests, and audit execution histories from a single UI.
- **Scheduled & Recurring Tasks:** Schedule a heavy job to run overnight when tokens are cheaper, plan a server reboot and get an alert if it fails, or set up a recurring cron schedule for daily issue triaging.
- **Fire-and-Forget Resiliency:** Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Backed by [Temporal](https://temporal.io/), workflows survive container crashes and restarts. Automatic stuck detection and smart retries keep your agent on track — and off your API bill.
- **Step-Based Context Management:** Agents perform better on small, focused tasks. Inject the right context into each step and clear it between steps.

### 🔓 Free Yourself from Vendor Lock-In
MoonMind supports multiple agent runtimes with multiple model providers behind those runtimes and will be adding support for many more.
* **Open-Source:** MoonMind is 100% free and open-source software.
* **Workflow Portability:** Swap between proprietary cloud models and local open-source models with a single configuration change. Only use expensive models for the steps that actually need them.
* **Own Your Data:** Context, artifacts, and memory are stored on your infrastructure. Switch providers without losing what your agents have learned.

## Architecture

MoonMind runs as a set of decoupled containers from a single `docker-compose.yaml`:

| Component | Role |
| --- | --- |
| **API Service** | FastAPI control plane for Mission Control, `/api/executions`, artifacts, templates, proposals, and MCP/context surfaces. |
| **Temporal Server** | Durable execution engine with PostgreSQL persistence. |
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, managed runtime supervision, and external integrations. |
| **Managed Session Plane** | Task-scoped owned runtime sessions. Codex is the current concrete session-plane implementation; future runtime adapters can adopt the same pattern. |
| **External Agent Adapters** | Provider adapters for delegated external agents such as Jules and Codex Cloud. |
| **Docker Workload Plane** | Tool-backed specialized workload containers, such as build/test toolchain images, kept separate from managed agent session identity. |
| **Mission Control** | Operational dashboard for managing tasks, reviewing per-step progress, and inspecting logs, diagnostics, and artifacts. |
| **Qdrant & MinIO** | Vector database for RAG/memory, and S3-compatible artifact storage. |
| **Docker Proxy** | Restricted Docker socket access for sandboxed worker containers. |

## License

MIT — free for personal and commercial use.
