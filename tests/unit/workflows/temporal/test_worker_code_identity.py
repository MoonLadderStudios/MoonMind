"""Unit tests for stale worker code detection (MoonLadderStudios/MoonMind#4224)."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Mapping

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)
from moonmind.workflows.skills.deployment_execution import DeploymentUpdateExecutor
from moonmind.workflows.skills.deployment_execution import DeploymentUpdateLockManager
from moonmind.workflows.skills.deployment_execution import InMemoryDesiredStateStore
from moonmind.workflows.skills.deployment_execution import InMemoryEvidenceWriter
from moonmind.workflows.skills.deployment_execution import ComposeVerification
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from moonmind.workflows.temporal import worker_code_identity as wci
from moonmind.workflows.temporal.worker_code_identity import (
    WorkerCodeAdmissionError,
    WorkerCodeIdentity,
    collect_worker_code_freshness,
    compare_code_identities,
    compute_package_digest,
    enforce_worker_code_admission,
    evaluate_worker_freshness,
    format_stale_code_message,
    plan_stale_worker_recovery,
    readiness_urls_from_env,
    resolve_git_revision,
    resolve_worker_code_identity,
    stale_workers_only,
)
from moonmind.workflows.temporal.worker_healthcheck import (
    WorkerHealthState,
    _build_response_body,
    start_healthcheck_server,
)


@pytest.fixture(autouse=True)
def _clear_digest_cache():
    wci._digest_cache.clear()
    wci._STARTUP_IDENTITY = None
    yield
    wci._digest_cache.clear()
    wci._STARTUP_IDENTITY = None


def _clear_code_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOONMIND_BUILD_SHA", raising=False)
    monkeypatch.delenv("MOONMIND_IMAGE_DIGEST", raising=False)
    monkeypatch.delenv("MOONMIND_CODE_PACKAGE_ROOT", raising=False)


def test_resolve_git_revision_prefers_build_sha_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_SHA", "abc123")
    revision, source = resolve_git_revision(repo_root=tmp_path)
    assert revision == "abc123"
    assert source == "MOONMIND_BUILD_SHA"


def test_resolve_git_revision_unknown_outside_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_code_env(monkeypatch)
    revision, source = resolve_git_revision(repo_root=tmp_path)
    assert revision is None
    assert source == "unknown"


def test_compute_package_digest_detects_module_change(tmp_path) -> None:
    module = tmp_path / "mod.py"
    module.write_text("x = 1\n", encoding="utf-8")
    first = compute_package_digest(tmp_path)
    assert first is not None and first.startswith("sha256:")
    module.write_text("x = 2\n", encoding="utf-8")
    second = compute_package_digest(tmp_path)
    assert second is not None and second != first


def test_compare_equal_identities_is_healthy() -> None:
    startup = WorkerCodeIdentity(revision="abc", digest="sha256:1", source="git")
    current = WorkerCodeIdentity(revision="abc", digest="sha256:1", source="git")
    assert compare_code_identities(startup, current) == "healthy"


def test_compare_different_revision_is_stale() -> None:
    startup = WorkerCodeIdentity(revision="old", digest="sha256:1", source="git")
    current = WorkerCodeIdentity(revision="new", digest="sha256:1", source="git")
    assert compare_code_identities(startup, current) == "stale"


def test_compare_same_revision_changed_digest_is_stale() -> None:
    startup = WorkerCodeIdentity(revision="abc", digest="sha256:1", source="git")
    current = WorkerCodeIdentity(revision="abc", digest="sha256:2", source="git")
    assert compare_code_identities(startup, current) == "stale"


def test_compare_missing_startup_identity_is_unknown_not_healthy() -> None:
    startup = WorkerCodeIdentity(revision=None, digest=None, source="unknown")
    current = WorkerCodeIdentity(revision="abc", digest="sha256:1", source="git")
    assert compare_code_identities(startup, current) == "unknown"


def test_compare_missing_current_identity_is_unknown() -> None:
    startup = WorkerCodeIdentity(revision="abc", digest="sha256:1", source="git")
    current = WorkerCodeIdentity(revision=None, digest=None, source="unknown")
    assert compare_code_identities(startup, current) == "unknown"


def test_plan_stale_worker_recovery_splits_idle_and_busy() -> None:
    plan = plan_stale_worker_recovery(["a", "b", "c"], busy=["b"])
    assert plan.restart_now == ("a", "c")
    assert plan.drain_then_restart == ("b",)


def test_plan_stale_worker_recovery_never_kills_busy() -> None:
    plan = plan_stale_worker_recovery(["busy-one"], busy=["busy-one"])
    assert plan.restart_now == ()
    assert plan.drain_then_restart == ("busy-one",)


def test_admission_blocks_only_when_all_known_workers_stale() -> None:
    checkout = WorkerCodeIdentity(revision="new", source="git")
    stale = evaluate_worker_freshness(
        name="workflow",
        startup=WorkerCodeIdentity(revision="old", source="git"),
        current=checkout,
    )
    blocked, items = stale_workers_only([stale])
    assert blocked is True
    assert [item.name for item in items] == ["workflow"]
    with pytest.raises(WorkerCodeAdmissionError, match="stale_code"):
        enforce_worker_code_admission([stale])


def test_admission_fail_open_when_all_unknown() -> None:
    unknown = evaluate_worker_freshness(
        name="workflow",
        startup=WorkerCodeIdentity(),
        current=WorkerCodeIdentity(revision="new", source="git"),
    )
    assert unknown.status == "unknown"
    blocked, _ = stale_workers_only([unknown])
    assert blocked is False
    assert enforce_worker_code_admission([unknown]) == []


def test_admission_allows_mixed_healthy_and_stale() -> None:
    checkout = WorkerCodeIdentity(revision="new", source="git")
    healthy = evaluate_worker_freshness(
        name="a", startup=WorkerCodeIdentity(revision="new", source="git"), current=checkout
    )
    stale = evaluate_worker_freshness(
        name="b", startup=WorkerCodeIdentity(revision="old", source="git"), current=checkout
    )
    blocked, remaining = stale_workers_only([healthy, stale])
    assert blocked is False
    assert [item.name for item in remaining] == ["b"]


def test_readiness_urls_from_env_supports_named_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MOONMIND_WORKER_READINESS_URLS",
        "workflow=http://w:8080/readyz, http://other:8080/readyz",
    )
    monkeypatch.delenv("TEMPORAL_WORKFLOW_READINESS_URL", raising=False)
    assert readiness_urls_from_env() == [
        ("workflow", "http://w:8080/readyz"),
        ("http://other:8080/readyz", "http://other:8080/readyz"),
    ]


def test_collect_worker_code_freshness_reports_both_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_SHA", "checkout-rev")
    current = WorkerCodeIdentity(revision="checkout-rev", source="MOONMIND_BUILD_SHA")

    def _probe(url: str) -> dict[str, Any]:
        assert url == "http://w:8080/readyz"
        return {"codeRevision": "startup-rev", "codeDigest": "sha256:x"}

    freshness = collect_worker_code_freshness(
        [("workflow", "http://w:8080/readyz")], current=current, probe=_probe
    )
    assert len(freshness) == 1
    assert freshness[0].status == "stale"
    assert freshness[0].startup_revision == "startup-rev"
    assert freshness[0].current_revision == "checkout-rev"
    message = format_stale_code_message(freshness)
    assert "stale_code" in message and "workflow" in message


def test_worker_health_state_missing_identity_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_code_env(monkeypatch)
    state = WorkerHealthState(
        temporal_connected=True, workers_constructed=True, pollers_started=True
    )
    payload = state.code_identity()
    assert payload["codeRevision"] == "unknown"
    assert payload["codeIdentityStatus"] == "unknown"


def test_worker_health_state_reports_stale_with_both_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_SHA", "checkout-rev")
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        code_revision="startup-rev",
        code_identity_source="git",
    )
    payload = state.code_identity()
    assert payload["codeIdentityStatus"] == "stale"
    assert payload["reasonCode"] == "stale_code"
    assert payload["staleCode"]["startupRevision"] == "startup-rev"
    # The checkout revision may carry a -dirty suffix when the working tree
    # has uncommitted changes; the stale signal is what matters here.
    assert payload["staleCode"]["currentRevision"].startswith("checkout-rev")


def test_readiness_body_reports_stale_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_SHA", "checkout-rev")
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "workflow")
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        code_revision="startup-rev",
        code_identity_source="git",
    )
    body = json.loads(_build_response_body(state, readiness=True))
    assert body["codeRevision"] == "startup-rev"
    assert body["codeIdentityStatus"] == "stale"
    assert body["reasonCode"] == "stale_code"
    assert body["staleCode"]["worker"] == "workflow"
    assert body["staleCode"]["currentRevision"].startswith("checkout-rev")


@pytest.mark.asyncio
async def test_readyz_serves_503_while_stale_and_200_after_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_code_env(monkeypatch)
    module = tmp_path / "worker_mod.py"
    module.write_text("v = 1\n", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_CODE_PACKAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "workflow")

    startup = resolve_worker_code_identity(package_root=tmp_path)
    state = WorkerHealthState(
        temporal_connected=True,
        workers_constructed=True,
        pollers_started=True,
        code_revision=startup.revision,
        code_digest=startup.digest,
        code_identity_source=startup.source,
    )
    server = await start_healthcheck_server(state)
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    loop = asyncio.get_running_loop()

    def _get() -> tuple[int | None, dict[str, Any]]:
        try:
            raw = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/readyz", timeout=5
            ).read()
            return None, json.loads(raw)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        code, body = await loop.run_in_executor(None, _get)
        assert code is None, body
        assert body["codeIdentityStatus"] == "healthy"

        # Modify the bind-mounted module: the next readiness probe must report
        # stale_code with both identities.
        module.write_text("v = 2\n", encoding="utf-8")
        wci._digest_cache.clear()
        code, body = await loop.run_in_executor(None, _get)
        assert code == 503, body
        assert body["codeIdentityStatus"] == "stale"
        assert body["reasonCode"] == "stale_code"
        assert body["staleCode"]["startupRevision"] == (startup.revision or "unknown")
        assert body["staleCode"]["currentRevision"] == (startup.revision or "unknown")
        assert body["staleCode"]["worker"] == "workflow"

        # A restart re-records the identity and clears the stale signal.
        restarted = resolve_worker_code_identity(package_root=tmp_path)
        state.code_revision = restarted.revision
        state.code_digest = restarted.digest
        state.code_identity_source = restarted.source
        code, body = await loop.run_in_executor(None, _get)
        assert code is None, body
        assert body["codeIdentityStatus"] == "healthy"
    finally:
        server.close()
        await server.wait_closed()


def test_agent_run_models_carry_worker_code_revision() -> None:
    handle = AgentRunHandle(
        runId="run-1",
        agentKind="managed",
        agentId="agent",
        status="running",
        startedAt="2026-09-11T00:00:00Z",
        workerCodeRevision="abc123",
    )
    assert handle.worker_code_revision == "abc123"
    assert handle.model_dump(by_alias=True)["workerCodeRevision"] == "abc123"

    status = AgentRunStatus(
        runId="run-1", agentKind="managed", agentId="agent", status="running"
    )
    assert status.worker_code_revision is None

    result = AgentRunResult(workerCodeRevision="rev-2")
    assert result.worker_code_revision == "rev-2"


def test_base_adapter_builders_stamp_worker_code_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_SHA", "worker-rev")
    handle = BaseExternalAgentAdapter.build_handle(
        run_id="r",
        agent_id="jules",
        status="running",
        provider_status="ACTIVE",
        normalized_status="running",
    )
    assert handle.worker_code_revision == "worker-rev"
    assert handle.metadata["workerCodeRevision"] == "worker-rev"

    status = BaseExternalAgentAdapter.build_status(
        run_id="r",
        agent_id="jules",
        status="running",
        provider_status="ACTIVE",
        normalized_status="running",
    )
    assert status.worker_code_revision == "worker-rev"

    result = BaseExternalAgentAdapter.build_result(
        run_id="r",
        provider_status="COMPLETE",
        normalized_status="completed",
        provider_name="Jules",
    )
    assert result.worker_code_revision == "worker-rev"
    assert result.metadata["workerCodeRevision"] == "worker-rev"


class _FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple[str, ...]]] = []

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        return {"stack": stack, "phase": phase}

    async def pull(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.commands.append(("pull", command))
        return {"exitCode": 0}

    async def up(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.commands.append(("up", command))
        return {"exitCode": 0}

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        return {"Id": "sha256:" + "b" * 64, "RepoTags": [requested_image]}

    async def verify(
        self, *, stack: str, requested_image: str, resolved_digest: str | None
    ) -> ComposeVerification:
        return ComposeVerification(
            succeeded=True,
            updated_services=("api",),
            running_services=({"name": "api", "state": "running"},),
            details={},
        )


def _update_inputs() -> dict[str, Any]:
    return {
        "stack": "moonmind",
        "image": {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "20260425.1234",
        },
        "mode": "changed_services",
        "removeOrphans": True,
        "wait": True,
        "reason": "stale worker test",
    }


def _update_executor(
    *,
    checker=None,
    restarter=None,
) -> DeploymentUpdateExecutor:
    return DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=_FakeRunner(),  # type: ignore[arg-type]
        stale_worker_checker=checker,
        stale_worker_restarter=restarter,
    )


@pytest.mark.asyncio
async def test_update_restarts_idle_stale_workers_and_succeeds() -> None:
    seen: list[Sequence[Mapping[str, Any]]] = []

    async def _checker() -> Sequence[Mapping[str, Any]]:
        if seen:
            return []
        seen.append([{"worker": "workflow"}])
        return [
            {
                "worker": "workflow",
                "startupRevision": "old",
                "currentRevision": "new",
                "busy": False,
            }
        ]

    restarted: list[str] = []

    async def _restarter(stale: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        restarted.extend(str(item["worker"]) for item in stale)
        return {"restarted": list(restarted), "drained": [], "failed": []}

    executor = _update_executor(checker=_checker, restarter=_restarter)
    result = await executor.execute(_update_inputs())
    assert result.status == "COMPLETED"
    assert restarted == ["workflow"]


@pytest.mark.asyncio
async def test_update_drains_busy_stale_worker_rather_than_killing() -> None:
    received_busy: list[bool] = []

    async def _checker() -> Sequence[Mapping[str, Any]]:
        if received_busy:
            return []
        received_busy.append(True)
        return [
            {
                "worker": "workflow",
                "startupRevision": "old",
                "currentRevision": "new",
                "busy": True,
            }
        ]

    from moonmind.workflows.temporal.worker_code_identity import (
        plan_stale_worker_recovery,
    )

    drained: list[str] = []
    killed: list[str] = []

    async def _restarter(stale: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        plan = plan_stale_worker_recovery(
            [str(item["worker"]) for item in stale],
            busy=[
                str(item["worker"])
                for item in stale
                if item.get("busy") is True
            ],
        )
        assert plan.restart_now == ()
        drained.extend(plan.drain_then_restart)
        assert killed == []
        return {"restarted": [], "drained": list(drained), "failed": []}

    executor = _update_executor(checker=_checker, restarter=_restarter)
    result = await executor.execute(_update_inputs())
    assert result.status == "COMPLETED"
    assert drained == ["workflow"]


@pytest.mark.asyncio
async def test_update_fails_loudly_without_restarter() -> None:
    async def _checker() -> Sequence[Mapping[str, Any]]:
        return [
            {
                "worker": "workflow",
                "startupRevision": "old",
                "currentRevision": "new",
                "busy": False,
            }
        ]

    executor = _update_executor(checker=_checker, restarter=None)
    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_update_inputs())
    assert exc_info.value.error_code == "DEPLOYMENT_STALE_WORKER_CODE"
    assert "stale_code" in str(exc_info.value.details)
