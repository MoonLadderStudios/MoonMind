import pytest

from moonmind.provider_profiles import codex_configuration as config


def test_seed_bytes_are_not_templates_and_retry_is_exact_once(tmp_path, monkeypatch):
    enrolled = tmp_path / 'enrolled'
    enrolled.mkdir()
    (enrolled / 'AGENTS.md').write_text('mutable source')
    home = tmp_path / 'private'
    home.mkdir()
    (home / 'AGENTS.md').symlink_to(enrolled / 'AGENTS.md')
    monkeypatch.setattr(config, '_ENROLLED_CODEX_HOME', str(enrolled))
    document = {
        'document': {}, 'privateHomeOnly': True,
        'additionalFiles': [{'path': '{{runtime_support_dir}}/AGENTS.md',
            'format': 'text', 'content_template': 'addition',
            'merge_strategy': 'append', 'permissions': '0600'}],
        'fileSeeds': {'version': 1, 'files': [{'path': 'AGENTS.md',
            'present': True, 'content': 'Literal {{unbound}} seed'}]},
    }
    admitted = config.validate_oauth_configuration(document)
    for _ in range(2):
        config.configure_profile_files(admitted, home, tmp_path, enrolled_home=str(enrolled))
    assert (home / 'AGENTS.md').read_text() == 'Literal {{unbound}} seed\naddition'
    assert (enrolled / 'AGENTS.md').read_text() == 'mutable source'


def test_missing_seed_removes_live_enrollment_value_before_append(tmp_path, monkeypatch):
    enrolled = tmp_path / "enrolled"
    enrolled.mkdir()
    (enrolled / "AGENTS.md").write_text("late unadmitted bytes")
    home = tmp_path / "private"
    home.mkdir()
    (home / "AGENTS.md").symlink_to(enrolled / "AGENTS.md")
    monkeypatch.setattr(config, "_ENROLLED_CODEX_HOME", str(enrolled))
    document = {
        "document": {}, "privateHomeOnly": True,
        "additionalFiles": [{"path": "{{runtime_support_dir}}/AGENTS.md",
            "format": "text", "content_template": "only admitted addition",
            "merge_strategy": "append", "permissions": "0600"}],
        "fileSeeds": {"version": 1, "files": [{"path": "AGENTS.md", "present": False}]},
    }
    config.configure_profile_files(document, home, tmp_path, enrolled_home=str(enrolled))
    assert (home / "AGENTS.md").read_text() == "only admitted addition"
    assert (enrolled / "AGENTS.md").read_text() == "late unadmitted bytes"
    document["fileSeeds"]["files"][0] = {"path": "AGENTS.md", "present": True, "content": "different"}
    with pytest.raises(ValueError, match="conflicts"):
        config.configure_profile_files(document, home, tmp_path, enrolled_home=str(enrolled))


@pytest.mark.parametrize("files", [[], [{"path": "auth.json", "present": False}],
    [{"path": "AGENTS.md", "present": False}, {"path": "AGENTS.md", "present": False}]])
def test_seed_set_must_exactly_match_first_nonreplacement(files):
    with pytest.raises(ValueError):
        config.validate_oauth_configuration({
            "document": {}, "privateHomeOnly": True,
            "additionalFiles": [{"path": "{{runtime_support_dir}}/AGENTS.md",
                "format": "text", "content_template": "addition",
                "merge_strategy": "append", "permissions": "0600"}],
            "fileSeeds": {"version": 1, "files": files},
        })


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt_available', [False, True])
async def test_custom_seed_materializer_requires_receipt_before_delivery(receipt_available):
    import json
    from dataclasses import replace
    from moonmind.omnigent.credential_materializers import DockerCodexOauthHomeMaterializer
    from moonmind.omnigent.harness_platform.materializers import ProviderLaunchEnvironment
    from tests.unit.omnigent.test_oauth_home_materializers import FakeDockerBackend, _context, _home

    backend = FakeDockerBackend()
    target = '/home/app/selected-codex-auth'
    configuration = {'document': {}, 'privateHomeOnly': True, 'additionalFiles': [
        {'path': '{{runtime_support_dir}}/AGENTS.md', 'format': 'text',
         'content_template': 'append', 'merge_strategy': 'append', 'permissions': '0600'}]}
    launch = ProviderLaunchEnvironment(codexConfiguration=configuration,
        codexEnrollmentHome={'volumeRef': 'codex_auth_volume', 'targetPath': target, 'credentialGeneration': 4})
    calls = []

    async def retained(**kwargs):
        calls.append(kwargs)
        # An accepted receipt does not invoke the reader after source disappearance.
        return {'version': 1, 'files': [{'path': 'AGENTS.md', 'present': True, 'content': 'frozen'}]}

    context = replace(_context(backend, home=_home(target=target)),
        provider_launch_environment=launch,
        codex_file_seed_receipt=retained if receipt_available else None)
    materializer = DockerCodexOauthHomeMaterializer(backend)
    if not receipt_available:
        with pytest.raises(Exception, match='durable receipt authority'):
            await materializer.materialize(context)
        return
    handle = await materializer.materialize(context)
    assert len(calls) == 1
    assert calls[0]['selected_home'].target_path == target
    runtime = json.loads(handle.runtimeEnvironment['MOONMIND_CODEX_PROFILE_CONFIGURATION'])
    assert runtime['fileSeeds']['files'][0]['content'] == 'frozen'
    assert 'fileSeeds' not in launch.codexConfiguration
    assert 'fileSeeds' not in configuration


def test_deep_merge_seed_is_raw_json_and_new_home_ignores_changed_seed(tmp_path, monkeypatch):
    import json
    enrolled = tmp_path / 'enrolled'
    (enrolled / 'rules').mkdir(parents=True)
    source = enrolled / 'rules/preferences.json'
    source.write_text('{"live":"old"}')
    monkeypatch.setattr(config, '_ENROLLED_CODEX_HOME', str(enrolled))
    document = config.validate_oauth_configuration({
        'document': {}, 'privateHomeOnly': True,
        'additionalFiles': [{'path': '{{runtime_support_dir}}/rules/preferences.json',
            'format': 'json', 'content_template': {'nested': {'added': True}},
            'merge_strategy': 'deep_merge', 'permissions': '0600'}],
        'fileSeeds': {'version': 1, 'files': [{'path': 'rules/preferences.json',
            'present': True, 'content': '{"literal":"{{not_a_template}}","nested":{"seed":1}}'}]},
    })
    for attempt in ('first', 'cold'):
        source.write_text('{"live":"' + attempt + '"}')
        home = tmp_path / attempt
        home.mkdir()
        (home / 'rules').symlink_to(enrolled / 'rules', target_is_directory=True)
        config.configure_profile_files(document, home, tmp_path, enrolled_home=str(enrolled))
        assert json.loads((home / 'rules/preferences.json').read_text()) == {
            'literal': '{{not_a_template}}', 'nested': {'seed': 1, 'added': True}}
        assert source.read_text() == '{"live":"' + attempt + '"}'


@pytest.mark.parametrize("mutation", ["removed", "outside-link"])
def test_frozen_seed_never_reads_replaced_source(tmp_path, monkeypatch, mutation):
    enrolled = tmp_path / "enrolled"
    enrolled.mkdir()
    source = enrolled / "AGENTS.md"
    source.write_text("old")
    home = tmp_path / "home"
    home.mkdir()
    (home / "AGENTS.md").symlink_to(source)
    source.unlink()
    if mutation == "outside-link":
        source.symlink_to(tmp_path / "unrelated-missing-file")
    monkeypatch.setattr(config, "_ENROLLED_CODEX_HOME", str(enrolled))
    configuration = {"privateHomeOnly": True, "document": {}, "additionalFiles": [
        {"path": "{{runtime_support_dir}}/AGENTS.md", "format": "text",
         "content_template": "append", "merge_strategy": "append", "permissions": "0600"}],
        "fileSeeds": {"version": 1, "files": [{"path": "AGENTS.md", "present": True, "content": "frozen"}]}}
    config.configure_profile_files(configuration, home, tmp_path, enrolled_home=str(enrolled))
    assert (home / "AGENTS.md").read_text() == "frozen\nappend"
    assert not (tmp_path / "unrelated-missing-file").exists()


def test_admitted_capture_requirement_cannot_launch_without_seed_receipt(tmp_path, monkeypatch):
    enrolled = tmp_path / 'source'
    enrolled.mkdir()
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(config, '_ENROLLED_CODEX_HOME', str(enrolled))
    document = config.validate_oauth_configuration({'document': {}, 'privateHomeOnly': True,
        'fileSeedPolicy': 'capture-once-v1', 'additionalFiles': [
            {'path': '{{runtime_support_dir}}/AGENTS.md', 'format': 'text',
             'content_template': 'append', 'merge_strategy': 'append', 'permissions': '0600'}]})
    with pytest.raises(ValueError, match='frozen materialization receipt'):
        config.configure_profile_files(document, home, tmp_path, enrolled_home=str(enrolled))
    assert not (home / 'AGENTS.md').exists()
