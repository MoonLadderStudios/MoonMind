"""ReaderAdapter protocol and type registry.

Defines the interface that all manifest data source adapters must implement,
and a registry that maps manifest ``dataSources[].type`` strings to concrete
adapter classes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, Optional, Protocol, Tuple, Type, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan result returned by ReaderAdapter.plan()
# ---------------------------------------------------------------------------


class PlanResult:
    """Estimated scope of a reader fetch.

    Attributes:
        estimated_docs: Approximate number of documents.
        estimated_size_bytes: Estimated total size in bytes (0 = unknown).
        metadata: Arbitrary reader-specific estimates (e.g., file list).
    """

    __slots__ = ("estimated_docs", "estimated_size_bytes", "metadata")

    def __init__(
        self,
        estimated_docs: int = 0,
        estimated_size_bytes: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.estimated_docs = estimated_docs
        self.estimated_size_bytes = estimated_size_bytes
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return (
            f"PlanResult(docs={self.estimated_docs}, "
            f"bytes={self.estimated_size_bytes})"
        )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ReaderAdapter(Protocol):
    """Interface for manifest data source adapters.

    Every adapter must implement three methods:

    * ``plan()`` — enumerate files/docs to estimate scope without I/O writes.
    * ``fetch()`` — yield ``(text, metadata)`` document chunks.
    * ``state()`` — return a cursor dict for incremental runs.
    """

    def plan(self) -> PlanResult:
        """Return an estimated scope for this data source."""
        ...

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Yield ``(text, metadata)`` pairs for each document chunk."""
        ...

    def state(self) -> Dict[str, Any]:
        """Return a cursor for incremental re-index (e.g., commit SHA)."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: Dict[str, Type[ReaderAdapter]] = {}


def register_adapter(type_name: str, cls: Type[ReaderAdapter]) -> None:
    """Register a :class:`ReaderAdapter` for a manifest ``dataSources[].type``."""
    if type_name in _ADAPTER_REGISTRY:
        logger.warning(
            "Overwriting existing adapter for type '%s': %s -> %s",
            type_name,
            _ADAPTER_REGISTRY[type_name].__name__,
            cls.__name__,
        )
    _ADAPTER_REGISTRY[type_name] = cls


def get_adapter(type_name: str) -> Type[ReaderAdapter]:
    """Look up a registered adapter by its manifest type string.

    Raises:
        KeyError: If no adapter is registered for *type_name*.
    """
    try:
        return _ADAPTER_REGISTRY[type_name]
    except KeyError:
        available = sorted(_ADAPTER_REGISTRY.keys()) or ["(none)"]
        raise KeyError(
            f"No ReaderAdapter registered for type '{type_name}'. "
            f"Available: {', '.join(available)}"
        ) from None


def registered_types() -> list[str]:
    """Return sorted list of registered adapter type names."""
    return sorted(_ADAPTER_REGISTRY.keys())


def _reset_registry() -> None:
    """Clear the registry (for testing only)."""
    _ADAPTER_REGISTRY.clear()
