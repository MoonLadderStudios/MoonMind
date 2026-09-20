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
async def test_maintenance_reports_an_unfinished_release_without_relaunching_it(
    tmp_path, monkeypatch
):
    """Maintenance never relaunches a release updater.

    The updater runs from the image its request pinned, so relaunching a job
    authored before recreate-in-place would execute the removed blue/green
    controller against the installed fleet. Re-running the update command
    starts a fresh audited release instead, which cannot resurrect it.
    """
    from unittest.mock import AsyncMock
    import time

    directory = tmp_path / "job"
    directory.mkdir()
    release.write_record(
        directory / "request.json",
        {
            "authored": {"owner": "exact-owner"},
            "image": "image@sha256:b",
            "imageId": "b",
            "deadline": time.time() + 300,
        },
    )
    monkeypatch.setattr(maintenance, "inspect_owned", AsyncMock(return_value=None))
    removed = AsyncMock()
    monkeypatch.setattr(maintenance, "docker", removed)

    result = await maintenance.reconcile_release(directory)

    assert result["resumed"] is False
    assert result["pending"] == ["unfinished"]
    removed.assert_not_awaited()
    assert not hasattr(maintenance, "launch_updater")


@pytest.mark.asyncio
async def test_leftover_cohort_containers_are_reported_not_deleted(monkeypatch):
    """Blocking containers are named for an operator, never auto-removed.

    They reuse the deployment's own Compose project and service labels, so
    they must go before an update can succeed -- but nothing available to a
    background pass proves one is not the last poller for pinned work.
    """
    from unittest.mock import AsyncMock

    listing = AsyncMock(
        return_value=(
            "mm-candidate-4f25ac89e639-workflow\n"
            "mm-retained-162f5ef17681-llm\n"
            "moonmind-api-1\n"
        )
    )
    monkeypatch.setattr(maintenance, "docker", listing)

    observed = await maintenance.observed_legacy_cohorts()

    assert observed == [
        "mm-candidate-4f25ac89e639-workflow",
        "mm-retained-162f5ef17681-llm",
    ]
    # One listing call only: reporting must not mutate anything.
    assert listing.await_count == 1
