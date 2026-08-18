#!/usr/bin/env python3
"""Run the Omnigent fault matrix inside the exact deployable image (AC7).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — the
exact-image fault-matrix smoke).

This driver is invoked *inside* the built API and worker images by the
``omnigent-fault-image-smoke`` workflow. It runs a small deterministic fault
matrix against the runtime that is actually shipped (``moonmind`` as installed in
the image), so image authority drift (#3694) — a missing dependency, a different
interpreter, a stripped module — fails the smoke instead of passing silently on a
developer checkout. It writes a secret-safe report and exits non-zero on any
invariant violation or nondeterminism.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Resolve ``moonmind`` from the tree this driver ships in. In the deployable image
# that root is the image's app directory (so the matrix runs against the *image's*
# moonmind, catching authority drift #3694); in a local checkout it is the repo
# root. Prepending it keeps a stale globally-installed copy from shadowing the
# runtime under test.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from moonmind.omnigent.faultlab.image_smoke import (  # noqa: E402
    DEFAULT_IMAGE_SMOKE_SEED_COUNT,
    run_image_fault_matrix,
)


def main() -> int:
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
        "--source-commit",
        default=None,
        help="Commit the image was built from, recorded in the report.",
    )
    args = parser.parse_args()

    report = run_image_fault_matrix(
        seed_count=args.seed_count, source_commit=args.source_commit
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


if __name__ == "__main__":
    raise SystemExit(main())
