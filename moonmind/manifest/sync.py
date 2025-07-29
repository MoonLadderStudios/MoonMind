from __future__ import annotations

import hashlib
from enum import Enum
from typing import Optional

import yaml

from moonmind.schemas import Manifest


class ManifestChange(str, Enum):
    NEW = "new"
    UNCHANGED = "unchanged"
    MODIFIED = "modified"


def compute_content_hash(manifest: Manifest) -> str:
    """Return SHA256 hash of the manifest spec."""
    dumped = yaml.safe_dump(manifest.spec.model_dump(), sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def detect_change(stored_hash: Optional[str], manifest: Manifest) -> ManifestChange:
    """Return change status compared to the stored hash."""
    new_hash = compute_content_hash(manifest)
    if stored_hash is None:
        return ManifestChange.NEW
    if stored_hash == new_hash:
        return ManifestChange.UNCHANGED
    return ManifestChange.MODIFIED
