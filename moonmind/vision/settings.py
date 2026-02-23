"""Helper utilities for resolving runtime vision configuration."""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.config.settings import settings


@dataclass(frozen=True)
class VisionConfig:
    """Resolved configuration snapshot for rendering attachment context."""

    enabled: bool
    provider: str
    model: str
    max_tokens: int
    ocr_enabled: bool


def get_vision_config() -> VisionConfig:
    """Return the current vision configuration derived from settings."""

    spec = settings.spec_workflow
    return VisionConfig(
        enabled=spec.vision_context_enabled,
        provider=spec.vision_provider,
        model=spec.vision_model,
        max_tokens=spec.vision_max_tokens,
        ocr_enabled=spec.vision_ocr_enabled,
    )
