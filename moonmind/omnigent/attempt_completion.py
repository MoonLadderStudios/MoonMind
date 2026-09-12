"""Bounded completion of a Skill on its existing, fenced runtime binding.

Skill evidence owns the decision. This module supplies durable turn receipts,
same-session delivery and a cumulative budget; it never selects domain fixes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    resolve_execution_budget,
)

MAX_CONTINUATIONS = 2
VERIFICATION_READ_BUDGET_SECONDS = 90


def recorded_attempt_result(binding):
    """Reconcile terminal evidence without reacquiring released resources."""
    phases = binding.phaseResults or {}
    receipt = binding.terminalResult or phases.get("publication")
    if receipt is not None:
        return AgentRunResult.model_validate(receipt)
    turns = [
        (int(key.split(":")[1]), value)
        for key, value in phases.items()
        if key.startswith("turn:")
    ]
    if not turns:
        return None
    result = AgentRunResult.model_validate(max(turns, key=lambda item: item[0])[1])
    saved = phases.get("saved")
    return result.model_copy(
        update={
            "failure_class": "integration_error",
            "provider_error_code": "ATTEMPT_FINALIZATION_INTERRUPTED",
            "retry_recommendation": "do_not_retry",
            "summary": "The provider turn completed; finalization needs recovery from its saved candidate.",
            "metadata": {
                **(result.metadata or {}),
                "unfinishedPhase": "finalization",
                "workPreserved": bool(saved),
                "savedWorkspaceCheckpoint": saved,
            },
        }
    )


async def complete_skill_turns(
    *, request, sink, driver, inspect_terminal, deliver_continuation=None
):
    phases = sink.binding.phaseResults or {}
    if "budget" not in phases:
        await sink.record_phase("budget", {"startedAt": datetime.now(UTC).isoformat()})
    started = datetime.fromisoformat(sink.binding.phaseResults["budget"]["startedAt"])
    seconds = resolve_execution_budget(
        agent_kind=request.agent_kind, timeout_policy=request.timeout_policy
    ).base_seconds
    instruction = None
    previous_progress = None
    for ordinal in range(MAX_CONTINUATIONS + 1):
        key = f"turn:{ordinal}"
        phases = sink.binding.phaseResults or {}
        receipt = phases.get(key)
        if receipt is not None:
            result = AgentRunResult.model_validate(receipt)
            if ordinal and deliver_continuation is not None:
                result = await deliver_continuation(ordinal, instruction, result)
        else:
            remaining = seconds - (datetime.now(UTC) - started).total_seconds()
            if remaining <= 0:
                raise TimeoutError(
                    "The durable attempt's execution budget is exhausted"
                )
            async with asyncio.timeout(remaining):
                if ordinal == 0:
                    result = await driver(request, session_authority_sink=sink)
                elif deliver_continuation is not None:
                    result = await deliver_continuation(ordinal, instruction, None)
                else:
                    turn_request = request.model_copy(
                        update={
                            "idempotency_key": f"{request.idempotency_key}:terminal-contract:{ordinal}"
                        }
                    )
                    result = await driver(
                        turn_request,
                        session_authority_sink=sink,
                        resume_session_id=sink.binding.omnigentSessionId,
                        first_message_text=instruction,
                        allow_same_session_continuation=True,
                    )
            if key not in (sink.binding.phaseResults or {}):
                await sink.record_phase(
                    key,
                    result.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
        if result.failure_class is not None or request.terminal_contract is None:
            return result
        saved_continuation = (sink.binding.phaseResults or {}).get(
            f"continuation:{ordinal + 1}"
        )
        if saved_continuation is not None:
            instruction = saved_continuation["instructions"]
            previous_progress = saved_continuation.get("progressKey")
            continue
        # A transient evidence read retries verification of the recorded turn,
        # never a billed implementation turn. Counters survive worker retries.
        evaluation = None
        budget_key = f"verification_budget:{ordinal}"
        if budget_key not in (sink.binding.phaseResults or {}):
            await sink.record_phase(
                budget_key, {"startedAt": datetime.now(UTC).isoformat()}
            )
        verification_started = datetime.fromisoformat(
            sink.binding.phaseResults[budget_key]["startedAt"]
        )
        for evidence_attempt in range(3):
            failure_key = f"verification_failure:{ordinal}:{evidence_attempt}"
            if failure_key in (sink.binding.phaseResults or {}):
                continue
            remaining = (
                VERIFICATION_READ_BUDGET_SECONDS
                - (datetime.now(UTC) - verification_started).total_seconds()
            )
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(min(30, remaining)):
                    evaluation = await inspect_terminal(request)
                break
            except (OSError, TimeoutError) as exc:
                await sink.record_phase(failure_key, {"type": type(exc).__name__})
                if evidence_attempt < 2:
                    await asyncio.sleep(2**evidence_attempt)
        if evaluation is None:
            return result.model_copy(
                update={
                    "failure_class": "integration_error",
                    "provider_error_code": "VERIFICATION_EVIDENCE_UNAVAILABLE",
                    "retry_recommendation": "do_not_retry",
                    "summary": "The provider turn completed; verification evidence retries are exhausted.",
                    "metadata": {
                        **(result.metadata or {}),
                        "unfinishedPhase": "verification",
                        "terminalContractRecoveryOwner": "runtime_binding",
                    },
                }
            )
        if f"verification:{ordinal}" not in (sink.binding.phaseResults or {}):
            await sink.record_phase(
                f"verification:{ordinal}",
                {
                    "satisfied": evaluation.satisfied,
                    "outcome": evaluation.outcome,
                    "failureCode": evaluation.failure_code,
                    "missingEvidence": list(evaluation.missing_evidence),
                    "metadata": evaluation.metadata,
                },
            )
        if evaluation.satisfied or evaluation.outcome == "continuation_requested":
            return result
        continuation = evaluation.metadata.get("skillContinuation")
        # A validated terminal verdict is final. Only an explicit portable
        # continuation or a declared incomplete contract permits another turn.
        recoverable = bool(continuation) or evaluation.failure_code in {
            "INCOMPLETE_TERMINAL_CONTRACT",
            "MALFORMED_TERMINAL_EVIDENCE",
            "INVALID_TERMINAL_EVIDENCE",
        }
        progress = (continuation or {}).get("progressKey")
        exhausted = ordinal == MAX_CONTINUATIONS or (
            progress is not None and progress == previous_progress
        )
        if not recoverable or exhausted:
            return result.model_copy(
                update={
                    "metadata": {
                        **(result.metadata or {}),
                        "terminalContractRecoveryOwner": "runtime_binding",
                        "terminalContractContinuationCount": ordinal,
                        "terminalContractRecoveryOutcome": (
                            "exhausted" if exhausted else "skill_terminal_verdict"
                        ),
                    }
                }
            )
        if not sink.binding.omnigentSessionId:
            raise ValueError("Contract continuation has no authoritative session")
        previous_progress = progress
        instruction = (continuation or {}).get("instructions") or (
            "Continue the resolved Skill in this same workspace. Its declared terminal "
            "evidence is incomplete: "
            + ", ".join(evaluation.missing_evidence)
            + ". Preserve immutable inputs and authority; finish and verify the remaining work."
        )
        # Persist the actual instruction before dispatch. Retry never substitutes
        # a newly observed instruction for a turn that might already be running.
        saved = phases.get(f"continuation:{ordinal + 1}")
        if saved is not None:
            instruction = saved["instructions"]
        else:
            await sink.record_phase(
                f"continuation:{ordinal + 1}",
                {
                    "instructions": instruction,
                    "progressKey": progress,
                },
            )
    raise AssertionError("unreachable bounded continuation state")
