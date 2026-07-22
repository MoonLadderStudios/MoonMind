import hashlib
import json

import pytest
from pydantic import ValidationError

from moonmind.omnigent.execution_profiles import (
    OmnigentLaunchPolicy,
    compile_effective_launch,
    public_execution_catalog,
    selection_from_request,
    validate_effective_launch_snapshot,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime


@pytest.fixture(autouse=True)
def immutable_bootstrap_images(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", "example.test/host@sha256:" + "2" * 64)


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
    validate_effective_launch_snapshot(first)


def test_mutated_snapshot_fails_conflict_validation() -> None:
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex-oauth",
    )
    launch["limits"]["memoryMiB"] = 8192
    with pytest.raises(OmnigentOAuthHostError) as error:
        validate_effective_launch_snapshot(launch)
    assert error.value.code == "OMNIGENT_EFFECTIVE_LAUNCH_CONFLICT"


def test_profile_identifier_may_contain_token_word() -> None:
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex-token-profile",
    )
    validate_effective_launch_snapshot(launch)


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
            networkRef="local-network",
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


def test_compile_rejects_missing_or_placeholder_bootstrap_images(monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_IMAGE_REF")
    with pytest.raises(OmnigentOAuthHostError) as missing:
        compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="codex-on-demand@1",
            provider_profile_id="codex-oauth",
        )
    assert missing.value.code == "OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE"

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "0" * 64)
    with pytest.raises(OmnigentOAuthHostError) as placeholder:
        compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="codex-on-demand@1",
            provider_profile_id="codex-oauth",
        )
    assert placeholder.value.code == "OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE"


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


def test_public_catalog_exposes_only_safe_stable_product_refs() -> None:
    catalog = public_execution_catalog()
    assert [profile["ref"] for profile in catalog["profiles"]] == [
        "omnigent-codex@1"
    ]
    assert {policy["ref"] for policy in catalog["policies"]} == {
        "codex-static@1",
        "codex-on-demand@1",
    }
    assert "credential" not in str(catalog).lower()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "serverImageRef",
            "omnigent:latest",
            "OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
        ),
        ("readOnlyRoot", False, "OMNIGENT_LAUNCH_ROOT_UNREALIZABLE"),
        (
            "capture",
            {"required": False, "retentionDays": 30},
            "OMNIGENT_LAUNCH_CAPTURE_UNREALIZABLE",
        ),
        (
            "cleanup",
            {"mode": "drain", "janitor": True},
            "OMNIGENT_LAUNCH_CLEANUP_UNREALIZABLE",
        ),
        (
            "controlCapabilities",
            ["terminate"],
            "OMNIGENT_LAUNCH_CONTROLS_UNREALIZABLE",
        ),
    ],
)
def test_runtime_revalidates_complete_on_demand_policy_before_mutation(
    field: str, value: object, code: str
) -> None:
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex-oauth",
    )
    launch[field] = value
    canonical = json.dumps(
        {key: item for key, item in launch.items() if key != "snapshotRef"},
        sort_keys=True,
        separators=(",", ":"),
    )
    launch["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()

    with pytest.raises(OmnigentOAuthHostError) as error:
        OmnigentOAuthHostRuntime._validate_effective_launch(
            binding=type("Binding", (), {"host_launch_profile_ref": "on-demand"})(),
            effective_launch=launch,
        )

    assert error.value.code == code
