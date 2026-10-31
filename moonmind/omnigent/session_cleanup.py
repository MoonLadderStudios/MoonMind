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
            # A failed stop does not prove credential consumers are gone. The
            # caller must retain cleanup or confirm its fenced host removal
            # before releasing credentials and provider capacity.
            raise HarnessPlatformError(
                "Omnigent session could not be drained before host cleanup",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            ) from exc
        return {"drained": True, "sessionId": session_id}


__all__ = ["OmnigentSessionCleanupService"]
