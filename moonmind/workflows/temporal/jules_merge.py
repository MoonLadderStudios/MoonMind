"""Durable continuation of the existing Jules branch-publication activity."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from temporalio import workflow


async def continue_jules_merge(
    result: Any,
    *,
    authored_payload: dict[str, Any],
    execute_merge: Callable[[dict[str, Any]], Awaitable[Any]],
) -> Any:
    """Carry a read-only candidate result into a separately recorded mutation.

    Completed old histories retain their original results. Pending old calls
    can request a binding, but only workflow-owned inputs supply that binding.
    Activity retries then retain the recorded head instead of resolving it anew.
    """
    if not workflow.patched("jules-merge-durable-candidate-v1"):
        return result
    if (
        isinstance(result, Mapping)
        and result.get("reasonCode") == "merge_authority_required"
    ):
        result = await execute_merge(dict(authored_payload))
    if (
        isinstance(result, Mapping)
        and result.get("reasonCode") == "merge_head_resolved"
    ):
        result = await execute_merge(
            {
                **authored_payload,
                "expected_head_sha": result.get("expectedHeadSha"),
            }
        )
    return result
