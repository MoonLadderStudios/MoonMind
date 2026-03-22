"""Manifest contract validation errors.

Relocated from the deleted ``moonmind.workflows.agent_queue.manifest_contract``
module as part of the single-substrate migration.
"""

from __future__ import annotations


class ManifestContractError(ValueError):
    """Raised when manifest payloads violate the contract."""


__all__ = ["ManifestContractError"]
