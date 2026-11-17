"""Credential-recovery qualification of an exact legacy host image.

The receipt never carries raw inspect credentials, and the recovery Host Class
comes from the image's installed harness registry.
"""

from types import SimpleNamespace

import pytest


def test_qualification_receipt_excludes_raw_inspect_credentials():
    import json

    from tests.integration.omnigent.test_exact_docker_n_way_concurrency import (
        _credential_recovery_container_facts,
    )

    secret = "syntheticRawLegacyCredentialNeverPersist"
    facts = _credential_recovery_container_facts(
        json.dumps(
            {
                "containerId": "a" * 64,
                "hostImageId": "sha256:" + "b" * 64,
                "Env": ["GH_TOKEN=" + secret],
                "config": {"token": secret},
                "mounts": [
                    {
                        "Type": "volume",
                        "Destination": "/home/app/.omnigent",
                        "Name": "test-state",
                        "rawCredential": secret,
                    }
                ],
            }
        ),
        expected_state_volume="test-state",
    )
    assert set(facts) == {"containerId", "hostImageId", "stateVolume"}
    assert secret not in json.dumps(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("installed", [True, False])
@pytest.mark.parametrize("has_capabilities", [True, False])
async def test_exact_recovery_class_uses_installed_native_registry_without_picker_row(
    monkeypatch, tmp_path, installed, has_capabilities
):
    import contextlib
    import importlib.metadata
    import io
    import runpy
    import sys
    from types import ModuleType

    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness
    from tests.integration.omnigent.test_exact_docker_n_way_concurrency import (
        _credential_recovery_host_class,
    )

    capabilities = {"integration_mode": "native-server", "auth": "own-auth"}
    registry = ModuleType("omnigent.harness_plugins")
    registry.valid_harnesses = lambda: {"opencode-native"} if installed else set()
    registry.harness_capabilities = lambda: (
        {"opencode-native": SimpleNamespace(as_dict=lambda: capabilities)}
        if has_capabilities
        else {}
    )
    monkeypatch.setitem(sys.modules, "omnigent.harness_plugins", registry)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")

    async def inspect(args):
        if "moonmind.omnigent.build_digest" in " ".join(args):
            return 0, "sha256:" + "a" * 64, ""
        if "{{.Os}}/{{.Architecture}}" in args:
            return 0, "linux/arm64", ""
        assert args[:4] == ["docker", "run", "--rm", "--network"]
        assert args[4] == "none"
        assert args[7] == "example.test/host@sha256:" + "b" * 64
        output = io.StringIO()
        probe = tmp_path / "installed-harness-probe.py"
        probe.write_text(args[-1])
        with contextlib.redirect_stdout(output):
            runpy.run_path(str(probe), run_name="__main__")
        # Keep the installed registry producer and its actual command-output
        # projection in this test: a backend mock alone hides identity drift.
        from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend

        return await DockerCommandBackend().run(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input_bytes=output.getvalue().encode(),
            timeout_seconds=10,
        )

    # The real upstream picker deliberately omits native wrapper rows; the
    # installed registry must be sufficient without an HTTP catalog client.
    if not installed or not has_capabilities:
        with pytest.raises(
            AssertionError, match="installed recovery harness is unavailable"
        ):
            await _credential_recovery_host_class(
                SimpleNamespace(run=inspect),
                "example.test/host@sha256:" + "b" * 64,
            )
        return
    result = await _credential_recovery_host_class(
        SimpleNamespace(run=inspect),
        "example.test/host@sha256:" + "b" * 64,
    )
    assert result.omnigentVersion == "1.2.3"
    assert result.omnigentBuildDigest == "sha256:" + "a" * 64
    assert result.architectures == ("linux/arm64",)
    expected = _normalize_harness(
        {"id": "opencode-native", "capabilities": capabilities},
        omnigent_version="1.2.3",
        omnigent_build_digest="sha256:" + "a" * 64,
    )
    assert result.declaredHarnessImplementations[0].implementationRef == (
        expected.implementation.implementation_ref()
    )
