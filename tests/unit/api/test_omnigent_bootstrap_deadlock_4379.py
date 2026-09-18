"""Replay #4379: default < latest-active deadlock wedges bootstrap auto-update.

Minimized fixture: policy default v1 (old digest) + active non-default v2
(same old digest) + resolved image advanced to a new digest. Pre-fix
``seed_bootstrap_policies`` defers forever ("later policy version owns
evolution") and creates nothing; post-fix it creates/activates v3 (or
equivalent) without manual DB writes.
"""

from __future__ import annotations

import pytest

from api_service.services import omnigent_policies as policies
from moonmind.omnigent.policies import PolicyDocument, PolicyState
from tests.unit.api.test_omnigent_policy_service import policy_db


OLD_SERVER = "ghcr.io/example/omnigent-server@sha256:" + "a" * 64
OLD_HOST = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
NEW_SERVER = "ghcr.io/example/omnigent-server@sha256:" + "b" * 64
NEW_HOST = "ghcr.io/example/omnigent-host@sha256:" + "b" * 64


def _image_resolver_factory(server_ref: str, host_ref: str):
    async def _image(image_ref: str):
        kind = "host" if "host" in image_ref else "server"
        return host_ref if kind == "host" else server_ref

    return _image


async def _live_server(image_ref: str):
    # Live container already carries the resolved digest: deployment intent is
    # authority, not a pulled tag.
    return image_ref


def _no_daemon(monkeypatch):
    async def command(argv, **kwargs):
        raise AssertionError(f"bootstrap must not probe Docker: {argv!r}")

    monkeypatch.setattr(policies, "run_runtime_command", command)


@pytest.mark.asyncio
async def test_bootstrap_advances_past_stale_active_non_default(tmp_path, monkeypatch):
    """Default v1 + active v2 (old image) + advanced image => v3 default."""
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
        assert default["policyRef"] == f"{policy_id}@1"
        assert default["boundaries"]["host"]["hostImageRef"] == OLD_HOST

        # Active non-default v2: same old image, operator resource tweak.
        # Mirrors issue v15 (resource-only change, same old image).
        current_doc = PolicyDocument.model_validate(dict(default["boundaries"]))
        tweaked = PolicyDocument.model_validate(
            {
                **current_doc.model_dump(by_alias=True),
                "resources": {
                    **current_doc.model_dump(by_alias=True)["resources"],
                    "cpuMillis": 3000,
                },
            }
        )
        assert tweaked.resources.cpu_millis == 3000
        v2 = await service.new_version(
            policy_id=policy_id,
            document=tweaked,
            actor="operator",
            expected_parent_ref=f"{policy_id}@1",
        )
        await service.transition(
            policy_id=policy_id,
            version=v2.version,
            state=PolicyState.ACTIVE,
            actor="operator",
        )
        # Default stays at v1 while latest active is v2: the deadlock shape.

        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@3"
        assert latest["boundaries"]["host"]["hostImageRef"] == NEW_HOST
        assert latest["boundaries"]["host"]["serverImageRef"] == NEW_SERVER
        # History preserved: v1 and v2 still resolve.
        assert (await service.resolve_runtime_snapshot(f"{policy_id}@1"))["boundaries"][
            "host"
        ]["hostImageRef"] == OLD_HOST
        assert (await service.resolve_runtime_snapshot(f"{policy_id}@2"))["boundaries"][
            "host"
        ]["hostImageRef"] == OLD_HOST


@pytest.mark.asyncio
async def test_bootstrap_reuses_later_active_with_desired_digest(tmp_path, monkeypatch):
    """No duplicate successor when a later active already carries the image."""
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
        new_doc = PolicyDocument.model_validate(
            {
                **default["boundaries"],
                "host": {
                    **default["boundaries"]["host"],
                    "serverImageRef": NEW_SERVER,
                    "hostImageRef": NEW_HOST,
                },
            }
        )
        v2 = await service.new_version(
            policy_id=policy_id,
            document=new_doc,
            actor="bootstrap",
            expected_parent_ref=f"{policy_id}@1",
        )
        await service.transition(
            policy_id=policy_id,
            version=v2.version,
            state=PolicyState.ACTIVE,
            actor="bootstrap",
        )
        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@2"
        assert len(await service.versions(policy_id)) == 2
