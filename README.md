WARNING: This project is still pre-release. A release should be ready in April 2026 after the full migration to Temporal.

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

*Note: The local Temporal deployment defaults to the built-in `default` namespace. You can override this by setting `TEMPORAL_NAMESPACE` in your `.env` file.*

### Authenticate a runtime with OAuth

```bash
./tools/auth-codex-volume.sh    # Codex CLI (OAuth)
./tools/auth-gemini-volume.sh   # Gemini CLI (OAuth)
```

## Why MoonMind?

### 🛰️ Bring Your Own Agent — or let MoonMind run one for you
Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, orchestrating state-of-the-art agents out of the box.
- **Managed Runtimes:** MoonMind can run Claude Code, Gemini CLI, and Codex CLI as managed workers on your own infrastructure using your existing subscriptions or API keys.
- **Black-Box Coordination:** Even cloud-hosted agents like Jules and Codex Cloud benefit from coordination. MoonMind tracks status, injects context, and closes the feedback loop — whether you control the internals or not.
- **Sandboxed Execution:** Runtimes run behind a Docker socket proxy with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs.
- **Personal-use friendly defaults:** A fresh local install should boot successfully with `docker compose up -d`, then let you enter a small number of secrets in Mission Control instead of forcing enterprise-only secret infrastructure up front.

### 1️⃣ Orchestration Starts At One
You don't need ten agents to benefit from a task execution system. MoonMind supercharges the planning, resiliency, and context management of even a single agent.
- **Mission Control:** See what your agent is doing in real time. Track run status, browse generated artifacts, monitor intervention requests, and audit full execution histories from a single UI.
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
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, and external integrations. |
| **Mission Control** | Operational dashboard for managing tasks and reviewing artifacts. |
| **Qdrant & MinIO** | Vector database for RAG/memory, and S3-compatible artifact storage. |
| **Docker Proxy** | Restricted Docker socket access for sandboxed worker containers. |

## License

MIT — free for personal and commercial use.

### UI Development
To develop the frontend UI, run `npm install` first. Then run `npm run ui:dev` to start the Vite development server; FastAPI still owns routes, auth, and template delivery, so keep the backend running as well. Use `npm run ui:test`, `npm run ui:typecheck`, and `npm run ui:lint` for frontend verification. Use `npm run generate` when you need to refresh checked-in generated frontend artifacts such as `frontend/src/generated/openapi.ts` and the Vite-built Mission Control bundles. Production builds use `npm run ui:build`, which emits assets into `api_service/static/task_dashboard/dist/`. Do not edit files in `dist/` directly.
