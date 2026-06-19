from __future__ import annotations

from pathlib import Path

import pytest

from tests import conftest


class _FakeItem:
    def __init__(self, path: Path, marker_names: set[str] | None = None) -> None:
        self.fspath = str(path)
        self._marker_names = set(marker_names or set())

    @property
    def marker_names(self) -> set[str]:
        return self._marker_names

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self._marker_names else None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self._marker_names.add(marker.name)


def _item_for(relative_path: str, markers: set[str] | None = None) -> _FakeItem:
    return _FakeItem(conftest._REPO_ROOT / relative_path, markers)


def test_mm849_api_unit_paths_are_component_by_structure() -> None:
    item = _item_for("tests/unit/api/routers/test_workflow_console.py")

    conftest.pytest_collection_modifyitems([item])

    assert "component" in item.marker_names
    assert "unit_fast" not in item.marker_names


def test_mm849_api_service_unit_paths_are_component_by_structure() -> None:
    item = _item_for("tests/unit/api_service/api/test_oauth_terminal_websocket.py")

    conftest.pytest_collection_modifyitems([item])

    assert "component" in item.marker_names
    assert "unit_fast" not in item.marker_names


def test_mm849_temporal_boundary_paths_are_marked_by_structure() -> None:
    item = _item_for("tests/unit/workflows/temporal/workflows/test_run_scheduling.py")

    conftest.pytest_collection_modifyitems([item])

    assert "temporal_boundary" in item.marker_names
    assert "unit_fast" not in item.marker_names


def test_mm849_regular_temporal_unit_paths_remain_unit_fast() -> None:
    item = _item_for("tests/unit/workflows/temporal/test_activity_runtime.py")

    conftest.pytest_collection_modifyitems([item])

    assert item.marker_names == {"unit_fast"}


def test_mm849_collection_classification_does_not_read_source(monkeypatch) -> None:
    def _fail_if_source_is_read(self: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError(f"source content should not be read for {self}")

    monkeypatch.setattr(Path, "read_text", _fail_if_source_is_read)
    item = _item_for("tests/unit/services/test_example.py")

    conftest.pytest_collection_modifyitems([item])

    assert item.marker_names == {"unit_fast"}


def test_mm849_explicit_component_marker_exception_remains_supported() -> None:
    item = _item_for("tests/unit/services/test_explicit_component.py", {"component"})

    conftest.pytest_collection_modifyitems([item])

    assert "component" in item.marker_names
    assert "unit_fast" not in item.marker_names


def test_mm849_explicit_temporal_boundary_marker_exception_remains_supported() -> None:
    item = _item_for(
        "tests/unit/services/test_explicit_temporal_boundary.py",
        {"temporal_boundary"},
    )

    conftest.pytest_collection_modifyitems([item])

    assert "temporal_boundary" in item.marker_names
    assert "unit_fast" not in item.marker_names
