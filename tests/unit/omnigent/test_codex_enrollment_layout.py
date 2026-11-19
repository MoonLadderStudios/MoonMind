"""Custom layout admits only selected bounded credential ownership."""

from copy import deepcopy

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    ProviderConfigurationSlot,
    ProviderLaunchEnvironment,
    snapshot_provider_configuration,
    untranslated_oauth_launch_options,
)
from tests.unit.api_service.test_oauth_profile_launch_options import profile_and_configuration


def profile():
    value, _ = profile_and_configuration("codex_cli", "v2")
    value.file_templates = []
    value.volume_ref = "selected-enrollment"
    value.credential_generation = 3
    value.volume_mount_path = "/home/app/selected-codex-auth"
    value.home_path_overrides = {"CODEX_HOME": value.volume_mount_path}
    return value


@pytest.mark.parametrize("declare_selector", [False, True])
def test_selected_layout_matches_native_without_mutating_profile(declare_selector):
    from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer

    value = profile()
    if not declare_selector:
        value.home_path_overrides = {}
    before = deepcopy(vars(value))
    native = {}
    ProviderProfileMaterializer._apply_oauth_home_runtime_env(value, native)
    selected = snapshot_provider_configuration(value)
    assert selected.codexEnrollmentHome.targetPath == native["CODEX_HOME"]
    assert selected.codexEnrollmentHome.volumeRef == value.volume_ref
    assert (
        selected.codexEnrollmentHome.credentialGeneration
        == value.credential_generation
    )
    assert vars(value) == before



@pytest.mark.parametrize("preferences", [False, True])
def test_configuration_and_enrollment_roundtrip(preferences):
    import hashlib
    from types import SimpleNamespace
    from moonmind.omnigent.harness_platform.materializers import (
        load_provider_configuration_snapshot, provider_configuration_snapshot_bytes,
    )
    value = profile()
    if preferences:
        value.file_templates = [{"path": value.volume_mount_path + "/config.toml", "format": "toml",
                                 "content_template": {"model_verbosity": "low"}}]
    selected = snapshot_provider_configuration(value)
    assert selected.codexConfiguration == ({"document": {"model_verbosity": "low"}, "privateHomeOnly": True} if preferences else None)
    bindings = {"model": SimpleNamespace(providerProfileRef=value.profile_id, materializerRef="codex-oauth-home@1")}
    content = provider_configuration_snapshot_bytes(bindings, {"model": selected})
    snapshot = SimpleNamespace(contentDigest="sha256:" + hashlib.sha256(content).hexdigest())
    value.file_templates = []; value.volume_ref = "later"; value.volume_mount_path = "/home/app/later-auth"
    restored = load_provider_configuration_snapshot(content, snapshot, bindings)["model"]
    assert restored == selected
    assert restored.codexEnrollmentHome.model_dump() == {
        "volumeRef": "selected-enrollment", "targetPath": "/home/app/selected-codex-auth", "credentialGeneration": 3,
    }


@pytest.mark.parametrize("change", ["path", "selector", "file", "setting"])
def test_unqualified_custom_options_stay_native(change):
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    value = profile()
    if change == "path":
        value.volume_mount_path = "/home/app/.omnigent"
        value.home_path_overrides = {"CODEX_HOME": value.volume_mount_path}
    elif change == "selector":
        value.home_path_overrides = {"CODEX_HOME": "/home/app/sibling-auth"}
    elif change == "file":
        value.file_templates = [{"path": value.volume_mount_path + "/AGENTS.md", "format": "text", "content_template": "Keep native."}]
    else:
        value.file_templates = [{"path": value.volume_mount_path + "/config.toml", "format": "toml", "content_template": {"shell_environment_policy": {"inherit": "all"}}}]
    assert untranslated_oauth_launch_options(value)
    with pytest.raises(HarnessPlatformError):
        snapshot_provider_configuration(value)


def test_wrong_provider_cannot_claim_selected_enrollment():
    selected = snapshot_provider_configuration(profile())
    with pytest.raises(ValueError):
        ProviderConfigurationSlot(providerProfileRef="profile", materializerRef="claude-oauth-home@1", endpoint=None,
                                  codexEnrollmentHome=selected.codexEnrollmentHome)
    with pytest.raises(ValueError):
        ProviderLaunchEnvironment(codexEnrollmentHome=selected.codexEnrollmentHome,
                                  claudeConfiguration={"homePathOverrides": {}, "fileTemplates": []})


def test_private_projection_never_edits_selected_or_sibling_source(tmp_path, monkeypatch):
    from moonmind.provider_profiles import codex_configuration as config
    source = tmp_path / "selected"; source.mkdir()
    private = tmp_path / "private"; private.mkdir()
    sibling = tmp_path / "sibling"; sibling.mkdir()
    monkeypatch.setattr(config, "_ENROLLED_CODEX_HOME", str(source))
    settings = {"document": {"model_verbosity": "low"}, "privateHomeOnly": True}
    (source / "config.toml").write_text('model_verbosity = "high"\n')
    with pytest.raises(ValueError, match="credential home"):
        config.configure_private_oauth_home(settings, source, enrolled_home=str(source))
    config.configure_private_oauth_home(settings, private, enrolled_home=str(source))
    assert '"high"' in (source / "config.toml").read_text()
    (source / "AGENTS.md").write_text("selected")
    (sibling / "AGENTS.md").write_text("sibling")
    (private / "AGENTS.md").symlink_to(sibling / "AGENTS.md")
    with pytest.raises(ValueError, match="unowned symbolic link"):
        config._privatize_native_file_path(private, private / "AGENTS.md", enrolled_home=str(source))
    (private / "AGENTS.md").unlink()
    (private / "AGENTS.md").symlink_to(source / "AGENTS.md")
    config._privatize_native_file_path(private, private / "AGENTS.md", enrolled_home=str(source))
    (private / "AGENTS.md").write_text("private")
    assert (source / "AGENTS.md").read_text() == "selected"
    assert (sibling / "AGENTS.md").read_text() == "sibling"


def test_detachment_rejects_unowned_hardlink(tmp_path, monkeypatch):
    import os
    from moonmind.provider_profiles import codex_configuration as config
    source = tmp_path / "selected"; source.mkdir()
    private = tmp_path / "private"; private.mkdir()
    monkeypatch.setattr(config, "_ENROLLED_CODEX_HOME", str(source))
    (source / "AGENTS.md").write_text("source")
    os.link(source / "AGENTS.md", private / "AGENTS.md")
    with pytest.raises(ValueError, match="unowned hard link"):
        config._privatize_native_file_path(private, private / "AGENTS.md", enrolled_home=str(source))
    assert (source / "AGENTS.md").read_text() == "source"


def test_generated_wrapper_preserves_private_home_with_frozen_source(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from moonmind.provider_profiles import codex_configuration as config

    private = tmp_path / "private"; private.mkdir()
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps({"document": {"model_verbosity": "low"}, "privateHomeOnly": True}))
    binary = tmp_path / "codex"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_HOME"\ncat "$CODEX_HOME/config.toml"\n')
    binary.chmod(0o700)
    wrapper = tmp_path / "wrapper"
    environment = {"PATH": os.defpath, "CODEX_HOME": str(private)}
    prepare = subprocess.run([sys.executable, str(Path(config.__file__)), "prepare", str(configuration),
                              str(binary), str(wrapper), "/usr/bin/env", "/home/app/selected-codex-auth"],
                             env=environment, capture_output=True, text=True, check=True)
    assert prepare.returncode == 0
    assert "--enrolled-home /home/app/selected-codex-auth" in wrapper.read_text()
    launched = subprocess.run([str(wrapper), "app-server"], env=environment,
                              capture_output=True, text=True, check=True)
    assert launched.stdout.splitlines()[0] == str(private)
    assert 'model_verbosity = "low"' in launched.stdout
