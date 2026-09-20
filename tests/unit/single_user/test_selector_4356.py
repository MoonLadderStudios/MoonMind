"""MoonLadderStudios/MoonMind#4356 R8: single-user impact selection cannot skip.

Representative changes to auth/admission, routes, models/migrations,
settings/secrets/presets, frontend transport, Compose, worker binding, and
the conformance mapping itself must select the suites that own those
boundaries. Each case asserts selector behavior (not filenames or wording).
"""

from __future__ import annotations

import pytest

from tools.select_test_suites import select_suites


def _outputs(paths: list[str]) -> dict[str, str]:
    return select_suites(paths, event_name="pull_request").as_outputs()


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/auth.py",
        "api_service/auth_providers.py",
        "api_service/main.py",
        "api_service/api/routers/worker_auth.py",
    ],
)
def test_auth_admission_changes_select_integration(changed_path: str) -> None:
    assert _outputs([changed_path])["integration_ci"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/db/models.py",
        "api_service/migrations/versions/999_single_user_probe.py",
    ],
)
def test_model_migration_changes_select_component_and_integration(
    changed_path: str,
) -> None:
    outputs = _outputs([changed_path])
    assert outputs["integration_ci"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/api/routers/settings.py",
        "api_service/api/routers/secrets.py",
        "api_service/api/routers/presets.py",
        "api_service/services/secrets.py",
        "api_service/services/settings_catalog.py",
        "api_service/services/presets/catalog.py",
    ],
)
def test_settings_secrets_presets_changes_select_integration(
    changed_path: str,
) -> None:
    outputs = _outputs([changed_path])
    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
    assert outputs["integration_ci"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "frontend/src/lib/api/client.ts",
        "frontend/src/generated/openapi.ts",
    ],
)
def test_frontend_transport_changes_select_integration(changed_path: str) -> None:
    outputs = _outputs([changed_path])
    assert outputs["integration_ci"] == "true"
    assert outputs["frontend_static"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "docker-compose.yaml",
        "docker-compose.test.yaml",
        "moonmind/workflows/temporal/worker_runtime.py",
        "moonmind/security/container_job_capabilities.py",
    ],
)
def test_compose_and_worker_binding_changes_select_integration(
    changed_path: str,
) -> None:
    assert _outputs([changed_path])["integration_ci"] == "true"


@pytest.mark.parametrize(
    "changed_path",
    [
        "moonmind/single_user/conformance_4356.py",
        "tests/unit/single_user/test_conformance_mapping_4356.py",
    ],
)
def test_conformance_mapping_changes_stay_visible(changed_path: str) -> None:
    outputs = _outputs([changed_path])
    assert outputs["unit_fast"] == "true"
    # The mapping owns required-CI selection, so its change must also run
    # the hermetic integration foundation rather than unit-only shards.
    assert outputs["integration_ci"] == "true"
    assert outputs["full_backend"] == "false"


def test_unknown_paths_still_fail_open_to_full_backend() -> None:
    outputs = _outputs(["totally-unknown-single-user-path-xyz"])
    assert outputs["full_backend"] == "true"
    assert all(value == "true" for value in outputs.values())


def test_design_doc_change_stays_unit_fast_without_integration() -> None:
    """A design-doc edit must not drag the integration foundation along."""
    outputs = _outputs(["docs/SingleUserApplicationDesign.md"])
    assert outputs["unit_fast"] == "true"
    assert outputs["integration_ci"] == "false"
    assert outputs["full_backend"] == "false"


@pytest.mark.parametrize(
    "changed_path",
    [
        "api_service/api/routers/temporal_artifacts.py",
        "api_service/api/routers/workflows.py",
    ],
)
def test_generic_route_changes_select_api_component(changed_path: str) -> None:
    """Ordinary route edits run the component suite that owns the boundary."""
    outputs = _outputs([changed_path])
    assert outputs["unit_fast"] == "true"
    assert outputs["api_component"] == "true"
