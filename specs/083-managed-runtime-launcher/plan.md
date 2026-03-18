# Technical Plan: Implement ManagedRuntimeLauncher to be fully functional

## Objective
Implement Phase 4 of the managed agent launcher, where `ManagedRuntimeLauncher` is used to launch background subprocesses, monitored by `ManagedRunSupervisor`, and initiated by a new Temporal activity `agent_runtime.launch`.

## Architecture
1. **Activity Registration**: Define `agent_runtime_launch` in `TemporalAgentRuntimeActivities` and map it in `_ACTIVITY_HANDLER_ATTRS`.
2. **Dependency Injection**: Instantiate `ManagedRuntimeLauncher` in `worker_runtime.py` and pass it to the activity worker.
3. **Workflow Integration**: In `agent_run.py`, inject a `_run_launcher` callback into `ManagedAgentAdapter` that executes the `agent_runtime.launch` activity.
4. **Adapter Execution**: Update `ManagedAgentAdapter.start()` to call the injected `_run_launcher` instead of synchronously creating a run record in the local store.

## Steps
1. Update `moonmind/workflows/adapters/managed_agent_adapter.py`.
2. Update `moonmind/workflows/temporal/activity_runtime.py`.
3. Update `moonmind/workflows/temporal/worker_runtime.py`.
4. Update `moonmind/workflows/temporal/workflows/agent_run.py`.
5. Update `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`.