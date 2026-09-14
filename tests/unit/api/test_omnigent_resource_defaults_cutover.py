"""New defaults advance bootstrap authority without rewriting admitted work."""

from copy import deepcopy

import pytest

from api_service.services import omnigent_policies as policies
from moonmind.omnigent.harness_platform.host_classes import (
    launch_policy_from_effective_launch,
)
from moonmind.omnigent.policies import PolicyDocument, PolicyState
from tests.unit.api.test_omnigent_policy_service import policy_db


async def _image(image_ref):
    kind = "host" if "host" in image_ref else "server"
    return f"images/{kind}@sha256:" + ("1" if kind == "host" else "2") * 64


def _docker_authority(monkeypatch, *, info=b"2\tcgroupfs\t[]", image=None):
    state = {
        "info": info,
        "image": image if image is not None else b"sha256:" + b"a" * 64,
        "commands": [],
    }
    monkeypatch.setenv("HOSTNAME", "trusted-api")

    async def command(argv, **kwargs):
        state["commands"].append(argv)
        assert kwargs["timeout_seconds"] == 5
        if argv[1] == "info":
            value = state["info"]
        else:
            assert argv[1:] == ("inspect", "--format", "{{.Image}}", "trusted-api")
            value = state["image"]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return value
        return 0, value, b""

    monkeypatch.setattr(policies, "run_runtime_command", command)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operator_owned,custom_memory", [(False, False), (True, False), (False, True)]
)
async def test_startup_versions_default_cpu_limits_and_preserves_pinned_limits(
    tmp_path,
    monkeypatch,
    operator_owned,
    custom_memory,
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _docker_authority(monkeypatch)
    current_bootstrap = policies.bootstrap_document

    def old_bootstrap(**kwargs):
        data = current_bootstrap(**kwargs).model_dump(by_alias=True)
        data["resources"]["cpuMillis"] = 2000
        if custom_memory:
            data["resources"]["memoryMiB"] = 3072
        return PolicyDocument.model_validate(data)

    async with policy_db(tmp_path) as sessions, sessions() as session:
        with monkeypatch.context() as previous_release:
            previous_release.setattr(policies, "bootstrap_document", old_bootstrap)
            await policies.seed_bootstrap_policies(session, image_resolver=_image)
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        old = await service.resolve_runtime_snapshot(f"{policy_id}@1")
        old_document = deepcopy(old["boundaries"])
        expected_version = 1 if custom_memory else 2
        if operator_owned:
            custom = await service.new_version(
                policy_id=policy_id,
                document=PolicyDocument.model_validate(old_document),
                actor="operator",
                expected_parent_ref=f"{policy_id}@1",
            )
            await service.transition(
                policy_id=policy_id,
                version=custom.version,
                state=PolicyState.ACTIVE,
                actor="operator",
                make_default=True,
            )

        # This is the startup owner, including persistent versioning and default
        # selection, with unchanged images (no image update to trigger a cutover).
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@{expected_version}"
        assert latest["boundaries"]["resources"]["cpuMillis"] == (
            2000 if operator_owned or custom_memory else 0
        )
        assert latest["boundaries"]["host"] == old_document["host"]
        assert await service.resolve_runtime_snapshot(f"{policy_id}@1") == old
        # Adapters consume the recorded limits, even after defaults change.
        recorded = launch_policy_from_effective_launch(
            {
                "launchPolicyRef": f"{policy_id}@1",
                "hostMode": "on-demand",
                "limits": {
                    k: v
                    for k, v in old_document["resources"].items()
                    if k != "concurrency"
                },
                "capture": old_document["capture"],
                "cleanup": {"mode": "remove"},
            }
        )
        assert recorded.limits["cpuMillis"] == 2000
        # Repeated startup cannot create another version for an unchanged default.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert len(await service.versions(policy_id)) == expected_version


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observation",
    [
        b"1\tcgroupfs\t[]",
        b'2\tsystemd\t["name=rootless"]',
        b"2\tunknown\t[]",
        (1, b"", b"daemon unavailable"),
        OSError("Docker is temporarily unavailable"),
        TimeoutError("Docker info timed out"),
    ],
)
async def test_startup_retains_fixed_defaults_until_shared_authority_is_observed(
    tmp_path, monkeypatch, observation
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    daemon = _docker_authority(monkeypatch, info=observation)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        # The real startup owner persists a usable fresh installation even when
        # the optional CPU-pool authority cannot currently be established.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert await policies.bootstrap_policies_ready(session)
        service = policies.OmnigentPolicyService(session)
        snapshots = {}
        for policy_id in (
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
            "omnigent-on-demand",
            "opencode-on-demand",
        ):
            snapshot = await service.resolve_default_runtime_snapshot(policy_id)
            snapshots[policy_id] = snapshot
            assert snapshot["policyRef"] == f"{policy_id}@1"
            assert snapshot["boundaries"]["resources"]["cpuMillis"] == 2000

        # Repeating startup cannot publish an unusable stock cutover either.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert len(daemon["commands"]) == 2
        assert all(command[1] == "info" for command in daemon["commands"])
        for policy_id, snapshot in snapshots.items():
            assert await service.resolve_default_runtime_snapshot(policy_id) == snapshot

        # No operator setting or process-global cache suppresses a later retry.
        daemon["info"] = b"2\tsystemd\t[]"
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        for policy_id, snapshot in snapshots.items():
            current = await service.resolve_default_runtime_snapshot(policy_id)
            on_demand = snapshot["boundaries"]["host"]["mode"] == "on_demand_docker"
            assert current["boundaries"]["resources"]["cpuMillis"] == (
                0 if on_demand else 2000
            )
            assert current["policyRef"] == f"{policy_id}@{2 if on_demand else 1}"
            assert await service.resolve_runtime_snapshot(f"{policy_id}@1") == snapshot


@pytest.mark.asyncio
async def test_startup_requires_immutable_helper_authority_for_shared_defaults(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    daemon = _docker_authority(monkeypatch, image=b"worker:latest")
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        service = policies.OmnigentPolicyService(session)
        fixed = await service.resolve_default_runtime_snapshot("omnigent-on-demand")
        assert fixed["boundaries"]["resources"]["cpuMillis"] == 2000
        daemon["image"] = b"sha256:" + b"b" * 64
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        shared = await service.resolve_default_runtime_snapshot("omnigent-on-demand")
        assert shared["boundaries"]["resources"]["cpuMillis"] == 0
        # A later transient observation cannot rewrite already-admitted authority.
        daemon["info"] = OSError("daemon connection lost")
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert (
            await service.resolve_default_runtime_snapshot("omnigent-on-demand")
            == shared
        )
        assert await service.resolve_runtime_snapshot("omnigent-on-demand@1") == fixed
