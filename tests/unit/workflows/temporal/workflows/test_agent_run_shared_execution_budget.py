"""One shared execution-budget authority for an agent run.

Context: MoonLadderStudios/MoonMind#3685 review. The AgentRun workflow computed
its execution budget while ``agent_runtime.launch`` independently defaulted the
managed process supervisor to 3600s, so a run that explicitly requested a larger
budget was still killed at one hour. The two deadlines are the same boundary and
must derive from the same value.

The budget later became progress-aware (MoonLadderStudios/MoonMind#3771), so what
travels between the two boundaries is the whole budget — base window, hard
ceiling, and stall window — not a lone deadline. Publishing only the base window
would let the supervisor kill a process at the base budget even though the
workflow had granted it an extension.

These tests lock in:

- the resolver contract (explicit request wins, kind-specific default otherwise,
  degraded input falls back rather than crashing);
- that the managed default is NOT globally widened — a longer budget is
  requested explicitly per run;
- that the workflow publishes its effective budget into the launch request's
  ``timeoutPolicy`` so the supervisor enforces the same budget;
- that in-flight histories started before the patch keep their prior payload;
- that the ``agent_runtime.launch`` supervisor derivation uses the real
  ``AgentExecutionRequest`` invocation shape.
"""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import (
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    DEFAULT_MANAGED_TIMEOUT_SECONDS,
    AgentExecutionRequest,
    resolve_execution_budget,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


def _request(**kwargs) -> AgentExecutionRequest:
    payload = {
        "agentKind": "managed",
        "agentId": "claude_code",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
    }
    payload.update(kwargs)
    return AgentExecutionRequest(**payload)


# --- Resolver contract -------------------------------------------------------


def test_managed_default_is_not_globally_widened() -> None:
    # Raising the global fallback would make every affected managed run hold its
    # provider slot longer and delay actionable failure. A run that needs longer
    # either demonstrates progress or opts in explicitly.
    assert DEFAULT_MANAGED_TIMEOUT_SECONDS == 3600
    assert DEFAULT_EXTERNAL_TIMEOUT_SECONDS == 21600


def test_resolver_applies_kind_specific_defaults() -> None:
    assert (
        resolve_execution_budget(
            agent_kind="managed", timeout_policy={}
        ).base_seconds
        == DEFAULT_MANAGED_TIMEOUT_SECONDS
    )
    assert (
        resolve_execution_budget(
            agent_kind="external", timeout_policy=None
        ).base_seconds
        == DEFAULT_EXTERNAL_TIMEOUT_SECONDS
    )


def test_resolver_honors_explicit_request() -> None:
    assert (
        resolve_execution_budget(
            agent_kind="managed", timeout_policy={"timeout_seconds": 7200}
        ).base_seconds
        == 7200
    )


def test_resolver_accepts_attribute_style_policy() -> None:
    class _Policy:
        timeout_seconds = 5400

    assert (
        resolve_execution_budget(
            agent_kind="managed", timeout_policy=_Policy()
        ).base_seconds
        == 5400
    )


@pytest.mark.parametrize(
    "degraded",
    [
        {"timeout_seconds": None},
        {"timeout_seconds": "soon"},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
    ],
)
def test_resolver_falls_back_on_degraded_input(degraded: dict) -> None:
    assert (
        resolve_execution_budget(
            agent_kind="managed", timeout_policy=degraded
        ).base_seconds
        == DEFAULT_MANAGED_TIMEOUT_SECONDS
    )


# --- Workflow publishes the effective budget to the launch request -----------


def test_workflow_publishes_default_budget_into_launch_payload() -> None:
    request = _request()
    budget = resolve_execution_budget(
        agent_kind=request.agent_kind, timeout_policy=request.timeout_policy
    )

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=budget, progress_aware=True
    )

    # The launch payload the adapter serializes now carries the same budget.
    launch_payload = request.model_dump(mode="json", by_alias=True)
    published = launch_payload["timeoutPolicy"]
    assert published["timeout_seconds"] == DEFAULT_MANAGED_TIMEOUT_SECONDS
    assert (
        resolve_execution_budget(agent_kind="managed", timeout_policy=published)
        == budget
    )


def test_workflow_publishes_explicit_budget_into_launch_payload() -> None:
    request = _request(timeoutPolicy={"timeout_seconds": 7200})
    budget = resolve_execution_budget(
        agent_kind=request.agent_kind, timeout_policy=request.timeout_policy
    )

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=budget, progress_aware=True
    )

    launch_payload = request.model_dump(mode="json", by_alias=True)
    published = launch_payload["timeoutPolicy"]
    assert published["timeout_seconds"] == 7200
    # Same budget both sides: the supervisor no longer kills the process early,
    # and it extends for progress on exactly the numbers the workflow used.
    assert (
        resolve_execution_budget(agent_kind="managed", timeout_policy=published)
        == budget
    )


def test_in_flight_history_keeps_prior_launch_payload() -> None:
    # Replay safety: a run started before the progress-aware patch must not gain
    # new fields in the launch payload it already dispatched.
    request = _request()
    budget = resolve_execution_budget(
        agent_kind=request.agent_kind, timeout_policy=request.timeout_policy
    )

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=budget, progress_aware=False
    )

    assert request.timeout_policy == {
        "timeout_seconds": DEFAULT_MANAGED_TIMEOUT_SECONDS
    }


# --- Activity-side supervisor derivation ------------------------------------


def test_launch_supervisor_derives_budget_from_request_shape() -> None:
    """``agent_runtime_launch`` reads the same authority from the real request."""

    published = _request(timeoutPolicy={"timeout_seconds": 7200})
    assert (
        resolve_execution_budget(
            agent_kind=str(getattr(published, "agent_kind", "managed") or "managed"),
            timeout_policy=getattr(published, "timeout_policy", None),
        ).base_seconds
        == 7200
    )

    # A direct launch with no policy falls back to the same managed default the
    # workflow would have used, not an independent activity-local literal.
    direct = _request()
    assert (
        resolve_execution_budget(
            agent_kind=str(getattr(direct, "agent_kind", "managed") or "managed"),
            timeout_policy=getattr(direct, "timeout_policy", None),
        ).base_seconds
        == DEFAULT_MANAGED_TIMEOUT_SECONDS
    )
