import json

import pytest

from moonmind.workflows.skills import deployment_maintenance as maintenance
from moonmind.workflows.skills import deployment_release as release
from moonmind.workflows.temporal import release_routing


@pytest.mark.asyncio
@pytest.mark.parametrize("drained", [False, True])
async def test_retirement_requires_temporal_drainage_and_preserves_foreign_owners(
    tmp_path, monkeypatch, drained
):
    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    release.write_record(
        directory / "request.json",
        {"authored": {"owner": "owner"}, "image": "image@sha256:b", "imageId": "b"},
    )
    release.write_record(directory / "result.json", {"owner": "owner"})
    release.write_record(
        directory / "routing.json",
        {"candidate": "b", "previous": "fleet.a", "deployment": "fleet"},
    )
    release.write_record(
        directory / "retained.json", {"owner": "owner", "version": "fleet.a"}
    )
    release.write_record(
        directory / "deployment-result.json",
        {"owner": "owner", "result": {"status": "COMPLETED"}},
    )
    removed = []

    async def inspect(name, owner):
        return None

    async def snapshot(*args):
        return "fleet.b"

    async def is_drained(client, version):
        assert version == "fleet.a"
        return drained

    async def verify(self, image, **kwargs):
        assert image == "image@sha256:b" and kwargs["expected"] == "b"

    async def cleanup(self):
        removed.extend(self.names)

    monkeypatch.setattr(maintenance, "inspect_owned", inspect)
    monkeypatch.setattr(release_routing, "routing_snapshot", snapshot)
    monkeypatch.setattr(release_routing, "current_version", lambda snapshot: snapshot)
    monkeypatch.setattr(release_routing, "version_drained", is_drained)
    monkeypatch.setattr(release.ReleaseCohort, "verify_installed", verify)
    monkeypatch.setattr(release.ReleaseCohort, "cleanup", cleanup)
    result = await maintenance.reconcile_release(directory, None, None)
    assert len([name for name in removed if "candidate" in name]) == 8
    assert len([name for name in removed if "retained" in name]) == (
        7 if drained else 0
    )
    assert result["pending"] == ([] if drained else ["retained"])
    removed.clear()
    await maintenance.reconcile_release(directory, None, None)
    assert not removed
    release.write_record(
        directory / "retained.json", {"owner": "foreign", "version": "fleet.a"}
    )
    with pytest.raises(ValueError, match="owner differs"):
        await maintenance.reconcile_release(directory, None, None)


@pytest.mark.asyncio
async def test_failed_installed_readiness_cannot_release_the_only_current_pollers(
    tmp_path, monkeypatch
):
    directory = tmp_path / "job"
    directory.mkdir()
    release.write_record(
        directory / "request.json",
        {"authored": {"owner": "owner"}, "image": "image", "imageId": "b"},
    )
    release.write_record(directory / "result.json", {"owner": "owner"})
    release.write_record(
        directory / "routing.json", {"candidate": "b", "deployment": "fleet"}
    )
    release.write_record(
        directory / "deployment-result.json",
        {"owner": "owner", "result": {"status": "COMPLETED"}},
    )

    async def absent(*args):
        return None

    async def snapshot(*args):
        return "fleet.b"

    async def unavailable(*args, **kwargs):
        raise RuntimeError("installed worker unavailable")

    monkeypatch.setattr(maintenance, "inspect_owned", absent)
    monkeypatch.setattr(release_routing, "routing_snapshot", snapshot)
    monkeypatch.setattr(release_routing, "current_version", lambda value: value)
    monkeypatch.setattr(release.ReleaseCohort, "verify_installed", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        await maintenance.reconcile_release(directory, None, None)
    assert not (directory / "candidate-retired.json").exists()


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
    result = await maintenance.reconcile_release(directory, "runner", None)
    assert result["resumed"] is True
    launch.assert_awaited_once_with("runner", directory, request)
    assert json.loads((directory / "deliveries.json").read_text())["count"] == 3
    assert json.loads((directory / "request.json").read_text()) == request
    launch.reset_mock()
    result = await maintenance.reconcile_release(directory, "runner", None)
    assert result["pending"] == ["execution_budget_exhausted"]
    launch.assert_not_awaited()
    terminal = {"owner": "exact-owner", "error": "original terminal failure"}
    release.write_record(directory / "result.json", terminal)
    await maintenance.reconcile_release(directory, "runner", None)
    launch.assert_not_awaited()
    assert json.loads((directory / "result.json").read_text()) == terminal


@pytest.mark.asyncio
async def test_promoted_candidate_retires_from_an_attempt_receipt(
    tmp_path, monkeypatch
):
    """Retirement follows observable state, not which receipt file was written.

    A release whose deployment completed - routing promoted the candidate and
    the installed fleet runs it - can still finish through the attempt receipt
    when a later step fails. Requiring ``deployment-result.json`` wedged those
    jobs permanently: the candidate is current, so no drainage or inactivity
    evidence can ever arrive, and its qualification cohort kept polling the
    deployment task queues alongside the installed fleet.
    """
    directory = tmp_path / ("c" * 32)
    directory.mkdir()
    release.write_record(
        directory / "request.json",
        {"authored": {"owner": "owner"}, "image": "image@sha256:b", "imageId": "b"},
    )
    release.write_record(
        directory / "result.json",
        {
            "owner": "owner",
            "result": {"status": "COMPLETED", "outputs": {"finalError": "boom"}},
        },
    )
    release.write_record(
        directory / "routing.json",
        {"candidate": "b", "previous": "fleet.a", "deployment": "fleet"},
    )
    release.write_record(
        directory / "attempt-result.json",
        {"owner": "owner", "result": {"status": "COMPLETED"}},
    )
    removed = []
    verified = []

    async def inspect(name, owner):
        return None

    async def snapshot(*args):
        return "fleet.b"

    async def is_drained(client, version):
        return False

    async def verify(self, image, **kwargs):
        verified.append((image, kwargs["expected"]))

    async def cleanup(self):
        removed.extend(self.names)

    monkeypatch.setattr(maintenance, "inspect_owned", inspect)
    monkeypatch.setattr(release_routing, "routing_snapshot", snapshot)
    monkeypatch.setattr(release_routing, "current_version", lambda value: value)
    monkeypatch.setattr(release_routing, "version_drained", is_drained)
    monkeypatch.setattr(release.ReleaseCohort, "verify_installed", verify)
    monkeypatch.setattr(release.ReleaseCohort, "cleanup", cleanup)

    result = await maintenance.reconcile_release(directory, None, None)

    assert verified == [("image@sha256:b", "b")]
    assert "candidate" in result["retired"]
    assert "candidate" not in result["pending"]
    assert len([name for name in removed if "candidate" in name]) == 8
    assert (directory / "candidate-retired.json").exists()


@pytest.mark.asyncio
async def test_attempt_receipt_owner_must_match_before_retirement(
    tmp_path, monkeypatch
):
    directory = tmp_path / ("d" * 32)
    directory.mkdir()
    release.write_record(
        directory / "request.json",
        {"authored": {"owner": "owner"}, "image": "image@sha256:b", "imageId": "b"},
    )
    release.write_record(directory / "result.json", {"owner": "owner"})
    release.write_record(
        directory / "routing.json",
        {"candidate": "b", "previous": "fleet.a", "deployment": "fleet"},
    )
    release.write_record(
        directory / "attempt-result.json",
        {"owner": "foreign", "result": {"status": "COMPLETED"}},
    )

    async def inspect(name, owner):
        return None

    async def snapshot(*args):
        return "fleet.b"

    monkeypatch.setattr(maintenance, "inspect_owned", inspect)
    monkeypatch.setattr(release_routing, "routing_snapshot", snapshot)
    monkeypatch.setattr(release_routing, "current_version", lambda value: value)

    with pytest.raises(ValueError, match="owner differs"):
        await maintenance.reconcile_release(directory, None, None)
