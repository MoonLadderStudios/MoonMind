"""Backend-settings normalization coverage for MoonLadderStudios/MoonMind#3254."""

from __future__ import annotations

import pytest

from moonmind.config.container_backend_settings import (
    PYTHON_TEST_FINGERPRINT_INPUTS,
    PYTHON_TEST_IMAGE_SOURCE_REF,
    LocalImageRecipe,
    RegistryImageSource,
    SUPPORTED_CONTAINER_BACKEND_KINDS,
    ContainerBackendConfigError,
    ContainerBackendReadinessError,
    resolve_container_backend_settings,
)


def test_defaults_are_deployment_safe() -> None:
    settings = resolve_container_backend_settings({})
    assert settings.enabled is True
    assert settings.kind == "docker-engine"
    assert settings.default_backend_ref == "system"
    # Endpoint falls back to the docker-proxy default when none is configured.
    assert settings.endpoint == "tcp://docker-proxy:2375"
    # The raw Docker CLI escape hatch is disabled unless explicitly enabled.
    assert settings.raw_cli_enabled is False
    source = settings.image_source(PYTHON_TEST_IMAGE_SOURCE_REF)
    assert isinstance(source, LocalImageRecipe)
    assert source.image == "moonmind-python-tests:local"
    assert source.target == "test-runtime"
    assert source.fingerprint_inputs == PYTHON_TEST_FINGERPRINT_INPUTS
    for pattern in source.fingerprint_inputs:
        assert any(path.is_file() for path in source.context_root.glob(pattern))


def test_endpoint_is_sourced_from_deployment_config_only() -> None:
    settings = resolve_container_backend_settings(
        {"SYSTEM_DOCKER_HOST": "tcp://trusted:2375", "DOCKER_HOST": "tcp://other:2375"}
    )
    # SYSTEM_DOCKER_HOST is the deployment authority handoff and wins.
    assert settings.endpoint == "tcp://trusted:2375"

    settings = resolve_container_backend_settings({"DOCKER_HOST": "tcp://only:2375"})
    assert settings.endpoint == "tcp://only:2375"


def test_unsupported_kind_fails_fast() -> None:
    with pytest.raises(ContainerBackendConfigError):
        resolve_container_backend_settings({"MOONMIND_CONTAINER_BACKEND_KIND": "podman"})
    assert SUPPORTED_CONTAINER_BACKEND_KINDS == frozenset({"docker-engine"})


def test_require_endpoint_raises_when_missing() -> None:
    settings = resolve_container_backend_settings({})
    object.__setattr__(settings, "endpoint", None)
    with pytest.raises(ContainerBackendReadinessError):
        settings.require_endpoint()


def test_raw_cli_flag_and_ceilings_are_overridable() -> None:
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_RAW_CLI_ENABLED": "true",
            "MOONMIND_CONTAINER_BACKEND_MAX_CPU_MILLIS": "2000",
            "MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB": "1024",
            "MOONMIND_CONTAINER_BACKEND_MAX_PIDS": "128",
            "MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB": "32",
            "MOONMIND_CONTAINER_BACKEND_MAX_TIMEOUT_SECONDS": "60",
        }
    )
    assert settings.raw_cli_enabled is True
    assert settings.max_cpu_millis == 2000
    assert settings.max_memory_mib == 1024
    assert settings.max_pids == 128
    assert settings.shm_size_mib == 32
    assert settings.max_timeout_seconds == 60


def test_invalid_boolean_and_integer_fail_fast() -> None:
    with pytest.raises(ContainerBackendConfigError):
        resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_ENABLED": "maybe"}
        )
    with pytest.raises(ContainerBackendConfigError):
        resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_CPU_MILLIS": "not-an-int"}
        )


def test_prebuilt_python_test_image_replaces_local_recipe() -> None:
    settings = resolve_container_backend_settings(
        {"MOONMIND_PYTHON_TEST_IMAGE": "registry.example/tests:v2"}
    )

    source = settings.image_source(PYTHON_TEST_IMAGE_SOURCE_REF)
    assert isinstance(source, RegistryImageSource)
    assert source.image == "registry.example/tests:v2"
    assert source.pull_policy == "if-missing"


def test_python_test_recipe_uses_deployment_root_and_optional_max_age(
    tmp_path,
) -> None:
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR": str(tmp_path),
            "MOONMIND_PYTHON_TEST_IMAGE_MAX_AGE_SECONDS": "3600",
        }
    )

    source = settings.image_source(PYTHON_TEST_IMAGE_SOURCE_REF)
    assert isinstance(source, LocalImageRecipe)
    assert source.context_root == tmp_path.resolve()
    assert source.max_age_seconds == 3600
