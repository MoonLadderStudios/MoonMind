#!/usr/bin/env python3
"""Run the Omnigent fault matrix inside the exact deployable image (AC7).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — the
exact-image fault-matrix smoke).

This is a thin local-convenience wrapper around the test-tooling CLI in
``tools.omnigent_faultlab.image_smoke`` (MoonLadderStudios/MoonMind#3958: the
faultlab harness lives under ``tools/`` and is absent from the production
``moonmind`` package and deployable image). The ``omnigent-fault-image-smoke``
workflow mounts the checkout into the built API/worker image and runs
``python -m tools.omnigent_faultlab.image_smoke`` there with
``PYTHONPATH=/app:/src`` so production modules resolve from the image while the
test-only harness resolves from the mount; image authority drift (#3694) still
fails the smoke. This wrapper only makes the checkout importable and delegates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``moonmind`` importable from a local checkout without an editable install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.omnigent_faultlab.image_smoke import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
