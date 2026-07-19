"""Load the pinned upstream runner-tunnel frame implementation."""

from __future__ import annotations

from functools import cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from typing import Any


@cache
def runner_frames() -> Any:
    """Return the exact frame module shipped by the pinned Omnigent source."""

    bundle = Path(__file__).resolve().parents[2] / "omnigent"
    path = bundle / "omnigent" / "runner" / "transports" / "ws_tunnel" / "frames.py"
    bundle_text = str(bundle)
    if bundle_text not in sys.path:
        sys.path.insert(0, bundle_text)
    spec = spec_from_file_location("_moonmind_pinned_omnigent_runner_frames", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("pinned Omnigent runner frame codec is unavailable")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
