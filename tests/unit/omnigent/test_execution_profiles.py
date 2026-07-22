import pytest
from pydantic import ValidationError

from moonmind.omnigent.execution_profiles import (
    OmnigentLaunchPolicy,
    compile_effective_launch,
    selection_from_request,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError


def test_versioned_profile_and_policy_compile_to_stable_safe_snapshot() -> None:
    first = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex-oauth",
    )
    second = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex-oauth",
    )

    assert first == second
    assert first["hostMode"] == "on_demand_docker"
    assert first["harness"] == "codex-native"
    assert first["snapshotRef"].startswith("omnigent-launch:sha256:")
    assert "credential" not in str(first).lower()
    assert "docker.sock" not in str(first)


def test_missing_explicit_policy_fails_closed() -> None:
    with pytest.raises(OmnigentOAuthHostError) as error:
        compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="missing@1",
            provider_profile_id="codex-oauth",
        )
    assert error.value.code == "OMNIGENT_LAUNCH_POLICY_UNAVAILABLE"


def test_policy_rejects_mutable_image_before_launch() -> None:
    with pytest.raises(ValidationError, match="immutable sha256 digest"):
        OmnigentLaunchPolicy(
            policyId="bad",
            version=1,
            hostMode="on_demand_docker",
            serverImageRef="omnigent:latest",
            hostImageRef="host:latest",
            networkRef="moonmind_local-network",
            enforcedEgress=True,
            limits={
                "cpuMillis": 1,
                "memoryMiB": 1,
                "processes": 1,
                "timeoutSeconds": 1,
                "temporaryStorageMiB": 1,
            },
            mountClasses=("oauth_home",),
            capture={"required": True},
            cleanup={"mode": "remove"},
            controlCapabilities=(),
        )


def test_workflow_cannot_supply_host_or_credential_authority() -> None:
    with pytest.raises(OmnigentOAuthHostError) as error:
        selection_from_request({"omnigent": {"session": {"hostId": "manual"}}})
    assert error.value.code == "OMNIGENT_LAUNCH_POLICY_FORBIDDEN_INPUT"


def test_product_selection_is_explicit_and_versioned() -> None:
    assert selection_from_request(
        {
            "omnigent": {
                "executionTargetRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-static@1",
            }
        }
    ) == ("omnigent-codex@1", "codex-static@1")
