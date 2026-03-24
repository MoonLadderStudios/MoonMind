1. Add `@workflow.query` method `get_status` to `MoonMindRunWorkflow` in `moonmind/workflows/temporal/workflows/run.py`
    - It should return `_state`, `_paused`, `_cancel_requested`, `_step_count`, `_summary`, `_awaiting_external`, and `_waiting_reason`.

2. Refactor `_run_integration_stage` loop in `moonmind/workflows/temporal/workflows/run.py` to separate operator resume and poll terminal flag
    - Add `_poll_terminal` flag to explicitly mark when the loop should break because the external integration has reached a terminal status.
    - Change loop exit to use `_poll_terminal`.
    - Stop re-setting `_resume_requested` flag at the end of loop.

3. Refactor `_ensure_manager_and_signal` in `moonmind/workflows/temporal/workflows/agent_run.py`
    - Check if SDK supports `start_workflow` with `start_signal`.
    - Update logic to start the workflow with `ensure_manager` implicitly through signal-with-start if supported.
    - If SDK does not support this gracefully in Python or through `Client`, leave the existing behavior but document the limitation.

4. Create `docs/Temporal/ErrorTaxonomy.md`
    - Add documentation for the different application error types mapped to retry and non-retryable classifications.
    - Update `moonmind/workflows/temporal/activity_catalog.py` `non_retryable_error_codes` to match if possible.
