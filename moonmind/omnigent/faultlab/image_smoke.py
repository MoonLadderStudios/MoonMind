"""Deterministic fault matrix for the exact deployable image (AC7 image layer).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — a small
deterministic fault matrix must run inside the deployable API and worker images so
image authority drift (#3694) and process/runtime-dependency behavior are
included, not only the developer checkout).

This module is the *portable core* of the exact-image smoke: it runs a bounded,
seed-selected fault matrix (the same generator, reducer, reference model, and
invariants the pure-domain suite uses) and produces a secret-safe report. The
deployable image supplies only the runtime — the CI job runs this exact module
*inside* the built API/worker image, so a divergence between the checkout and the
image (a missing dependency, a different Python, a stripped module) surfaces as a
failed matrix rather than passing silently.

The report is digest-only and bounded (seed, invariant-violation count,
determinism flag) so retained smoke evidence never carries raw payloads or
secrets (invariant 11).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ci_seeds import resolve_seed_corpus
from .generator import generate_plan, is_deterministic
from .harness import run_plan
from .invariants import violations

#: The image smoke deliberately runs a *small* bounded matrix so it stays a fast
#: liveness/authority check on the exact image, not a full sweep.
DEFAULT_IMAGE_SMOKE_SEED_COUNT = 16


@dataclass(frozen=True)
class ImageSeedResult:
    """The bounded, secret-safe result of one seed on the exact image."""

    seed: int
    converged: bool
    deterministic: bool
    violation_count: int
    #: Invariant *names* only (no payloads) so a failure is diagnosable without
    #: leaking scenario content.
    violated_invariants: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.deterministic and self.violation_count == 0


@dataclass(frozen=True)
class ImageSmokeReport:
    """The secret-safe report of a whole exact-image fault matrix run."""

    schema_version: str
    source_commit: str | None
    seed_count: int
    results: tuple[ImageSeedResult, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results) and bool(self.results)

    @property
    def failing_seeds(self) -> tuple[int, ...]:
        return tuple(result.seed for result in self.results if not result.ok)

    def to_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "sourceCommit": self.source_commit,
            "seedCount": self.seed_count,
            "ok": self.ok,
            "failingSeeds": list(self.failing_seeds),
            "results": [
                {
                    "seed": result.seed,
                    "converged": result.converged,
                    "deterministic": result.deterministic,
                    "violationCount": result.violation_count,
                    "violatedInvariants": list(result.violated_invariants),
                    "ok": result.ok,
                }
                for result in self.results
            ],
        }


IMAGE_SMOKE_SCHEMA_VERSION = "moonmind.omnigent-fault-image-smoke/v1"


def _seed_matrix(seed_count: int) -> list[int]:
    """Resolve the bounded smoke seed window (rotating policy aware, PR-safe).

    The full seed corpus resolves the rotating window when enabled and the fixed
    PR corpus otherwise; the smoke keeps it bounded to a small, fast matrix.
    """

    corpus = resolve_seed_corpus()
    return list(corpus)[:seed_count]


def run_image_fault_matrix(
    *, seed_count: int = DEFAULT_IMAGE_SMOKE_SEED_COUNT, source_commit: str | None = None
) -> ImageSmokeReport:
    """Run the bounded fault matrix on the current (in-image) runtime.

    Each seed's plan is played against the production reducer and cross-checked
    against the twelve invariants and strict determinism. The result is a bounded,
    secret-safe report the CI job uploads as the exact-image smoke evidence.
    """

    results: list[ImageSeedResult] = []
    for seed in _seed_matrix(seed_count):
        plan = generate_plan(seed)
        trace = run_plan(plan)
        found = violations(trace)
        results.append(
            ImageSeedResult(
                seed=seed,
                converged=trace.converged,
                deterministic=is_deterministic(plan),
                violation_count=len(found),
                # Keep the invariant name (prefix before the first ": ") only.
                violated_invariants=tuple(
                    sorted({detail.split(":", 1)[0] for detail in found})
                ),
            )
        )
    return ImageSmokeReport(
        schema_version=IMAGE_SMOKE_SCHEMA_VERSION,
        source_commit=source_commit,
        seed_count=seed_count,
        results=tuple(results),
    )


__all__ = [
    "DEFAULT_IMAGE_SMOKE_SEED_COUNT",
    "IMAGE_SMOKE_SCHEMA_VERSION",
    "ImageSeedResult",
    "ImageSmokeReport",
    "run_image_fault_matrix",
]
