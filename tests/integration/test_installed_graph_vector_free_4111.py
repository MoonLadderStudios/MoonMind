"""Installed-graph qualification of the built test-runtime image (#4111).

The integration-ci row runs this module inside the ``test-runtime`` target of
``api_service/Dockerfile``, which extends the shared ``runtime-dependencies``
layer installed from ``poetry export`` output. Enumerating installed package
metadata here qualifies the dependency graph the image actually ships rather
than the runner's own environment. The distribution guard and its negative
controls stay owned by ``tests/unit/config/test_vector_free_regression_4114.py``.
"""

from __future__ import annotations

import importlib
import importlib.metadata

import pytest

from tests.unit.config.test_vector_free_regression_4114 import (
    check_dependency_vector_free,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _installed_distribution_names() -> list[str]:
    names: list[str] = []
    for distribution in importlib.metadata.distributions():
        name = (distribution.metadata.get("Name") or "").strip()
        if name:
            names.append(name)
    return names


def test_installed_image_graph_carries_no_retired_distribution() -> None:
    names = _installed_distribution_names()
    assert names, "expected installed distributions to enumerate"
    assert check_dependency_vector_free(names) == []


@pytest.mark.parametrize(
    "module_name",
    [
        "typer",
        "fastapi",
        "temporalio",
        "httpx",
        "requests",
        "yaml",
        "sqlalchemy",
        "git",
    ],
)
def test_surviving_library_imports_on_installed_image_graph(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
