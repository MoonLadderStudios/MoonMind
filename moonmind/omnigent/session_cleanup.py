"""Session drain authority used before generic host cleanup."""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class OmnigentSessionCleanupService:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def drain(self, session_id: str) -> dict[str, Any]:
        if not session_id.strip():
            return {"drained": False, "reason": "no_session"}
        try:
            await self._client.stop_session(session_id)
        except Exception as exc:
            # A host may not be removed while its session could still consume
            # mounted credentials. Preserve this as janitor-retryable cleanup.
            raise HarnessPlatformError(
                "Omnigent session could not be drained before host cleanup",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            ) from exc
        return {"drained": True, "sessionId": session_id}


__all__ = ["OmnigentSessionCleanupService"]
