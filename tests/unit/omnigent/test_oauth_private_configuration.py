"""OAuth preferences reach private native homes without changing enrolled state."""

import json
import os
from dataclasses import replace
import subprocess
import sys

import pytest

from moonmind.omnigent.credential_materializers import DockerCodexOauthHomeMaterializer
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.materializers import snapshot_provider_configuration
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from tests.unit.api_service.test_oauth_profile_launch_options import private_codex_profile
from tests.unit.omnigent.test_oauth_home_materializers import FakeDockerBackend, _context, _home


@pytest.mark.asyncio
async def test_private_configuration_keeps_the_enrolled_home_and_its_cleanup_owner():
    selected, _ = private_codex_profile()
    backend = FakeDockerBackend()
    owner = DockerCodexOauthHomeMaterializer(backend)
    context = replace(
        _context(backend, home=_home()),
        provider_launch_environment=snapshot_provider_configuration(selected),
    )
    handle = await owner.materialize(context)
    configuration = json.loads(handle.runtimeEnvironment["MOONMIND_CODEX_PROFILE_CONFIGURATION"])
    assert configuration == context.provider_launch_environment.codexConfiguration
    assert len(handle.attachments) == 1
    assert handle.attachments[0].sourceRef == "codex_auth_volume"
    cleanup = await owner.cleanup(handle, handle.credentialGeneration)
    assert cleanup.removed is False and cleanup.evidence["preserved"] is True
    assert all(cmd[:3] not in (["docker", "volume", "create"], ["docker", "volume", "rm"]) for cmd in backend.commands)
    assert all("config.toml" not in str(cmd) for cmd in backend.commands)


@pytest.mark.parametrize("use_enrolled_home", [False, True], ids=["two-private-homes", "refuse-enrolled-home"])
@pytest.mark.parametrize("delivery", ["oauth-home", "provider-config"])
@pytest.mark.parametrize("source_home", ["per-run", "enrolled", "implicit-enrolled"])
def test_private_configuration_runs_only_in_native_private_home(tmp_path, use_enrolled_home, delivery, source_home):
    selected, _ = private_codex_profile(
        home_path=("{{runtime_support_dir}}/codex-home" if source_home == "per-run" else "/home/app/.codex"),
        declare_home=source_home != "implicit-enrolled",
    )
    tuning = snapshot_provider_configuration(selected)
    home, skills, binaries = (tmp_path / name for name in ("home", "skills", "bin"))
    for directory in (home, skills, binaries):
        directory.mkdir()
    enrolled = home / ".codex"
    enrolled.mkdir()
    enrolled.joinpath("config.toml").write_text('model = "enrolled-default"\n')
    enrolled.joinpath("auth.json").write_bytes(b'{"synthetic-enrollment":"preserve"}')
    enrolled.joinpath("AGENTS.md").write_text("Enrollment instructions.\n")
    enrolled.joinpath(".moonmind-generation").write_text("4\n")
    before = {path.name: path.read_bytes() for path in enrolled.iterdir()}
    if delivery == "provider-config":
        directory = home / ".moonmind-provider-config"
        directory.mkdir()
        configuration_projection = directory / "codex-profile.json"
        configuration_projection.write_text(json.dumps(tuning.codexConfiguration))
        configuration_projection.chmod(0o600)
        directory.joinpath(".moonmind-generation").write_text("4\n")
        handle = {
            "credentialGeneration": 4, "materializerRef": "omnigent-provider-config@1",
            "attachments": [{"targetPath": "/home/app/.moonmind-provider-config"}],
            "runtimeEnvironment": {"OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config", "MOONMIND_CODEX_PROFILE_CONFIG_FILE": "/home/app/.moonmind-provider-config/codex-profile.json"},
        }
    else:
        configuration_projection = home / ".omnigent/moonmind/runtime-context/provider-MOONMIND_CODEX_PROFILE_CONFIGURATION"
        handle = {
            "credentialGeneration": 4, "materializerRef": "codex-oauth-home@1",
            "attachments": [{"targetPath": "/home/app/.codex"}],
            "runtimeEnvironment": {"MOONMIND_CODEX_PROFILE_CONFIGURATION": json.dumps(tuning.codexConfiguration)},
        }
    binaries.joinpath("python3").symlink_to(sys.executable)
    native = binaries / "codex"
    native.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,tomllib\n"
        "home=pathlib.Path(os.environ['CODEX_HOME'])\n"
        "config=tomllib.loads((home/'config.toml').read_text())\n"
        "print(json.dumps({'home':home.name,'model':config['model'],"
        "'preferences':config.get('developer_instructions'),"
        "'instructions':(home/'AGENTS.md').read_text()}))\n"
    )
    native.chmod(0o700)
    omnigent = binaries / "omnigent"
    omnigent.write_text(
        f"#!{sys.executable}\nimport os,pathlib,shutil,subprocess\n"
        "source=pathlib.Path(os.environ['CODEX_HOME'])\n"
        + ("homes=[source]\n" if use_enrolled_home else
           "homes=[source.parent/'private-one',source.parent/'private-two']\n")
        + "for home in homes:\n"
        + (" pass\n" if use_enrolled_home else " shutil.copytree(source,home)\n")
        + ("" if use_enrolled_home else
           " (home/'AGENTS.md').unlink()\n (home/'AGENTS.md').symlink_to(source/'AGENTS.md')\n")
        + ("" if use_enrolled_home else
           " (home/'config.toml').write_text(" + repr('model = "admitted-plan"\ndeveloper_instructions = "Runtime-owned instructions."\n') + ")\n")
        + " subprocess.run([os.environ['OMNIGENT_CODEX_PATH'],'app-server'],check=True,"
        "env={'PATH':os.environ['PATH'],'CODEX_HOME':str(home)})\n"
        " (home/'AGENTS.md').write_text('Private-only edit')\n"
    )
    omnigent.chmod(0o700)
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[handle],
        skill_attachment={"targetPath": str(skills)},
        step_execution_id="workflow:run:work:execution:1",
        enable_codex_runtime=True, control_credential_available=False,
    )
    environment = {key: value.replace("/home/app", str(home)) for key, value in environment.items()}
    result = subprocess.run(
        ["/bin/sh", "-ceu", script.replace("/home/app", str(home)), "--", "http://test-omnigent"],
        env={**os.environ, **environment, "PATH": f"{binaries}:{os.environ['PATH']}"},
        text=True, capture_output=True,
    )
    assert {path.name: path.read_bytes() for path in enrolled.iterdir()} == before
    if use_enrolled_home:
        assert result.returncode != 0
        assert "enrolled" in result.stderr.lower()
        assert not result.stdout
    else:
        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert rows == [{
            "home": name, "model": "admitted-plan", "preferences": "Runtime-owned instructions.\n\nPreserve accepted work.",
            "instructions": "Enrollment instructions.\nSelected global instructions.\n",
        } for name in ("private-one", "private-two")]
        assert configuration_projection.stat().st_mode & 0o777 == 0o600
        assert not any(path.is_symlink() for path in enrolled.iterdir())


@pytest.mark.parametrize("materializer", ["claude-oauth-home@1", "omnigent-provider-config@1"])
def test_private_configuration_cannot_change_credential_owner(materializer):
    selected, _ = private_codex_profile()
    configuration = snapshot_provider_configuration(selected).codexConfiguration
    with pytest.raises(ValueError, match="enrolled Codex materializer"):
        OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[{
                "materializerRef": materializer, "attachments": [],
                "runtimeEnvironment": {"MOONMIND_CODEX_PROFILE_CONFIGURATION": json.dumps(configuration)},
            }],
            skill_attachment={"targetPath": "/opt/skills"},
            step_execution_id="workflow:run:work:execution:1", enable_codex_runtime=True,
        )


@pytest.mark.parametrize("escape", [
    "auth.json", "config.toml/../auth.json", "sessions/other.jsonl",
    ".credentials.json", "hooks.json", "mcp-oauth-locks/selected.lock",
    "plugins/cache/selected/plugin.json", "state_9.sqlite",
    ".omnigent-developer-instructions-state.toml",
])
def test_oauth_extra_files_cannot_replace_authentication_or_history(escape):
    selected, _ = private_codex_profile()
    selected.file_templates[1]["path"] = "{{runtime_support_dir}}/codex-home/" + escape
    with pytest.raises(HarnessPlatformError):
        snapshot_provider_configuration(selected)


@pytest.mark.parametrize("link_kind", ["private-auth", "foreign-sibling", "hardlink"])
def test_private_instruction_projection_cannot_write_another_file(tmp_path, link_kind):
    from moonmind.provider_profiles.codex_configuration import configure_profile_files

    home = tmp_path / "private"
    home.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    target = home / "auth.json" if link_kind != "foreign-sibling" else sibling / "AGENTS.md"
    target.write_text("Preserve this owner.\n")
    if link_kind == "hardlink":
        os.link(target, home / "AGENTS.md")
    else:
        (home / "AGENTS.md").symlink_to(target)
    selected, _ = private_codex_profile()
    configuration = snapshot_provider_configuration(selected).codexConfiguration
    with pytest.raises(ValueError):
        configure_profile_files(configuration, home, tmp_path)
    assert target.read_text() == "Preserve this owner.\n"


def test_resolved_profile_file_context_cannot_select_private_authentication(tmp_path):
    from moonmind.provider_profiles.codex_configuration import configure_profile_files

    home = tmp_path / "private"
    home.mkdir()
    auth = home / "auth.json"
    auth.write_text("Preserve authentication.\n")
    configuration = {"additionalFiles": [{
        "path": "{{runtime_support_dir}}/{{workspace_path}}/auth.json",
        "format": "text", "merge_strategy": "replace", "permissions": "0600",
        "content_template": "Do not replace authentication.",
    }]}
    with pytest.raises(ValueError):
        configure_profile_files(configuration, home, "/")
    assert auth.read_text() == "Preserve authentication.\n"


@pytest.mark.parametrize("relative", ["AGENTS.md", "rules/nested/selected.md"])
def test_native_bridged_instructions_are_projected_without_changing_the_source(
    tmp_path, monkeypatch, relative,
):
    from moonmind.provider_profiles import codex_configuration as module

    enrolled, private = tmp_path / "enrolled", tmp_path / "private"
    enrolled.mkdir()
    private.mkdir()
    source = enrolled / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Enrolled instructions.\n")
    untouched = source.parent / ("AGENTS.override.md" if relative == "AGENTS.md" else "untouched.md")
    untouched.write_text("Other instructions.\n")
    root = relative.split("/", 1)[0]
    (private / root).symlink_to(enrolled / root)
    if relative == "AGENTS.md":
        (private / untouched.name).symlink_to(untouched)
    monkeypatch.setattr(module, "_ENROLLED_CODEX_HOME", str(enrolled))
    configuration = {"additionalFiles": [{
        "path": "{{runtime_support_dir}}/" + relative,
        "format": "text", "merge_strategy": "append", "permissions": "0600",
        "content_template": "Selected instructions.\n",
    }]}
    module.configure_profile_files(configuration, private, tmp_path)
    assert source.read_text() == "Enrolled instructions.\n"
    assert (private / relative).read_text() == "Enrolled instructions.\nSelected instructions.\n"
    assert (private / relative).resolve().is_relative_to(private)
    assert (private / relative).stat().st_mode & 0o777 == 0o600
    untouched.write_text("Updated other instructions.\n")
    assert (private / relative).parent.joinpath(untouched.name).read_text() == "Updated other instructions.\n"
