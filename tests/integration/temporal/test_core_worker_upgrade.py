"""An admitted production run survives split fleets, core replacement and loss.

Only the substrate is local: a Temporal development server, SQLite, and the
supported local filesystem artifact store. Worker startup, workflow admission,
planning, Activities, persistence and history replay use production code. No
test workflow, activity implementation, provider or Docker daemon is required.
The Docker/journal round trip remains in host_update/test_journal_transition.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes
from tools.worker_runtime_activity_probe import run_probe

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.temporal_boundary,
    pytest.mark.timeout(300),
]

FLEETS = ("workflow", "artifacts", "llm", "integrations")

# Configure real isolated persistence before worker_runtime imports its session
# dependencies. DatabaseSettings intentionally exposes only the deployed
# PostgreSQL URL; this is test substrate wiring, not another worker entrypoint.
WORKER_BOOTSTRAP = """
import asyncio, os
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from api_service.db import base
base.engine = create_async_engine(os.environ['CORE_JOURNEY_DATABASE_URL'])
base.async_session_maker = async_sessionmaker(base.engine, expire_on_commit=False)
from moonmind.workflows.temporal.worker_runtime import main_async
asyncio.run(main_async())
"""


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _ready(port: int) -> dict | None:
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(b"GET /readyz HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
                headers, body = (await reader.read()).split(b"\r\n\r\n", 1)
                if headers.startswith(b"HTTP/1.1 200"):
                    return json.loads(body)
            finally:
                writer.close()
                await writer.wait_closed()
    except (OSError, TimeoutError):
        pass
    return None


@asynccontextmanager
async def _worker(
    name, fleets, environment, receipts, output, *, interrupted=False,
):
    port = _port()
    env = {
        **os.environ,
        **environment,
        "WORKER_HEALTHCHECK_PORT": str(port),
        "TEMPORAL_WORKER_FLEET": fleets[0] if len(fleets) == 1 else "artifacts",
    }
    if len(fleets) == 1:
        env.pop("TEMPORAL_WORKER_FLEETS", None)
    else:
        env["TEMPORAL_WORKER_FLEETS"] = json.dumps(fleets)
    with (output / f"{name}.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            WORKER_BOOTSTRAP,
            env=env,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(40):
                while True:
                    assert process.returncode is None, (
                        f"{name} exited {process.returncode}; see {log.name}"
                    )
                    ready = await _ready(port)
                    if ready:
                        break
                    await asyncio.sleep(0.05)
            assert ready["fleets"] == list(fleets)
            assert set(ready["fleetSpecs"]) == set(fleets)
            receipts.append(
                {
                    "phase": name,
                    "pid": process.pid,
                    "readyz": ready,
                    "selection": {
                        "TEMPORAL_WORKER_FLEET": env["TEMPORAL_WORKER_FLEET"],
                        "TEMPORAL_WORKER_FLEETS": env.get("TEMPORAL_WORKER_FLEETS"),
                    },
                }
            )
            yield process
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                process.kill()
                await process.wait()
                pytest.fail(f"{name} failed to drain; see {log.name}")
            receipts.append(
                {"phase": name + "-stopped", "exitCode": process.returncode}
            )
            assert process.returncode == (-signal.SIGKILL if interrupted else 0), log.name


def _activities(history) -> list[dict]:
    completed = {
        event.activity_task_completed_event_attributes.scheduled_event_id: event.event_id
        for event in history.events
        if event.HasField("activity_task_completed_event_attributes")
    }
    return [
        {
            "scheduledEventId": event.event_id,
            "activityType": item.activity_type.name,
            "taskQueue": item.task_queue.name,
            "completedEventId": completed.get(event.event_id),
        }
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and (item := event.activity_task_scheduled_event_attributes)
    ]


async def _wait_for_activity(handle, name, *, completed):
    async with asyncio.timeout(30):
        while True:
            history = await handle.fetch_history()
            rows = [row for row in _activities(history) if row["activityType"] == name]
            if rows and bool(rows[0]["completedEventId"]) == completed:
                return history, rows[0]
            if (await handle.describe()).status != WorkflowExecutionStatus.RUNNING:
                await handle.result()  # Surface the original workflow failure.
                pytest.fail(f"workflow closed before {name}")
            await asyncio.sleep(0.05)


async def _result(client, history, event_id):
    event = next(event for event in history.events if event.event_id == event_id)
    [result] = await client.data_converter.decode(
        event.activity_task_completed_event_attributes.result.payloads
    )
    return result


async def _save_history(handle, report, output):
    try:
        async with asyncio.timeout(10):
            (output / "history.json").write_text((await handle.fetch_history()).to_json())
            report["observedStatus"] = (await handle.describe()).status.name
    except Exception as exc:
        # Diagnostic collection must not replace the scenario's original error.
        report["historyCollectionError"] = type(exc).__name__


async def _observe_paused(handle, receipts, phase):
    async with asyncio.timeout(30):
        while True:
            state = await handle.query("control_state")
            if state["safePoint"]:
                break
            await asyncio.sleep(0.05)
    description = await handle.describe()
    assert description.status == WorkflowExecutionStatus.RUNNING
    assert description.run_id == state["runId"] == handle.result_run_id
    receipts.append(
        {
            "phase": phase,
            "workflowId": handle.id,
            "runId": description.run_id,
            "status": description.status.name,
            "controlState": state,
            "activities": _activities(await handle.fetch_history()),
        }
    )


async def test_in_progress_workflow_completes_after_core_upgrade_and_restart(
    tmp_path, monkeypatch,
):
    output = Path("artifacts/core-worker-upgrade")
    output.mkdir(parents=True, exist_ok=True)
    receipts = []
    report = {
        "status": "failed",
        "observations": receipts,
        "workload": "admitted MoonMind.UserWorkflow with two document.discover tool steps",
        "dependencies": {
            "database": "SQLite through production repositories and session factories",
            "artifactStore": "LocalTemporalArtifactStore (local_fs)",
            "worker": "worker_runtime.main_async in separate Python processes",
            "temporal": "WorkflowEnvironment.start_local with deployment search attributes",
        },
    }
    database_url = f"sqlite+aiosqlite:///{tmp_path}/journey.db"
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts"),
    )
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    workspace = tmp_path / "documents"
    workspace.mkdir()
    document = workspace / "retained.md"
    document.write_text("# Recorded before the topology transition\n")
    remaining_workspace = tmp_path / "remaining-documents"
    remaining_workspace.mkdir()
    (remaining_workspace / "after.md").write_text("# Discover after the core restart\n")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with (
            await WorkflowEnvironment.start_local(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ) as env,
            AsyncExitStack() as stack,
        ):
            await register_deployment_search_attributes(env)
            monkeypatch.setattr(
                settings.temporal, "address",
                env.client.service_client.config.target_host,
            )
            monkeypatch.setattr(settings.temporal, "namespace", env.client.namespace)
            environment = {
                "CORE_JOURNEY_DATABASE_URL": database_url,
                "TEMPORAL_ADDRESS": settings.temporal.address,
                "TEMPORAL_NAMESPACE": settings.temporal.namespace,
                "TEMPORAL_ARTIFACT_BACKEND": "local_fs",
                "TEMPORAL_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
                "MOONMIND_ENABLE_OPENTELEMETRY": "0",
            }
            async with AsyncExitStack() as split:
                for fleet in FLEETS:
                    await split.enter_async_context(_worker(
                        "split-" + fleet, (fleet,), environment, receipts, output,
                    ))
                adapter = TemporalClientAdapter(env.client)
                async with sessions() as session:
                    service = TemporalExecutionService(
                        session,
                        client_adapter=adapter,
                        namespace=env.client.namespace,
                    )
                    # The actual admission owner creates the canonical identity
                    # and trusted owner metadata. No test principal or scripted
                    # Workflow start stands in for production admission.
                    started = await service.create_execution(
                        workflow_type="MoonMind.UserWorkflow",
                        owner_id=None,
                        title="Core worker topology transition",
                        input_artifact_ref=None,
                        plan_artifact_ref=None,
                        manifest_artifact_ref=None,
                        failure_policy=None,
                        idempotency_key=f"core-upgrade-{uuid4().hex}",
                        initial_parameters={"workflow": {
                            "instructions": "Discover the saved document.",
                            "publish": {"mode": "none"},
                            "steps": [{
                                "id": "discover-before", "type": "tool",
                                "tool": {"id": "document.discover", "inputs": {
                                    "directory": str(workspace),
                                }},
                            }, {
                                "id": "discover-after", "type": "tool",
                                "tool": {"id": "document.discover", "inputs": {
                                    "directory": str(remaining_workspace),
                                }},
                            }],
                        }},
                    )
                handle = env.client.get_workflow_handle(
                    started.workflow_id, run_id=started.run_id,
                )
                report.update(workflowId=handle.id, runId=started.run_id)
                stack.push_async_callback(_save_history, handle, report, output)
                planned_history, planned = await _wait_for_activity(
                    handle, "plan.generate", completed=True,
                )
                plan = await _result(
                    env.client, planned_history, planned["completedEventId"],
                )
                plan_ref = plan["plan_ref"]["artifact_id"]

                async def read_plan():
                    async with sessions() as session:
                        service = TemporalArtifactService(
                            TemporalArtifactRepository(session), store=store,
                        )
                        _, content = await service.read(
                            artifact_id=plan_ref,
                            access=ArtifactAccessContext.for_execution(handle.id),
                        )
                        return hashlib.sha256(content).hexdigest()

                plan_digest = await read_plan()
                # Pause while the first tool is queued on the deliberately
                # not-yet-started sandbox worker. Once it completes, the real
                # workflow pauses before its second step. No custom blocking
                # Activity or test workflow supplies this safe boundary.
                await _wait_for_activity(handle, "mm.tool.execute", completed=False)
                await asyncio.wait_for(handle.execute_update("Pause"), 15)
                # Sandbox retains its independent process across both core
                # replacements, just as in the supported Compose topology.
                await stack.enter_async_context(_worker(
                    "sandbox", ("sandbox",), environment, receipts, output,
                ))
                await _observe_paused(handle, receipts, "before-upgrade")
                _, confirmed_tool = await _wait_for_activity(
                    handle, "mm.tool.execute", completed=True,
                )
                report["confirmedStep"] = confirmed_tool
                report["splitActivityProbe"] = await run_probe(
                    "moonmind-test-split-upgrade"
                )
                before = await handle.fetch_history()
                report["confirmedPlan"] = {
                    **planned, "artifactId": plan_ref, "sha256": plan_digest,
                }

            async with _worker(
                "core", FLEETS, environment, receipts, output, interrupted=True,
            ) as core:
                await _observe_paused(handle, receipts, "after-upgrade")
                assert await read_plan() == plan_digest
                # Re-executing the accepted discovery would now return a
                # different result. Its original bytes must come from history.
                document.unlink()
                # Actual process loss discards every cached workflow instance;
                # Temporal owns the paused run and its confirmed Activity results.
                core.kill()
                await asyncio.wait_for(core.wait(), 10)
                assert core.returncode == -signal.SIGKILL
                assert (
                    await handle.describe()
                ).status == WorkflowExecutionStatus.RUNNING

            async with _worker("core-restarted", FLEETS, environment, receipts, output):
                await _observe_paused(handle, receipts, "after-core-restart")
                assert await read_plan() == plan_digest
                report["coreActivityProbe"] = await run_probe(
                    "moonmind-test-core-upgrade"
                )
                await asyncio.wait_for(handle.execute_update("Resume"), 15)
                result = await asyncio.wait_for(handle.result(), 45)
                assert result["status"] == "success"
                after = await handle.fetch_history()
                description = await handle.describe()
                assert description.status == WorkflowExecutionStatus.COMPLETED
                assert description.run_id == started.run_id
                # Compare actual committed history bytes and schedule counts,
                # rather than treating a new successful execution as continuity.
                prefix = after.events[:len(before.events)]
                assert [e.SerializeToString(deterministic=True) for e in prefix] == [
                    e.SerializeToString(deterministic=True) for e in before.events
                ]
                activities = _activities(after)
                assert all(row["completedEventId"] for row in activities)
                assert len([
                    r for r in activities if r["activityType"] == "plan.generate"
                ]) == 1
                tools = [r for r in activities if r["activityType"] == "mm.tool.execute"]
                assert len(tools) == 2
                # Production admission may select its existing publication
                # lane. Both steps must keep the queue observed before upgrade.
                assert all(
                    row["taskQueue"] == confirmed_tool["taskQueue"]
                    for row in tools
                )
                recorded_tool = await _result(
                    env.client, after, confirmed_tool["completedEventId"],
                )
                assert recorded_tool["outputs"]["documentPaths"] == ["retained.md"]
                assert recorded_tool["outputs"]["source"] == "filesystem"
                assert await read_plan() == plan_digest
                async with sessions() as session:
                    record = await session.get(TemporalExecutionCanonicalRecord, handle.id)
                    assert record.run_id == started.run_id
                    assert record.state == MoonMindWorkflowState.COMPLETED
                    report["canonicalTerminal"] = {
                        "workflowId": record.workflow_id,
                        "runId": record.run_id, "state": record.state.value,
                    }
                await Replayer(
                    workflows=[MoonMindUserWorkflow],
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(after)
                (output / "history.json").write_text(after.to_json())
                report.update(
                    status="completed", result=result,
                    activities=activities, replay="passed",
                )
    finally:
        await engine.dispose()
        (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
