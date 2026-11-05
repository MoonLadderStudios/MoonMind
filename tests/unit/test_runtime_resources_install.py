"""Installing a reusable resource package never discards previous inputs."""

import json

import pytest
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.config.runtime_resources_install import _package_digest


def package(tmp_path, contents="first"):
    root = tmp_path / "source"
    root.mkdir(exist_ok=True)
    (root / "Dockerfile").write_text("FROM scratch\nCOPY payload /payload\n")
    (root / "payload").write_text(contents)
    (root / "resources.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "imageSources": [
                    {
                        "sourceRef": "asset-preparer",
                        "image": "asset-preparer:local",
                        "build": {
                            "context": ".",
                            "dockerfile": "Dockerfile",
                            "validationCommand": ["true"],
                        },
                    }
                ],
                "environment": {"ASSET_PREPARATION_IMAGE": "asset-preparer"},
            }
        )
    )
    return root


def activated_manifest(deployment):
    root = deployment / "deploy/runtime-resources"
    identity = (root / "activation.env").read_text().strip().split("=", 1)[1]
    assert len(identity) == 64
    manifest = root / "packages" / identity / "resources.json"
    assert manifest.is_file()
    return manifest


def test_install_and_update_are_self_contained_and_retain_saved_contexts(tmp_path):
    source = package(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    runner = CliRunner()
    arguments = [
        "runtime-resources",
        "install",
        str(source),
        "--project-dir",
        str(deployment),
    ]
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    installed = activated_manifest(deployment)
    first = resolve_container_backend_settings(
        {"MOONMIND_RUNTIME_RESOURCES_FILE": str(installed)}
    ).image_source("asset-preparer")
    assert (first.context_root / "payload").read_text() == "first"
    assert first.context_root != source
    source.joinpath("payload").write_text("second")
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    second = resolve_container_backend_settings(
        {"MOONMIND_RUNTIME_RESOURCES_FILE": str(activated_manifest(deployment))}
    ).image_source("asset-preparer")
    assert second.context_root != first.context_root
    assert (second.context_root / "payload").read_text() == "second"
    assert (first.context_root / "payload").read_text() == "first"
    assert runner.invoke(app, arguments).exit_code == 0
    assert (
        len(list(deployment.joinpath("deploy/runtime-resources/packages").iterdir()))
        == 2
    )


def test_invalid_install_preserves_current_resources(tmp_path):
    source = package(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    arguments = [
        "runtime-resources",
        "install",
        str(source),
        "--project-dir",
        str(deployment),
    ]
    runner = CliRunner()
    assert runner.invoke(app, arguments).exit_code == 0
    installed = activated_manifest(deployment)
    activation = deployment.joinpath(
        "deploy/runtime-resources/activation.env"
    ).read_bytes()
    before = installed.read_bytes()
    source.joinpath("resources.json").write_text(
        json.dumps({"schemaVersion": 1, "environment": {"API_TOKEN": "private"}})
    )
    result = runner.invoke(app, arguments)
    assert result.exit_code != 0
    assert "private" not in result.output
    assert installed.read_bytes() == before
    assert (
        deployment.joinpath("deploy/runtime-resources/activation.env").read_bytes()
        == activation
    )


@pytest.mark.parametrize("origin", ["deployment-env", "environment"])
def test_install_uses_the_deployments_resource_directory(tmp_path, monkeypatch, origin):
    source = package(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    setting = "MOONMIND_RUNTIME_RESOURCES_DIRECTORY"
    monkeypatch.delenv(setting, raising=False)
    if origin == "deployment-env":
        deployment.joinpath(".env").write_text(f'{setting}="./custom resources"\n')
    else:
        monkeypatch.setenv(setting, "./custom resources")
    result = CliRunner().invoke(
        app,
        ["runtime-resources", "install", str(source), "--project-dir", str(deployment)],
    )
    assert result.exit_code == 0, result.output
    assert deployment.joinpath("custom resources/activation.env").is_file()
    assert not deployment.joinpath("deploy/runtime-resources").exists()


def test_package_identity_distinguishes_file_boundaries_and_empty_directories(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    two.joinpath("a").write_bytes(b"")
    two.joinpath("b").write_bytes(b"payload")
    mode = str(two.joinpath("b").stat().st_mode & 0o777).encode()
    one.joinpath("a").write_bytes(b"b\0" + mode + b"\0payload")
    assert _package_digest(one) != _package_digest(two)
    before = _package_digest(two)
    two.joinpath("empty").mkdir()
    assert _package_digest(two) != before


def test_install_stages_only_immutable_package_until_controller_activation(
    tmp_path, monkeypatch
):
    from moonmind.config import runtime_resources

    source = package(tmp_path)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    result = CliRunner().invoke(
        app,
        ["runtime-resources", "install", str(source), "--project-dir", str(deployment)],
    )
    assert result.exit_code == 0, result.output
    root = deployment / "deploy/runtime-resources"
    assert not (root / "resources.json").exists()
    identity = (root / "activation.env").read_text().strip().split("=", 1)[1]
    assert (root / "packages" / identity / "resources.json").is_file()
    monkeypatch.setattr(
        runtime_resources, "DEFAULT_RUNTIME_RESOURCES_FILE", root / "resources.json"
    )
    monkeypatch.delenv("MOONMIND_RUNTIME_RESOURCES_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", raising=False)
    assert runtime_resources.load_runtime_resources().path is None
    monkeypatch.setenv("MOONMIND_RUNTIME_RESOURCES_DIGEST", identity)
    assert runtime_resources.load_runtime_resources().environment == {
        "ASSET_PREPARATION_IMAGE": "asset-preparer"
    }
