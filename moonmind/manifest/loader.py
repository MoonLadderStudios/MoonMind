from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import yaml
from pydantic import ValidationError

import moonmind.config as settings_module
from moonmind.config.settings import AppSettings
from moonmind.schemas import Manifest


class ManifestLoader:
    """Load a YAML manifest and merge defaults with application settings."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def load(self) -> Tuple[Optional[Manifest], AppSettings]:
        """Return the parsed Manifest and merged settings."""
        current_settings = settings_module.settings

        if not self.path:
            return None, current_settings

        manifest_path = Path(self.path)
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        try:
            manifest = Manifest.model_validate_yaml(manifest_path)
        except (yaml.YAMLError, ValidationError) as exc:
            raise ValueError(f"Failed to parse manifest: {exc}") from exc

        if manifest.spec.defaults is not None:
            merged = {
                **current_settings.model_dump(),
                **manifest.spec.defaults.model_dump(),
            }
            new_settings = AppSettings.model_validate(merged)
        else:
            new_settings = current_settings

        return manifest, new_settings
