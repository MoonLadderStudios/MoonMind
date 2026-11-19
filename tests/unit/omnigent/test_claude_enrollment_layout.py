"""Custom layout admits only selected bounded credential ownership."""

from copy import deepcopy

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    ClaudeEnrollmentHome,
    ProviderConfigurationSlot,
    ProviderLaunchEnvironment,
    snapshot_provider_configuration,
    untranslated_oauth_launch_options,
)
from tests.unit.omnigent.test_oauth_private_configuration import claude_oauth_profile


def profile():
    value = claude_oauth_profile()
    value.file_templates = []
    value.volume_ref = "selected-enrollment"
    value.credential_generation = 3
    value.volume_mount_path = "/home/app/selected-claude-auth"
    value.home_path_overrides = {"CLAUDE_CONFIG_DIR": value.volume_mount_path}
    return value


def test_selected_layout_matches_native_without_mutating_profile():
    from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer

    value = profile()
    before = deepcopy(vars(value))
    native = {}
    ProviderProfileMaterializer._apply_oauth_home_runtime_env(value, native)
    selected = snapshot_provider_configuration(value)
    assert selected.claudeEnrollmentHome.targetPath == native["CLAUDE_CONFIG_DIR"]
    assert selected.claudeEnrollmentHome.volumeRef == value.volume_ref
    assert (
        selected.claudeEnrollmentHome.credentialGeneration
        == value.credential_generation
    )
    assert vars(value) == before


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/home/app",
        "/home/app/.omnigent",
        "/home/app/.codex",
        "/root/.codex",
        "/home/app/.claude/nested-auth",
        "/home/app/foo-auth/child",
        "/opt/tool-auth",
        "/home/app/../bad-auth",
        "/home/app/safe-auth,readonly",
        "/home/app/ordinary",
    ],
)
def test_unsafe_or_unbounded_layout_keeps_native_route(target):
    value = profile()
    value.volume_mount_path = target
    value.home_path_overrides = {"CLAUDE_CONFIG_DIR": target}
    assert untranslated_oauth_launch_options(value)
    with pytest.raises(ValueError):
        ClaudeEnrollmentHome(
            volumeRef="selected", targetPath=target, credentialGeneration=1
        )


@pytest.mark.parametrize("change", ["file", "HOME", "wrong-selector"])
def test_untranslated_custom_configuration_is_not_dropped(change):
    value = profile()
    if change == "file":
        value.file_templates = [
            {
                "path": value.volume_mount_path + "/settings.json",
                "format": "json",
                "content_template": {"language": "English"},
            }
        ]
    else:
        value.home_path_overrides["HOME" if change == "HOME" else "CLAUDE_HOME"] = (
            "/home/app/other-auth"
        )
    assert untranslated_oauth_launch_options(value)


def test_wrong_owner_and_mixed_configuration_fail_closed():
    selected = snapshot_provider_configuration(profile())
    with pytest.raises(ValueError):
        ProviderConfigurationSlot(
            providerProfileRef="profile",
            materializerRef="codex-oauth-home@1",
            endpoint=None,
            claudeEnrollmentHome=selected.claudeEnrollmentHome,
        )
    with pytest.raises(ValueError):
        ProviderLaunchEnvironment(
            claudeEnrollmentHome=selected.claudeEnrollmentHome,
            claudeConfiguration={"homePathOverrides": {}, "fileTemplates": []},
        )


def test_snapshot_keeps_account_and_layout_authority():
    import hashlib
    from types import SimpleNamespace

    from moonmind.omnigent.harness_platform.materializers import (
        load_provider_configuration_snapshot,
        provider_configuration_snapshot_bytes,
    )

    value = profile()
    selected = snapshot_provider_configuration(value)
    bindings = {
        "model": SimpleNamespace(
            providerProfileRef=value.profile_id, materializerRef="claude-oauth-home@1"
        )
    }
    content = provider_configuration_snapshot_bytes(bindings, {"model": selected})
    snapshot = SimpleNamespace(
        contentDigest="sha256:" + hashlib.sha256(content).hexdigest()
    )
    assert (
        load_provider_configuration_snapshot(content, snapshot, bindings)["model"]
        == selected
    )
    bindings["model"].providerProfileRef = "other-account"
    with pytest.raises(ValueError, match="account"):
        load_provider_configuration_snapshot(content, snapshot, bindings)


@pytest.mark.parametrize("wrong", ["volume", "target", "generation"])
def test_materializer_rejects_layout_not_matching_selected_enrollment(wrong):
    from types import SimpleNamespace

    from moonmind.omnigent.credential_materializers import (
        ProfileCredentialHome,
        anticipated_credential_handle,
    )
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    selected = snapshot_provider_configuration(profile())
    home = selected.claudeEnrollmentHome
    acquired = SimpleNamespace(
        provider_profile_ref="selected-oauth",
        provider_lease_ref="lease",
        credential_generation=4 if wrong == "generation" else 3,
        admission_epoch=1,
    )
    supplied = ProfileCredentialHome(
        volume_ref="other" if wrong == "volume" else home.volumeRef,
        target_path="/home/app/other-auth" if wrong == "target" else home.targetPath,
    )
    with pytest.raises(HarnessPlatformError, match="admitted enrollment"):
        anticipated_credential_handle(
            acquired,
            "claude-oauth-home@1",
            profile_credential_home=supplied,
            provider_launch_environment=selected,
        )
