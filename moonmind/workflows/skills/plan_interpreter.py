"""Deterministic plan interpreter for DAG skill execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Mapping

from .artifact_store import ArtifactStore
from .plan_validation import PlanValidationError, ValidatedPlan, validate_plan
from .tool_plan_contracts import ToolFailure, Step, ToolResult
from .tool_registry import ToolRegistrySnapshot

ToolExecutor = Callable[[Step], ToolResult | Awaitable[ToolResult]]


class PlanExecutionError(RuntimeError):
    """Raised when plan execution cannot proceed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlanProgress:
    """Structured progress object for plan execution query surfaces."""

    total_nodes: int
    pending: int
    running: int
    succeeded: int
    failed: int
    last_event: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        total_nodes: int,
        pending: int,
        running: int,
        succeeded: int,
        failed: int,
        last_event: str,
    ) -> "PlanProgress":
        timestamp = datetime.now(tz=UTC).replace(microsecond=0)
        return cls(
            total_nodes=total_nodes,
            pending=pending,
            running=running,
            succeeded=succeeded,
            failed=failed,
            last_event=last_event,
            updated_at=timestamp.isoformat().replace("+00:00", "Z"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "pending": self.pending,
            "running": self.running,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "last_event": self.last_event,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class PlanExecutionSummary:
    """Aggregated terminal result of a plan execution."""

    status: str
    results: Mapping[str, ToolResult]
    failures: Mapping[str, ToolFailure]
    skipped: tuple[str, ...]
    progress: PlanProgress
    progress_artifact_ref: str | None = None
    summary_artifact_ref: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "results": {
                node_id: result.to_payload()
                for node_id, result in sorted(
                    self.results.items(), key=lambda item: item[0]
                )
            },
            "failures": {
                node_id: failure.to_payload()
                for node_id, failure in sorted(
                    self.failures.items(), key=lambda item: item[0]
                )
            },
            "skipped": list(self.skipped),
            "progress": self.progress.to_payload(),
            "progress_artifact_ref": self.progress_artifact_ref,
            "summary_artifact_ref": self.summary_artifact_ref,
        }


@dataclass(slots=True)
class PlanInterpreter:
    """Deterministically execute a validated DAG plan."""

    validated_plan: ValidatedPlan
    registry_snapshot: ToolRegistrySnapshot
    executor: ToolExecutor
    artifact_store: ArtifactStore | None = None
    write_progress_artifact: bool = False
    _latest_progress: PlanProgress | None = field(default=None, init=False)
    _last_progress_artifact_ref: str | None = field(default=None, init=False)

    @classmethod
    def create(
        cls,
        *,
        plan: Any,
        registry_snapshot: ToolRegistrySnapshot,
        executor: ToolExecutor,
        artifact_store: ArtifactStore | None = None,
        write_progress_artifact: bool = False,
    ) -> "PlanInterpreter":
        validated = validate_plan(plan=plan, registry_snapshot=registry_snapshot)
        return cls(
            validated_plan=validated,
            registry_snapshot=registry_snapshot,
            executor=executor,
            artifact_store=artifact_store,
            write_progress_artifact=write_progress_artifact,
        )

    def query_progress(self) -> PlanProgress | None:
        """Return the latest progress snapshot for query APIs."""

        return self._latest_progress

    @property
    def _plan(self):
        return self.validated_plan.plan

    def _dependencies(self) -> dict[str, tuple[str, ...]]:
        deps: dict[str, list[str]] = {node.id: [] for node in self._plan.nodes}
        for edge in self._plan.edges:
            deps[edge.to_node].append(edge.from_node)
        return {node_id: tuple(sorted(values)) for node_id, values in deps.items()}

    def _set_progress(
        self,
        *,
        pending: int,
        running: int,
        succeeded: int,
        failed: int,
        last_event: str,
    ) -> None:
        progress = PlanProgress.create(
            total_nodes=len(self._plan.nodes),
            pending=pending,
            running=running,
            succeeded=succeeded,
            failed=failed,
            last_event=last_event,
        )
        self._latest_progress = progress

        if self.write_progress_artifact and self.artifact_store is not None:
            artifact = self.artifact_store.put_json(
                progress.to_payload(),
                metadata={
                    "name": "progress.json",
                    "producer": "plan.interpreter",
                    "labels": ["plan", "progress"],
                },
            )
            self._last_progress_artifact_ref = artifact.artifact_ref

    @staticmethod
    def _resolve_json_pointer(document: Any, pointer: str) -> Any:
        if pointer == "":
            return document
        if not pointer.startswith("/"):
            raise PlanExecutionError(
                "invalid_reference", f"json_pointer must start with '/': {pointer}"
            )
        current = document
        for token in pointer.split("/")[1:]:
            decoded = token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping):
                if decoded not in current:
                    raise PlanExecutionError(
                        "invalid_reference",
                        f"json_pointer segment '{decoded}' not found in mapping",
                    )
                current = current[decoded]
                continue
            if isinstance(current, list):
                if not decoded.isdigit():
                    raise PlanExecutionError(
                        "invalid_reference",
                        f"json_pointer segment '{decoded}' is not a list index",
                    )
                index = int(decoded)
                if index >= len(current):
                    raise PlanExecutionError(
                        "invalid_reference",
                        f"json_pointer list index out of range: {index}",
                    )
                current = current[index]
                continue
            raise PlanExecutionError(
                "invalid_reference",
                f"json_pointer segment '{decoded}' cannot be applied to scalar value",
            )
        return current

    def _resolve_inputs(
        self,
        value: Any,
        *,
        results: Mapping[str, ToolResult],
    ) -> Any:
        if (
            isinstance(value, Mapping)
            and set(value.keys()) == {"ref"}
            and isinstance(value.get("ref"), Mapping)
        ):
            ref = value["ref"]
            node_id = str(ref.get("node") or "").strip()
            pointer = str(ref.get("json_pointer") or "").strip()
            if node_id not in results:
                raise PlanExecutionError(
                    "invalid_reference",
                    f"Referenced node '{node_id}' has not produced results",
                )
            payload = results[node_id].to_payload()
            return self._resolve_json_pointer(payload, pointer)

        if isinstance(value, Mapping):
            return {
                key: self._resolve_inputs(item, results=results)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_inputs(item, results=results) for item in value]
        return value

    async def _execute_node(self, invocation: Step) -> ToolResult:
        resolved = self.executor(invocation)
        if asyncio.iscoroutine(resolved):
            resolved = await resolved
        if not isinstance(resolved, ToolResult):
            raise PlanExecutionError(
                "invalid_result",
                f"Executor returned unsupported result type: {type(resolved)!r}",
            )
        return resolved

    @staticmethod
    def _record_task_outcome(
        *,
        task: asyncio.Task[ToolResult],
        node_id: str,
        succeeded: dict[str, ToolResult],
        failures: dict[str, ToolFailure],
    ) -> None:
        if node_id in succeeded or node_id in failures:
            return
        try:
            result = task.result()
        except asyncio.CancelledError:
            failures[node_id] = ToolFailure(
                error_code="CANCELLED",
                message=f"Node '{node_id}' was cancelled",
                retryable=False,
            )
            return
        except ToolFailure as failure:
            failures[node_id] = failure
            return
        except Exception as exc:
            failures[node_id] = ToolFailure(
                error_code="INTERNAL",
                message=f"Node '{node_id}' raised: {exc}",
                retryable=True,
            )
            return

        if result.status == "SUCCEEDED":
            succeeded[node_id] = result
        elif result.status == "CANCELLED":
            failures[node_id] = ToolFailure(
                error_code="CANCELLED",
                message=f"Node '{node_id}' returned CANCELLED",
                retryable=False,
            )
        else:
            failures[node_id] = ToolFailure(
                error_code="EXTERNAL_FAILED",
                message=f"Node '{node_id}' returned status {result.status}",
                retryable=False,
                details={"status": result.status},
            )

    async def run(self) -> PlanExecutionSummary:
        """Execute plan nodes according to dependency and policy semantics."""

        dependencies = self._dependencies()
        nodes = {node.id: node for node in self._plan.nodes}

        pending: set[str] = set(nodes.keys())
        running: dict[asyncio.Task[ToolResult], str] = {}
        succeeded: dict[str, ToolResult] = {}
        failures: dict[str, ToolFailure] = {}
        skipped: set[str] = set()

        max_concurrency = self._plan.policy.max_concurrency
        failure_mode = self._plan.policy.failure_mode

        self._set_progress(
            pending=len(pending),
            running=0,
            succeeded=0,
            failed=0,
            last_event="Plan execution started",
        )

        while pending or running:
            if failure_mode == "FAIL_FAST" and failures:
                for task in running:
                    task.cancel()
                if running:
                    await asyncio.gather(*running.keys(), return_exceptions=True)
                    for task, node_id in list(running.items()):
                        self._record_task_outcome(
                            task=task,
                            node_id=node_id,
                            succeeded=succeeded,
                            failures=failures,
                        )
                    running.clear()
                skipped.update(sorted(pending))
                pending.clear()
                self._set_progress(
                    pending=len(pending),
                    running=0,
                    succeeded=len(succeeded),
                    failed=len(failures),
                    last_event="Fail-fast cancelled in-flight nodes",
                )
                break

            ready = sorted(
                node_id
                for node_id in pending
                if all(dep in succeeded for dep in dependencies.get(node_id, ()))
            )

            if not ready and not running:
                blocked = sorted(pending)
                skipped.update(blocked)
                pending.clear()
                break

            while ready and len(running) < max_concurrency:
                node_id = ready.pop(0)
                node = nodes[node_id]
                try:
                    resolved_inputs = self._resolve_inputs(
                        node.inputs, results=succeeded
                    )
                except PlanExecutionError as exc:
                    failures[node_id] = ToolFailure(
                        error_code=exc.code.upper(),
                        message=str(exc),
                        retryable=False,
                    )
                    pending.remove(node_id)
                    self._set_progress(
                        pending=len(pending),
                        running=len(running),
                        succeeded=len(succeeded),
                        failed=len(failures),
                        last_event=f"Failed to resolve inputs for {node_id}",
                    )
                    continue

                invocation = Step(
                    id=node.id,
                    skill_name=node.skill_name,
                    skill_version=node.skill_version,
                    inputs=resolved_inputs,
                    options=node.options,
                )
                task = asyncio.create_task(self._execute_node(invocation))
                running[task] = node_id
                pending.remove(node_id)

                self._set_progress(
                    pending=len(pending),
                    running=len(running),
                    succeeded=len(succeeded),
                    failed=len(failures),
                    last_event=f"Started {node_id}",
                )

            if not running:
                continue

            done, _ = await asyncio.wait(
                running.keys(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                node_id = running.pop(task)
                self._record_task_outcome(
                    task=task,
                    node_id=node_id,
                    succeeded=succeeded,
                    failures=failures,
                )

                self._set_progress(
                    pending=len(pending),
                    running=len(running),
                    succeeded=len(succeeded),
                    failed=len(failures),
                    last_event=f"Completed {node_id}",
                )

        if failures and failure_mode == "FAIL_FAST":
            status = "FAILED"
        elif failures:
            status = "PARTIAL"
        elif skipped:
            status = "PARTIAL"
        else:
            status = "SUCCEEDED"

        progress = self._latest_progress or PlanProgress.create(
            total_nodes=len(self._plan.nodes),
            pending=0,
            running=0,
            succeeded=len(succeeded),
            failed=len(failures),
            last_event="Plan execution completed",
        )

        summary_artifact_ref: str | None = None
        if self.artifact_store is not None:
            artifact = self.artifact_store.put_json(
                {
                    "status": status,
                    "results": {
                        node_id: result.to_payload()
                        for node_id, result in sorted(succeeded.items())
                    },
                    "failures": {
                        node_id: failure.to_payload()
                        for node_id, failure in sorted(failures.items())
                    },
                    "skipped": sorted(skipped),
                    "progress": progress.to_payload(),
                },
                metadata={
                    "name": "plan_summary.json",
                    "producer": "plan.interpreter",
                    "labels": ["plan", "summary"],
                },
            )
            summary_artifact_ref = artifact.artifact_ref

        return PlanExecutionSummary(
            status=status,
            results=succeeded,
            failures=failures,
            skipped=tuple(sorted(skipped)),
            progress=progress,
            progress_artifact_ref=self._last_progress_artifact_ref,
            summary_artifact_ref=summary_artifact_ref,
        )


def create_validated_interpreter(
    *,
    plan: Any,
    registry_snapshot: ToolRegistrySnapshot,
    executor: ToolExecutor,
    artifact_store: ArtifactStore | None = None,
    write_progress_artifact: bool = False,
) -> PlanInterpreter:
    """Validate the plan then create an interpreter instance."""

    validated = validate_plan(plan=plan, registry_snapshot=registry_snapshot)
    return PlanInterpreter(
        validated_plan=validated,
        registry_snapshot=registry_snapshot,
        executor=executor,
        artifact_store=artifact_store,
        write_progress_artifact=write_progress_artifact,
    )


def validate_then_execute(
    *,
    plan: Any,
    registry_snapshot: ToolRegistrySnapshot,
    executor: ToolExecutor,
    artifact_store: ArtifactStore | None = None,
    write_progress_artifact: bool = False,
) -> Awaitable[PlanExecutionSummary]:
    """Convenience wrapper that validates and runs a plan."""

    try:
        interpreter = create_validated_interpreter(
            plan=plan,
            registry_snapshot=registry_snapshot,
            executor=executor,
            artifact_store=artifact_store,
            write_progress_artifact=write_progress_artifact,
        )
    except PlanValidationError as exc:
        raise PlanExecutionError("validation_failed", str(exc)) from exc
    return interpreter.run()


__all__ = [
    "PlanExecutionError",
    "PlanExecutionSummary",
    "PlanInterpreter",
    "PlanProgress",
    "create_validated_interpreter",
    "validate_then_execute",
]
