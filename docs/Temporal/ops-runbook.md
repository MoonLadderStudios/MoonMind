# Operations Runbook (Temporal)

MoonMind background jobs and agent runs are orchestrated by Temporal.

- **User ID:** `00000000-0000-0000-0000-000000000000`
- **Email:** `default@example.com`

Keys for model providers (e.g. Google and OpenAI) are injected from this user's profile when Agent tasks execute. In `disabled` auth mode, the values from `.env` seed this profile on startup.

**Agent auth**: Configure `MOONMIND_WORKER_AUTH_MODE` for worker subprocesses. Ensure the Vault integration (`temporal-worker-sandbox`) correctly configures the GitHub credentials or use standard environment variables for the Fast Path logic.

## Temporal Workflow Operations

- **Services to run**: `docker compose up temporal api temporal-worker-sandbox temporal-worker-sandbox` (Temporal Server, API service, Agent Sandbox, and system worker). Ensure PostgreSQL is reachable as the persistent store for the Temporal cluster.
- **Task Queues**: Confirm that the appropriate workers are bound to their intended Temporal Task Queues.
- **Metrics**: The Temporal Server and Workers emit Prometheus metrics. Point `PROMETHEUS_ENDPOINT` to your collector before triggering runs to capture observability data.
- **Log review / UI**: Navigate to the **Temporal UI** (e.g., `http://localhost:8233`) to find `Workflow Execution` entries. You can confirm each Activity transitions through `Started`, `Completed`, or `Failed` with summarized payloads.
- **Credential validation**: Failed workflows often stem from missing provider or GitHub credentials. The first Activity attempt records the failure reason in Temporal's execution history. Once you resolve secrets, you can retry the workflow or rely on Temporal's native Activity retry policies.
- **Artifact locations**: Patches, JSONL logs, and GitHub API responses are stored under `var/artifacts/workflows/<workflow_id>/`.

### Execution Observability

- **Metrics detail**: Temporal workflows increment standard Prometheus series automatically:
  - `workflow_started` / `workflow_success` / `workflow_failed` counters.
  - `activity_execution_latency` timer reported when an Activity succeeds.
  Use these to graph Task Queue throughput and alert on elevated retry rates.
- **Structured visibility**: Use Temporal's advanced visibility (Search Attributes) to query running workflows by custom state or tenant ID. Forward worker container logs to a JSON-aware sink so debug extras remain queryable when triaging routing or sandbox issues.
- **Retry procedure**: When a workflow fails entirely, you can issue a Native Temporal Reset to the last successful Activity checkpoint via the Temporal UI or CLI (`temporal workflow reset ...`).

## Workflow Recovery and Safeguards

- **Activity Timeouts** (`StartToCloseTimeout`, `HeartbeatTimeout`): Workflows set specific timeouts for LLM activities and long-running Docker builds. If a container hangs, Temporal times out the Activity and automatically retries it according to the RetryPolicy.
- **Stale workers**: If the `temporal-worker-sandbox` crashes, Temporal simply re-routes pending Activities to another healthy worker polling the Task Queue.
- **Operator recovery**: Use the **Temporal UI** to `Terminate` a stuck Workflow Execution, or `Cancel` it to allow it to run its graceful cleanup/rollback Activities. There is no need to clear legacy queue rows in the database, as Temporal acts as the source of truth for all inflight workflows.
