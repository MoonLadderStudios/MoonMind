"""Candidate resolution for the singular Omnigent release must use working helpers.

Regression: ``_default_resolve_candidates`` imported
``resolve_bootstrap_image_ref`` from
``moonmind.omnigent.bootstrap.image_resolution``, which never defined it, so
every Omnigent release migration died with ImportError after an otherwise good
fleet update.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
RELEASE_PATH = ROOT / "moonmind/workflows/skills/omnigent_release.py"

UPSTREAM_SERVER = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
UPSTREAM_HOST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "2" * 64
LIVE_SERVER = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
LIVE_HOST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "4" * 64


def _load_release_module(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "omnigent_release_under_test", RELEASE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module via sys.modules at class creation.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release_module(monkeypatch):
    """Load omnigent_release with stubbed resolver modules.

    The image_resolution stub mirrors reality: it provides
    ``resolve_omnigent_images`` but deliberately not
    ``resolve_bootstrap_image_ref``.
    """
    calls = []

    async def resolve_omnigent_images(env):
        return SimpleNamespace(
            server_image_ref=LIVE_SERVER,
            opencode_host_image_ref=LIVE_HOST,
            shared_host_image_ref=LIVE_HOST,
            pi_host_image_ref=LIVE_HOST,
        )

    def configured_bootstrap_image_refs(env):
        return ("omnigent-server:1.2.3", "omnigent-host:1.2.3")

    async def resolve_bootstrap_image_ref(image_ref):
        calls.append(image_ref)
        return UPSTREAM_SERVER if "server" in image_ref else UPSTREAM_HOST

    for name in (
        "moonmind",
        "moonmind.omnigent",
        "moonmind.omnigent.bootstrap",
        "api_service",
        "api_service.services",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)

    resolution = types.ModuleType("moonmind.omnigent.bootstrap.image_resolution")
    resolution.resolve_omnigent_images = resolve_omnigent_images
    monkeypatch.setitem(
        sys.modules, "moonmind.omnigent.bootstrap.image_resolution", resolution
    )

    policies = types.ModuleType("api_service.services.omnigent_policies")
    policies.configured_bootstrap_image_refs = configured_bootstrap_image_refs
    policies.resolve_bootstrap_image_ref = resolve_bootstrap_image_ref
    monkeypatch.setitem(
        sys.modules, "api_service.services.omnigent_policies", policies
    )

    module = _load_release_module(monkeypatch)
    module._resolver_calls = calls
    return module


def test_resolve_candidates_prefers_upstream_refs(release_module):
    candidates = asyncio.run(release_module._default_resolve_candidates({}))
    assert candidates == {
        "server": UPSTREAM_SERVER,
        "codex": UPSTREAM_HOST,
        "opencode": LIVE_HOST,
        "shared": LIVE_HOST,
        "pi": LIVE_HOST,
    }
    assert release_module._resolver_calls == [
        "omnigent-server:1.2.3",
        "omnigent-host:1.2.3",
    ]


def test_deployment_inputs_preserve_explicit_operator_image_pins(
    release_module, monkeypatch, tmp_path
):
    # Generated refs in a running worker are from .env.deploy and must not
    # freeze the next update. Only an explicit operator .env pin is retained.
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", LIVE_SERVER)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", LIVE_HOST)
    operator_env = tmp_path / ".env"
    operator_env.write_text(
        f'OMNIGENT_OPENCODE_HOST_IMAGE_REF="{UPSTREAM_HOST}"\n'
        'OMNIGENT_SHARED_HOST_IMAGE_TAG="latest"\n',
        encoding="utf-8",
    )
    inputs = asyncio.run(release_module._default_deployment_inputs(operator_env))
    assert "OMNIGENT_IMAGE_REF" not in inputs
    assert inputs["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] == UPSTREAM_HOST
    assert inputs["OMNIGENT_SHARED_HOST_IMAGE_TAG"] == "latest"


def test_legacy_template_host_tag_advances_with_normal_update(
    release_module, monkeypatch, tmp_path
):
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", LIVE_HOST)
    operator_env = tmp_path / ".env"
    operator_env.write_text(
        'OMNIGENT_SHARED_HOST_IMAGE="ghcr.io/moonladderstudios/omnigent-host-moonmind"\n'
        'OMNIGENT_SHARED_HOST_IMAGE_TAG="1.18.11"\n',
        encoding="utf-8",
    )
    inputs = asyncio.run(release_module._default_deployment_inputs(operator_env))
    assert inputs["OMNIGENT_SHARED_HOST_IMAGE_TAG"] == "latest"
    assert "OMNIGENT_SHARED_HOST_IMAGE_REF" not in inputs


def test_production_update_reads_operator_pin_not_generated_overlay(
    release_module, monkeypatch, tmp_path
):
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", LIVE_HOST)
    (tmp_path / ".env").write_text(
        f'OMNIGENT_SHARED_HOST_IMAGE_REF="{UPSTREAM_HOST}"\n',
        encoding="utf-8",
    )
    drivers = release_module.production_drivers(
        runner=SimpleNamespace(local_project_dir=str(tmp_path)),
        moonmind_image="moonmind:updated",
        actor="release",
    )
    inputs = asyncio.run(drivers.deployment_inputs())
    assert inputs["OMNIGENT_SHARED_HOST_IMAGE_REF"] == UPSTREAM_HOST


def _load_image_resolution_module(monkeypatch):
    """Load the real image_resolution with its light dependencies stubbed."""
    for name in (
        "moonmind",
        "moonmind.omnigent",
        "moonmind.omnigent.bootstrap",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    models = types.ModuleType("moonmind.omnigent.bootstrap.models")
    models.ResolvedOmnigentDeploymentState = SimpleNamespace
    monkeypatch.setitem(sys.modules, "moonmind.omnigent.bootstrap.models", models)
    compatibility = types.ModuleType("moonmind.omnigent.compatibility")
    compatibility.versions_compatible = lambda *args, **kwargs: True
    monkeypatch.setitem(
        sys.modules, "moonmind.omnigent.compatibility", compatibility
    )
    path = ROOT / "moonmind/omnigent/bootstrap/image_resolution.py"
    spec = importlib.util.spec_from_file_location(
        "image_resolution_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_resolve_live_server_image_ref_delegates_to_running_server(monkeypatch):
    """The release migration's live-container check must resolve to a helper.

    Regression: ``_default_verify_live_container`` imported
    ``resolve_live_server_image_ref`` from image_resolution, which never
    defined it.
    """
    module = _load_image_resolution_module(monkeypatch)
    calls = []

    async def fake_running_server(image, env):
        calls.append((image, env))
        return LIVE_SERVER

    monkeypatch.setattr(module, "_resolve_running_server_image", fake_running_server)
    assert (
        asyncio.run(module.resolve_live_server_image_ref("omnigent-server:1.2.3"))
        == LIVE_SERVER
    )
    assert calls and calls[0][0] == "omnigent-server:1.2.3"
