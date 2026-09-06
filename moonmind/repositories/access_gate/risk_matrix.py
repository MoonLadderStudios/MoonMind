"""Bounded risk-based conformance matrix for the issue #4024 release gate.

A bounded matrix, not the full runtime x source x every-fault Cartesian
product on each PR: every advertised combination names a positive
production journey owner and a negative/recovery owner at the authority
handoff. Common substrate tests are shared only where the same production
implementation is demonstrably used (``shared_substrate`` names it);
differing runtimes, materializers, and storage modes keep
boundary-specific coverage.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdvertisedCombination:
    """One advertised runtime x source x access x output/publication lane."""

    runtime: str
    source: str
    access: str
    output_publication: str
    positive_journey_owner: str
    negative_recovery_owner: str
    shared_substrate: str


def advertised_combinations() -> tuple[AdvertisedCombination, ...]:
    """Return the bounded set of advertised combinations.

    Existing Git/Lore authority and both credentialless/keyed OpenCode
    behavior are preserved as first-class lanes.
    """
    capture_restore = "production capture/restore engines"
    publisher = "existing publisher"
    return (
        AdvertisedCombination(
            runtime="generic-opencode",
            source="scratch",
            access="credentialless",
            output_publication="saved-required/publish-none",
            positive_journey_owner="slice-3/slice-4/slice-6",
            negative_recovery_owner="slice-4",
            shared_substrate=capture_restore,
        ),
        AdvertisedCombination(
            runtime="generic-opencode",
            source="repository",
            access="anonymous",
            output_publication="saved-required/publish-none",
            positive_journey_owner="slice-2/slice-3",
            negative_recovery_owner="slice-2",
            shared_substrate="bound transport clients",
        ),
        AdvertisedCombination(
            runtime="generic-opencode",
            source="repository",
            access="explicit-connection",
            output_publication="saved-required/publish-branch-pr",
            positive_journey_owner="slice-2/slice-5",
            negative_recovery_owner="slice-5",
            shared_substrate=publisher,
        ),
        AdvertisedCombination(
            runtime="managed-profile-bound",
            source="scratch",
            access="keyed-provider-profile",
            output_publication="saved-required/publish-none",
            positive_journey_owner="slice-2/slice-6",
            negative_recovery_owner="slice-2",
            shared_substrate=capture_restore,
        ),
        AdvertisedCombination(
            runtime="generic-codex",
            source="artifact-import",
            access="artifact-authorization",
            output_publication="saved-required/publish-none",
            positive_journey_owner="slice-3/slice-4",
            negative_recovery_owner="slice-4",
            shared_substrate=capture_restore,
        ),
        AdvertisedCombination(
            runtime="generic-claude",
            source="checkpoint-restore",
            access="checkpoint-authorization",
            output_publication="saved-required/publish-saved-work",
            positive_journey_owner="slice-3/slice-4/slice-5",
            negative_recovery_owner="slice-5",
            shared_substrate=publisher,
        ),
        AdvertisedCombination(
            runtime="generic-opencode",
            source="repository",
            access="lore-authoritative",
            output_publication="saved-required/publish-lore",
            positive_journey_owner="slice-2/slice-5",
            negative_recovery_owner="slice-5",
            shared_substrate="Lore provider authority",
        ),
    )


def _key(combination: AdvertisedCombination) -> tuple[str, str, str, str]:
    return (
        combination.runtime,
        combination.source,
        combination.access,
        combination.output_publication,
    )


def positive_owner_for(runtime: str, source: str, access: str,
                       output_publication: str) -> str:
    """Return the positive production journey owner for a lane."""
    for combination in advertised_combinations():
        if _key(combination) == (runtime, source, access, output_publication):
            return combination.positive_journey_owner
    raise KeyError(
        f"unadvertised combination: {runtime}/{source}/{access}/{output_publication}; "
        "unsupported combinations must fail admission, not run unowned"
    )


def negative_owner_for(runtime: str, source: str, access: str,
                       output_publication: str) -> str:
    """Return the named negative/recovery owner for a lane's handoff."""
    for combination in advertised_combinations():
        if _key(combination) == (runtime, source, access, output_publication):
            return combination.negative_recovery_owner
    raise KeyError(
        f"unadvertised combination: {runtime}/{source}/{access}/{output_publication}"
    )
