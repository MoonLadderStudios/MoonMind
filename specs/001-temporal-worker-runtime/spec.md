# Spec: Implement the real Temporal worker runtime

## 4.1 Implement the real Temporal worker runtime

**Goal**

Create the missing long-running worker process that connects to Temporal Server and polls the configured task queues for each fleet.

**Why this is needed**

Today `start-worker.sh` prints topology information using `workers.py` and then only runs whatever is in `TEMPORAL_WORKER_COMMAND`. In Compose, that currently falls back to `sleep infinity`, which means the containers can start without actually doing any Temporal work.

**Implementation requirements**

- Add a checked-in Python entrypoint under `moonmind/workflows/temporal/` that:
  - connects to `settings.temporal.address`
  - uses `settings.temporal.namespace`
  - determines the current fleet from `settings.temporal.worker_fleet`
  - starts a `temporalio.worker.Worker` for that fleet
  - registers workflows on the workflow fleet
  - registers activities on non-workflow fleets
  - uses the task queue names already defined in `settings.py` and `workers.py`
- preserves the existing topology bootstrap output for observability, but changes the default startup path so a worker process is launched without custom overrides
- update `start-worker.sh` so the script remains useful for debugging but no longer defaults to an idle container in normal operation

**Acceptance tests**

- Starting `temporal-worker-workflow`, `temporal-worker-artifacts`, `temporal-worker-llm`, `temporal-worker-sandbox`, and `temporal-worker-integrations` creates active Temporal pollers for their configured task queues.
- Worker logs clearly identify fleet, namespace, task queues, and registered workflows or activities.
- Removing `TEMPORAL_WORKER_COMMAND` from `.env` does not cause workers to idle; they still run.
- Stopping and restarting a worker container does not require any manual in-container command to resume polling.