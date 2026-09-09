"""Guardrail checks shared between CLI and worker doctor (vector-free)."""

from __future__ import annotations

import httpx

from moonmind.rag.settings import RagRuntimeSettings

class GuardrailError(RuntimeError):
    """Raised when a required guardrail fails."""

def ensure_rag_ready(settings: RagRuntimeSettings) -> None:
    # MoonLadderStudios/MoonMind#4112: native vector backend retired. No
    # shipped doctor, CLI, or worker path may query, require, or probe
    # Qdrant. Only the optional RetrievalGateway health probe remains;
    # a deliberately removed vector component is neither unhealthy nor
    # reported as verified healthy.
    if not settings.rag_enabled:
        return
    transport = settings.resolved_transport(None)
    if transport == "gateway" and settings.retrieval_gateway_url:
        _verify_gateway(settings.retrieval_gateway_url)
    return

def _verify_gateway(url: str) -> None:
    try:
        response = httpx.get(url.rstrip("/") + "/health", timeout=5.0)
    except httpx.HTTPError as exc:  # pragma: no cover
        raise GuardrailError(f"RetrievalGateway unreachable: {exc}") from exc
    if response.status_code >= 300:
        raise GuardrailError(
            f"RetrievalGateway health check failed with status {response.status_code}"
        )
