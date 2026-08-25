"""Managed run supervision: heartbeats, timeout, exit classification, cancellation."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import shutil
from collections.abc import Awaitable, Callable
from contextlib import suppress
from temporalio import activity
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for the optional callback fired when a supervised process completes.
# Signature: async (result_dict) -> None
# The result_dict is AgentRunResult-compatible.
CompletionCallback = Callable[[dict[str, Any]], Awaitable[None]]

from moonmind.schemas.agent_runtime_models import (
    MANAGED_PROCESS_LOST_DURING_RECONCILIATION,
    ExecutionBudget,
    ExecutionBudgetVerdict,
    ManagedRunRecord,
    evaluate_execution_budget,
)

from .log_streamer import RuntimeLogStreamer
from .process_activity import ProcessActivityProbe
from .store import ManagedRunStore
from .strategies import get_strategy
from .strategies.base import ManagedRuntimeExitResult
from .output_parser import ParsedOutput

HEARTBEAT_INTERVAL = 30  # seconds
NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS = 30  # seconds
_DUPLICATE_WARNING_THRESHOLD = 3
_WARNING_SUBSTRINGS = ("warning", "warn", "deprecated", "rate limit")
GRACEFUL_TERMINATE_WAIT_SECONDS = (
    1.0  # seconds to wait for graceful SIGTERM before SIGKILL
)

class ManagedRunSupervisor:
    """Supervises managed agent subprocess lifecycle."""

    def __init__(
        self,
        store: ManagedRunStore,
        log_streamer: RuntimeLogStreamer,
        *,
        completion_callback: CompletionCallback | None = None,
    ) -> None:
        self._store = store
        self._log_streamer = log_streamer
        self._completion_callback = completion_callback
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._cleanup_paths: dict[str, tuple[str, ...]] = {}
        self._deferred_cleanup_paths: dict[str, tuple[str, ...]] = {}

    async def supervise(
        self,
        *,
        run_id: str,
        process: asyncio.subprocess.Process,
        budget: ExecutionBudget,
        exit_code_path: str | None = None,
        cleanup_paths: list[str] | None = None,
        deferred_cleanup_paths: list[str] | None = None,
    ) -> ManagedRunRecord:
        """Supervise a process and track heartbeat, completion, and cleanup.

        ``budget`` is the run's progress-aware execution budget, resolved by the
        caller from the same ``timeoutPolicy`` the AgentRun workflow published, so
        the supervisor's kill deadline and the workflow's cannot diverge. The
        process is terminated when the budget's base window elapses *and*
        progress has gone stale, or when its hard ceiling is reached — never on
        elapsed wall-clock alone.
        """
        self._active_processes[run_id] = process
        registered_paths: list[str] = list(cleanup_paths or [])
        if exit_code_path:
            registered_paths.append(exit_code_path)
        self._cleanup_paths[run_id] = tuple(
            path for path in dict.fromkeys(registered_paths) if path
        )
        self._deferred_cleanup_paths[run_id] = tuple(
            path for path in dict.fromkeys(deferred_cleanup_paths or []) if path
        )
        self._store.update_status(run_id, "running")
        start_time = datetime.now(tz=UTC)

        try:
            # Resolve strategy output parser for this runtime
            record = self._store.load(run_id)
            runtime_id = record.runtime_id if record else None
            strategy = get_strategy(runtime_id) if runtime_id else None
            parser = strategy.create_output_parser() if strategy else None
            live_rate_limit_detected = asyncio.Event()
            stalled_progress_detected = asyncio.Event()
            timed_out_by_supervisor = False
            live_rate_limit_requested = False
            stalled_no_progress = False
            stalled_progress_reason: str | None = None
            last_output_seen_at = start_time
            last_no_output_annotation_at = start_time
            # Cumulative observed output size and the supervisor's latest
            # progress timestamp. These are persisted to the run store on each
            # heartbeat (alongside last_heartbeat_at) so the workflow-level
            # no-progress watchdog can observe genuine progress — not just
            # process liveness — for one-shot runtimes such as claude_code.
            # The watchdog (agent_run._status_progress_signature) deliberately
            # ignores last_heartbeat_at, so without these fields a healthy,
            # actively-working run is falsely escalated to intervention.
            output_offset = 0
            latest_observed_progress_at: datetime | None = None
            # Runtime-neutral activity evidence. Output and workspace mutation
            # both go quiet during legitimate work (a buffered one-shot CLI, or a
            # test run that only writes ignored directories), while a wedged
            # process consumes no CPU at all. Progress is the newest of all three
            # signals so no single blind spot can strand a healthy run.
            activity_probe = ProcessActivityProbe(session_pid=process.pid)
            budget_verdict: ExecutionBudgetVerdict = "continue"
            budget_extended = False
            first_stdout_seen = False
            first_stderr_seen = False
            stderr_buffer = ""
            warning_counts: dict[str, int] = {}
            warning_dedup_announced: set[str] = set()
            progress_probe_warning_logged = False
            progress_timeout_seconds = (
                strategy.progress_stall_timeout_seconds(
                    timeout_seconds=budget.base_seconds
                )
                if strategy is not None
                else None
            )

            def _record_annotation(
                annotation_type: str,
                text: str,
                *,
                reason: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> None:
                metadata = dict(metadata or {})
                metadata.setdefault("annotation_type", annotation_type)
                if reason is not None:
                    metadata["reason"] = reason
                metadata.setdefault("source", "supervisor")
                metadata["text"] = text
                self._log_streamer.emit_system_annotation(
                    run_id=run_id,
                    workspace_path=record.workspace_path if record else None,
                    text=text,
                    metadata=metadata,
                    annotation_type=annotation_type,
                )

            def _is_warning_text(text: str) -> bool:
                lowered = text.lower()
                return any(keyword in lowered for keyword in _WARNING_SUBSTRINGS)

            def _handle_stream_chunk(stream_name: str, text: str) -> None:
                nonlocal first_stdout_seen, first_stderr_seen, last_output_seen_at
                nonlocal stderr_buffer, output_offset
                if not text:
                    return
                output_offset += len(text)
                if not text.isspace():
                    last_output_seen_at = datetime.now(tz=UTC)
                if not first_stdout_seen and stream_name == "stdout":
                    first_stdout_seen = True
                    _record_annotation(
                        annotation_type="first_stdout_seen",
                        text="Supervisor: first stdout output received.",
                        reason="stream_observed",
                    )
                if not first_stderr_seen and stream_name == "stderr":
                    first_stderr_seen = True
                    _record_annotation(
                        annotation_type="first_stderr_seen",
                        text="Supervisor: first stderr output received.",
                        reason="stream_observed",
                    )
                if stream_name != "stderr":
                    return
                
                stderr_buffer += text
                if "\n" not in stderr_buffer:
                    return
                
                lines = stderr_buffer.split("\n")
                # The last element is either an empty string (if text ended with \n)
                # or a partial line that we need to buffer.
                stderr_buffer = lines.pop()
                
                for raw_line in lines:
                    line = raw_line.strip()
                    if not line:
                        continue
                    line_key = line.lower()
                    if not _is_warning_text(line_key):
                        continue
                    count = warning_counts.get(line_key, 0) + 1
                    warning_counts[line_key] = count
                    if (
                        count >= _DUPLICATE_WARNING_THRESHOLD
                        and line_key not in warning_dedup_announced
                    ):
                        _record_annotation(
                            annotation_type="warning_deduplicated",
                            text=(
                                "Supervisor: repeated config warning observed "
                                f"{count} times; suppressing duplicates in live view."
                            ),
                            reason="warning_deduplication",
                            metadata={
                                "duplicate_count": count,
                                "warning_text": line,
                            },
                        )
                        warning_dedup_announced.add(line_key)

            _record_annotation(
                annotation_type="run_started",
                text="Supervisor: managed run started.",
                reason="supervisor_state",
            )
            _record_annotation(
                annotation_type="command_launched",
                text="Supervisor: runtime command launched in managed mode.",
                reason="supervisor_state",
            )
            if not (record and record.workspace_path):
                _record_annotation(
                    annotation_type="live_stream_unavailable",
                    text="Supervisor: live streaming unavailable; durable artifact capture continues.",
                    reason="stream_unavailable",
                )

            async def _handle_stream_events(events: list[dict[str, Any]]) -> None:
                nonlocal live_rate_limit_requested
                if strategy is None or not strategy.terminate_on_live_rate_limit():
                    return
                for event in events:
                    if self._is_live_rate_limit_event(event):
                        live_rate_limit_detected.set()
                        live_rate_limit_requested = True
                        break

            async def _latest_progress_at() -> datetime:
                nonlocal progress_probe_warning_logged
                latest = last_output_seen_at
                activity_at = await asyncio.to_thread(
                    activity_probe.sample,
                    datetime.now(tz=UTC),
                )
                if activity_at is not None:
                    latest = max(latest, activity_at)
                if (
                    strategy is None
                    or record is None
                    or not record.workspace_path
                ):
                    return latest
                progress_started_at = record.started_at or start_time
                if progress_started_at.tzinfo is None:
                    progress_started_at = progress_started_at.replace(tzinfo=UTC)
                try:
                    progress_at = await asyncio.to_thread(
                        strategy.probe_progress_at,
                        workspace_path=record.workspace_path,
                        run_id=run_id,
                        started_at=progress_started_at,
                    )
                except Exception:
                    if not progress_probe_warning_logged:
                        logger.warning(
                            "Progress probe failed for managed run %s",
                            run_id,
                            exc_info=True,
                        )
                        progress_probe_warning_logged = True
                    return latest
                if progress_at is None:
                    return latest
                if progress_at.tzinfo is None:
                    progress_at = progress_at.replace(tzinfo=UTC)
                return max(latest, progress_at)

            async def _emit_no_output_annotation(now: datetime) -> None:
                nonlocal last_no_output_annotation_at, stalled_no_progress, stalled_progress_reason
                nonlocal latest_observed_progress_at
                latest_progress_at = await _latest_progress_at()
                # Cache the supervisor's authoritative progress signal so the
                # heartbeat loop can persist it to the run store. This runs on
                # every heartbeat (it is the no_output_callback), just before
                # the heartbeat store update reads the snapshot below.
                latest_observed_progress_at = latest_progress_at
                idle_progress_seconds = max(
                    0.0,
                    (now - latest_progress_at).total_seconds(),
                )
                if (
                    progress_timeout_seconds is not None
                    and not stalled_progress_detected.is_set()
                    and idle_progress_seconds >= progress_timeout_seconds
                ):
                    stalled_no_progress = True
                    stalled_progress_reason = (
                        "Managed runtime made no observable progress for "
                        f"{int(idle_progress_seconds)}s."
                    )
                    _record_annotation(
                        annotation_type="termination_requested_stalled_progress",
                        text=(
                            "Supervisor: process termination requested after "
                            f"{int(idle_progress_seconds)}s without observable progress."
                        ),
                        reason="stalled_no_progress",
                        metadata={
                            "progress_timeout_seconds": progress_timeout_seconds,
                            "idle_progress_seconds": int(idle_progress_seconds),
                            "last_progress_at": latest_progress_at.isoformat(),
                        },
                    )
                    stalled_progress_detected.set()
                    return
                if (
                    now - last_output_seen_at
                ).total_seconds() < NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS:
                    return
                if (
                    now - last_no_output_annotation_at
                ).total_seconds() < NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS:
                    return
                _record_annotation(
                    annotation_type="no_output_interval",
                    text=(
                        "Supervisor: no stdout/stderr observed for "
                        f"{NO_OUTPUT_ANNOTATION_INTERVAL_SECONDS}s; process still running."
                    ),
                    reason="no_output",
                )
                last_no_output_annotation_at = now

            def budget_elapsed_seconds() -> float:
                """Real elapsed run time, for messages that must not understate it."""
                return (datetime.now(tz=UTC) - start_time).total_seconds()

            def _idle_progress_seconds(now: datetime) -> float | None:
                # ``None`` means no progress has ever been observed for this run.
                # That is not evidence of health, so the budget treats it exactly
                # as it did before progress-awareness: terminate at the base
                # window. Only positively observed progress buys an extension.
                if latest_observed_progress_at is None:
                    return None
                return max(
                    0.0,
                    (now - latest_observed_progress_at).total_seconds(),
                )

            def _on_budget_extended(
                elapsed_seconds: float,
                idle_seconds: float | None,
            ) -> None:
                nonlocal budget_extended
                if budget_extended:
                    return
                budget_extended = True
                _record_annotation(
                    annotation_type="execution_budget_extended_for_progress",
                    text=(
                        "Supervisor: base execution budget of "
                        f"{budget.base_seconds}s elapsed while progress was still "
                        "observable; continuing until progress goes stale or the "
                        f"{budget.max_seconds}s ceiling is reached."
                    ),
                    reason="progress_observed",
                    metadata={
                        "base_seconds": budget.base_seconds,
                        "max_seconds": budget.max_seconds,
                        "progress_stall_seconds": budget.progress_stall_seconds,
                        "elapsed_seconds": int(elapsed_seconds),
                        "idle_progress_seconds": (
                            int(idle_seconds) if idle_seconds is not None else None
                        ),
                    },
                )

            def _progress_snapshot() -> tuple[datetime | None, int | None]:
                # Snapshot of observed output progress for the heartbeat store
                # update. ``latest_observed_progress_at`` is refreshed every
                # heartbeat by ``_emit_no_output_annotation`` (which runs just
                # before this snapshot is read). Return ``None`` for the
                # timestamp until real progress has been seen so we never
                # persist a misleading ``last_log_at`` placeholder.
                progress_at = latest_observed_progress_at
                if progress_at is None or progress_at <= start_time:
                    progress_at = None
                return (
                    progress_at,
                    output_offset if output_offset > 0 else None,
                )

            # Run heartbeat/wait and log streaming CONCURRENTLY so that OS
            # pipe buffers are drained in real-time.  Sequential streaming
            # (heartbeat first, then stream) fills the kernel pipe buffer for
            # processes with large output, causing the subprocess write-end to
            # block indefinitely — a deadlock.  Concurrent streaming also means
            # output is captured as it is produced, enabling true live output.
            heartbeat_task = asyncio.create_task(
                self._heartbeat_and_wait_within_budget(
                    run_id,
                    process,
                    budget,
                    started_at=start_time,
                    idle_progress_seconds=_idle_progress_seconds,
                    no_output_callback=_emit_no_output_annotation,
                    progress_snapshot=_progress_snapshot,
                    on_budget_extended=_on_budget_extended,
                )
            )
            stream_task = asyncio.create_task(
                self._log_streamer.stream_and_parse(
                    process.stdout,
                    process.stderr,
                    run_id=run_id,
                    workspace_path=record.workspace_path if record else None,
                    parser=parser,
                    chunk_callback=_handle_stream_chunk,
                    event_callback=_handle_stream_events,
                )
            )
            terminate_on_rate_limit_task = None
            if strategy is not None and strategy.terminate_on_live_rate_limit():
                terminate_on_rate_limit_task = asyncio.create_task(
                    self._terminate_on_signal(
                        process=process,
                        trigger=live_rate_limit_detected,
                    )
                )
            terminate_on_stall_task = None
            if progress_timeout_seconds is not None:
                terminate_on_stall_task = asyncio.create_task(
                    self._terminate_on_signal(
                        process=process,
                        trigger=stalled_progress_detected,
                    )
                )
            process_exit_code, budget_verdict = await heartbeat_task
            timed_out = budget_verdict != "continue"
            # Descendants can inherit the managed process pipes after the CLI
            # exits. Terminate the owned group before waiting for EOF so those
            # inherited descriptors cannot keep stream collection alive.
            self._terminate_owned_process_group_best_effort(process)
            (
                log_refs,
                stdout_content,
                stderr_content,
                parsed_output,
                events,
            ) = await stream_task
            if terminate_on_rate_limit_task is not None:
                if live_rate_limit_detected.is_set():
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_rate_limit_task
                else:
                    terminate_on_rate_limit_task.cancel()
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_rate_limit_task
            stalled_progress_termination_performed = False
            if terminate_on_stall_task is not None:
                if stalled_progress_detected.is_set():
                    with suppress(asyncio.CancelledError):
                        stalled_progress_termination_performed = bool(
                            await terminate_on_stall_task
                        )
                else:
                    terminate_on_stall_task.cancel()
                    with suppress(asyncio.CancelledError):
                        _ = await terminate_on_stall_task

            if timed_out:
                exit_code = None
                timed_out_by_supervisor = True
            else:
                if exit_code_path:
                    exit_code = self._resolve_effective_exit_code(
                        process_exit_code=process_exit_code,
                        exit_code_path=exit_code_path,
                    )
                    _record_annotation(
                        annotation_type="exit_code_resolved",
                        text=f"Supervisor: authoritative exit code resolved to {exit_code}.",
                        reason="exit_code",
                    )
                else:
                    exit_code = process_exit_code
            if timed_out_by_supervisor:
                _record_annotation(
                    annotation_type="termination_requested_timeout",
                    text=(
                        "Supervisor: process termination requested after "
                        f"{self._budget_expiry_text(budget, budget_verdict, budget_elapsed_seconds())}."
                    ),
                    reason="timeout",
                    metadata={
                        "budget_verdict": budget_verdict,
                        "base_seconds": budget.base_seconds,
                        "max_seconds": budget.max_seconds,
                        "progress_stall_seconds": budget.progress_stall_seconds,
                        "budget_extended_for_progress": budget_extended,
                    },
                )
            if live_rate_limit_requested:
                _record_annotation(
                    annotation_type="termination_requested_rate_limit",
                    text=(
                        "Supervisor: process termination requested due to "
                        "live rate-limit detection."
                    ),
                    reason="rate_limit",
                )
            if stalled_no_progress and stalled_progress_termination_performed:
                _record_annotation(
                    annotation_type="run_classified_stalled_progress",
                    text=(
                        "Supervisor: managed runtime stalled without progress; "
                        "classifying as failed."
                    ),
                    reason="stalled_no_progress",
                )
            elif stalled_no_progress:
                _record_annotation(
                    annotation_type="run_completed_before_stalled_progress_termination",
                    text=(
                        "Supervisor: stalled-progress threshold was reached, but the "
                        "process exited before termination completed; using normal "
                        "exit classification."
                    ),
                    reason="stalled_no_progress",
                )

            # Classify exit
            if stalled_no_progress and stalled_progress_termination_performed:
                exit_result = ManagedRuntimeExitResult(
                    status="failed",
                    failure_class="system_error",
                )
            else:
                exit_result = self._classify_exit(
                    runtime_id=runtime_id,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    stdout=stdout_content,
                    stderr=stderr_content,
                    parsed_output=parsed_output,
                )
            status = exit_result.status
            failure_class = exit_result.failure_class

            error_message = None
            if status == "failed":
                if stalled_no_progress and stalled_progress_termination_performed:
                    error_message = stalled_progress_reason or (
                        "Managed runtime stalled without observable progress"
                    )
                elif exit_result.provider_error_code == "429":
                    error_message = "Provider API rate limit exceeded"
                elif exit_result.provider_error_code == "401":
                    error_message = (
                        "Provider authentication required; reauthenticate the "
                        "selected provider profile"
                    )
                else:
                    error_message = f"Process exited with code {exit_code}"
                    if parsed_output.error_messages:
                        error_message += f": {parsed_output.error_messages[0]}"
            elif status == "timed_out":
                error_message = (
                    "Process timed out after "
                    f"{self._budget_expiry_text(budget, budget_verdict, budget_elapsed_seconds())}"
                )
            if status == "completed":
                _record_annotation(
                    annotation_type="run_classified_completed",
                    text="Supervisor: run classified as completed.",
                    reason="classification",
                )
            elif status == "timed_out":
                _record_annotation(
                    annotation_type="run_classified_timed_out",
                    text="Supervisor: run classified as timed_out.",
                    reason="classification",
                )
            else:
                _record_annotation(
                    annotation_type="run_classified_failed",
                    text=(
                        f"Supervisor: run classified as failed "
                        f"({failure_class or 'unknown'})."
                    ),
                    reason="classification",
                    metadata={"failure_class": failure_class},
                )
            duration = (datetime.now(tz=UTC) - start_time).total_seconds()
            _record_annotation(
                annotation_type="diagnostics_collection_started",
                text="Supervisor: persisting diagnostics bundle.",
                reason="diagnostics",
            )
            annotations = self._log_streamer.consume_annotations(run_id)
            observability_events = self._log_streamer.consume_observability_events(run_id)

            diagnostics_ref = self._log_streamer.collect_diagnostics(
                run_id=run_id,
                exit_code=exit_code,
                duration_seconds=duration,
                log_refs=log_refs,
                annotations=annotations,
                parsed_output=parsed_output,
                events=events,
                observability_events=observability_events,
            )
            observability_events_ref = await asyncio.to_thread(
                self._log_streamer.persist_observability_events,
                run_id=run_id,
                workspace_path=record.workspace_path if record else None,
            )

            record = self._store.update_status(
                run_id,
                status,
                exit_code=exit_code,
                finished_at=datetime.now(tz=UTC),
                diagnostics_ref=diagnostics_ref,
                stdout_artifact_ref=log_refs.get("stdout"),
                stderr_artifact_ref=log_refs.get("stderr"),
                last_log_at=datetime.now(tz=UTC),
                failure_class=failure_class,
                provider_error_code=exit_result.provider_error_code,
                error_message=error_message,
                observability_events_ref=observability_events_ref,
            )

            # Fire completion callback (best-effort, never crashes the supervisor).
            if self._completion_callback is not None:
                try:
                    payload = self._build_completion_payload(record, log_refs)
                    await self._completion_callback(payload)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "completion_callback failed for run_id=%s",
                        run_id,
                        exc_info=True,
                    )

            return record
        finally:
            self._terminate_owned_process_group_best_effort(process)
            self._log_streamer.consume_annotations(run_id)
            self._log_streamer.consume_observability_events(run_id)
            self._active_processes.pop(run_id, None)
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))

    @staticmethod
    def _terminate_owned_process_group_best_effort(
        process: asyncio.subprocess.Process,
    ) -> None:
        """Terminate descendants left in the managed CLI's owned process group."""

        if os.name == "nt" or not process.pid:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
            logger.info(
                "Managed CLI exited; killed owned process group pgid=%s",
                process.pid,
            )
        except ProcessLookupError:
            return
        except OSError:
            logger.warning(
                "Unable to clean up owned process group pgid=%s",
                process.pid,
                exc_info=True,
            )

    @staticmethod
    def _budget_expiry_text(
        budget: ExecutionBudget,
        verdict: ExecutionBudgetVerdict,
        elapsed_seconds: float,
    ) -> str:
        """Describe *why* the budget ended the run, not merely that it did.

        A run terminated at the hard ceiling and one terminated for going quiet
        need different operator responses (raise the ceiling versus investigate a
        wedged runtime), so the two are never collapsed into one message. The
        elapsed time is the real one — a run that earned an extension and then
        went quiet ran longer than its base window, and saying otherwise would
        send the operator looking for the wrong thing.
        """

        if verdict == "expired_max_budget":
            return (
                f"{int(elapsed_seconds)}s, reaching the maximum execution budget "
                f"of {budget.max_seconds}s (progress no longer extends the run "
                "past this ceiling)"
            )
        return (
            f"{int(elapsed_seconds)}s with no observable progress for "
            f"{budget.progress_stall_seconds}s "
            f"(base execution budget {budget.base_seconds}s)"
        )

    async def _heartbeat_and_wait_within_budget(
        self,
        run_id: str,
        process: asyncio.subprocess.Process,
        budget: ExecutionBudget,
        *,
        started_at: datetime,
        idle_progress_seconds: Callable[[datetime], float | None],
        no_output_callback: Callable[[datetime], Awaitable[None]] | None = None,
        progress_snapshot: Callable[[], tuple[datetime | None, int | None]]
        | None = None,
        on_budget_extended: Callable[[float, float | None], None] | None = None,
    ) -> tuple[int | None, ExecutionBudgetVerdict]:
        """Heartbeat while waiting, enforcing the progress-aware budget.

        Returns ``(exit_code, verdict)``. A ``continue`` verdict means the process
        exited on its own and ``exit_code`` is authoritative; any other verdict
        means the budget ended the run and ``exit_code`` is ``None``.

        This replaces a flat ``asyncio.wait_for(timeout_seconds)``. That deadline
        could not see progress, so it killed runs that were actively working —
        the whole point of the budget is that elapsed wall-clock alone is not
        evidence of a stuck run. The budget is re-evaluated on the fast tick
        rather than only per heartbeat so the ceiling is still enforced promptly;
        the progress timestamp it reads is refreshed by ``no_output_callback``
        once per heartbeat, which is far finer than the stall window it feeds.

        On expiry the process is terminated immediately so the concurrent
        streaming task observes EOF and completes, allowing the caller's gather
        to unblock. Without this it would block forever on EOF that never comes.
        """
        loop = asyncio.get_running_loop()
        next_heartbeat_at = loop.time() + HEARTBEAT_INTERVAL
        while True:
            if process.returncode is not None:
                return process.returncode, "continue"
            await asyncio.sleep(0.1)

            now = datetime.now(tz=UTC)
            elapsed = (now - started_at).total_seconds()
            idle_seconds = idle_progress_seconds(now)
            verdict = evaluate_execution_budget(
                budget=budget,
                elapsed_seconds=elapsed,
                idle_progress_seconds=idle_seconds,
            )
            if verdict != "continue":
                await self._terminate_process(process)
                if no_output_callback is not None:
                    await no_output_callback(datetime.now(tz=UTC))
                return None, verdict
            if elapsed >= budget.base_seconds and on_budget_extended is not None:
                on_budget_extended(elapsed, idle_seconds)

            if loop.time() < next_heartbeat_at:
                continue
            next_heartbeat_at = loop.time() + HEARTBEAT_INTERVAL
            if no_output_callback is not None:
                await no_output_callback(datetime.now(tz=UTC))
            try:
                activity.heartbeat({"run_id": run_id})
            except Exception as e:
                # Activity heartbeat failures are non-fatal for the supervisor loop
                logger.debug("Activity heartbeat failed: %s", e)
            progress_fields: dict[str, Any] = {}
            if progress_snapshot is not None:
                last_log_at, last_log_offset = progress_snapshot()
                if last_log_at is not None:
                    progress_fields["last_log_at"] = last_log_at
                if last_log_offset is not None:
                    progress_fields["last_log_offset"] = last_log_offset
            self._store.update_status(
                run_id,
                "running",
                last_heartbeat_at=datetime.now(tz=UTC),
                **progress_fields,
            )

    async def cancel(self, run_id: str) -> None:
        """Cancel a running managed process: terminate -> wait -> kill."""
        process = self._active_processes.get(run_id)
        record = self._store.load(run_id)

        def _record_cancel_annotation() -> None:
            self._log_streamer.emit_system_annotation(
                run_id=run_id,
                workspace_path=record.workspace_path if record else None,
                text="Supervisor: process termination requested due to operator cancel.",
                annotation_type="termination_requested_cancel",
                metadata={"source": "supervisor", "reason": "operator_cancel"},
            )

        if process is None:
            self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
            self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))
            _record_cancel_annotation()
            self._store.update_status(
                run_id,
                "canceled",
                finished_at=datetime.now(tz=UTC),
                error_message="Cancelled (process not found in supervisor)",
            )
            self._log_streamer.consume_annotations(run_id)
            return

        _record_cancel_annotation()
        await self._terminate_process(process)
        self._active_processes.pop(run_id, None)
        self._cleanup_runtime_files(self._cleanup_paths.pop(run_id, ()))
        self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))
        self._store.update_status(
            run_id,
            "canceled",
            finished_at=datetime.now(tz=UTC),
            exit_code=process.returncode,
            error_message="Cancelled by supervisor",
        )

    async def reconcile(self) -> list[ManagedRunRecord]:
        """On startup: scan active records and mark lost PIDs as failed."""
        active_records = self._store.list_active()
        reconciled: list[ManagedRunRecord] = []

        for record in active_records:
            if record.pid is not None and not self._pid_alive(record.pid):
                updated = self._store.update_status(
                    record.run_id,
                    "failed",
                    finished_at=datetime.now(tz=UTC),
                    failure_class="system_error",
                    provider_error_code=(
                        MANAGED_PROCESS_LOST_DURING_RECONCILIATION
                    ),
                    error_message=(
                        f"Process {record.pid} not found during reconciliation"
                    ),
                )
                self._cleanup_runtime_files(self._cleanup_paths.pop(record.run_id, ()))
                self._cleanup_runtime_files(
                    self._deferred_cleanup_paths.pop(record.run_id, ())
                )
                reconciled.append(updated)

        return reconciled

    def cleanup_deferred_run_files(self, run_id: str) -> None:
        """Best-effort cleanup for runtime files needed after process exit."""
        self._cleanup_runtime_files(self._deferred_cleanup_paths.pop(run_id, ()))

    @staticmethod
    async def _terminate_on_signal(
        *,
        process: asyncio.subprocess.Process,
        trigger: asyncio.Event,
    ) -> bool:
        await trigger.wait()
        if process.returncode is not None:
            return False
        await ManagedRunSupervisor._terminate_process(process)
        return True

    @staticmethod
    def _is_live_rate_limit_event(event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "rate_limit":
            return True
        status_code = event.get("status_code") or event.get("statusCode")
        try:
            return int(status_code) == 429
        except (TypeError, ValueError):
            return False

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        """Graceful terminate -> wait(2s) -> kill sequence."""
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=GRACEFUL_TERMINATE_WAIT_SECONDS,
            )
        except (asyncio.TimeoutError, ProcessLookupError):
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.wait()

    @staticmethod
    def _classify_exit(
        *,
        runtime_id: str | None,
        exit_code: int | None,
        timed_out: bool,
        stdout: str,
        stderr: str,
        parsed_output: ParsedOutput | None = None,
    ) -> ManagedRuntimeExitResult:
        """Classify process exit into a run state and optional failure class."""

        if timed_out:
            return ManagedRuntimeExitResult(
                status="timed_out",
                failure_class="execution_error",
            )

        if runtime_id:
            strategy = get_strategy(runtime_id)
            if strategy:
                return strategy.classify_result(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    parsed_output=parsed_output,
                )

        if exit_code == 0:
            return ManagedRuntimeExitResult(
                status="completed",
                failure_class=None,
            )
        return ManagedRuntimeExitResult(
            status="failed",
            failure_class="execution_error",
        )

    @staticmethod
    def _resolve_effective_exit_code(
        *,
        process_exit_code: int | None,
        exit_code_path: str | None,
    ) -> int | None:
        """Resolve the authoritative exit code for the managed child process."""
        if not exit_code_path:
            return process_exit_code

        parsed = ManagedRunSupervisor._read_exit_code_file(exit_code_path)
        if parsed is None:
            logger.warning(
                "Missing or invalid managed exit code file at %s; failing closed",
                exit_code_path,
            )
            return 1
        return parsed

    @staticmethod
    def _read_exit_code_file(exit_code_path: str) -> int | None:
        """Read one integer exit code from the given path."""
        try:
            raw_value = Path(exit_code_path).read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return None
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    @staticmethod
    def _cleanup_runtime_files(paths: tuple[str, ...]) -> None:
        """Best-effort cleanup for launcher runtime files."""
        for path in paths:
            with suppress(OSError):
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    @staticmethod
    def _build_completion_payload(
        record: ManagedRunRecord,
        log_refs: dict[str, str],
    ) -> dict[str, Any]:
        """Build an AgentRunResult-compatible dict from a completed ManagedRunRecord."""
        output_refs: list[str] = []
        if record.stdout_artifact_ref:
            output_refs.append(record.stdout_artifact_ref)
        if record.stderr_artifact_ref:
            output_refs.append(record.stderr_artifact_ref)
        if record.diagnostics_ref:
            output_refs.append(record.diagnostics_ref)
        for ref in log_refs.values():
            if ref and ref not in output_refs:
                output_refs.append(ref)

        summary = record.error_message or f"Process exited with status {record.status}"
        return {
            "summary": summary,
            "output_refs": output_refs,
            "failure_class": record.failure_class,
            "provider_error_code": record.provider_error_code,
        }

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check whether a process with the given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False
