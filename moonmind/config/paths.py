"""Shared path constants for configuration modules."""

import os
from pathlib import Path

_base_path = os.path.abspath(__file__)
ENV_FILE = Path(_base_path).parent.parent.parent / ".env"
