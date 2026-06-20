from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retriever import QdrantRAG

__all__ = ["QdrantRAG"]


def __getattr__(name: str):
    if name == "QdrantRAG":
        from .retriever import QdrantRAG

        return QdrantRAG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
