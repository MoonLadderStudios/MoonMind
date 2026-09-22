"""Small authenticated local HTTP endpoint for the controller (stdlib only).

One endpoint family on loopback with a deployment-owned shared secret:

  GET  /status   - operation record, prepared target, installed config
  POST /update   - submit an update {targetImage, services[], mode}
  POST /retry    - explicit Retry (fresh bounded attempt, history retained)
  GET  /logs     - redacted log tail

No agents receive sockets or unrestricted access: every mutating request must
carry ``Authorization: Bearer <signature>`` where signature is
``HMAC(secret, body)``. The transport (loopback listener) and the Docker
socket mount live in the controller's own Compose project, so they survive
target-project shutdown. Stdlib only (``http.server``).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Mapping

from . import auth, controller as controller_mod
from .compose_plan import PlanRequest


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


class EndpointHandler(BaseHTTPRequestHandler):
    delegate: Any = None  # set by serve()

    def log_message(self, *args: object) -> None:  # keep test output quiet
        pass

    def _send(self, status: int, payload: Mapping[str, Any]) -> None:
        body = (json.dumps(payload) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, body: bytes) -> bool:
        secret = str(self.delegate["secret"])
        presented = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return auth.verify(secret, body, presented)

    def do_GET(self) -> None:  # noqa: N802
        store = self.delegate["store"]
        if self.path == "/status":
            record = store.load_operation()
            self._send(200, {
                "operation": record.to_dict() if record else None,
                "prepared": store.read_prepared(),
                "installed": store.read_installed(),
            })
        elif self.path == "/logs":
            try:
                text = store.log_path.read_text(encoding="utf-8")[-8000:]
            except OSError:
                text = ""
            self._send(200, {"logTail": text})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = _read_body(self)
        if not self._authorized(body):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            payload = json.loads(body.decode() or "{}")
        except ValueError:
            self._send(400, {"error": "invalid JSON"})
            return
        ctrl: controller_mod.Controller = self.delegate["controller"]
        pre_builder: Callable[[Mapping[str, Any]], Any] = self.delegate["pre_builder"]
        try:
            if self.path == "/update":
                request = PlanRequest(
                    target_image=str(payload.get("targetImage") or ""),
                    services=tuple(payload.get("services") or ()),
                    mode=str(payload.get("mode") or "changed_services"),
                    explicit_repair=bool(payload.get("explicitRepair", False)),
                    infrastructure_change=bool(payload.get("infrastructureChange", False)),
                )
                result = ctrl.update(request, pre=pre_builder(payload))
                self._send(200, {"operationId": result.operation_id, "status": result.status})
            elif self.path == "/retry":
                store = self.delegate["store"]
                record = store.explicit_retry(target_image=str(payload.get("targetImage") or "") or None)
                self._send(200, {"operationId": record.operation_id, "status": record.status})
            else:
                self._send(404, {"error": "not found"})
        except controller_mod.ControllerError as exc:
            self._send(422, {"error": str(exc), "exitCode": exc.exit_code, "logTail": exc.log_tail})


def serve(
    *,
    host: str,
    port: int,
    secret: str,
    store: Any,
    controller: Any,
    pre_builder: Callable[[Mapping[str, Any]], Any],
) -> HTTPServer:
    handler = type("BoundHandler", (EndpointHandler,), {"delegate": {
        "secret": secret, "store": store, "controller": controller, "pre_builder": pre_builder,
    }})
    return HTTPServer((host, port), handler)
