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
