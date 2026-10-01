"""In-process standalone deployment controller for client tests.

Serves the shipped ``deploy/controller`` endpoint (production threaded HTTP
server, bearer check, operation store, and reattach rules) on a loopback
port. Only the Compose applier and, optionally, the target resolver are
replaced, so API, host-entrypoint, and tool clients exercise the real
controller interface.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

CONTROLLER_DIR = Path(__file__).resolve().parents[2] / "deploy" / "controller"
CONTROLLER_MODULES = ("redact", "mounts", "lock", "record", "engine", "server")
DEFAULT_TARGET = {
    "project": "moonmind",
    "projectDir": "/srv/moonmind",
    "composeFiles": ["docker-compose.yaml"],
    "services": ["api"],
}


class InProcessController:
    """One running controller endpoint with a recorded applier."""

    def __init__(
        self,
        state_dir: Path,
        *,
        secret: str,
        applier: Callable[[InProcessController, dict], Any] | None = None,
        target_resolver: Callable[[str], dict] | None = None,
    ) -> None:
        self.server = importlib.import_module("server")
        self.engine = importlib.import_module("engine")
        self.record = importlib.import_module("record")
        self.store = self.record.OperationStore(state_dir)
        self.applied: list[str] = []

        def tracking_applier(operation: dict) -> Any:
            self.applied.append(operation["operationId"])
            if applier is not None:
                return applier(self, operation)
            self.store.confirm_installed(
                operation["operationId"], image=operation["desired"]["image"]
            )
            return None

        app = self.server.build_app(
            store=self.store,
            secret=secret,
            applier=tracking_applier,
            target_resolver=target_resolver,
        )
        self.httpd = self.server.make_http_server("127.0.0.1", 0, app)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=10)
        self.httpd.server_close()


def install_controller_state(state_dir: Path, *, port: int, secret: str) -> None:
    """Write what bootstrap install leaves in the deployment state."""
    (state_dir / "secrets").mkdir(parents=True, exist_ok=True)
    (state_dir / "secrets" / "controller-bearer").write_text(secret + "\n")
    (state_dir / "controller-identity.json").write_text(
        json.dumps({"project": "moonmind-controller-test", "port": port})
    )
    (state_dir / "controller-image.json").write_text(
        json.dumps({"pinned": "ctl@sha256:" + "a" * 64, "verified": True})
    )


def slow_applier(
    release: threading.Event,
) -> Callable[[InProcessController, dict], None]:
    """An applier that stays ``applying`` until ``release`` is set."""

    def apply(controller: InProcessController, operation: dict) -> None:
        controller.store.mark_stage(operation["operationId"], stage="applying")
        assert release.wait(timeout=30)
        controller.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    return apply


def load_controller_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import the stdlib controller modules from ``deploy/controller``."""
    monkeypatch.syspath_prepend(str(CONTROLLER_DIR))
    forget_controller_modules()


def forget_controller_modules() -> None:
    for name in CONTROLLER_MODULES:
        sys.modules.pop(name, None)
