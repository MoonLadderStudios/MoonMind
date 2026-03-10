"""Shared path constants for configuration modules."""

from pathlib import Path
import os

_base_path = os.path.abspath(__file__)
ENV_FILE = Path(_base_path).parent.parent.parent / ".env"
