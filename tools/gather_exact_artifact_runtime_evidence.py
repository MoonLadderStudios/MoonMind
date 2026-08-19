#!/usr/bin/env python3
"""Gather runtime capability evidence from the exact deployable image.

Source issue: MoonLadderStudios/MoonMind#3710.

Invoked by the Tier-1 exact-artifact CI job.  It exercises the *running* exact
image (built and pinned by the caller) through its real entrypoints and emits
the runtime capability signals that the in-image probe cannot determine by
import alone:

* ``server`` — the API entrypoint starts, HTTP/SSE/WebSocket routes complete a
  handshake or fail through the real handler (never a fall-through HTTP 404),
  and Alembic migrations apply to a clean PostgreSQL and upgrade a prior
  schema;
* ``worker`` — the worker advertises its required task queues and readiness
  capabilities;
* ``ui`` — the compiled native UI consumes the hosted bootstrap and sends no
  root ``/v1/*`` request in hosted mode;
* ``fakeProviderExecution`` — a bounded fake-provider run converges to a
  terminal success state and its history replays after the fake host is
  removed.

Each probe reflects the *observed* result: a failed probe emits ``ok=False``
(never a fabricated pass), so the downstream gate
(:func:`moonmind.omnigent.exact_artifact_conformance.evaluate_exact_artifact_conformance`)
fails closed.  Every Docker/network probe requires a container runtime and is
therefore CI-only; the evidence-assembly and secret-scan core is pure and
unit-tested.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    ConformanceContractError,
    assert_secret_free,
)


def signal(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def build_runtime_evidence(
    *,
    server: Sequence[Mapping[str, Any]],
    worker: Sequence[Mapping[str, Any]],
    ui: Sequence[Mapping[str, Any]],
    fake_provider_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble and secret-scan the runtime evidence document.

    Pure: no container runtime.  Raises if any gathered detail contains
    secret-like material so retained evidence never leaks credentials.
    """
    evidence = {
        "capabilities": {
            "server": [dict(entry) for entry in server],
            "worker": [dict(entry) for entry in worker],
            "ui": [dict(entry) for entry in ui],
        },
        "fakeProviderExecution": dict(fake_provider_execution),
    }
    try:
        assert_secret_free(evidence)
    except ConformanceContractError as exc:
        raise ConformanceContractError(
            "runtime evidence contained secret-like material"
        ) from exc
    evidence["secretScan"] = {"status": "passed", "scope": "exact-artifact-runtime"}
    return evidence


# --- Docker/network probes (CI-only) ---------------------------------------
#
# These are thin wrappers around the running exact image; they are executed
# only when a container runtime is available.  Import them lazily so this
# module is importable (and its pure core testable) without Docker.


def _import_runtime_probes() -> Callable[..., dict[str, Any]]:
    from tools._exact_artifact_runtime_probes import gather_from_image

    return gather_from_image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        gather_from_image = _import_runtime_probes()
        server, worker, ui, fake_provider = gather_from_image(
            image=args.image, database_url=args.database_url
        )
        evidence = build_runtime_evidence(
            server=server,
            worker=worker,
            ui=ui,
            fake_provider_execution=fake_provider,
        )
    except (ConformanceContractError, RuntimeError, OSError) as exc:
        print(f"::error::runtime evidence could not be gathered: {exc}")
        return 2

    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote runtime evidence to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
