# Task Breakdown: Implement ManagedRuntimeLauncher to be fully functional

## Tasks

- [X] **Task 1**: Add `RunLauncherFunc` to type aliases and `run_launcher` to `ManagedAgentAdapter` in `moonmind/workflows/adapters/managed_agent_adapter.py`.
- [X] **Task 2**: Update `ManagedAgentAdapter.start` to execute `self._run_launcher`.
- [X] **Task 3**: Add `agent_runtime.launch` mapping to `_ACTIVITY_HANDLER_ATTRS` and `agent_runtime_launch` method to `TemporalAgentRuntimeActivities` in `moonmind/workflows/temporal/activity_runtime.py`.
- [X] **Task 4**: Inject `ManagedRuntimeLauncher` and pass it to `TemporalAgentRuntimeActivities` in `moonmind/workflows/temporal/worker_runtime.py`.
- [X] **Task 5**: Define `_run_launcher` in `moonmind/workflows/temporal/workflows/agent_run.py` to trigger the `agent_runtime.launch` activity, and pass it to `ManagedAgentAdapter`.
- [X] **Task 6**: Fix assertion failure and indentation issue in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`.
- [X] **Task 7**: Run the full unit test suite.