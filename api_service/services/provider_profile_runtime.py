"""Runtime-ownership invariant for Provider Profiles.

Provider Profiles are runtime-owned launch contracts: ``runtime_id`` decides
provider and credential selection, credential materialization, environment and
file shaping, command behavior, model and effort tiers, and concurrency policy.
The same upstream provider therefore needs one profile per runtime rather than a
multi-runtime object.

This module owns the single typed contract for that relationship so every
launch-authoring boundary enforces the same rule before persisting or launching
work: direct execution submission, step authoring, and recurring-schedule
authoring. Routers map :class:`ProviderProfileRuntimeMismatchError` onto an HTTP
409 with :attr:`ProviderProfileRuntimeMismatchError.detail`; the payload is
identical across authoring paths so an alternate client cannot find a boundary
that accepts a pair the runtime-scoped selectors would never offer.
"""

from __future__ import annotations

from typing import Any, Mapping

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
            f"Provider Profile {profile_id!r} belongs to runtime "
            f"{profile_runtime!r} and cannot be used with runtime "
            f"{selected_runtime!r}."
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
    ``codex`` is compared as ``codex_cli``. With no profile, no effective
    runtime, or an unset ``runtime_id`` there is nothing to compare, and the
    ``omnigent`` facade defers to the Omnigent selection service.
    """

    if profile is None or not selected_runtime:
        return
    canonical_runtime = normalize_runtime_id(selected_runtime)
    if canonical_runtime == OMNIGENT_RUNTIME_ID:
        return
    raw_profile_runtime = str(getattr(profile, "runtime_id", "") or "").strip()
    if not raw_profile_runtime:
        return
    profile_runtime = normalize_runtime_id(raw_profile_runtime)
    if profile_runtime == canonical_runtime:
        return
    raise ProviderProfileRuntimeMismatchError(
        profile_id=profile_id,
        profile_runtime=profile_runtime,
        selected_runtime=canonical_runtime,
    )


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


def resolve_launch_target_profile_selection(
    target: Any,
) -> tuple[str | None, str | None]:
    """Return ``(canonical_runtime, profile_id)`` authored in a launch target.

    Mirrors the layered lookup the executions router applies to the same
    workflow-start payload shape: the runtime block wins over the task block,
    which wins over the initial parameters, which win over the target envelope.
    Nested Omnigent Agent Profile selections are deliberately not consulted —
    they name an Agent Profile, not a Provider Profile.
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

    raw_runtime = (
        parameters.get("targetRuntime")
        or target_map.get("targetRuntime")
        or runtime_payload.get("mode")
    )
    runtime_id: str | None = None
    if str(raw_runtime or "").strip():
        runtime_id = normalize_runtime_id(raw_runtime)

    profile_id: str | None = None
    for source in (runtime_payload, task_payload, parameters, target_map):
        profile_id = provider_profile_ref_from_mapping(source)
        if profile_id:
            break

    return runtime_id, profile_id


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
    """

    runtime_id, profile_id = resolve_launch_target_profile_selection(target)
    if not runtime_id or not profile_id:
        return
    if runtime_id == OMNIGENT_RUNTIME_ID:
        return
    session_get = getattr(session, "get", None)
    if session is None or not callable(session_get):
        return
    profile = await session_get(ManagedAgentProviderProfile, profile_id)
    require_provider_profile_runtime(
        profile=profile,
        profile_id=profile_id,
        selected_runtime=runtime_id,
    )
