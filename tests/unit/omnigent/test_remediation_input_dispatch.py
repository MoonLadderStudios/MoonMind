from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.workflows.temporal.activities import (
    omnigent_activities,
    omnigent_session_activities,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)


def remediation_request(request):
    return request.model_copy(
        update={
            "parameters": {
                **request.parameters,
                "workflow": {"remediation": {"target": {"workflowId": "mm:failed"}}},
            }
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleaned", [False, True])
@pytest.mark.parametrize("context_ref", [None, "artifact://art_context"])
async def test_generic_remediation_inputs_are_required_only_for_new_dispatch(
    monkeypatch, cleaned, context_ref
):
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    cleanup_materialized = harness.realizer._credentials.cleanup_all

    async def cleanup_credentials(handles):
        # Missing inputs can stop before credential materialization. The real
        # provisioner accepts no handles; this harness normally assumes one.
        return await cleanup_materialized(handles) if handles else ()

    monkeypatch.setattr(
        harness.realizer._credentials, "cleanup_all", cleanup_credentials
    )
    if cleaned:
        result = await harness.realizer.execute(harness.publish_request, plan)
        harness.events.clear()
    request = remediation_request(harness.publish_request)
    lookup = AsyncMock(return_value=context_ref)
    monkeypatch.setattr(
        omnigent_session_activities, "_remediation_context_input_ref", lookup
    )
    prepare = AsyncMock(wraps=harness.realizer._host_runtime.prepare)
    monkeypatch.setattr(harness.realizer._host_runtime, "prepare", prepare)

    async def dispatch(bound, **_kwargs):
        return await harness.realizer.execute(bound, plan)

    monkeypatch.setattr(omnigent_activities, "_try_generic_realizer_dispatch", dispatch)
    if not cleaned and context_ref is None:
        with pytest.raises(ValueError, match="remediation context"):
            await ActivityEnvironment().run(
                omnigent_activities.omnigent_execute_activity, request
            )
        prepare.assert_not_awaited()
        assert "message-completed" not in harness.events
        assert "provider-released" in harness.events
    else:
        actual = await ActivityEnvironment().run(
            omnigent_activities.omnigent_execute_activity, request
        )
        if cleaned:
            assert actual == result
    if cleaned:
        lookup.assert_not_awaited()
        prepare.assert_not_awaited()
        assert "message-completed" not in harness.events
    elif context_ref:
        assert prepare.await_args.kwargs["request"].input_refs == [context_ref]
        assert "message-completed" in harness.events


@pytest.mark.asyncio
async def test_pending_generic_cleanup_does_not_require_remediation_context(
    monkeypatch,
):
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    await _prime_attested_host_binding(harness, plan)
    key = stable_binding_id(
        execution_plan_ref=plan.planRef,
        idempotency_key=harness.publish_request.idempotency_key,
    )
    binding = await harness.runtime_store.get(key)
    for state in (RuntimeBindingState.failed, RuntimeBindingState.cleanup_pending):
        binding = await harness.runtime_store.update(
            key,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        omnigent_session_activities, "_remediation_context_input_ref", lookup
    )
    with pytest.raises(HarnessPlatformError, match="requires fenced cleanup"):
        await harness.realizer.execute(
            remediation_request(harness.publish_request), plan
        )
    assert (await harness.runtime_store.get(key)).state is RuntimeBindingState.cleaned
    assert "provider-released" in harness.events
    assert "message-completed" not in harness.events
    lookup.assert_not_awaited()
