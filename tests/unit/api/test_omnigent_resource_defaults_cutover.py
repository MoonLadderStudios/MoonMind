"""Bootstrap migrates retired shared-CPU defaults to fixed successors."""

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


def _no_daemon(monkeypatch):
    """Fail the test if bootstrap probes Docker for resource defaults."""

    async def command(argv, **kwargs):
        raise AssertionError(f"bootstrap must not probe Docker: {argv!r}")

    monkeypatch.setattr(policies, "run_runtime_command", command)


def _previous_release_bootstrap(monkeypatch, *, cpu_millis=0):
    """Simulate the previous release's stock defaults as admitted authority.

    The old release admitted shared-CPU stock as valid. New code marks such
    documents invalid for execution, so the simulation forces the old verdict
    to reproduce the exact rows an upgrade meets in a populated deployment.
    """

    current_bootstrap = policies.bootstrap_document
    current_validate = policies.validate_policy

    def old_bootstrap(**kwargs):
        data = current_bootstrap(**kwargs).model_dump(by_alias=True)
        data["resources"]["cpuMillis"] = cpu_millis
        return PolicyDocument.model_validate(data)

    def old_validate(document, **kwargs):
        validation, compatibility = current_validate(document, **kwargs)
        if document.resources.cpu_millis == cpu_millis:
            validation = {**validation, "valid": True, "diagnostics": []}
            compatibility = {"compatible": True, "diagnosticCodes": []}
        return validation, compatibility

    monkeypatch.setattr(policies, "bootstrap_document", old_bootstrap)
    monkeypatch.setattr(policies, "validate_policy", old_validate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operator_owned,custom_memory", [(False, False), (True, False), (False, True)]
)
async def test_startup_migrates_shared_cpu_defaults_to_fixed_successors(
    tmp_path,
    monkeypatch,
    operator_owned,
    custom_memory,
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        with monkeypatch.context() as previous_release:
            _previous_release_bootstrap(previous_release)
            if custom_memory:
                current_bootstrap = policies.bootstrap_document

                def old_bootstrap_custom(**kwargs):
                    data = current_bootstrap(**kwargs).model_dump(by_alias=True)
                    data["resources"]["cpuMillis"] = 0
                    data["resources"]["memoryMiB"] = 3072
                    return PolicyDocument.model_validate(data)

                previous_release.setattr(
                    policies, "bootstrap_document", old_bootstrap_custom
                )
            await policies.seed_bootstrap_policies(session, image_resolver=_image)
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        old = await service.resolve_runtime_snapshot(f"{policy_id}@1")
        assert old["boundaries"]["resources"]["cpuMillis"] == 0
        expected_version = 1 if custom_memory else 2
        if operator_owned:
            custom = await service.new_version(
                policy_id=policy_id,
                document=PolicyDocument.model_validate(
                    {
                        **deepcopy(old["boundaries"]),
                        "resources": {
                            **old["boundaries"]["resources"],
                            "cpuMillis": 3000,
                        },
                    }
                ),
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

        # Startup migrates only bootstrap-owned shared-CPU stock to the fixed
        # successor, preserving all unrelated fields and leaving active
        # bindings and historical versions untouched.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@{expected_version}"
        if custom_memory:
            # Customized shared-CPU stock is never guessed to be unmodified
            # stock: it stays exactly as admitted.
            assert latest["boundaries"]["resources"]["cpuMillis"] == 0
            assert latest["boundaries"]["resources"]["memoryMiB"] == 3072
        elif operator_owned:
            assert latest["boundaries"]["resources"]["cpuMillis"] == 3000
        else:
            assert latest["boundaries"]["resources"] == {
                **old["boundaries"]["resources"],
                "cpuMillis": 2000,
            }
            assert latest["boundaries"]["host"] == old["boundaries"]["host"]
        # Historical versions are never rewritten.
        assert await service.resolve_runtime_snapshot(f"{policy_id}@1") == old
        # The historical document stays decodable, but new execution never
        # interprets it as shared-pool authority.
        historical_document = PolicyDocument.model_validate(old["boundaries"])
        assert historical_document.resources.cpu_millis == 0
        with pytest.raises(Exception, match="(?i)positive|shared|limit"):
            launch_policy_from_effective_launch(
                {
                    "launchPolicyRef": f"{policy_id}@1",
                    "hostMode": "on-demand",
                    "limits": {
                        k: v
                        for k, v in old["boundaries"]["resources"].items()
                        if k != "concurrency"
                    },
                    "capture": old["boundaries"]["capture"],
                    "cleanup": {"mode": "remove"},
                }
            )
        # Repeated startup creates no duplicate successor versions or bindings.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert len(await service.versions(policy_id)) == expected_version


@pytest.mark.asyncio
async def test_startup_seeds_fixed_defaults_without_any_docker_probe(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    assert not hasattr(policies, "bootstrap_shared_cpu_supported")
    async with policy_db(tmp_path) as sessions, sessions() as session:
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

        # Repeating startup publishes nothing further and probes nothing.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        for policy_id, snapshot in snapshots.items():
            assert await service.resolve_default_runtime_snapshot(policy_id) == snapshot
            assert len(await service.versions(policy_id)) == 1


@pytest.mark.asyncio
async def test_customized_zero_policy_is_left_for_operator_disposition(
    tmp_path, monkeypatch
):
    """A customized zero-valued policy is never guessed to be stock."""

    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        current = await service.resolve_default_runtime_snapshot(policy_id)
        customized = PolicyDocument.model_validate(
            {
                **deepcopy(current["boundaries"]),
                "resources": {
                    **current["boundaries"]["resources"],
                    "cpuMillis": 0,
                    "memoryMiB": 3072,
                },
            }
        )
        validation, compatibility = policies.validate_policy(customized)
        assert validation["valid"] is False
        assert "OMNIGENT_CPU_LIMIT_REQUIRED" in compatibility["diagnosticCodes"]
        candidate = await service.new_version(
            policy_id=policy_id,
            document=customized,
            actor="operator",
            expected_parent_ref=current["policyRef"],
        )
        # An invalid policy cannot become executable authority.
        with pytest.raises(Exception):
            await service.transition(
                policy_id=policy_id,
                version=candidate.version,
                state=PolicyState.ACTIVE,
                actor="operator",
                make_default=True,
            )
        # Startup migrates nothing: the stored document is untouched and no
        # successor is guessed from it.
        await policies.seed_bootstrap_policies(session, image_resolver=_image)
        assert (
            await service.resolve_default_runtime_snapshot(policy_id)
        ) == current
        candidate_row = await service.get_version(policy_id, candidate.version)
        assert candidate_row.state == "draft"
