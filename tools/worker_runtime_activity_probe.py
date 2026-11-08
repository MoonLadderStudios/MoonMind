"""Exercise installed production activity pollers in a disposable Compose stack.

Run inside its API container: docker compose exec -T api python - --project
moonmind-test-example < tools/worker_runtime_activity_probe.py. Only the driver
workflow is registered here; artifact and integration handlers must be supplied
by the installed workers. Synthetic artifact bytes and workflow history belong
to the disposable stack and are retained until its ordinary teardown.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import uuid
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

CONTENT = b"MoonMind disposable worker activity probe.\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()
ACTIVITIES = (
    "artifact.create",
    "artifact.write_complete",
    "artifact.read",
    "memory.evaluate_proposals",
)


class ProbeFailure(RuntimeError):
    """Fail closed with a safe static diagnostic, never an activity payload."""


def validate_inputs(project: str) -> None:
    if not re.fullmatch(r"moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?", project):
        raise ValueError("disposable_project_required")


def memory_request(source: dict) -> dict:
    return {
        "proposal_refs": [],
        "source": source,
        "terminal_disposition": None,
        "publication_gate": None,
        "requested_target": "run://worker-runtime-probe",
        "policy_decision": "reject",
    }


@workflow.defn(name="MoonMind.WorkerRuntimeActivityProbe")
class ActivityProbe:
    @workflow.run
    async def run(self, queues: dict) -> dict:
        info = workflow.info()
        principal = "workflow:" + info.workflow_id
        options = {
            "start_to_close_timeout": timedelta(seconds=30),
            "schedule_to_close_timeout": timedelta(seconds=45),
            "retry_policy": RetryPolicy(maximum_attempts=1),
        }
        created = await workflow.execute_activity(
            "artifact.create",
            {
                "principal": principal,
                "content_type": "text/plain",
                "size_bytes": len(CONTENT),
            },
            task_queue=queues["artifacts"],
            **options,
        )
        artifact_id = created[0]["artifact_id"]
        written = await workflow.execute_activity(
            "artifact.write_complete",
            {
                "principal": principal,
                "artifact_id": artifact_id,
                "payload": base64.b64encode(CONTENT).decode("ascii"),
                "content_type": "text/plain",
            },
            task_queue=queues["artifacts"],
            **options,
        )
        content = await workflow.execute_activity(
            "artifact.read",
            {"artifact_ref": artifact_id},
            task_queue=queues["artifacts"],
            result_type=bytes,
            **options,
        )
        if (
            hashlib.sha256(content).hexdigest() != DIGEST
            or written.get("sha256") != DIGEST
        ):
            raise ApplicationError("artifact_digest_mismatch", non_retryable=True)
        evaluated = await workflow.execute_activity(
            "memory.evaluate_proposals",
            memory_request(
                {
                    "workflowId": info.workflow_id,
                    "runId": info.run_id,
                    "logicalStepId": "probe",
                    "executionOrdinal": 1,
                }
            ),
            task_queue=queues["integrations"],
            **options,
        )
        if evaluated != {"decisionRefs": [], "decisions": []}:
            raise ApplicationError("unexpected_memory_result", non_retryable=True)
        return {
            "artifactSha256": DIGEST,
            "artifactBytes": len(content),
            "memoryDecisions": 0,
        }


def verify_history(
    history, artifacts_queue: str, integrations_queue: str
) -> list[dict]:
    expected = {name: artifacts_queue for name in ACTIVITIES[:3]}
    expected[ACTIVITIES[3]] = integrations_queue
    scheduled, completed = {}, set()
    workflow_completed = False
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            item = event.activity_task_scheduled_event_attributes
            scheduled[event.event_id] = (item.activity_type.name, item.task_queue.name)
        if event.HasField("activity_task_completed_event_attributes"):
            completed.add(
                event.activity_task_completed_event_attributes.scheduled_event_id
            )
        if event.HasField("workflow_execution_completed_event_attributes"):
            workflow_completed = True
    if (
        not workflow_completed
        or len(scheduled) != len(expected)
        or set(scheduled) != completed
        or dict(scheduled.values()) != expected
    ):
        raise ProbeFailure("activity_completion_history_mismatch")
    return [
        {"activityType": name, "taskQueue": expected[name], "completed": True}
        for name in ACTIVITIES
    ]


async def stop_owned_workflow(handle) -> str:
    """Only the exact workflow started by this probe is eligible for cleanup."""
    from temporalio.client import WorkflowExecutionStatus

    terminal = {
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.TIMED_OUT,
    }
    try:
        description = await asyncio.wait_for(handle.describe(), timeout=5)
        if description.status in terminal:
            return "closed"
    except (
        Exception
    ):  # noqa: BLE001 - still try bounded cancellation when status is unavailable
        description = None
    try:
        await asyncio.wait_for(handle.cancel(), timeout=5)
        try:
            await asyncio.wait_for(handle.result(), timeout=5)
        except Exception:  # noqa: BLE001 - bounded best-effort cleanup
            # Inspect closure rather than assuming any result error proves it.
            description = await asyncio.wait_for(handle.describe(), timeout=5)
            if description.status not in terminal:
                raise ProbeFailure("probe_still_running")
        return "closed"
    except Exception:  # noqa: BLE001 - bounded best-effort cleanup
        try:
            await asyncio.wait_for(
                handle.terminate("Disposable worker activity probe cleanup"), timeout=5
            )
            return "terminated"
        except Exception:  # noqa: BLE001 - bounded best-effort cleanup
            return "unconfirmed"


async def run_probe(project: str) -> dict:
    validate_inputs(project)
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.client import get_temporal_client

    workflow_id = f"{project}:worker-activity-probe:{uuid.uuid4().hex}"
    queue = workflow_id + ":driver"
    client = await asyncio.wait_for(
        get_temporal_client(settings.temporal.address, settings.temporal.namespace),
        timeout=15,
    )
    queues = {
        "artifacts": settings.temporal.activity_artifacts_task_queue,
        "integrations": settings.temporal.activity_integrations_task_queue,
    }
    handle = None
    async with asyncio.timeout(240), Worker(
        client,
        task_queue=queue,
        workflows=[ActivityProbe],
        workflow_runner=UnsandboxedWorkflowRunner(),
        graceful_shutdown_timeout=timedelta(seconds=5),
    ):
        try:
            async with asyncio.timeout(210):
                handle = await client.start_workflow(
                    ActivityProbe.run,
                    queues,
                    id=workflow_id,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=195),
                )
                result = await handle.result()
                if result != {
                    "artifactSha256": DIGEST,
                    "artifactBytes": len(CONTENT),
                    "memoryDecisions": 0,
                }:
                    raise ProbeFailure("unexpected_probe_result")
                observed = await handle.fetch_history()
                activities = verify_history(
                    observed, queues["artifacts"], queues["integrations"]
                )
                return {
                    "status": "completed",
                    "workflowId": workflow_id,
                    "runId": handle.first_execution_run_id,
                    "activities": activities,
                    **result,
                }
        except BaseException as exc:  # noqa: BLE001 - cleanup also owns cancellation
            cleanup = await stop_owned_workflow(
                handle or client.get_workflow_handle(workflow_id)
            )
            raise ProbeFailure(
                f"probe_failed:{type(exc).__name__}:cleanup_{cleanup}"
            ) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_probe(args.project))
    except Exception as exc:  # noqa: BLE001 - emit only a safe failure receipt
        # Do not retain traceback, activity result bytes, connection strings or
        # provider details in shared measurement receipts.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "reason": (
                        str(exc) if isinstance(exc, ProbeFailure) else "probe_failed"
                    ),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
