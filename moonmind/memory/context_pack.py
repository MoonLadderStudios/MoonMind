"""Token-budgeted memory context packaging with required provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryPlane = Literal["planning", "history", "long_term", "document"]
DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS = 4096
MEMORY_CONTEXT_BUILDER_VERSION = "memory-context-builder-v1"
_SECRETISH_RE = re.compile(
    r"(ghp_|github_pat_|AIza|ATATT|AKIA|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


class MemoryContextBudgetExceeded(ValueError):
    """Raised when a candidate cannot fit into the remaining memory token budget."""


class MemoryContextCandidate(BaseModel):
    """One candidate from a memory plane before budgeted packaging."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    text: str
    source: str
    plane: MemoryPlane
    trust_class: str = Field(alias="trustClass")
    provenance: dict[str, Any]
    recency: str | None = None
    token_cost: int | None = Field(default=None, alias="tokenCost")
    score: float | None = None

    @field_validator("text", "source", "trust_class")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("token_cost")
    @classmethod
    def _positive_token_cost(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("tokenCost must be positive")
        return value

    @model_validator(mode="after")
    def _validate_provenance_and_content(self) -> "MemoryContextCandidate":
        if not _has_provenance_pointer(self.provenance):
            raise ValueError("memory context items require provenance pointers")
        _reject_secretish_values(self.model_dump(by_alias=True), path="memoryContext")
        return self

    def estimated_token_cost(self) -> int:
        if self.token_cost is not None:
            return self.token_cost
        return _estimate_tokens(self.text)


class MemoryContextItem(BaseModel):
    """One normalized, budget-accepted memory context item."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    text: str
    source: str
    plane: MemoryPlane
    trust_class: str = Field(alias="trustClass")
    provenance: dict[str, Any]
    recency: str | None = None
    token_cost: int = Field(alias="tokenCost")
    score: float | None = None
    item_ref: str = Field(alias="itemRef")


class MemoryContextPack(BaseModel):
    """Compact memory context package metadata for runtime injection boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    builder_version: str = Field(alias="builderVersion")
    items: list[MemoryContextItem]
    budgets: dict[str, int]
    usage: dict[str, int]
    skipped_refs: list[str] = Field(default_factory=list, alias="skippedRefs")
    memory_context_ref: str = Field(alias="memoryContextRef")
    created_at: str = Field(alias="createdAt")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def build_memory_context_pack(
    candidates: Sequence[Mapping[str, Any] | MemoryContextCandidate],
    *,
    token_budget: int = DEFAULT_MEMORY_CONTEXT_BUDGET_TOKENS,
    builder_version: str = MEMORY_CONTEXT_BUILDER_VERSION,
) -> MemoryContextPack:
    """Normalize memory candidates and keep only items that fit the token budget."""

    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    accepted: list[MemoryContextItem] = []
    skipped_refs: list[str] = []
    used_tokens = 0

    for raw_candidate in candidates:
        candidate = (
            raw_candidate
            if isinstance(raw_candidate, MemoryContextCandidate)
            else MemoryContextCandidate.model_validate(raw_candidate)
        )
        token_cost = candidate.estimated_token_cost()
        payload = candidate.model_dump(by_alias=True, exclude_none=True)
        payload["tokenCost"] = token_cost
        item_ref = f"memory-context-item://{_digest_payload(payload)}"

        if used_tokens + token_cost > token_budget:
            skipped_refs.append(item_ref)
            continue

        used_tokens += token_cost
        accepted.append(
            MemoryContextItem.model_validate({**payload, "itemRef": item_ref})
        )

    base_payload = {
        "schemaVersion": "v1",
        "builderVersion": builder_version,
        "items": [
            item.model_dump(by_alias=True, exclude_none=True) for item in accepted
        ],
        "budgets": {"tokens": token_budget},
        "usage": {
            "tokens": used_tokens,
            "acceptedItems": len(accepted),
            "skippedItems": len(skipped_refs),
        },
        "skippedRefs": skipped_refs,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    digest = _digest_payload(
        {
            key: value
            for key, value in base_payload.items()
            if key != "createdAt"
        }
    )
    return MemoryContextPack.model_validate(
        {**base_payload, "memoryContextRef": f"memory-context-pack://{digest}"}
    )


def _estimate_tokens(text: str) -> int:
    return max(1, (len(str(text)) + 3) // 4)


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _has_provenance_pointer(value: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for pointer in value.values():
        if isinstance(pointer, str) and pointer.strip():
            return True
        if isinstance(pointer, Sequence) and not isinstance(pointer, str):
            if any(str(item).strip() for item in pointer):
                return True
        if isinstance(pointer, Mapping) and _has_provenance_pointer(pointer):
            return True
    return False


def _reject_secretish_values(value: Any, *, path: str) -> None:
    if isinstance(value, str):
        if _SECRETISH_RE.search(value):
            raise ValueError(f"{path} contains raw secret material")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secretish_values(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_secretish_values(item, path=f"{path}[{index}]")
