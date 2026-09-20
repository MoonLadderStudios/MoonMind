import json

import pytest

from moonmind.workflows.skills import deployment_maintenance as maintenance
from moonmind.workflows.skills import deployment_release as release
from moonmind.workflows.temporal import release_routing


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_job_retries_keep_original_budget_across_restart(
    tmp_path, monkeypatch, recovers
):
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    directory = release.state_root() / "job"
    directory.mkdir(parents=True)
    request_file = directory / "request.json"
    release.write_record(
        request_file,
        {"authored": {"owner": "owner"}, "deadline": release.time.time() + 300},
    )
    calls = []

    async def body(path):
        calls.append(json.loads((path.parent / "attempts.json").read_text())["count"])
        if recovers and len(calls) == 2:
            release.write_record(
                path.parent / "result.json",
                {"owner": "owner", "result": {"status": "COMPLETED"}},
            )
            return
        raise ConnectionError("lost worker acknowledgement")

    async def no_wait(*args):
        pass

    monkeypatch.setattr(release, "_run_job_body", body)
    monkeypatch.setattr(release.asyncio, "sleep", no_wait)
    await release.run_job(request_file)
    await release.run_job(request_file)
    assert calls == ([1, 2] if recovers else [1, 2, 3])
    result = json.loads((directory / "result.json").read_text())
    assert ("result" in result) is recovers


def test_release_reconciliation_has_only_the_deployment_activity_owner():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity("release.reconcile")
    assert route.fleet == "deployment"
    assert route.capability_class == "deployment_control"


@pytest.mark.asyncio
async def test_missing_updater_resumes_immutable_request_without_resetting_budget(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock
    import time

    directory = tmp_path / "job"
    directory.mkdir()
    request = {
        "authored": {"owner": "exact-owner"},
        "image": "image@sha256:b",
        "imageId": "b",
        "deadline": time.time() + 300,
    }
    release.write_record(directory / "request.json", request)
    release.write_record(directory / "deliveries.json", {"count": 2})
    monkeypatch.setattr(maintenance, "inspect_owned", AsyncMock(return_value=None))
    launch = AsyncMock()
    monkeypatch.setattr(maintenance, "launch_updater", launch)
    result = await maintenance.reconcile_release(directory, "runner")
    assert result["resumed"] is True
    launch.assert_awaited_once_with("runner", directory, request)
    assert json.loads((directory / "deliveries.json").read_text())["count"] == 3
    assert json.loads((directory / "request.json").read_text()) == request
    launch.reset_mock()
    result = await maintenance.reconcile_release(directory, "runner")
    assert result["pending"] == ["execution_budget_exhausted"]
    launch.assert_not_awaited()
    terminal = {"owner": "exact-owner", "error": "original terminal failure"}
    release.write_record(directory / "result.json", terminal)
    await maintenance.reconcile_release(directory, "runner")
    launch.assert_not_awaited()
    assert json.loads((directory / "result.json").read_text()) == terminal


