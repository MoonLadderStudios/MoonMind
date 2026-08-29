"""Data-driven registration of approved Omnigent harnesses.

Source issue: MoonLadderStudios/MoonMind#3711.

Approving a harness for the MoonMind product is *registration data*: a canonical
id, its endpoint aliases, the product execution target it launches through, the
Host Class that admits it, and the credential materializer its auth model
requires. Canonical session lifecycle code, the execution-plan compiler, and the
planning service all read this registry, so adding an approved harness does not
add a lifecycle branch, an activity edit, or another harness-name comparison.

The registry is pure. It carries no image digest, credential, endpoint URL, or
deployment default: exact host image and attestation authority stay with Host
Classes and the synchronized catalog, which remain authoritative when present.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class HarnessProductRegistration(BaseModel):
    """One approved harness and the product authority refs bound to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    harnessId: str = Field(alias="harnessId")
    aliases: tuple[str, ...] = ()
    executionTargetRef: str = Field(alias="executionTargetRef")
    hostClassRef: str = Field(alias="hostClassRef")
    materializerRef: str = Field(alias="materializerRef")
    authModel: str = Field(alias="authModel")
    integrationMode: str = Field("native-server", alias="integrationMode")


_REGISTRATIONS: dict[str, HarnessProductRegistration] = {}
_ALIASES: dict[str, str] = {}


def register_harness_product(registration: HarnessProductRegistration) -> None:
    """Register an approved harness. Re-registering the same data is a no-op."""

    existing = _REGISTRATIONS.get(registration.harnessId)
    if existing is not None and existing != registration:
        raise HarnessPlatformError(
            f"harness {registration.harnessId} is already registered with "
            "different product authority",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    # Canonical ids and aliases share one resolution namespace, and
    # ``canonical_harness_id`` reads ``_ALIASES`` first. So an alias that
    # shadows another harness's canonical id silently reroutes that harness to
    # the wrong product authority, and a canonical id that shadows an existing
    # alias registers a harness no lookup can reach. Validate both names
    # against both registries before mutating either one.
    alias_owner = _ALIASES.get(registration.harnessId)
    if alias_owner is not None and alias_owner != registration.harnessId:
        raise HarnessPlatformError(
            f"harness {registration.harnessId} is already an alias of "
            f"{alias_owner}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    for alias in registration.aliases:
        owner = _ALIASES.get(alias)
        if owner is not None and owner != registration.harnessId:
            raise HarnessPlatformError(
                f"harness alias {alias} already resolves to {owner}",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
            )
        if alias != registration.harnessId and alias in _REGISTRATIONS:
            raise HarnessPlatformError(
                f"harness alias {alias} collides with canonical harness "
                f"{alias}",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
            )
    _REGISTRATIONS[registration.harnessId] = registration
    for alias in registration.aliases:
        _ALIASES[alias] = registration.harnessId


def canonical_harness_id(value: object) -> str:
    """Normalize an endpoint, Agent Profile, or authored harness name.

    Agent Profile v2 documents carry the harness as ``{"id": ...}``; v1 and
    endpoint payloads carry a bare short name. Unknown values are returned
    normalized rather than rejected so trust classification, not string
    matching, stays the authority that admits a harness.
    """

    if isinstance(value, dict):
        value = value.get("id") or value.get("harnessId") or value.get("harness_id")
    normalized = str(value or "").strip().lower()
    return _ALIASES.get(normalized, normalized)


def harness_registration(harness_id: str) -> HarnessProductRegistration:
    """Return the product registration for an approved harness."""

    registration = _REGISTRATIONS.get(canonical_harness_id(harness_id))
    if registration is None:
        raise HarnessPlatformError(
            f"harness {harness_id} has no approved product registration",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    return registration


def find_harness_registration(
    harness_id: str,
) -> HarnessProductRegistration | None:
    """Return the registration for ``harness_id`` or ``None`` when unapproved."""

    return _REGISTRATIONS.get(canonical_harness_id(harness_id))


def product_execution_target_ref(harness_id: str) -> str:
    """Return the product execution target an approved harness launches with."""

    return harness_registration(harness_id).executionTargetRef


def approved_harness_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRATIONS))


register_harness_product(
    HarnessProductRegistration.model_validate(
        {
            "harnessId": "codex-native",
            "aliases": ["codex"],
            "executionTargetRef": "omnigent-codex@1",
            "hostClassRef": "omnigent-codex-current@1",
            "materializerRef": "codex-oauth-home@1",
            "authModel": "oauth_volume",
        }
    )
)
register_harness_product(
    HarnessProductRegistration.model_validate(
        {
            "harnessId": "opencode-native",
            "aliases": ["opencode"],
            "executionTargetRef": "omnigent-opencode@1",
            "hostClassRef": "omnigent-opencode@1",
            "materializerRef": "opencode-auth-json@1",
            "authModel": "own-auth",
        }
    )
)
register_harness_product(
    HarnessProductRegistration.model_validate(
        {
            "harnessId": "pi-native",
            "aliases": ["pi"],
            "executionTargetRef": "omnigent-pi@1",
            "hostClassRef": "omnigent-pi@1",
            "materializerRef": "omnigent-provider-config@1",
            "authModel": "omnigent-provider-config",
        }
    )
)


__all__ = [
    "HarnessProductRegistration",
    "approved_harness_ids",
    "canonical_harness_id",
    "find_harness_registration",
    "harness_registration",
    "product_execution_target_ref",
    "register_harness_product",
]
