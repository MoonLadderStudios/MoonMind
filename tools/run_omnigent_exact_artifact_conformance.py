#!/usr/bin/env python3
"""Tier-1 exact deployable-artifact conformance driver (noncredentialed).

Source issue: MoonLadderStudios/MoonMind#3710.

This is the CI entrypoint for the required, per-PR, noncredentialed
exact-artifact gate.  It tests the **exact deployable images by immutable
digest**:

* it runs the in-image capability probe (``tools/omnigent_exact_artifact_probe``)
  inside the ``server`` and ``worker`` images via ``docker run`` — proving the
  deployed process retains the required import/introspection capabilities (for
  example the Uvicorn WebSocket implementation dropped in #3697);
* it merges those results with the *runtime* evidence gathered by the
  surrounding workflow steps (HTTP/SSE/WebSocket route handshakes, clean and
  materialized-prior-revision database migrations, an entrypoint restart
  against the migrated schema, worker task-queue/readiness advertisement
  against a real Temporal server, and the compiled native-UI hosted-boot /
  no-root-``/v1`` network capture); and
* it feeds the assembled report to
  :func:`moonmind.omnigent.exact_artifact_conformance.evaluate_exact_artifact_conformance`,
  which is the authoritative fail-closed decision, then writes the retained
  evidence and exits non-zero on any gate failure.

Building/pulling images and gathering runtime evidence requires Docker, which
is unavailable inside MoonMind-managed agent workspaces; the driver fails loud
with an actionable message rather than silently skipping when Docker is
missing.  The report-assembly and evaluation core is pure and unit-tested so
the same gate CI relies on is exercised without a container runtime.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.exact_artifact_conformance import (  # noqa: E402
    REQUIRED_IMAGE_ROLES,
    ExactArtifactConformanceError,
    evaluate_exact_artifact_conformance,
)

IN_IMAGE_PROBE = "tools/omnigent_exact_artifact_probe.py"
IN_IMAGE_ROLES = ("server", "worker")

# The probe harness is supplied to the deployable image through an explicit
# read-only mount rather than being copied into the production image, which
# keeps the deployable artifact free of test-only assets.  ``PYTHONPATH`` points
# at the image's own application root only, so every import the probe resolves
# comes from the artifact under test and never from the mount.
PROBE_MOUNT = "/probe"
APP_ROOT = "/app"


class DriverError(RuntimeError):
    """Raised when the exact-artifact driver cannot produce a verdict."""


def _require_docker() -> None:
    if shutil.which("docker") is None:
        raise DriverError(
            "docker CLI is required to test the exact deployable images by "
            "digest; it is unavailable in this environment. Run this gate on a "
            "runner with Docker (the CI Tier-1 job) — do not skip it."
        )


def in_image_probe_command(image: str, role: str) -> list[str]:
    """Build the ``docker run`` command for the in-image capability probe.

    ``image`` must be locally resolvable.  A locally built image has no registry
    repo digest, so ``name@sha256:<image id>`` is unpullable and Docker would try
    to reach a registry; the caller therefore passes the ``sha256:<image id>``
    content id, which is both immutable and locally resolvable.
    """

    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-w",
        APP_ROOT,
        "-e",
        f"PYTHONPATH={APP_ROOT}",
        "-v",
        f"{REPO_ROOT}:{PROBE_MOUNT}:ro",
        "--entrypoint",
        "python",
        image,
        f"{PROBE_MOUNT}/{IN_IMAGE_PROBE}",
        "--role",
        role,
    ]


def run_in_image_probe(image: str, role: str) -> list[dict[str, Any]]:
    """Run the in-image capability probe inside the exact image."""
    completed = subprocess.run(
        in_image_probe_command(image, role),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise DriverError(
            f"in-image probe failed for role {role!r} (exit {completed.returncode}): "
            f"{completed.stderr.strip()[:500]}"
        )
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DriverError(
            f"in-image probe for role {role!r} did not emit valid JSON"
        ) from exc
    if not isinstance(parsed, list):
        raise DriverError(f"in-image probe for role {role!r} must emit a list")
    return parsed


def _merge_capabilities(
    in_image: Mapping[str, Sequence[Mapping[str, Any]]],
    runtime: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge in-image and runtime capability signals per role (runtime wins)."""
    merged: dict[str, list[dict[str, Any]]] = {}
    roles = set(in_image) | set(runtime) | set(REQUIRED_IMAGE_ROLES)
    for role in roles:
        by_name: dict[str, dict[str, Any]] = {}
        for entry in in_image.get(role, ()):  # type: ignore[arg-type]
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                by_name[entry["name"]] = dict(entry)
        for entry in runtime.get(role, ()):  # type: ignore[arg-type]
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                by_name[entry["name"]] = dict(entry)
        merged[role] = list(by_name.values())
    return merged


def assemble_report(
    *,
    images: Mapping[str, str],
    source_commit: str,
    in_image_probes: Mapping[str, Sequence[Mapping[str, Any]]],
    runtime_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble the exact-artifact report from probe + runtime evidence.

    ``runtime_evidence`` is the JSON gathered by the surrounding workflow's
    Docker/Compose steps: ``capabilities`` (per-role runtime signals) and
    ``secretScan``.
    """
    runtime_caps = runtime_evidence.get("capabilities") or {}
    if not isinstance(runtime_caps, Mapping):
        raise DriverError("runtime evidence capabilities must be an object")
    return {
        "sourceCommit": source_commit,
        "images": dict(images),
        "capabilities": _merge_capabilities(in_image_probes, runtime_caps),
        "secretScan": runtime_evidence.get("secretScan") or {"status": "unknown"},
    }


def load_required_digests(
    manifest_path: Path | None, images: Mapping[str, str]
) -> dict[str, str]:
    """Resolve required digests from the admitted compatibility manifest.

    When no manifest is supplied the deployable image digests are the admitted
    Tier-1 digests (the gate still proves capabilities and convergence).
    """
    if manifest_path is None:
        return {
            role: images[role].rsplit("@", 1)[-1]
            for role in REQUIRED_IMAGE_ROLES
            if role in images and "@" in images[role]
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digests = manifest.get("digests") if isinstance(manifest, Mapping) else None
    if not isinstance(digests, Mapping):
        raise DriverError("compatibility manifest must contain a 'digests' object")
    return {role: str(digests[role]) for role in REQUIRED_IMAGE_ROLES if role in digests}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument(
        "--runnable-image",
        required=True,
        help="Locally resolvable reference to the same immutable artifact used "
        "for the docker runs — the 'sha256:<image id>' content id for a locally "
        "built image, which no registry can resolve by repo digest.",
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--runtime-evidence",
        required=True,
        type=Path,
        help="JSON of runtime capability signals, fake-provider execution, and "
        "secret scan gathered by the workflow's Docker/Compose steps.",
    )
    parser.add_argument(
        "--compatibility-manifest",
        type=Path,
        help="Admitted compatibility manifest JSON with a 'digests' object.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/omnigent-exact-artifact")
    )
    args = parser.parse_args(argv)

    images = {
        "server": args.server_image,
        "worker": args.worker_image,
        "ui": args.ui_image,
    }
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        _require_docker()
        in_image_probes = {
            role: run_in_image_probe(args.runnable_image, role)
            for role in IN_IMAGE_ROLES
        }
        runtime_evidence = json.loads(args.runtime_evidence.read_text(encoding="utf-8"))
        if not isinstance(runtime_evidence, Mapping):
            raise DriverError("runtime evidence must be a JSON object")
        report = assemble_report(
            images=images,
            source_commit=args.source_commit,
            in_image_probes=in_image_probes,
            runtime_evidence=runtime_evidence,
        )
        required_digests = load_required_digests(args.compatibility_manifest, images)
        projection = evaluate_exact_artifact_conformance(
            report, required_digests=required_digests
        )
    except (DriverError, ExactArtifactConformanceError, OSError, json.JSONDecodeError) as exc:
        print(f"::error::exact-artifact conformance could not be evaluated: {exc}")
        return 2

    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "projection.json").write_text(
        json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if projection["verdict"] != "passed":
        codes = ", ".join(f["code"] for f in projection["failures"]) or "unknown"
        print(f"::error::exact-artifact gate failed: {codes}")
        return 1
    print("Exact deployable-artifact conformance passed for all required images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
