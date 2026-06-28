"""MM-995 live Omnigent smoke checks.

These tests are provider verification only. They require a real disposable
Omnigent server and are intentionally excluded from credential-free CI.
Source issue traceability: MM-981 -> MM-995.
"""

from __future__ import annotations

import os

import pytest

from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]


def _live_env() -> dict[str, str]:
    required = {
        "OMNIGENT_ENABLED": os.environ.get("OMNIGENT_ENABLED", ""),
        "OMNIGENT_SERVER_URL": os.environ.get("OMNIGENT_SERVER_URL", ""),
        "OMNIGENT_API_TOKEN": os.environ.get("OMNIGENT_API_TOKEN", ""),
        "OMNIGENT_DEFAULT_AGENT_NAME": os.environ.get(
            "OMNIGENT_DEFAULT_AGENT_NAME",
            "",
        ),
    }
    missing = [key for key, value in required.items() if not value.strip()]
    if missing:
        pytest.skip(
            "live Omnigent smoke requires provider credentials: "
            + ", ".join(missing)
        )
    return required


async def test_live_omnigent_smoke_disposable_managed_session() -> None:
    _live_env()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm-995-live-smoke",
        idempotencyKey="mm-995-live-smoke",
        parameters={
            "title": "MM-995 live smoke",
            "omnigent": {
                "session": {"hostType": "managed", "allowEmptyWorkspace": True},
                "prompt": {"text": "Reply with: MM-995 live smoke complete"},
            },
        },
    )

    result = await run_omnigent_execution(request)

    assert result.failure_class is None
    assert result.metadata["providerName"] == "omnigent"
