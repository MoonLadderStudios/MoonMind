"""Shared schema validation helpers."""

from __future__ import annotations

from typing import Annotated, TypeAlias

from pydantic import StringConstraints

NonBlankStr: TypeAlias = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]

def require_non_blank(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized
