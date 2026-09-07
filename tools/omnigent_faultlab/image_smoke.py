"""Deterministic fault matrix for the exact deployable image (AC7 image layer).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — a small
deterministic fault matrix must run inside the deployable API and worker images so
image authority drift (#3694) and process/runtime-dependency behavior are
included, not only the developer checkout).

This module is the *portable core* of the exact-image smoke: it runs a bounded,
seed-selected fault matrix (the same generator, reducer, reference model, and
invariants the pure-domain suite uses) and produces a secret-safe report. The
faultlab package lives under ``tools/`` (MoonLadderStudios/MoonMind#3958) and is
deliberately absent from the production ``moonmind`` package and deployable
image. The ``omnigent-fault-image-smoke`` workflow mounts only the checkout's
``tools/`` tree into the built API/worker image at ``/src/tools`` and runs this
exact module there with ``PYTHONPATH=/app:/src``: ``moonmind`` (including the
production reconciler under test) resolves from the image's own ``/app`` while
``tools`` resolves from the mounted checkout, so a divergence between the
checkout and the image (a missing dependency, a different Python, a stripped
module) surfaces as a failed matrix rather than passing silently. Mounting only
``tools/`` keeps ``/src/moonmind`` and ``/src/api_service`` off ``PYTHONPATH``;
the driver additionally fails unless production imports resolve under ``/app``.

The report is digest-only and bounded (seed, invariant-violation count,
determinism flag) so retained smoke evidence never carries raw payloads or
secrets (invariant 11).
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path

from .ci_seeds import resolve_seed_corpus
from .generator import generate_plan, is_deterministic
from .harness import run_plan
from .invariants import violations

#: The image smoke deliberately runs a *small* bounded matrix so it stays a fast
#: liveness/authority check on the exact image, not a full sweep.
DEFAULT_IMAGE_SMOKE_SEED_COUNT = 16

#: Where the deployable image stamps its own build id (``api-runtime`` stage of
#: ``api_service/Dockerfile``). The driver runs *inside* the image, so it reads
#: this to self-report the image's verified build identity rather than trusting a
#: workflow-checkout commit that need not match an arbitrary pinned digest.
DEFAULT_BUILD_ID_FILE = "/app/.moonmind-build-id"
BUILD_ID_FILE_ENV = "MOONMIND_BUILD_ID_FILE"

#: The real runtime startup module each deployment role launches from the single
#: shared app image. The smoke resolves the role's module spec in the exact image
#: so a role-specific dependency stripped from the shipped image fails the smoke,
#: rather than running the identical command twice and only renaming the report.
_ROLE_ENTRYPOINT_MODULES = {
    "api": "api_service.main",
    "worker": "moonmind.workflows.temporal.worker_runtime",
}


def read_image_build_id(build_id_file: str | os.PathLike[str] | None = None) -> str | None:
    """Return the image's stamped build id, or ``None`` when it is not present.

    Read from inside the running image so the recorded provenance is the image's
    own verified identity. A missing file (for example a local checkout that was
    never built into an image) yields ``None`` rather than an error.
    """

    path = Path(
        build_id_file
        if build_id_file is not None
        else os.environ.get(BUILD_ID_FILE_ENV, DEFAULT_BUILD_ID_FILE)
    )
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


class UnknownImageSmokeRoleError(ValueError):
    """Raised when an image-smoke role has no known runtime startup module."""


def verify_role_entrypoint(role: str) -> str:
    """Resolve the module the given deployment role starts from in this image.

    Uses :func:`importlib.util.find_spec` so a stripped or missing role module in
    the shipped image is caught (authority drift #3694) without executing the
    server/worker startup and its side effects. Returns the module name; raises
    :class:`ImportError` when the role's module is absent and
    :class:`UnknownImageSmokeRoleError` for an unknown role.
    """

    try:
        module = _ROLE_ENTRYPOINT_MODULES[role]
    except KeyError as exc:
        raise UnknownImageSmokeRoleError(
            f"unknown image-smoke role {role!r}; expected one of "
            f"{sorted(_ROLE_ENTRYPOINT_MODULES)}"
        ) from exc
    if importlib.util.find_spec(module) is None:
        raise ImportError(
            f"role {role!r} startup module {module!r} is not importable in this "
            "image; the shipped runtime is missing its role dependency"
        )
    return module


#: Production top-level packages that must resolve from the image's own
#: ``/app`` install, never from a workflow-checkout mount on ``PYTHONPATH``.
_IMAGE_PRODUCTION_PACKAGES = ("moonmind", "api_service")


def verify_production_imports_from_image(*, enforce: bool | None = None) -> None:
    """Fail unless production packages resolve beneath the image's ``/app``.

    The smoke workflow mounts only ``tools/`` at ``/src/tools``, so
    ``/src/moonmind`` and ``/src/api_service`` must not exist on the probe
    path. If a future workflow change ever re-exposes checkout production
    sources on ``PYTHONPATH``, a broken image could pass by falling back to
    checkout copies; this guard turns that misconfiguration into a hard
    failure instead of a false-passing report.

    When ``enforce`` is ``None`` (default) the check auto-detects: it enforces
    only when an image-like ``/app`` install is present (``/app/moonmind`` or
    ``/app/api_service`` exists), and skips in a plain developer checkout or
    unit-test environment where production packages legitimately resolve from
    the working tree. Pass ``enforce=True`` to require ``/app`` resolution
    unconditionally (for example in hermetic tests of this guard itself).
    """

    app_root = Path("/app")
    image_like = (app_root / "moonmind").is_dir() or (
        app_root / "api_service"
    ).is_dir()
    if enforce is None and not image_like:
        return
    if enforce is False:
        return
    offenders: list[str] = []
    for package in _IMAGE_PRODUCTION_PACKAGES:
        spec = importlib.util.find_spec(package)
        if spec is None or spec.origin is None:
            offenders.append(f"{package} is not importable in this image")
            continue
        origin = os.path.realpath(spec.origin)
        if not (origin == "/app" or origin.startswith("/app/")):
            offenders.append(
                f"{package} resolves to {spec.origin}, not the image's /app install"
            )
    if offenders:
        raise ImportError(
            "production imports must resolve beneath /app in this image: "
            + "; ".join(offenders)
        )


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
    """The secret-safe report of a whole exact-image fault matrix run.

    Provenance is the image's own verified identity: ``image_ref`` is the
    digest-pinned image the matrix ran against and ``image_build_id`` is the build
    id the image stamped on itself. Neither is a workflow-checkout commit, so the
    evidence can never be misattributed to a source revision that does not match
    the pinned digest. ``role`` records which deployment role's runtime surface
    this run exercised.
    """

    schema_version: str
    seed_count: int
    image_ref: str | None = None
    image_build_id: str | None = None
    role: str | None = None
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
            "imageRef": self.image_ref,
            "imageBuildId": self.image_build_id,
            "role": self.role,
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


IMAGE_SMOKE_SCHEMA_VERSION = "moonmind.omnigent-fault-image-smoke/v2"


def _seed_matrix(seed_count: int) -> list[int]:
    """Resolve the bounded smoke seed window (rotating policy aware, PR-safe).

    The full seed corpus resolves the rotating window when enabled and the fixed
    PR corpus otherwise; the smoke keeps it bounded to a small, fast matrix.
    """

    corpus = resolve_seed_corpus()
    return list(corpus)[:seed_count]


def run_image_fault_matrix(
    *,
    seed_count: int = DEFAULT_IMAGE_SMOKE_SEED_COUNT,
    image_ref: str | None = None,
    role: str | None = None,
    build_id_file: str | os.PathLike[str] | None = None,
    verify_image_authority: bool | None = None,
) -> ImageSmokeReport:
    """Run the bounded fault matrix on the current (in-image) runtime.

    Each seed's plan is played against the production reducer and cross-checked
    against the twelve invariants and strict determinism. When ``role`` is set the
    role's real startup module is resolved in the exact image first, so a
    role-specific dependency missing from the shipped runtime fails the smoke. The
    result is a bounded, secret-safe report the CI job uploads as the exact-image
    smoke evidence, stamped with the image's own verified build identity.
    Production packages are verified to resolve beneath the image's ``/app``
    install before the matrix runs, so checkout sources can never mask a broken
    image.
    """

    verify_production_imports_from_image(enforce=verify_image_authority)
    if role is not None:
        verify_role_entrypoint(role)

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
        seed_count=seed_count,
        image_ref=image_ref,
        image_build_id=read_image_build_id(build_id_file),
        role=role,
        results=tuple(results),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: run the fault matrix inside the exact deployable image.

    Runnable as ``python -m tools.omnigent_faultlab.image_smoke`` from a checkout.
    The ``omnigent-fault-image-smoke`` workflow mounts only ``tools/`` at
    ``/src/tools`` and runs this module inside the image with
    ``PYTHONPATH=/app:/src`` so the production reconciler and role entrypoints
    resolve from the image's own ``/app`` install while the test-only faultlab
    harness resolves from the mount (MoonLadderStudios/MoonMind#3958). Writes a
    secret-safe report and exits non-zero on any invariant violation or
    nondeterminism.
    """

    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write the secret-safe JSON report (default: stdout only).",
    )
    parser.add_argument(
        "--seed-count",
        type=int,
        default=DEFAULT_IMAGE_SMOKE_SEED_COUNT,
        help="Bounded number of seeds in the smoke matrix.",
    )
    parser.add_argument(
        "--image-ref",
        default=None,
        help="Digest-pinned image the matrix ran against, recorded in the report.",
    )
    parser.add_argument(
        "--role",
        choices=sorted(_ROLE_ENTRYPOINT_MODULES),
        default=None,
        help="Deployment role whose runtime startup surface to exercise.",
    )
    args = parser.parse_args(argv)

    report = run_image_fault_matrix(
        seed_count=args.seed_count, image_ref=args.image_ref, role=args.role
    )
    payload = report.to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)

    if not report.ok:
        print(
            f"FAULT IMAGE SMOKE FAILED: failing seeds {list(report.failing_seeds)}",
            file=sys.stderr,
        )
        return 1
    print(
        f"FAULT IMAGE SMOKE OK: {report.seed_count} seeds, zero invariant violations",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "DEFAULT_IMAGE_SMOKE_SEED_COUNT",
    "IMAGE_SMOKE_SCHEMA_VERSION",
    "BUILD_ID_FILE_ENV",
    "DEFAULT_BUILD_ID_FILE",
    "ImageSeedResult",
    "ImageSmokeReport",
    "UnknownImageSmokeRoleError",
    "read_image_build_id",
    "verify_production_imports_from_image",
    "verify_role_entrypoint",
    "run_image_fault_matrix",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised via the image smoke job
    raise SystemExit(main())
