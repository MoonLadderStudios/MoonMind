"""Installed-image dependency evidence for the Qdrant removal (#4111).

Runs inside the ``test-runtime`` image that ``integration-ci`` builds from
``api_service/Dockerfile``. That image is ``runtime-dependencies`` (the
``poetry export`` of the lock shared with ``runtime-base`` ->
``worker-runtime`` -> ``api-runtime``) plus only the ``tests`` extras, so its
installed distribution set is a superset of the shipped API/worker layer.
The guard is the shared #4114 contract, not a separate scanner.
"""

import importlib

import pytest

from tests.unit.config.test_vector_free_regression_4114 import (
    assert_installed_graph_vector_free,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_image_dependency_layer_is_vector_free() -> None:
    assert_installed_graph_vector_free()


@pytest.mark.parametrize(
    "module",
    [
        "api_service.main",
        "moonmind.workflows.temporal.worker_runtime",
        "moonmind.cli",
        "moonmind.agents.codex_worker.cli",
    ],
)
def test_entrypoints_import_on_image_dependency_layer(module: str) -> None:
    importlib.import_module(module)
