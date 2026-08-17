"""Evidence harvesting over the artifact port.

Persists terminal evidence bodies via the ``ArtifactStore`` port and returns
durable refs. Harvesting failures are optional-resource degradations, not
lifecycle decisions; callers decide escalation via the domain failure classifier.
"""

from __future__ import annotations

from moonmind.omnigent.ports.artifacts import ArtifactStore


class EvidenceHarvester:
    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    async def harvest(self, bridge_session_id: str, name: str, body: bytes) -> str:
        return await self._artifacts.put(f"{bridge_session_id}/{name}", body)


__all__ = ["EvidenceHarvester"]
