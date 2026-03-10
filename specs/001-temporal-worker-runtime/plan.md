# Implementation Plan: Temporal worker runtime

## 1. Implement Python Entrypoint

Create `moonmind/workflows/temporal/worker_runtime.py` to act as the true long-running worker.

- It must set up basic async logging.
- It will connect a `temporalio.client.Client` using `settings.temporal.address` and `settings.temporal.namespace`.
- It will resolve the `TemporalWorkerTopology` for the configured fleet (`settings.temporal.worker_fleet`) using `moonmind.workflows.temporal.workers.describe_configured_worker`.
- It will print the resolved topology properties (like `service_name`, `fleet`, `task_queues`, `concurrency_limit`) similar to how `workers.py` does it for the `--describe-json` flag (or standard print). Wait, `describe_configured_worker` returns a dataclass, we'll access `topology.fleet` etc.
- If `topology.fleet` is `workflow`:
  - It registers placeholder workflows `MoonMind.Run` and `MoonMind.ManifestIngest` (defined via `temporalio.workflow.defn` and `temporalio.workflow.run`) because actual implementations are deferred to 4.2.
- If `topology.fleet` is non-workflow:
  - It resolves activities using `build_activity_bindings` for the fleet.
  - Registers the `handler` callables in the `activities` list.
- Instantiates a `temporalio.worker.Worker` with the `client`, the first item of `topology.task_queues`, and the registered `workflows` or `activities`.
- Runs `await worker.run()`.

## 2. Update `start-worker.sh`

Modify `services/temporal/scripts/start-worker.sh` to remove its fallback to `sleep infinity`.

- Remove the block that invokes `python - ... --describe-json` to print topology, as the new entrypoint handles this observability itself. Wait, it's better to keep it if the entrypoint doesn't output the json format required by anything. The shell script says it parses it or just uses it. We can just run `python -m moonmind.workflows.temporal.worker_runtime`.
- If `TEMPORAL_WORKER_COMMAND` is empty, fall back to executing `python -m moonmind.workflows.temporal.worker_runtime`.
- Preserve support for executing an explicit `TEMPORAL_WORKER_COMMAND` if it is set.

## 3. Validate Scope

Run validation tests to ensure standard queue items work without issues, and unit tests pass.