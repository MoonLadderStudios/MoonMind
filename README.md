# 🌙 MoonMind — Mission control for your AI agents

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="420">
    </picture>
</p>

MoonMind is the first platform to orchestrate all of the state-of-the-art AI agents directly — Claude Code, Gemini CLI, Codex, etc. — with resiliency, secure sandboxing, and managed context built in.

## Quick Start

1. [Install Docker Desktop](https://docs.docker.com/get-started/get-docker/)
2. Install git
3. `git clone https://github.com/MoonLadderStudios/MoonMind.git`
4. `cp .env-template .env`
5. configure your .env with `nano .env`, `notepad .env`, or your favorite text editor
6. run `docker compose up -d` to start the service

Go to [http://localhost:5000/tasks](http://localhost:5000/tasks) to access Mission Control.

### Authenticate a runtime with OAuth

```bash
./tools/auth-codex-volume.sh    # Codex CLI (OAuth)
./tools/auth-gemini-volume.sh   # Gemini CLI (OAuth)
```

## Why MoonMind?

### 🛰️ Bring Your Own Agent — or let MoonMind run one for you
Other platforms make you rebuild agents in their SDK. MoonMind operates at a higher level of abstraction, orchestrating state-of-the-art agents out of the box.
- **Managed Runtimes:** MoonMind can run Claude Code, Gemini CLI, and Codex CLI as managed workers on your own infrastructure using your existing subscriptions or API keys.
- **Black-Box Coordination:** Even cloud-hosted agents like Jules and Codex Cloud benefit from coordination. MoonMind tracks status, injects context, and responds with feedback even when you can't control the internals.
- **Universal Integration:** Connect any agent through MCP or standard API endpoints.
- **Sandboxed Execution:** Runtimes run behind a Docker socket proxy with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs.

### 1️⃣ Orchestration Starts At One
You don't need ten agents to benefit from an orchestrator. MoonMind supercharges the planning, resiliency, and context management of even a single agent.
- **Multi-Step Planning:** Agents perform better on small, focused tasks. Break a massive goal into discrete steps with presets, and let MoonMind schedule and sequence them.
- **Fire-and-Forget Resiliency:** Submit a refactoring job, close your laptop, and let MoonMind handle the rest. Backed by [Temporal](https://temporal.io/), workflows survive container crashes and restarts. Automatic stuck detection and smart retries keep your agent on track — and off your API bill.
- **Step-Based Context Management:** Inject the right context into each step and clear it between steps. Ground agents with built-in loaders for GitHub, Jira, Confluence, Google Drive, and local files. Procedural memory retains structured summaries from past runs so agents don't repeat the same mistakes.
- **Mission Control:** Track real-time run status, browse generated artifacts, monitor intervention requests, and audit full execution histories.

### 🔓 Free Yourself from Vendor Lock-In
MoonMind can manage any agent runtime and makes it easy to mix agents and models in even a single workflow.
* **Open-Source:** MoonMind is 100% free and open-source software.
* **Workflow Portability:** Swap between proprietary cloud models and local open-source models with a single configuration change. Keep your memory, artifacts, and orchestration logic intact regardless of the underlying LLM.
* **Multi-Agent Chaining:** Break massive goals into smaller steps. Only use expensive models for steps that need them.
* **Side-by-Side Comparison:** Easily run the same task with different models and runtimes to compare results.

## Architecture

MoonMind runs as a set of decoupled containers from a single `docker-compose.yaml`:

| Component | Role |
| --- | --- |
| **API Service** | FastAPI OpenAI-compatible endpoints, MCP server, and job queue API. |
| **Temporal Server** | Durable execution engine with PostgreSQL persistence. |
| **Worker Fleet** | Specialized isolated workers for orchestration, sandbox execution, LLM calls, and external integrations. |
| **Mission Control** | Operational dashboard for managing tasks and reviewing artifacts. |
| **Qdrant & MinIO** | Vector database for RAG/memory, and S3-compatible artifact storage. |
| **Docker Proxy** | Restricted Docker socket access for sandboxed worker containers. |

## License

MIT — free for personal and commercial use.
