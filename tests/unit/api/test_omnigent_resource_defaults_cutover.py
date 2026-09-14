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
