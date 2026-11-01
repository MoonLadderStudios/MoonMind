"""Native context lineage through the existing runtime-binding authority."""

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.runtime_bindings import (
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)

AUTHORITY_PHASE = "runtimeContextAuthority"
CONTINUATION_PHASE = "runtimeContextContinuation"


def _conflict(reason):
    raise HarnessPlatformError(
        reason, code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT
    )


def _profiles(binding):
    return {
        slot: {
            "providerProfileRef": value.get("providerProfileRef"),
            "credentialGeneration": value.get("credentialGeneration"),
        }
        for slot, value in binding.providerLeases.items()
    }


async def record_runtime_context_authority(*, request, plan, binding, store):
    """Freeze selected context before credentials are materialized or a host runs.

    Every attempt keeps its own canonical session, resources and cleanup fence.
    Reuse carries a completed context, never the old attempt's live authority.
    """
    step = request.step_execution
    if step is None or plan.payload.harnessId != "codex-native":
        if step is not None and step.runtime_session_context is not None:
            _conflict("shared runtime context requires the selected Codex harness")
        return binding
    reference = step.runtime_session_context
    continuation = None
    epoch = step.step_execution_id
    if reference is not None:
        if step.runtime_context_policy != "reuse_session_same_epoch":
            _conflict("runtime context was supplied for a fresh execution")
        source = await store.get(reference.runtime_binding_ref)
        if source is None or source.bindingId == binding.bindingId:
            _conflict("runtime context source binding is unavailable")
        authority = (source.phaseResults or {}).get(AUTHORITY_PHASE)
        if (
            source.state is not RuntimeBindingState.cleaned
            or not source.omnigentSessionId
            or source.terminalResult is None
            or not isinstance(authority, dict)
        ):
            _conflict("runtime context source has no settled shared native authority")
        expected = reference.model_dump(
            by_alias=True, mode="json", exclude={"runtime_binding_ref"}
        )
        if any(authority.get(key) != value for key, value in expected.items()):
            _conflict("runtime context reference conflicts with its recorded source")
        if (
            reference.workflow_id != step.workflow_id
            or authority.get("harnessId") != plan.payload.harnessId
            or authority.get("endpointRef") != plan.payload.endpointRef
            or authority.get("profiles") != _profiles(binding)
            or authority.get("profiles") != _profiles(source)
            or not authority.get("profiles")
            or any(
                not value.get("providerProfileRef")
                or type(value.get("credentialGeneration")) is not int
                or value["credentialGeneration"] < 1
                for value in authority["profiles"].values()
            )
        ):
            _conflict(
                "runtime context crosses its workflow, harness, endpoint or credential authority"
            )
        epoch = reference.epoch
        continuation = {
            "sourceSessionId": source.omnigentSessionId,
            "sourceRuntimeBindingRef": source.bindingId,
            "sourceSnapshotRef": source.latestSnapshotRef,
            "epoch": epoch,
        }
    authority = {
        "workflowId": step.workflow_id,
        "runId": step.run_id,
        "stepExecutionId": step.step_execution_id,
        "epoch": epoch,
        "harnessId": plan.payload.harnessId,
        "endpointRef": plan.payload.endpointRef,
        "profiles": _profiles(binding),
    }
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(AUTHORITY_PHASE, authority)
    if continuation is not None:
        await sink.record_phase(CONTINUATION_PHASE, continuation)
    elif CONTINUATION_PHASE in (binding.phaseResults or {}):
        _conflict("a resumed runtime binding lost its admitted context")
    return sink.binding


def runtime_context_reference(binding):
    authority = (binding.phaseResults or {}).get(AUTHORITY_PHASE)
    if not authority or not binding.omnigentSessionId:
        return None
    return {
        **{
            key: authority[key]
            for key in ("workflowId", "runId", "stepExecutionId", "epoch")
        },
        "runtimeBindingRef": binding.bindingId,
    }
