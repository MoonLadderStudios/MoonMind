"""Thin adapter over the repository-pinned Omnigent host frame codec.

The embedded server must speak the stock host protocol.  This module therefore
loads the codec from the pinned submodule instead of duplicating frame names or
serialization rules in MoonMind.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from typing import Any

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT

SUPPORTED_HOST_FRAME_PROTOCOL_MAJOR = 1
MAX_HOST_FRAME_BYTES = 1024 * 1024
_cached_frames_module: Any | None = None


class UpstreamHostProtocolError(RuntimeError):
    """Stable failure for malformed, incompatible, or misdirected frames."""


class OmnigentHostProtocolAdapter:
    """Execute the exact pinned upstream host frame encoder and decoder."""

    def __init__(self) -> None:
        self._frames = _load_pinned_frames_module()

    def decode_host_frame(self, text: str) -> Any:
        """Decode a host-to-server frame and enforce direction/version bounds."""

        if len(text.encode("utf-8")) > MAX_HOST_FRAME_BYTES:
            raise UpstreamHostProtocolError("host frame exceeds the byte limit")
        try:
            frame = self._frames.decode_host_frame(text)
        except (TypeError, ValueError) as exc:
            raise UpstreamHostProtocolError("host frame was rejected") from exc
        allowed = (
            self._frames.HostHelloFrame,
            self._frames.HostHarnessReadinessFrame,
            self._frames.HostLaunchRunnerResultFrame,
            self._frames.HostStopRunnerResultFrame,
            self._frames.HostRunnerExitedFrame,
            self._frames.HostStatResultFrame,
            self._frames.HostListDirResultFrame,
            self._frames.HostCreateWorktreeResultFrame,
            self._frames.HostRemoveWorktreeResultFrame,
            self._frames.HostListWorktreesResultFrame,
            self._frames.HostCreateDirResultFrame,
            self._frames.HostInstallHarnessResultFrame,
        )
        if not isinstance(frame, allowed):
            raise UpstreamHostProtocolError("server-to-host frame received from host")
        if isinstance(frame, self._frames.HostHelloFrame):
            if frame.frame_protocol_version != SUPPORTED_HOST_FRAME_PROTOCOL_MAJOR:
                raise UpstreamHostProtocolError(
                    "host frame protocol major is incompatible"
                )
        return frame

    def encode_server_frame(self, frame: Any) -> str:
        """Encode only command frames that the stock host accepts."""

        allowed = (
            self._frames.HostLaunchRunnerFrame,
            self._frames.HostStopRunnerFrame,
            self._frames.HostStatFrame,
            self._frames.HostListDirFrame,
            self._frames.HostCreateWorktreeFrame,
            self._frames.HostRemoveWorktreeFrame,
            self._frames.HostListWorktreesFrame,
            self._frames.HostCreateDirFrame,
            self._frames.HostInstallHarnessFrame,
        )
        if not isinstance(frame, allowed):
            raise UpstreamHostProtocolError("host-to-server frame cannot be sent")
        encoded = self._frames.encode_host_frame(frame)
        if len(encoded.encode("utf-8")) > MAX_HOST_FRAME_BYTES:
            raise UpstreamHostProtocolError("host frame exceeds the byte limit")
        return encoded

    @property
    def frames(self) -> Any:
        """Expose pinned dataclasses for construction without local copies."""

        return self._frames


def _load_pinned_frames_module() -> Any:
    global _cached_frames_module
    if _cached_frames_module is not None:
        return _cached_frames_module

    bundle_root = Path(__file__).resolve().parents[2] / "omnigent"
    root = bundle_root / "omnigent"
    path = root / "host" / "frames.py"
    try:
        # The codec's encoder lazily imports the pinned telemetry helper.  Add
        # the submodule package root (never an installed distribution) so that
        # import resolves inside the same pinned bundle.
        bundle_text = str(bundle_root)
        if bundle_text not in sys.path:
            sys.path.insert(0, bundle_text)
        spec = spec_from_file_location("_moonmind_pinned_omnigent_host_frames", path)
        if spec is None or spec.loader is None:
            raise ImportError("host frame module spec unavailable")
        module = module_from_spec(spec)
        # ``dataclasses`` resolves annotations through ``sys.modules`` while
        # executing decorators, just as the normal import machinery does.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _cached_frames_module = module
        return module
    except (AttributeError, ImportError, OSError) as exc:
        raise UpstreamHostProtocolError(
            "pinned Omnigent host frame codec is unavailable"
        ) from exc


__all__ = [
    "MAX_HOST_FRAME_BYTES",
    "OmnigentHostProtocolAdapter",
    "PINNED_OMNIGENT_COMMIT",
    "SUPPORTED_HOST_FRAME_PROTOCOL_MAJOR",
    "UpstreamHostProtocolError",
]
