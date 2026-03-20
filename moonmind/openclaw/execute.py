"""Run one OpenClaw streaming execution inside a Temporal activity."""

from __future__ import annotations

import os

from temporalio import activity

from moonmind.openclaw.settings import (
    OPENCLAW_DISABLED_MESSAGE,
    build_openclaw_gate,
    resolved_default_model,
    resolved_gateway_url,
    resolved_timeout_seconds,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.openclaw_agent_adapter import (
    build_openclaw_chat_messages,
    openclaw_success_result,
)
from moonmind.workflows.adapters.openclaw_client import (
    OpenClawClientError,
    OpenClawHttpClient,
)


async def run_openclaw_execution(request: AgentExecutionRequest) -> AgentRunResult:
    """Stream chat completion, heartbeat periodically, return ``AgentRunResult``."""

    gate = build_openclaw_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OPENCLAW_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            f"{OPENCLAW_DISABLED_MESSAGE} (missing: OPENCLAW_GATEWAY_TOKEN)"
        )

    base_url = resolved_gateway_url()
    model = resolved_default_model()
    params = request.parameters or {}
    if isinstance(params.get("model"), str) and params["model"].strip():
        model = params["model"].strip()

    timeout_sec = float(resolved_timeout_seconds())
    if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
        try:
            timeout_sec = max(60.0, float(request.timeout_policy["timeout_seconds"]))
        except (TypeError, ValueError):
            pass  # Ignore invalid timeout value; keep default resolved timeout.

    client = OpenClawHttpClient(
        base_url=base_url,
        token=token,
        total_timeout_seconds=timeout_sec,
    )
    messages = build_openclaw_chat_messages(request)

    parts: list[str] = []
    chunk_count = 0
    try:
        async for delta in client.stream_chat_completion(model=model, messages=messages):
            parts.append(delta)
            chunk_count += 1
            if chunk_count % 8 == 0:
                activity.heartbeat(
                    {
                        "openclawChars": sum(len(p) for p in parts),
                        "chunks": chunk_count,
                    }
                )
    except OpenClawClientError as exc:
        return AgentRunResult(
            outputRefs=[],
            summary=str(exc)[:4096],
            failureClass="integration_error",
            providerErrorCode=str(exc.status_code or "openclaw_http_error"),
            metadata={"normalizedStatus": "failed", "providerName": "openclaw"},
        )

    full_text = "".join(parts)
    if not full_text.strip():
        return AgentRunResult(
            outputRefs=[],
            summary="OpenClaw stream ended with no assistant content",
            failureClass="integration_error",
            providerErrorCode="openclaw_empty_stream",
            metadata={"normalizedStatus": "failed", "providerName": "openclaw"},
        )

    return openclaw_success_result(full_text=full_text, request=request)


__all__ = ["run_openclaw_execution"]
