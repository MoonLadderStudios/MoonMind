"""Context pack schema shared by CLI and RetrievalGateway."""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional

ISOFORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(slots=True)
class ContextItem:
    score: float
    source: str
    text: str
    offset_start: Optional[int] = None
    offset_end: Optional[int] = None
    trust_class: str = "canonical"
    chunk_hash: Optional[str] = None
    payload: MutableMapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> MutableMapping[str, Any]:
        data = asdict(self)
        return data


@dataclass(slots=True)
class ContextPack:
    items: List[ContextItem]
    filters: MutableMapping[str, Any]
    budgets: MutableMapping[str, Any]
    usage: MutableMapping[str, Any]
    transport: str
    context_text: str
    retrieved_at: str
    telemetry_id: str

    def to_dict(self) -> MutableMapping[str, Any]:
        return {
            "context_text": self.context_text,
            "items": [item.to_dict() for item in self.items],
            "filters": dict(self.filters),
            "budgets": dict(self.budgets),
            "usage": dict(self.usage),
            "transport": self.transport,
            "retrieved_at": self.retrieved_at,
            "telemetry_id": self.telemetry_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _normalize_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def build_context_text(items: Iterable[ContextItem], *, max_chars: int) -> str:
    body: list[str] = ["### Retrieved Context"]
    remaining = max_chars
    for idx, item in enumerate(items, start=1):
        snippet = _normalize_whitespace(item.text)
        header = (
            f"{idx}. {item.source} (score: {item.score:.3f}, trust: {item.trust_class})"
        )
        chunk = f"{header}\n{textwrap.indent(snippet, prefix='    ')}"
        if len(chunk) > remaining and idx > 1:
            body.append("[Context truncated]")
            break
        body.append(chunk)
        remaining -= len(chunk)
        if remaining <= 0:
            body.append("[Context truncated]")
            break
    if len(body) == 1:
        body.append("No context retrieved.")
    return "\n".join(body)


def build_context_pack(
    *,
    items: List[ContextItem],
    filters: Mapping[str, Any],
    budgets: Mapping[str, Any],
    usage: Mapping[str, Any],
    transport: str,
    telemetry_id: str,
    max_chars: int,
) -> ContextPack:
    context_text = build_context_text(items, max_chars=max_chars)
    return ContextPack(
        items=items,
        filters=dict(filters),
        budgets=dict(budgets),
        usage=dict(usage),
        transport=transport,
        context_text=context_text,
        retrieved_at=datetime.now(timezone.utc).strftime(ISOFORMAT),
        telemetry_id=telemetry_id,
    )
