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


@pytest.mark.parametrize("settings", [
    {"model_reasoning_summary": "concise"},
    {"model_verbosity": "low"},
    {"model_context_window": 128000},
    {"model_auto_compact_token_limit": 96000},
    {"personality": "pragmatic"},
    {
        "model_reasoning_summary": "concise", "model_verbosity": "low",
        "model_context_window": 128000, "model_auto_compact_token_limit": 96000,
        "personality": "pragmatic",
    },
])
def test_oauth_session_preferences_match_api_key_delivery(settings):
    from moonmind.provider_profiles.codex_configuration import (
        configuration_for_profile,
        oauth_configuration_for_profile,
    )
    from tests.unit.omnigent.test_codex_profile_configuration import profile

    api_key = profile()
    api_key.file_templates[0]["content_template"].update(settings)
    control, reason = configuration_for_profile(api_key)
    assert control is not None, reason
    assert {key: control["document"][key] for key in settings} == settings

    selected, _ = private_codex_profile()
    selected.file_templates[0]["content_template"] = dict(settings)
    configuration, reason = oauth_configuration_for_profile(selected)
    assert configuration is not None, reason
    assert configuration["document"] == settings


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
    session_settings = {
        "model_reasoning_summary": "concise", "model_verbosity": "low",
        "model_context_window": 128000, "model_auto_compact_token_limit": 96000,
        "personality": "pragmatic",
    }
    selected.file_templates[0]["content_template"].update(session_settings)
    private_reference = delivery == "oauth-home" and source_home == "per-run"
    if private_reference:
        selected.env_template = {
            "PROJECT_CONFIG_FILE": "{{runtime_support_dir}}/codex-home/AGENTS.md",
            "PROJECT_SPACED_FILE": "{{ runtime_support_dir }}/codex-home/AGENTS.md",
            "PROJECT_GENERIC_ROOT": "{{runtime_support_dir}}/reports",
            "PROJECT_SIBLING_ROOT": "{{runtime_support_dir}}/codex-home-other",
            "PROJECT_WORKSPACE": "{{workspace_path}}/reports",
        }
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
            "runtimeEnvironment": {
                **tuning.launchEnvironment,
                "PROJECT_LITERAL_PATH": "{{runtime_support_dir}}/codex-home/AGENTS.md",
                "MOONMIND_CODEX_PROFILE_CONFIGURATION": json.dumps(tuning.codexConfiguration),
            },
            "runtimeEnvironmentTemplates": tuning.environmentTemplates,
        }
    binaries.joinpath("python3").symlink_to(sys.executable)
    native = binaries / "codex"
    native.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,tomllib\n"
        "home=pathlib.Path(os.environ['CODEX_HOME'])\n"
        "config=tomllib.loads((home/'config.toml').read_text())\n"
        "reference=os.environ.get('PROJECT_CONFIG_FILE')\n"
        "reference=pathlib.Path(reference) if reference else None\n"
        "print(json.dumps({'home':home.name,'model':config['model'],"
        "'session':{key:config[key] for key in " + repr(list(session_settings)) + "},"
        "'preferences':config.get('developer_instructions'),"
        "'instructions':(home/'AGENTS.md').read_text(),"
        "'reference':str(reference) if reference else None,"
        "'referenceExists':reference.is_file() if reference else None,"
        "'referenceText':reference.read_text() if reference and reference.is_file() else None,"
        "'spacedReference':os.environ.get('PROJECT_SPACED_FILE'),"
        "'genericRoot':os.environ.get('PROJECT_GENERIC_ROOT'),"
        "'siblingRoot':os.environ.get('PROJECT_SIBLING_ROOT'),"
        "'workspace':os.environ.get('PROJECT_WORKSPACE'),"
        "'literal':os.environ.get('PROJECT_LITERAL_PATH')}))\n"
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
        workspace_path=str(tmp_path),
    )
    environment = {key: value.replace("/home/app", str(home)) for key, value in environment.items()}
    result = subprocess.run(
        ["/bin/sh", "-ceu", script.replace("/home/app", str(home)), "--", "http://test-omnigent"],
        env={**os.environ, **environment, "PATH": f"{binaries}:{os.environ['PATH']}"},
        text=True, capture_output=True, cwd=tmp_path,
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
            "session": session_settings,
            "instructions": "Enrollment instructions.\nSelected global instructions.\n",
            "reference": str(home / name / "AGENTS.md") if private_reference else None,
            "referenceExists": True if private_reference else None,
            "referenceText": "Enrollment instructions.\nSelected global instructions.\n" if private_reference else None,
            "spacedReference": str(home / name / "AGENTS.md") if private_reference else None,
            "genericRoot": str(home / ".omnigent/moonmind/runtime-context/reports") if private_reference else None,
            "siblingRoot": str(home / ".omnigent/moonmind/runtime-context/codex-home-other") if private_reference else None,
            "workspace": str(tmp_path / "reports") if private_reference else None,
            "literal": "{{runtime_support_dir}}/codex-home/AGENTS.md" if delivery == "oauth-home" else None,
        } for name in ("private-one", "private-two")]
        assert configuration_projection.stat().st_mode & 0o777 == 0o600
        assert not any(path.is_symlink() for path in enrolled.iterdir())


@pytest.mark.parametrize("key", ["HOME", "CODEX_HOME", "GITHUB_TOKEN", "OMNIGENT_CODEX_PATH"])
def test_private_environment_references_cannot_replace_native_authority(key):
    selected, _ = private_codex_profile()
    selected.env_template = {key: "{{runtime_support_dir}}/codex-home/AGENTS.md"}
    with pytest.raises(HarnessPlatformError):
        snapshot_provider_configuration(selected)


def test_mixed_private_and_generic_support_references_keep_the_native_route():
    from api_service.services.profile_execution_selection import (
        configuration_accepts_profile,
        profile_has_native_inventory_route,
    )
    from moonmind.provider_profiles.codex_configuration import oauth_configuration_for_profile

    selected, configuration = private_codex_profile()
    selected.env_template = {
        "PROJECT_FILES": "{{runtime_support_dir}}/codex-home/AGENTS.md:{{runtime_support_dir}}/reports",
    }
    private, reason = oauth_configuration_for_profile(selected)
    assert private is None and "untranslated support path" in reason
    assert not configuration_accepts_profile(configuration[1].document, selected)
    assert profile_has_native_inventory_route(selected, [configuration])


@pytest.mark.parametrize("environment", [
    {"HOME": "{{runtime_support_dir}}/AGENTS.md"},
    {"PROJECT_CONFIG_FILE": "{{provider_api_key}}/AGENTS.md"},
])
def test_private_environment_projection_revalidates_native_authority(environment):
    selected, _ = private_codex_profile()
    configuration = snapshot_provider_configuration(selected).codexConfiguration
    configuration["environmentTemplates"] = environment
    with pytest.raises(ValueError):
        OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[{
                "materializerRef": "codex-oauth-home@1", "attachments": [],
                "runtimeEnvironment": {"MOONMIND_CODEX_PROFILE_CONFIGURATION": json.dumps(configuration)},
            }],
            skill_attachment={"targetPath": "/opt/skills"},
            step_execution_id="workflow:run:work:execution:1", enable_codex_runtime=True,
        )


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


def claude_oauth_profile():
    from tests.unit.api_service.test_oauth_profile_launch_options import (
        profile_and_configuration,
    )
    from tests.unit.omnigent.test_claude_profile_configuration import configuration

    profile, _ = profile_and_configuration("claude_code", "v2")
    profile.volume_mount_path = "/home/app/.claude"
    profile.home_path_overrides = {
        "CLAUDE_HOME": "/home/app/.claude",
        "CLAUDE_CONFIG_DIR": "/home/app/.claude",
    }
    profile.file_templates = [
        {
            **item,
            "path": item["path"].replace(
                "{{runtime_support_dir}}/claude-home", "/home/app/.claude"
            ),
        }
        for item in configuration()["fileTemplates"]
    ] + [
        {
            "path": "/home/app/.claude/rules/project.md",
            "format": "text",
            "content_template": "Use focused tests.\n",
        }
    ]
    return profile


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"CLAUDE_HOME": "/home/app/.claude"},
        {"CLAUDE_CONFIG_DIR": "/home/app/.claude"},
    ],
)
def test_claude_oauth_preferences_freeze_canonical_files_without_auth_state(overrides):
    import copy
    import hashlib
    from types import SimpleNamespace
    from moonmind.omnigent.harness_platform.materializers import (
        provider_configuration_snapshot_bytes,
        load_provider_configuration_snapshot,
    )
    from moonmind.provider_profiles.claude_configuration import render_oauth_files

    profile = claude_oauth_profile()
    profile.home_path_overrides = overrides
    selected = snapshot_provider_configuration(profile)
    frozen = copy.deepcopy(selected.claudeConfiguration)
    bindings = {
        "primary-model": SimpleNamespace(
            providerProfileRef=profile.profile_id, materializerRef="claude-oauth-home@1"
        )
    }
    content = provider_configuration_snapshot_bytes(
        bindings, {"primary-model": selected}
    )
    profile.file_templates.clear()
    loaded = load_provider_configuration_snapshot(
        content,
        SimpleNamespace(contentDigest="sha256:" + hashlib.sha256(content).hexdigest()),
        bindings,
    )
    assert loaded["primary-model"].claudeConfiguration == frozen
    for sibling in ("a", "b"):
        files = render_oauth_files(
            frozen,
            runtime_support_dir="/support/" + sibling,
            workspace_path="/work/" + sibling,
        )
        assert set(files) == {"settings.json", "CLAUDE.md", "rules/project.md"}
        assert json.loads(files["settings.json"]) == {
            "outputStyle": "concise",
            "alwaysThinkingEnabled": True,
        }
        assert (
            files["CLAUDE.md"]
            == f"Work in /work/{sibling}; support /support/{sibling}".encode()
        )
        assert files == render_oauth_files(
            frozen,
            runtime_support_dir="/support/" + sibling,
            workspace_path="/work/" + sibling,
        )


@pytest.mark.parametrize(
    "path,content",
    [
        (".credentials.json", "no"),
        ("hooks.json", "no"),
        ("rules/../../CLAUDE.md", "no"),
        ("rules/with,comma.md", "no"),
        ("CLAUDE.md", "{{selected_secret}}"),
    ],
)
def test_claude_oauth_unsupported_files_remain_direct(path, content):
    from moonmind.omnigent.harness_platform.materializers import (
        untranslated_oauth_launch_options,
    )

    profile = claude_oauth_profile()
    profile.file_templates = [
        {"path": "/home/app/.claude/" + path, "content_template": content}
    ]
    assert "file_templates" in untranslated_oauth_launch_options(profile)
    with pytest.raises(HarnessPlatformError):
        snapshot_provider_configuration(profile)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_target", [None, "settings.json", "rules", "projection"]
)
async def test_claude_oauth_file_mounts_preserve_atomic_refresh_and_fenced_cleanup(
    tmp_path,
    unsafe_target,
):
    from pathlib import Path
    from moonmind.omnigent.credential_materializers import (
        DockerClaudeOauthHomeMaterializer,
        anticipated_credential_handle,
    )
    from moonmind.omnigent.host_services.launcher import docker_attachment_mount

    class ExecutingPreferenceBackend(FakeDockerBackend):
        async def run(self, argv, **kwargs):
            if (
                argv[:3] == ["docker", "volume", "ls"]
                and '"owned"' not in argv[-1]
                and "}}owned" in argv[-1]
            ):
                self.commands.append(list(argv))
                self.inputs.append(None)
                return 0, b"owned", b""
            if kwargs.get("input_bytes"):
                assert argv[argv.index("--user") + 1] == "0:0"
                script = argv[argv.index("-c") + 1]
                # The production writer is Docker root assigning its runtime
                # UID. This local transport maps that UID to the test runner,
                # which need not have permission to chown to container UID 1000.
                script = script.replace("1000, 1000", f"{os.getuid()}, {os.getgid()}")
                mount = argv[argv.index("--mount") + 1]
                volume = mount.split("src=", 1)[1].split(",", 1)[0]
                root = tmp_path / volume
                root.mkdir(exist_ok=True)
                if unsafe_target == "projection":
                    (root / "files").symlink_to(outside, target_is_directory=True)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script.replace("'/preferences'", repr(str(root))).replace(
                            "'/credential'", repr(str(tmp_path / "claude_enrolled"))
                        ),
                    ],
                    input=kwargs["input_bytes"],
                    capture_output=True,
                )
                assert result.returncode == 0, result.stderr
            return await super().run(argv, **kwargs)

    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "settings.json").write_text('{"language":"unchanged"}')
    backend = ExecutingPreferenceBackend()
    owner = DockerClaudeOauthHomeMaterializer(backend)
    enrolled = tmp_path / "claude_enrolled"
    enrolled.mkdir()
    token = enrolled / ".credentials.json"
    token.write_text("initial-test-token")
    original_preferences = enrolled / "CLAUDE.md"
    original_preferences.write_text("Enrollment instructions stay unchanged.")
    context = replace(
        _context(backend, home=_home("claude_enrolled", "/home/app/.claude")),
        provider_launch_environment=snapshot_provider_configuration(
            claude_oauth_profile()
        ),
    )
    if unsafe_target:
        if unsafe_target != "projection":
            (enrolled / unsafe_target).symlink_to(
                outside if unsafe_target == "rules" else outside / "settings.json",
                target_is_directory=unsafe_target == "rules",
            )
        reason = {
            "settings.json": "mount target must be a regular file",
            "rules": "Not a directory",
            "projection": "source contains a symlink",
        }[unsafe_target]
        with pytest.raises(AssertionError, match=reason):
            await owner.materialize(context)
        assert (outside / "settings.json").read_text() == '{"language":"unchanged"}'
        assert list(outside.iterdir()) == [outside / "settings.json"]
        assert token.read_text() == "initial-test-token"
        return
    handle = await owner.materialize(context)
    # Untuned siblings see neutral mountpoints, never empty invalid settings.
    assert json.loads((enrolled / "settings.json").read_text()) == {}
    mountpoint = (enrolled / "settings.json").stat()
    assert mountpoint.st_mode & 0o777 == 0o600
    assert (mountpoint.st_uid, mountpoint.st_gid) == (os.getuid(), os.getgid())
    assert (enrolled / "rules/project.md").read_text() == ""
    assert (
        handle.attachments
        == anticipated_credential_handle(
            context.acquired,
            owner.ref,
            profile_credential_home=context.profile_credential_home,
            provider_launch_environment=context.provider_launch_environment,
        ).attachments
    )
    assert handle.attachments[0].sourceRef == "claude_enrolled"
    assert handle.attachments[0].accessMode == "read-write"
    for attachment in handle.attachments[2:]:
        mount = docker_attachment_mount(attachment.model_dump(mode="json"))
        assert (
            "volume-subpath=files/" in mount
            and ",readonly" in mount
            and ",volume-nocopy" in mount
        )
        root = tmp_path / attachment.sourceRef
        assert (root / attachment.subPath).is_file()
        observed = (root / attachment.subPath).stat()
        assert (observed.st_uid, observed.st_gid) == (os.getuid(), os.getgid())
    # Native refresh atomically replaces the enrolled file; neither projection
    # setup nor cleanup reads, redirects, copies or overwrites that credential.
    replacement = token.with_suffix(".tmp")
    replacement.write_text("refreshed-test-token")
    os.replace(replacement, token)
    (enrolled / "settings.json").write_text('{"language":"sibling default"}')
    again = await owner.materialize(context)
    assert json.loads((enrolled / "settings.json").read_text()) == {
        "language": "sibling default"
    }
    assert again.attachments == handle.attachments
    first_files = {
        item.subPath: (tmp_path / item.sourceRef / item.subPath).read_bytes()
        for item in handle.attachments[2:]
    }
    assert first_files["files/CLAUDE.md"].count(b"Work in") == 1
    newer_context = replace(
        context,
        acquired=type(context.acquired)(
            **{**vars(context.acquired), "credential_generation": 5}
        ),
    )
    newer = await owner.materialize(newer_context)
    assert newer.attachments[1].sourceRef != handle.attachments[1].sourceRef
    with pytest.raises(HarnessPlatformError, match="fenced"):
        await owner.cleanup(handle, 5)
    await owner.cleanup(handle, 4)
    assert token.read_text() == "refreshed-test-token"
    assert original_preferences.read_text() == "Enrollment instructions stay unchanged."
    removed = [
        argv[-1] for argv in backend.commands if argv[:3] == ["docker", "volume", "rm"]
    ]
    assert removed == [handle.attachments[1].sourceRef]
    assert newer.attachments[1].sourceRef not in removed
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[handle.model_dump(mode="json")],
        skill_attachment={"targetPath": "/skills"},
        step_execution_id="workflow:run:step:1",
        enable_claude_runtime=True,
        workspace_path="/workspaces/run",
    )
    checks = environment["MOONMIND_CREDENTIAL_GENERATION_CHECKS"]
    assert "CLAUDE.md/.moonmind-generation" not in checks
    assert "/home/app/.claude/.moonmind-generation:4" in checks
    assert "CLAUDE_CONFIG_DIR" not in environment
