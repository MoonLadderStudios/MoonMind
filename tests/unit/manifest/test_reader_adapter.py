"""Unit tests for ReaderAdapter protocol and registry."""

from __future__ import annotations

from typing import Any, Dict, Iterator, Tuple

import pytest

from moonmind.manifest.reader_adapter import (
    PlanResult,
    ReaderAdapter,
    _reset_registry,
    get_adapter,
    register_adapter,
    registered_types,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockReader:
    """A minimal ReaderAdapter implementation for testing."""

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self._docs = [("doc1 content", {"source": "test"})]

    def plan(self) -> PlanResult:
        return PlanResult(estimated_docs=len(self._docs), estimated_size_bytes=100)

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        yield from self._docs

    def state(self) -> Dict[str, Any]:
        return {"cursor": "abc123"}


class IncompleteMockReader:
    """Missing the state() method — should NOT satisfy the protocol."""

    def plan(self) -> PlanResult:
        return PlanResult()

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        yield ("text", {})


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the adapter registry before each test."""
    _reset_registry()
    yield
    _reset_registry()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_mock_reader_satisfies_protocol(self):
        reader = MockReader()
        assert isinstance(reader, ReaderAdapter)

    def test_incomplete_reader_does_not_satisfy(self):
        reader = IncompleteMockReader()
        assert not isinstance(reader, ReaderAdapter)


# ---------------------------------------------------------------------------
# PlanResult
# ---------------------------------------------------------------------------


class TestPlanResult:
    def test_defaults(self):
        pr = PlanResult()
        assert pr.estimated_docs == 0
        assert pr.estimated_size_bytes == 0
        assert pr.metadata == {}

    def test_repr(self):
        pr = PlanResult(estimated_docs=10, estimated_size_bytes=1024)
        assert "docs=10" in repr(pr)
        assert "bytes=1024" in repr(pr)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self):
        register_adapter("MockReader", MockReader)
        cls = get_adapter("MockReader")
        assert cls is MockReader

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="No ReaderAdapter"):
            get_adapter("UnknownType")

    def test_registered_types_sorted(self):
        register_adapter("Bravo", MockReader)
        register_adapter("Alpha", MockReader)
        assert registered_types() == ["Alpha", "Bravo"]

    def test_overwrite_warning(self, caplog):
        register_adapter("MockReader", MockReader)
        register_adapter("MockReader", MockReader)  # should warn
        assert "Overwriting" in caplog.text

    def test_reset_clears(self):
        register_adapter("MockReader", MockReader)
        _reset_registry()
        assert registered_types() == []


# ---------------------------------------------------------------------------
# Adapter usage
# ---------------------------------------------------------------------------


class TestAdapterUsage:
    def test_plan_returns_result(self):
        reader = MockReader()
        result = reader.plan()
        assert result.estimated_docs == 1

    def test_fetch_yields_docs(self):
        reader = MockReader()
        docs = list(reader.fetch())
        assert len(docs) == 1
        assert docs[0][0] == "doc1 content"

    def test_state_returns_cursor(self):
        reader = MockReader()
        state = reader.state()
        assert state["cursor"] == "abc123"
