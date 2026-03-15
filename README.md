# 🌙 MoonMind — Mission control for your AI agents

<p align="center">
    <picture>
        <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png">
        <img src="https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/assets/moonmindlogo.png" alt="MoonMind" width="420">
    </picture>
</p>

**MoonMind is Kubernetes for AI agents**

Instead of forcing you to build agents from scratch in a new proprietary framework, MoonMind lets you orchestrate the tools you already use—like **Claude Code, Gemini CLI, Codex, and Jules**. It wraps them in durable execution, secure sandboxing, shared context, and human-in-the-loop oversight.

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

### 🔌 Bring Your Own Agent
Most agent platforms ask you to rebuild your workflows inside their SDK. MoonMind takes the opposite approach: orchestrate existing agents, don't replace them.
* **Wrap Best-in-Class Tools:** Run Claude Code, Gemini CLI, and Codex as managed worker runtimes.
* **Black-Box Orchestration:** Run third-party cloud agents (like Jules) under a strict operational model. MoonMind tracks their status and injects context even if you don't control their internals.
* **Universal Integration:** Connect custom agents via MCP or standard API endpoints.

### 🔓 Free Yourself from Vendor Lock-In
MoonMind can manage any agent runtime and makes it easy to mix agents and models in even a single workflow.
* **Workflow Portability:** Swap between proprietary cloud models and local open-source models with a single configuration change. Keep your memory, artifacts, and orchestration logic intact regardless of the underlying LLM.
* **Multi-Agent Chaining:** Break massive goals into smaller steps. Only use expensive models for steps that need them.
* **Side-by-Side Comparison:** Easily run the same task with different models and runtimes to compare results.

### 🔄 Designed for Resilient Execution
Agent tasks fail in messy ways: rate limits, terminal hangs, and crashed containers. MoonMind expects failure and builds around it.
* **Durable Foundations:** Backed by [Temporal](https://temporal.io/), the system is designed so that workflows can survive container crashes and restarts with deterministic replay.
* **Anti-Loop Protection:** Automatic stuck-detection applies soft and hard resets.
* **Smart Retries:** Failure classification distinguishes between transient errors (safe to retry) and permanent failures (stopping execution before burning your API budget).

### 🛡️ Supervised Autonomy & Restricted Blast Radius
Agents with terminal access can do real damage. MoonMind puts a leash on your agents while still letting them be useful.
* **Human-in-the-Loop (HITL):** Pause execution, step in to answer clarifying questions mid-run, or take over a stuck terminal manually, then hand control back.
* **Approval Gates:** Require human sign-off before high-risk operations (e.g., executing arbitrary code, modifying production databases).
* **Restricted Blast Radius:** Execution runs behind a Docker socket proxy with strict capability routing. File allowlists restrict modifications, and credentials are automatically sanitized from logs.

### 🧠 Actionable Context & Observability
Stop blowing up your context windows with irrelevant data, and stop tailing terminal logs to guess what your agent is doing.
* **Procedural Memory:** Agents learn from past runs and failures, retaining structured summaries so they are less likely to repeat the same mistakes.
* **Universal RAG:** Ground agents in your real docs with built-in loaders for GitHub, Jira, Confluence, Google Drive, and local files.
* **Operator Visibility:** An evolving "Mission Control" surface to track real-time run status, browse generated artifacts, monitor intervention requests, and audit full execution histories.

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
