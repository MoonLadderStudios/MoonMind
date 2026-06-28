from __future__ import annotations

import pytest

from moonmind.workflows.adapters.omnigent_agent_adapter import OmnigentExternalAdapter


def test_omnigent_capability_is_streaming_gateway():
    adapter = OmnigentExternalAdapter()
    capability = adapter.provider_capability

    assert capability.provider_name == "omnigent"
    assert capability.supports_callbacks is False
    assert capability.supports_cancel is False
    assert capability.supports_result_fetch is False
    assert capability.execution_style == "streaming_gateway"


@pytest.mark.asyncio
async def test_omnigent_polling_hooks_fail_loudly():
    adapter = OmnigentExternalAdapter()

    with pytest.raises(RuntimeError, match="streaming execution"):
        await adapter.do_status("sess-1")
