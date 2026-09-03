"""Runtime-ownership invariant for Provider Profiles.

Provider Profiles are runtime-owned launch contracts: ``runtime_id`` decides
provider and credential selection, credential materialization, environment and
file shaping, command behavior, model and effort tiers, and concurrency policy.
The same upstream provider therefore needs one profile per runtime rather than a
multi-runtime object.

This module owns the single typed contract for that relationship so every
launch-authoring boundary enforces the same rule before persisting or launching
work. The primary placement is the shared authority handoff every launch
converges on — :meth:`TemporalExecutionService.create_execution` — which covers
direct submission, rerun, continuation, manifest ingest, and
deployment operations from one call site. Boundaries that durably persist a
launch target *before* reaching that handoff enforce it themselves as well.
Recurring-schedule authoring validates the stored target before a later
schedule action launches it. Routers map
:class:`ProviderProfileRuntimeMismatchError` onto an HTTP 409 with
:attr:`ProviderProfileRuntimeMismatchError.detail`; the payload is identical
across authoring paths so an alternate client cannot find a boundary that
accepts a pair the runtime-scoped selectors would never offer.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple

from api_service.db.models import ManagedAgentProviderProfile
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

PROVIDER_PROFILE_RUNTIME_MISMATCH_CODE = "provider_profile_runtime_mismatch"

#: ``omnigent`` is an execution facade rather than a Provider Profile owner: the
#: profile it launches stays owned by the underlying managed runtime, and
#: compatibility is enforced against the selected execution target by
#: :mod:`api_service.services.omnigent_agent_profile_selection`.
OMNIGENT_RUNTIME_ID = "omnigent"

#: Aliases an authored payload may use to name a Provider Profile, in the
#: precedence order the executions router already applies to the same payload.
PROVIDER_PROFILE_REF_KEYS: tuple[str, ...] = (
    "providerProfileRef",
    "profileId",
    "providerProfile",
    "executionProfileRef",
)

_TASK_PAYLOAD_KEYS: tuple[str, ...] = ("workflow", "task")


class ProviderProfileRuntimeMismatchError(ValueError):
    """A Provider Profile was paired with a runtime that does not own it."""

    def __init__(
        self,
        *,
        profile_id: str,
        profile_runtime: str,
        selected_runtime: str,
    ) -> None:
        super().__init__(
            (
                f"Provider Profile {profile_id!r} belongs to runtime "
                f"{profile_runtime!r} and cannot be used with runtime "
                f"{selected_runtime!r}."
            )
            if profile_runtime
            else (
                f"Provider Profile {profile_id!r} does not declare an owning "
                f"runtime and cannot be used with runtime "
                f"{selected_runtime!r}."
            )
        )
        self.profile_id = profile_id
        self.profile_runtime = profile_runtime
        self.selected_runtime = selected_runtime

    @property
    def detail(self) -> dict[str, Any]:
        """Return the error contract shared by every authoring boundary."""

        return {
            "code": PROVIDER_PROFILE_RUNTIME_MISMATCH_CODE,
            "message": str(self),
            "profileId": self.profile_id,
            "profileRuntime": self.profile_runtime,
            "selectedRuntime": self.selected_runtime,
        }


class ProviderProfileNotFoundError(LookupError):
    """An explicitly requested Provider Profile does not exist."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"Provider profile not found: {profile_id!r}.")
        self.profile_id = profile_id


def require_provider_profile_runtime(
    *,
    profile: Any,
    profile_id: str,
    selected_runtime: str | None,
) -> None:
    """Reject a Provider Profile that is not owned by the selected runtime.

    Canonical runtime IDs decide the comparison, so a legacy spelling such as
    ``codex`` is compared as ``codex_cli``. With no profile or no effective
    runtime there is nothing to compare, and the ``omnigent`` facade defers to
    the Omnigent selection service.

    A profile that names no owning runtime is rejected rather than accepted for
    every runtime. ``runtime_id`` is the field that decides credential
    materialization and launch strategy, so a blank or whitespace-only value is
    the ambiguous state this invariant exists to keep out of a launch, not a
    reason to skip the check.
    """

    if profile is None or not selected_runtime:
        return
    canonical_runtime = normalize_runtime_id(selected_runtime)
    if canonical_runtime == OMNIGENT_RUNTIME_ID:
        return
    raw_profile_runtime = str(getattr(profile, "runtime_id", "") or "").strip()
    if not raw_profile_runtime:
        # ``normalize_runtime_id`` substitutes the deployment default for an
        # empty value, so the unset owner is reported verbatim instead.
        raise ProviderProfileRuntimeMismatchError(
            profile_id=profile_id,
            profile_runtime="",
            selected_runtime=canonical_runtime,
        )
    profile_runtime = normalize_runtime_id(raw_profile_runtime)
    if profile_runtime == canonical_runtime:
        return
    raise ProviderProfileRuntimeMismatchError(
        profile_id=profile_id,
        profile_runtime=profile_runtime,
        selected_runtime=canonical_runtime,
    )


async def _load_owned_provider_profile(
    *,
    session: Any,
    profile_id: str,
) -> ManagedAgentProviderProfile | None:
    """Return the persisted Provider Profile row named by *profile_id*.

    The lookup is typed on purpose. Only a real
    :class:`~api_service.db.models.ManagedAgentProviderProfile` row carries an
    authoritative ``runtime_id``; anything else the session hands back names no
    runtime to compare, so it is treated as "no profile" rather than as a
    mismatch. Callers that need to distinguish "profile does not exist" from
    "profile is not runtime-owned" use
    :func:`load_provider_profile_for_runtime` instead.
    """

    session_get = getattr(session, "get", None)
    if session is None or not callable(session_get):
        return None
    profile = await session_get(ManagedAgentProviderProfile, profile_id)
    if not isinstance(profile, ManagedAgentProviderProfile):
        return None
    return profile


async def load_provider_profile_for_runtime(
    *,
    session: Any,
    profile_id: str,
    selected_runtime: str | None,
) -> ManagedAgentProviderProfile | None:
    """Load an explicitly requested Provider Profile for *selected_runtime*.

    Raises :class:`ProviderProfileNotFoundError` when the profile does not
    exist and :class:`ProviderProfileRuntimeMismatchError` when the pair is
    invalid, so an incompatible pair can never reach a persist or a launch.
    """

    session_get = getattr(session, "get", None)
    if session is None or not callable(session_get):
        return None
    profile = await session_get(ManagedAgentProviderProfile, profile_id)
    if profile is None:
        raise ProviderProfileNotFoundError(profile_id)
    require_provider_profile_runtime(
        profile=profile,
        profile_id=profile_id,
        selected_runtime=selected_runtime,
    )
    return profile


def provider_profile_ref_from_mapping(source: Any) -> str | None:
    """Return the Provider Profile named directly on *source*, if any."""

    if not isinstance(source, Mapping):
        return None
    for key in PROVIDER_PROFILE_REF_KEYS:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _task_payload(source: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in _TASK_PAYLOAD_KEYS:
        candidate = source.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    return None


class LaunchTargetProfileSelection(NamedTuple):
    """The runtime/profile selection an authored launch target carries."""

    #: Every distinct canonical runtime the payload names, most authoritative
    #: first. ``runtime_ids[0]`` is the runtime the worker resolves.
    runtime_ids: tuple[str, ...]
    #: The Provider Profile the payload names, if any.
    profile_id: str | None


def resolve_launch_target_profile_selection(
    target: Any,
) -> LaunchTargetProfileSelection:
    """Return the runtime/profile selection authored in a launch target.

    Runtimes are read in the precedence the worker itself applies to the same
    canonical payload — the runtime block's ``mode`` wins over the task block's
    ``targetRuntime``, which wins over the initial parameters, which win over
    the target envelope. Reading these fields in any other order lets a raw
    submission with conflicting runtime fields satisfy this boundary for one
    runtime and then execute as another.

    Every distinct runtime the payload names is returned, not only the winner,
    because a payload that names more than one has no unambiguous authored
    target: the invariant has to hold for each of them before launch.

    The Provider Profile lookup keeps the layered order the executions router
    applies to the same workflow-start payload shape. Nested Omnigent Agent
    Profile selections are deliberately not consulted — they name an Agent
    Profile, not a Provider Profile.
    """

    target_map: Mapping[str, Any] = target if isinstance(target, Mapping) else {}
    parameters: Mapping[str, Any] = {}
    for key in ("initialParameters", "initial_parameters"):
        candidate = target_map.get(key)
        if isinstance(candidate, Mapping):
            parameters = candidate
            break

    task_payload = _task_payload(parameters) or _task_payload(target_map) or {}
    runtime_payload: Mapping[str, Any] = {}
    for candidate in (task_payload.get("runtime"), parameters.get("runtime")):
        if isinstance(candidate, Mapping):
            runtime_payload = candidate
            break

    runtime_ids: list[str] = []
    for source, key in (
        (runtime_payload, "mode"),
        (task_payload, "targetRuntime"),
        (task_payload, "target_runtime"),
        (parameters, "targetRuntime"),
        (parameters, "target_runtime"),
        (target_map, "targetRuntime"),
        (target_map, "target_runtime"),
    ):
        raw_runtime = source.get(key)
        if not str(raw_runtime or "").strip():
            continue
        canonical_runtime = normalize_runtime_id(raw_runtime)
        if canonical_runtime not in runtime_ids:
            runtime_ids.append(canonical_runtime)

    profile_id: str | None = None
    for source in (runtime_payload, task_payload, parameters, target_map):
        profile_id = provider_profile_ref_from_mapping(source)
        if profile_id:
            break

    return LaunchTargetProfileSelection(
        runtime_ids=tuple(runtime_ids),
        profile_id=profile_id,
    )


async def require_launch_target_provider_profile_runtime(
    *,
    session: Any,
    target: Any,
) -> None:
    """Enforce runtime ownership for an authored launch target.

    Applies to any boundary that persists or launches a workflow-start target,
    including recurring schedules whose stored ``initialParameters`` are what a
    later schedule action launches.

    A named profile that does not exist is left to the launch path that
    resolves it: this boundary owns the runtime relationship, and an absent row
    carries no runtime to compare.

    A payload that names conflicting runtimes is checked against every one of
    them. Validating only the winner would let a second authored field decide
    the launch after this boundary approved the pair for a different runtime.
    """

    selection = resolve_launch_target_profile_selection(target)
    profile_id = selection.profile_id
    if not selection.runtime_ids or not profile_id:
        return
    if all(runtime == OMNIGENT_RUNTIME_ID for runtime in selection.runtime_ids):
        return
    profile = await _load_owned_provider_profile(
        session=session,
        profile_id=profile_id,
    )
    for runtime_id in selection.runtime_ids:
        require_provider_profile_runtime(
            profile=profile,
            profile_id=profile_id,
            selected_runtime=runtime_id,
        )
