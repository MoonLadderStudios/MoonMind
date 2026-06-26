"""Helpers for copying Temporal schedule metadata into MoonMind visibility."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from temporalio.common import SearchAttributeKey

TEMPORAL_SCHEDULED_START_TIME_SEARCH_ATTRIBUTE = "TemporalScheduledStartTime"
MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE = "mm_scheduled_for"


def temporal_scheduled_start_time(info: Any) -> datetime | None:
    """Return Temporal's scheduled start timestamp from workflow info."""

    scheduled_start_key = SearchAttributeKey.for_datetime(
        TEMPORAL_SCHEDULED_START_TIME_SEARCH_ATTRIBUTE
    )
    typed_search_attributes = getattr(info, "typed_search_attributes", None)
    if typed_search_attributes is not None:
        scheduled_start = _coerce_datetime(
            typed_search_attributes.get(scheduled_start_key)
        )
        if scheduled_start is not None:
            return scheduled_start

    search_attributes = getattr(info, "search_attributes", {}) or {}
    if not isinstance(search_attributes, Mapping):
        return None
    return _coerce_search_attribute_datetime(
        search_attributes.get(TEMPORAL_SCHEDULED_START_TIME_SEARCH_ATTRIBUTE)
    )


def _coerce_search_attribute_datetime(value: Any) -> datetime | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return None
        value = value[0]
    return _coerce_datetime(value)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


__all__ = [
    "MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE",
    "TEMPORAL_SCHEDULED_START_TIME_SEARCH_ATTRIBUTE",
    "temporal_scheduled_start_time",
]
