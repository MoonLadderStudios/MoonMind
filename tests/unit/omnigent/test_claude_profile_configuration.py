"""Private authored Claude preferences preserve shared launch authority."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from moonmind.provider_profiles.claude_configuration import (
    materialize_configuration, validate_configuration,
)
from moonmind.omnigent.harness_platform.materializers import (
    claude_provider_config_endpoint, provider_configuration_snapshot_bytes,
    load_provider_configuration_snapshot,
)
from tests.unit.omnigent.test_claude_api_key_provider_config import _api_key_profile, _provision


def configuration():
    return {
        "homePathOverrides": {"CLAUDE_CONFIG_DIR": "{{runtime_support_dir}}/claude-home"},
        "fileTemplates": [
            {"path": "{{runtime_support_dir}}/claude-home/settings.json", "format": "json", "content_template": {"outputStyle": "concise"}},
            {"path": "{{runtime_support_dir}}/claude-home/settings.json", "format": "json", "merge_strategy": "deep_merge", "content_template": {"alwaysThinkingEnabled": True}},
            {"path": "{{runtime_support_dir}}/claude-home/CLAUDE.md", "format": "text", "merge_strategy": "append", "content_template": "Work in {{workspace_path}}; support {{runtime_support_dir}}"},
        ],
    }


def test_shared_renderer_initializes_once_and_isolates_siblings(tmp_path):
    config = configuration()
    original = copy.deepcopy(config)
    for sibling in ("a", "b"):
        root = tmp_path / sibling
        for _ in range(2):
            materialize_configuration(config, runtime_support_dir=root, workspace_path="/work/" + sibling)
        home = root / "claude-home"
        assert json.loads((home / "settings.json").read_text()) == {"outputStyle": "concise", "alwaysThinkingEnabled": True}
        assert (home / "CLAUDE.md").read_text() == f"Work in /work/{sibling}; support {root}"
        assert (home / "settings.json").stat().st_mode & 0o777 == 0o600
        assert home.stat().st_mode & 0o777 == 0o700
    assert config == original
    with pytest.raises(ValueError, match="already owned"):
        materialize_configuration(config, runtime_support_dir=tmp_path / "a", workspace_path="/changed")


@pytest.mark.parametrize("path,content,format", [
    ("../sibling/CLAUDE.md", "escape", "text"),
    (".credentials.json", {}, "json"),
    ("settings.json", {"apiKeyHelper": "cat /secret"}, "json"),
    ("settings.json", {"env": {"ANTHROPIC_API_KEY": "secret"}}, "json"),
    ("settings.json", {"model": "other-provider"}, "json"),
    ("settings.json", {"hooks": {}}, "json"),
    ("CLAUDE.md", "{{provider_api_key}}", "text"),
    ("CLAUDE.md", {"from_secret_ref": "key"}, "text"),
    ("rules/{{workspace_path}}.md", "unsafe", "text"),
])
def test_untranslated_authority_and_paths_stay_native(path, content, format):
    config = configuration()
    config["fileTemplates"] = [{"path": "{{runtime_support_dir}}/claude-home/" + path, "format": format, "content_template": content}]
    with pytest.raises(ValueError):
        validate_configuration(config)
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(
        home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"],
    ))
    assert endpoint is None and reason


def test_configuration_snapshot_preserves_admitted_files_after_profile_edit():
    config = configuration()
    profile = _api_key_profile(home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"])
    endpoint, reason = claude_provider_config_endpoint(profile)
    assert endpoint is not None, reason
    binding = SimpleNamespace(providerProfileRef=profile.profile_id, materializerRef="omnigent-provider-config@1")
    bindings = {"primary-model": binding}
    content = provider_configuration_snapshot_bytes(bindings, {"primary-model": endpoint})
    profile.file_templates[0]["content_template"] = {"outputStyle": "changed"}
    saved = load_provider_configuration_snapshot(content, SimpleNamespace(contentDigest="sha256:" + hashlib.sha256(content).hexdigest()), bindings)
    assert saved["primary-model"].claudeConfiguration["fileTemplates"][0]["content_template"] == {"outputStyle": "concise"}


@pytest.mark.asyncio
async def test_authored_files_provision_without_extra_secret_resolution(tmp_path):
    config = configuration()
    handles, _, backend, _, resolver = await _provision(tmp_path, profile_values={
        "credential_source": "secret_ref", "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        "env_template": {"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        "home_path_overrides": config["homePathOverrides"], "file_templates": config["fileTemplates"],
    }, materializer_ref="omnigent-provider-config@1")
    assert resolver.resolved == ["ANTHROPIC_API_KEY"]
    assert handles[0].runtimeEnvironment["MOONMIND_CLAUDE_PROFILE_CONFIG_FILE"] == "/home/app/.moonmind-provider-config/claude-profile.json"
    body, = [data for _, data in backend.calls if data]
    assert json.loads(body)["_moonmind_claude_configuration"] == validate_configuration(config)


def test_actual_host_script_and_native_wrapper_preserve_files_and_auth_args(tmp_path):
    import os
    import subprocess
    import sys
    from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService

    home, skills, binaries = (tmp_path / name for name in ("home", "skills", "bin"))
    for directory in (home, skills, binaries):
        directory.mkdir()
    projection = home / ".moonmind-provider-config"
    projection.mkdir()
    (projection / "claude-profile.json").write_text(json.dumps(configuration()))
    (projection / ".moonmind-generation").write_text("3\n")
    (binaries / "python3").symlink_to(sys.executable)
    native = binaries / "claude"
    native.write_text(f"#!{sys.executable}\nimport os,json,pathlib,sys\nhome=pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'])\nprint(json.dumps({{ 'settings':json.loads((home/'settings.json').read_text()), 'instructions':(home/'CLAUDE.md').read_text(),'args':sys.argv[1:]}}))\n")
    native.chmod(0o700)
    omnigent = binaries / "omnigent"
    omnigent.write_text(f"#!{sys.executable}\nimport os,subprocess\nsubprocess.run([{str(home / '.omnigent/moonmind/bin/claude')!r},'--settings','/private/authority.json','--model','admitted-model'],check=True,env={{'PATH':os.environ['PATH'],'HOME':'/native-home'}})\n")
    omnigent.chmod(0o700)
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[{"credentialGeneration": 3, "materializerRef": "omnigent-provider-config@1",
            "attachments": [{"targetPath": "/home/app/.moonmind-provider-config"}],
            "runtimeEnvironment": {"MOONMIND_CLAUDE_PROFILE_CONFIG_FILE": "/home/app/.moonmind-provider-config/claude-profile.json", "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"}}],
        skill_attachment={"targetPath": str(skills)}, step_execution_id="workflow:run:work:1",
        workspace_path="/work/admitted", enable_claude_runtime=True,
    )
    environment = {key: value.replace("/home/app", str(home)) for key,value in environment.items()}
    for _ in range(2):
        result = subprocess.run(["/bin/sh", "-ceu", script.replace("/home/app", str(home)), "--", "http://omnigent.test"],
            env={**os.environ, **environment, "PATH": f"{binaries}:/usr/bin:/bin"}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "settings": {"outputStyle": "concise", "alwaysThinkingEnabled": True},
            "instructions": f"Work in /work/admitted; support {home}/.omnigent/moonmind/runtime-context",
            "args": ["--settings", "/private/authority.json", "--model", "admitted-model"],
        }


def test_existing_sibling_home_cannot_be_overwritten(tmp_path):
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    sentinel = sibling / "settings.json"
    sentinel.write_text("retained")
    support = tmp_path / "attempt"
    support.mkdir()
    (support / "claude-home").symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ValueError, match="already owned"):
        materialize_configuration(configuration(), runtime_support_dir=support, workspace_path="/work")
    assert sentinel.read_text() == "retained"


def test_queued_configuration_without_files_keeps_its_snapshot_shape():
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile())
    assert endpoint is not None, reason
    binding = SimpleNamespace(providerProfileRef="claude_anthropic_api", materializerRef="omnigent-provider-config@1")
    bindings = {"primary-model": binding}
    content = provider_configuration_snapshot_bytes(bindings, {"primary-model": endpoint})
    assert b"claudeConfiguration" not in content
    saved = load_provider_configuration_snapshot(content, SimpleNamespace(contentDigest="sha256:" + hashlib.sha256(content).hexdigest()), bindings)
    assert saved["primary-model"].claudeConfiguration is None
    assert provider_configuration_snapshot_bytes(bindings, saved) == content
