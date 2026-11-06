"""Installed resource bundles feed the existing backend and runtime settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from moonmind.config import runtime_resources
from moonmind.config.runtime_resources import (
    RUNTIME_RESOURCES_FILE_ENV_KEY,
    RuntimeResourcesConfigError,
    load_runtime_resources,
)


def _install(tmp_path: Path, payload: object) -> dict[str, str]:
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {RUNTIME_RESOURCES_FILE_ENV_KEY: str(path)}


def test_unconfigured_resources_leave_current_defaults_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime_resources,
        "DEFAULT_RUNTIME_RESOURCES_FILE",
        tmp_path / "not-installed.json",
    )
    resources = load_runtime_resources({})
    assert resources.path is None
    assert resources.image_sources == ()
    assert resources.cache_sources == ()
    assert resources.service_connections == ()
    assert resources.environment == {}
    assert load_runtime_resources({RUNTIME_RESOURCES_FILE_ENV_KEY: "  "}) == resources


def test_standard_installed_location_needs_no_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "resources.json"
    _install(
        tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "preparation"}}
    )
    monkeypatch.setattr(runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", installed)
    assert load_runtime_resources({}).environment == {"PROJECT_SOURCE": "preparation"}


def test_explicit_file_replaces_standard_installed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standard = tmp_path / "standard.json"
    standard.write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", standard)
    env = _install(
        tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "explicit"}}
    )
    assert load_runtime_resources(env).environment == {"PROJECT_SOURCE": "explicit"}


def test_invalid_standard_installation_does_not_silently_drop_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "resources.json"
    installed.write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", installed)
    with pytest.raises(RuntimeResourcesConfigError):
        load_runtime_resources({})


def test_process_resources_do_not_switch_under_running_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _install(
        tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "original"}}
    )
    monkeypatch.setenv(
        RUNTIME_RESOURCES_FILE_ENV_KEY, env[RUNTIME_RESOURCES_FILE_ENV_KEY]
    )
    monkeypatch.delenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", raising=False)
    assert load_runtime_resources().environment == {"PROJECT_SOURCE": "original"}
    _install(tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "new"}})

    assert load_runtime_resources().environment == {"PROJECT_SOURCE": "original"}
    assert load_runtime_resources(os.environ).environment == {
        "PROJECT_SOURCE": "original"
    }
    assert load_runtime_resources(env).environment == {"PROJECT_SOURCE": "new"}


def test_absent_default_remains_one_process_snapshot_until_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(RUNTIME_RESOURCES_FILE_ENV_KEY, raising=False)
    monkeypatch.delenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", raising=False)
    monkeypatch.setattr(
        runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", tmp_path / "resources.json"
    )
    assert load_runtime_resources().environment == {}
    _install(tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "new"}})

    assert load_runtime_resources(os.environ).environment == {}
    assert load_runtime_resources({}).environment == {"PROJECT_SOURCE": "new"}


def test_activation_selects_retained_package_even_before_first_process_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_digest = "a" * 64
    retained = tmp_path / "packages" / original_digest
    retained.mkdir(parents=True)
    _install(
        retained, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "original"}}
    )
    _install(tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "new"}})
    monkeypatch.setattr(
        runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", tmp_path / "resources.json"
    )
    monkeypatch.delenv(RUNTIME_RESOURCES_FILE_ENV_KEY, raising=False)
    monkeypatch.setenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", original_digest)

    resources = load_runtime_resources()

    assert resources.path == retained / "resources.json"
    assert resources.environment == {"PROJECT_SOURCE": "original"}


def test_new_activation_reads_replacement_package_without_mutable_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_digest, replacement_digest = "a" * 64, "b" * 64
    for digest, value in ((original_digest, "original"), (replacement_digest, "new")):
        retained = tmp_path / "packages" / digest
        retained.mkdir(parents=True)
        _install(
            retained, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": value}}
        )
    monkeypatch.setattr(
        runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", tmp_path / "resources.json"
    )
    monkeypatch.delenv(RUNTIME_RESOURCES_FILE_ENV_KEY, raising=False)
    monkeypatch.setenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", original_digest)

    assert load_runtime_resources().environment == {"PROJECT_SOURCE": "original"}
    replacement = load_runtime_resources(
        {"MOONMIND_RUNTIME_RESOURCES_DIGEST": replacement_digest}
    )

    assert replacement.environment == {"PROJECT_SOURCE": "new"}
    assert (
        replacement.path
        == tmp_path / "packages" / replacement_digest / "resources.json"
    )
    assert load_runtime_resources(os.environ).environment == {
        "PROJECT_SOURCE": "original"
    }


@pytest.mark.parametrize("digest", ["../private-data", "g" * 64, "a" * 63])
def test_invalid_activation_identity_fails_without_echoing_it(digest: str) -> None:
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources({"MOONMIND_RUNTIME_RESOURCES_DIGEST": digest})
    assert digest not in str(raised.value)


def test_missing_activated_package_is_not_an_absent_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", tmp_path / "resources.json"
    )
    with pytest.raises(RuntimeResourcesConfigError):
        load_runtime_resources({"MOONMIND_RUNTIME_RESOURCES_DIGEST": "a" * 64})


def test_explicit_file_override_wins_over_default_activation(
    tmp_path: Path,
) -> None:
    env = _install(
        tmp_path, {"schemaVersion": 1, "environment": {"PROJECT_SOURCE": "explicit"}}
    )
    assert load_runtime_resources(
        {**env, "MOONMIND_RUNTIME_RESOURCES_DIGEST": "a" * 64}
    ).environment == {"PROJECT_SOURCE": "explicit"}


def test_installed_resources_keep_existing_declarations_and_safe_environment(
    tmp_path: Path,
) -> None:
    image = {
        "sourceRef": "content-preparation",
        "image": "project-preparation:local",
        "build": {"context": "tooling", "dockerfile": "Dockerfile"},
    }
    cache = {
        "cacheRef": "project-objects",
        "volumeName": "project_objects",
        "target": "/cache/objects",
    }
    service = {
        "serviceRef": "project-service",
        "allowedImageSourceRefs": ["content-preparation"],
    }
    environment = {
        "PROJECT_PREPARATION_SOURCE": "content-preparation",
        "PROJECT_SERVER_URL": "tcp://project-server:1234",
    }
    env = _install(
        tmp_path,
        {
            "schemaVersion": 1,
            "imageSources": [image],
            "cacheSources": [cache],
            "serviceConnections": [service],
            "environment": environment,
        },
    )

    resources = load_runtime_resources(env)

    assert resources.path == (tmp_path / "resources.json").resolve()
    assert resources.image_sources == (image,)
    assert resources.cache_sources == (cache,)
    assert resources.service_connections == (service,)
    assert resources.environment == environment


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {},
        {"schemaVersion": True},
        {"schemaVersion": "1"},
        {"schemaVersion": 2},
        {"schemaVersion": 1, "unexpected": "private-data"},
    ],
)
def test_invalid_bundle_envelopes_fail_without_echoing_contents(
    tmp_path: Path, payload: object
) -> None:
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources(_install(tmp_path, payload))
    assert "private-data" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "ref"),
    [
        ("imageSources", "sourceRef"),
        ("cacheSources", "cacheRef"),
        ("serviceConnections", "serviceRef"),
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "private-data",
        [None],
        [{}],
        [{"OTHER": "private-data"}],
        [{"REF": 3}],
        [{"REF": ""}],
        [{"REF": "dup"}, {"REF": "dup"}],
    ],
)
def test_resource_lists_require_objects_and_unique_nonempty_refs(
    tmp_path: Path, field: str, ref: str, value: object
) -> None:
    if isinstance(value, list):
        value = [
            (
                {ref if key == "REF" else key: item for key, item in row.items()}
                if isinstance(row, dict)
                else row
            )
            for row in value
        ]
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources(_install(tmp_path, {"schemaVersion": 1, field: value}))
    assert "private-data" not in str(raised.value)


@pytest.mark.parametrize(
    "environment",
    [
        [],
        None,
        {"PROJECT_SERVER": 3},
        {"PATH": "/private-data"},
        {"PROJECT_TOKEN": "private-data"},
        {"PROJECT_URL": "https://name:private-data@example.invalid"},
        {"PROJECT_URL": "https://example.invalid?key=private-data"},
        {"PROJECT_SETTING": "line\nprivate-data"},
        {"PROJECT_SETTING": ""},
    ],
)
def test_unsafe_environment_invalidates_bundle_instead_of_partial_projection(
    tmp_path: Path, environment: object
) -> None:
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources(
            _install(tmp_path, {"schemaVersion": 1, "environment": environment})
        )
    assert "private-data" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "limit", "ref"),
    [
        ("imageSources", 128, "sourceRef"),
        ("cacheSources", 128, "cacheRef"),
        ("serviceConnections", 32, "serviceRef"),
    ],
)
def test_resource_list_size_is_bounded(
    tmp_path: Path, field: str, limit: int, ref: str
) -> None:
    rows = [{ref: f"item-{index}"} for index in range(limit + 1)]
    with pytest.raises(RuntimeResourcesConfigError):
        load_runtime_resources(_install(tmp_path, {"schemaVersion": 1, field: rows}))


def test_environment_size_is_bounded(tmp_path: Path) -> None:
    entries = {f"PROJECT_SETTING_{index}": "value" for index in range(129)}
    with pytest.raises(RuntimeResourcesConfigError):
        load_runtime_resources(
            _install(tmp_path, {"schemaVersion": 1, "environment": entries})
        )


def test_configured_missing_bundle_is_an_error_without_host_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources(
            {RUNTIME_RESOURCES_FILE_ENV_KEY: str(tmp_path / "private-data.json")}
        )
    assert "private-data" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json-private-data",
        b'{"schemaVersion":1,"schemaVersion":2}',
        b'{"schemaVersion":1,"environment":{"PROJECT_SETTING":"one","PROJECT_SETTING":"two"}}',
        b" " * (1024 * 1024 + 1),
    ],
    ids=["invalid-json", "duplicate-version", "duplicate-environment-key", "oversized"],
)
def test_unreadable_or_ambiguous_bundle_is_refused(tmp_path: Path, body: bytes) -> None:
    path = tmp_path / "resources.json"
    path.write_bytes(body)
    with pytest.raises(RuntimeResourcesConfigError) as raised:
        load_runtime_resources({RUNTIME_RESOURCES_FILE_ENV_KEY: str(path)})
    assert "private-data" not in str(raised.value)
