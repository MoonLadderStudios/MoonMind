"""Backend-settings normalization coverage for MoonLadderStudios/MoonMind#3254."""

from __future__ import annotations

import pytest

import json

from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    PYTHON_TEST_FINGERPRINT_INPUTS,
    PYTHON_TEST_IMAGE_SOURCE_REF,
    LocalImageRecipe,
    RegistryImageSource,
    SUPPORTED_CONTAINER_BACKEND_KINDS,
    ContainerBackendConfigError,
    ContainerBackendReadinessError,
    resolve_container_backend_settings,
)

#: A deployment declaration standing in for whatever a real operator approves.
#: MoonMind must carry no project's image, volume, or in-container path itself.
DECLARED_IMAGE_SOURCE_REF = "project-toolchain"
DECLARED_IMAGE = "registry.invalid/example/toolchain:1.0"
DECLARED_CACHE_REF = "project-build-cache"


def _declared_env(
    *,
    pull_policy: str | None = None,
    volume_name: str = "project_build_cache_volume",
    target: str = "/opt/project/cache",
    read_only: object = None,
) -> dict[str, str]:
    image: dict[str, str] = {
        "sourceRef": DECLARED_IMAGE_SOURCE_REF,
        "image": DECLARED_IMAGE,
    }
    if pull_policy is not None:
        image["pullPolicy"] = pull_policy
    cache: dict[str, object] = {
        "cacheRef": DECLARED_CACHE_REF,
        "volumeName": volume_name,
        "target": target,
    }
    if read_only is not None:
        cache["readOnly"] = read_only
    return {
        IMAGE_SOURCES_ENV_KEY: json.dumps([image]),
        CACHE_SOURCES_ENV_KEY: json.dumps([cache]),
    }


def test_defaults_are_deployment_safe() -> None:
    settings = resolve_container_backend_settings({})
    assert settings.enabled is True
    assert settings.kind == "docker-engine"
    assert settings.default_backend_ref == "system"
    # Endpoint falls back to the docker-proxy default when none is configured.
    assert settings.endpoint == "tcp://docker-proxy:2375"
    # The raw Docker CLI escape hatch is disabled unless explicitly enabled.
    assert settings.raw_cli_enabled is False
    assert settings.max_active_memory_mib is None
    source = settings.image_source(PYTHON_TEST_IMAGE_SOURCE_REF)
    assert isinstance(source, LocalImageRecipe)
    assert source.image == "moonmind-python-tests:local"
    assert source.target == "test-runtime"
    assert source.fingerprint_inputs == PYTHON_TEST_FINGERPRINT_INPUTS
    # MoonMind owns exactly one image source -- its own test image -- and no
    # cache source at all. Every project source is deployment-declared.
    assert [item.source_ref for item in settings.image_sources] == [
        PYTHON_TEST_IMAGE_SOURCE_REF
    ]
    assert settings.cache_sources == ()
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
            "MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_MEMORY_MIB": "768",
            "MOONMIND_CONTAINER_BACKEND_MAX_PIDS": "128",
            "MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB": "32",
            "MOONMIND_CONTAINER_BACKEND_MAX_TIMEOUT_SECONDS": "60",
        }
    )
    assert settings.raw_cli_enabled is True
    assert settings.max_cpu_millis == 2000
    assert settings.max_memory_mib == 1024
    assert settings.max_active_memory_mib == 768
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


def test_declared_image_pulls_on_first_use_and_reuses_declared_cache() -> None:
    settings = resolve_container_backend_settings(
        _declared_env(volume_name="shared-cache", read_only=True)
    )

    source = settings.image_source(DECLARED_IMAGE_SOURCE_REF)
    assert isinstance(source, RegistryImageSource)
    assert source.image == DECLARED_IMAGE
    assert source.pull_policy == "if-missing"
    cache = settings.cache_source(DECLARED_CACHE_REF)
    assert cache.volume_name == "shared-cache"
    assert cache.target == "/opt/project/cache"
    assert cache.read_only is True


def test_undeclared_refs_and_malformed_declarations_fail_closed() -> None:
    empty = resolve_container_backend_settings({})
    with pytest.raises(ContainerBackendConfigError, match="is not configured"):
        empty.image_source(DECLARED_IMAGE_SOURCE_REF)
    with pytest.raises(ContainerBackendConfigError, match="is not configured"):
        empty.cache_source(DECLARED_CACHE_REF)

    prewarmed = resolve_container_backend_settings(
        _declared_env(pull_policy="never")
    ).image_source(DECLARED_IMAGE_SOURCE_REF)
    assert isinstance(prewarmed, RegistryImageSource)
    assert prewarmed.pull_policy == "never"

    with pytest.raises(ContainerBackendConfigError, match="named volume"):
        resolve_container_backend_settings(_declared_env(volume_name="/host/cache"))

    with pytest.raises(ContainerBackendConfigError, match="absolute container path"):
        resolve_container_backend_settings(_declared_env(target="relative/cache"))

    with pytest.raises(ContainerBackendConfigError, match="absolute container path"):
        resolve_container_backend_settings(_declared_env(target="/opt/../etc"))

    with pytest.raises(ContainerBackendConfigError, match="pull policy"):
        resolve_container_backend_settings(_declared_env(pull_policy="sometimes"))

    with pytest.raises(ContainerBackendConfigError, match="JSON array of objects"):
        resolve_container_backend_settings({IMAGE_SOURCES_ENV_KEY: "not-json"})

    with pytest.raises(ContainerBackendConfigError, match="JSON array of objects"):
        resolve_container_backend_settings(
            {CACHE_SOURCES_ENV_KEY: json.dumps({"cacheRef": DECLARED_CACHE_REF})}
        )

    with pytest.raises(ContainerBackendConfigError, match="non-empty sourceRef"):
        resolve_container_backend_settings(
            {IMAGE_SOURCES_ENV_KEY: json.dumps([{"image": DECLARED_IMAGE}])}
        )

    with pytest.raises(ContainerBackendConfigError, match="reserved by MoonMind"):
        resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "sourceRef": PYTHON_TEST_IMAGE_SOURCE_REF,
                            "image": DECLARED_IMAGE,
                        }
                    ]
                )
            }
        )

    with pytest.raises(ContainerBackendConfigError, match="duplicate sourceRef"):
        resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {"sourceRef": DECLARED_IMAGE_SOURCE_REF, "image": "a:1"},
                        {"sourceRef": DECLARED_IMAGE_SOURCE_REF, "image": "b:1"},
                    ]
                )
            }
        )

    with pytest.raises(ContainerBackendConfigError, match="duplicate cacheRef"):
        resolve_container_backend_settings(
            {
                CACHE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "cacheRef": DECLARED_CACHE_REF,
                            "volumeName": "one",
                            "target": "/a",
                        },
                        {
                            "cacheRef": DECLARED_CACHE_REF,
                            "volumeName": "two",
                            "target": "/b",
                        },
                    ]
                )
            }
        )


def test_declarations_reject_keys_outside_their_documented_schema() -> None:
    # Every declaration key defaults to the permissive value when omitted, so a
    # misspelling must fail configuration instead of quietly weakening the
    # deployment policy the operator declared.
    with pytest.raises(ContainerBackendConfigError, match="unknown key"):
        resolve_container_backend_settings(
            {
                CACHE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "cacheRef": DECLARED_CACHE_REF,
                            "volumeName": "project_build_cache_volume",
                            "target": "/opt/project/cache",
                            "readonly": True,
                        }
                    ]
                )
            }
        )

    with pytest.raises(ContainerBackendConfigError, match="unknown key"):
        resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "sourceRef": DECLARED_IMAGE_SOURCE_REF,
                            "image": DECLARED_IMAGE,
                            "pullpolicy": "never",
                        }
                    ]
                )
            }
        )

    # The documented schemas themselves still resolve.
    settings = resolve_container_backend_settings(
        _declared_env(pull_policy="never", read_only=True)
    )
    assert settings.image_source(DECLARED_IMAGE_SOURCE_REF).pull_policy == "never"
    assert settings.cache_source(DECLARED_CACHE_REF).read_only is True


def test_shared_memory_default_must_fit_under_its_ceiling() -> None:
    # ``_enforce_resource_ceilings`` only inspects caller-supplied shmSize, so a
    # default above the ceiling would launch every omitted request above the
    # deployment's declared maximum.
    with pytest.raises(ContainerBackendConfigError, match="shmSize default"):
        resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB": "32"}
        )

    # Lowering the ceiling is supported when the default is lowered with it.
    tightened = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB": "32",
            "MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB": "32",
        }
    )
    assert tightened.shm_size_mib == 32
    assert tightened.max_shm_size_mib == 32

    with pytest.raises(ContainerBackendConfigError, match="shmSize default"):
        resolve_container_backend_settings(
            {
                "MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB": "512",
                "MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB": "256",
            }
        )


def test_shared_memory_ceiling_defaults_to_the_memory_ceiling() -> None:
    settings = resolve_container_backend_settings({})
    assert settings.shm_size_mib == 64
    assert settings.max_shm_size_mib == settings.max_memory_mib

    tightened = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB": "2048",
            "MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB": "512",
        }
    )
    assert tightened.max_memory_mib == 2048
    assert tightened.max_shm_size_mib == 512

    # An omitted shared-memory ceiling still tracks a tightened memory ceiling.
    derived = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB": "4096"}
    )
    assert derived.max_shm_size_mib == 4096


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
