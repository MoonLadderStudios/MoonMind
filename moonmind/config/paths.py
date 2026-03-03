"""Shared path constants for configuration modules."""

from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
