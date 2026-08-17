"""Backward-compatible re-export of the Omnigent failure classifier.

The canonical §17 failure-classification table moved into the Omnigent domain
layer (``moonmind.omnigent.domain.failures``) for
MoonLadderStudios/MoonMind#3711. This module re-exports the canonical symbols so
existing imports keep working while there is exactly one definition of the
vocabulary and table.
"""

from __future__ import annotations

from moonmind.omnigent.domain.failures import (
    OMNIGENT_FAILURE_CLASS_TABLE,
    OmnigentFailureReason,
    classify_omnigent_failure,
    classify_omnigent_http_status,
    failure_class_for_terminal_status,
)

__all__ = [
    "OMNIGENT_FAILURE_CLASS_TABLE",
    "OmnigentFailureReason",
    "classify_omnigent_failure",
    "classify_omnigent_http_status",
    "failure_class_for_terminal_status",
]
