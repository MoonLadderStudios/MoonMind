from __future__ import annotations

from pathlib import Path
from typing import Optional

import moonmind.config.settings as settings_module
from moonmind.config.settings import AppSettings
from moonmind.schemas import Manifest


class ManifestLoader:
    """Load a YAML manifest and merge defaults with application settings."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def load(self) -> Optional[Manifest]:
        """Return the parsed Manifest and apply defaults if present."""
        if not self.path:
            return None

        manifest_path = Path(self.path)
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        manifest = Manifest.model_validate_yaml(manifest_path)

        if manifest.spec.defaults is not None:
            merged = {
                **settings_module.settings.model_dump(),
                **manifest.spec.defaults.root,
            }
            settings_module.settings = AppSettings.model_validate(merged)

        return manifest
