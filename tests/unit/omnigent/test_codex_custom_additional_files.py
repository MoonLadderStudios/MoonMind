"""Custom enrollment files have a frozen, replacement-owned starting point."""

from copy import deepcopy
import json

import pytest

from moonmind.provider_profiles.codex_configuration import (
    configure_profile_files,
    oauth_configuration_for_profile,
)
from tests.unit.omnigent.test_codex_enrollment_layout import profile


def file(path, content, strategy="replace", format="text"):
    return {
        "path": path,
        "format": format,
        "content_template": content,
        "merge_strategy": strategy,
        "permissions": "0600",
    }


def selected(files):
    value = profile()
    value.file_templates = [
        file(value.volume_mount_path + "/" + path, content, strategy, format)
        for path, content, strategy, format in files
    ]
    return value


def test_replacement_seed_preserves_order_and_normalized_destination():
    value = selected(
        [
            ("rules//guide.md", "first", "replace", "text"),
            ("rules/./guide.md", "second", "append", "text"),
            ("settings.json", {"keep": 1}, "replace", "json"),
            ("settings.json", {"add": 2}, "deep_merge", "json"),
        ]
    )
    before = deepcopy(vars(value))
    configuration, reason = oauth_configuration_for_profile(value)
    assert configuration is not None, reason
    assert [item["merge_strategy"] for item in configuration["additionalFiles"]] == [
        "replace",
        "append",
        "replace",
        "deep_merge",
    ]
    assert vars(value) == before


@pytest.mark.parametrize(
    "files",
    [
        [("AGENTS.md", "more", "append", "text")],
        [("settings.json", {"more": 1}, "deep_merge", "json")],
        [("{{workspace_path}}.md", "text", "replace", "text")],
        [
            ("rules", "text", "replace", "text"),
            ("rules/a.md", "text", "replace", "text"),
        ],
        [
            ("rules/a.md", "text", "replace", "text"),
            ("rules", "text", "replace", "text"),
        ],
        [("../outside", "text", "replace", "text")],
        [("auth.json", "text", "replace", "text")],
        [("hooks.json", "text", "replace", "text")],
    ],
)
def test_unowned_or_ambiguous_seeds_remain_native(files):
    configuration, reason = oauth_configuration_for_profile(selected(files))
    assert configuration is None
    assert reason


def test_selected_seed_detaches_privately_and_restart_does_not_append_twice(
    tmp_path, monkeypatch
):
    value = selected(
        [
            ("AGENTS.md", "frozen", "replace", "text"),
            ("AGENTS.md", "addition", "append", "text"),
            ("rules/options.json", {"frozen": 1}, "replace", "json"),
            ("rules/options.json", {"extra": 2}, "deep_merge", "json"),
        ]
    )
    configuration, reason = oauth_configuration_for_profile(value)
    assert configuration is not None, reason
    enrolled = tmp_path / "selected"
    enrolled.mkdir()
    (enrolled / "auth.json").write_text("selected-auth")
    (enrolled / "AGENTS.md").write_text("mutable source")
    (enrolled / "rules").mkdir()
    (enrolled / "rules/options.json").write_text('{"ambient":true}')
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "auth.json").write_text("sibling-auth")
    # Translate the validated container coordinate into this hermetic filesystem.
    from moonmind.provider_profiles import codex_configuration

    validate = codex_configuration._validated_enrolled_home

    def local_enrollment(path):
        assert validate(path) == value.volume_mount_path
        return str(enrolled)

    monkeypatch.setattr(
        codex_configuration, "_validated_enrolled_home", local_enrollment
    )
    for attempt in ("first", "cold-successor"):
        home = tmp_path / attempt
        home.mkdir()
        for name in ("auth.json", "AGENTS.md", "rules"):
            (home / name).symlink_to(enrolled / name)
        configure_profile_files(
            configuration, home, tmp_path, enrolled_home=value.volume_mount_path
        )
        configure_profile_files(
            configuration, home, tmp_path, enrolled_home=value.volume_mount_path
        )
        assert (home / "AGENTS.md").read_text() == "frozen\naddition"
        assert json.loads((home / "rules/options.json").read_text()) == {
            "frozen": 1,
            "extra": 2,
        }
        assert not (home / "AGENTS.md").is_symlink()
        assert (home / "AGENTS.md").stat().st_mode & 0o777 == 0o600
        assert (home / "auth.json").is_symlink()
        assert (home / "auth.json").read_text() == "selected-auth"
        (enrolled / "AGENTS.md").write_text("changed since admission")
    assert (enrolled / "rules/options.json").read_text() == '{"ambient":true}'
    assert (sibling / "auth.json").read_text() == "sibling-auth"
