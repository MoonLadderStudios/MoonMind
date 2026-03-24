"""Adapter-level exceptions for Temporal Schedule operations.

These exceptions wrap raw Temporal SDK errors at the adapter boundary
so that callers (e.g. RecurringTasksService) never depend on SDK
internals.
"""

from __future__ import annotations


class ScheduleAdapterError(Exception):
    """Base exception for all schedule adapter errors."""


class ScheduleNotFoundError(ScheduleAdapterError):
    """Raised when a Temporal Schedule does not exist."""


class ScheduleAlreadyExistsError(ScheduleAdapterError):
    """Raised when creating a schedule that already exists."""


class ScheduleOperationError(ScheduleAdapterError):
    """Raised when a Temporal SDK schedule call fails unexpectedly."""


__all__ = [
    "ScheduleAdapterError",
    "ScheduleAlreadyExistsError",
    "ScheduleNotFoundError",
    "ScheduleOperationError",
]
