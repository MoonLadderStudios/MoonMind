#!/usr/bin/env python3
"""Run the Omnigent fault matrix inside the exact deployable image (AC7).

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — the
exact-image fault-matrix smoke).

This is a thin local-convenience wrapper around the packaged CLI in
``moonmind.omnigent.faultlab.image_smoke``. The canonical entrypoint the
``omnigent-fault-image-smoke`` workflow invokes inside the built API/worker image
is ``python -m moonmind.omnigent.faultlab.image_smoke`` — the module ships in the
image's own ``moonmind`` install, so image authority drift (#3694) fails the smoke
rather than depending on a ``tools/`` path the production image never copies. This
wrapper only makes ``moonmind`` importable from a local checkout and delegates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``moonmind`` importable from a local checkout without an editable install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from moonmind.omnigent.faultlab.image_smoke import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
