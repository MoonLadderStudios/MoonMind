"""One small authenticated local endpoint for the standalone controller.

Stdlib-only (``http.server``). This endpoint is how the host CLI and the
optional UI observer submit or observe the same controller operation; it is
not a general orchestration surface. Every request requires the
deployment-owned bearer secret (see :mod:`auth`). The controller never
replaces itself: there is no self-update route here; controller update is
host-owned (see ``tools/install-moonmind-controller.sh``).
"""

from __future__ import annotations

from moonmind_controller import auth


def is_authorized(headers: dict, secret: str | None) -> bool:
    """Check the Authorization header against the deployment-owned secret."""
    if not secret:
        return False
    presented = str((headers or {}).get("Authorization") or "")
    scheme, _, token = presented.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return auth.verify_bearer(token.strip(), secret)


def parse_operation_payload(raw: dict) -> dict:
    """Validate a submitted handoff payload (release config as data).

    The payload carries the desired target as data only; it never executes
    application code. Returns a normalized ``new_operation`` record.
    """
    if not isinstance(raw, dict):
        raise ValueError("Controller operation payload must be an object.")
    operation_id = str(raw.get("operationId") or "").strip()
    desired = raw.get("desired") or {}
    target_image = str(desired.get("targetImage") or "").strip()
    if not operation_id:
        raise ValueError("Controller operation payload names no operationId.")
    if not target_image:
        raise ValueError("Controller operation payload names no targetImage.")
    from moonmind_controller import state as _state

    record = _state.new_operation(
        operation_id=operation_id, target_image=target_image
    )
    services = desired.get("services")
    if services is not None:
        record["desired"]["services"] = [str(item) for item in list(services)]
    concrete = desired.get("concreteImages")
    if concrete is not None:
        if not isinstance(concrete, dict):
            raise ValueError("concreteImages must be an object.")
        record = _state.record_concrete_images(record, dict(concrete))
    return record


def status_payload(record: dict) -> dict:
    """Render the human-readable operation summary served to operators."""
    desired = record.get("desired") or {}
    installed = record.get("installed")
    attempts = list(record.get("attempts") or [])
    payload = {
        "operationId": record.get("operationId"),
        "status": record.get("status"),
        "desiredImage": desired.get("targetImage"),
        "installedImage": (installed or {}).get("image")
        if isinstance(installed, dict)
        else None,
        "attempt": record.get("attempt", 0),
        "lastError": attempts[-1].get("error") if attempts else None,
    }
    return payload


def serve(*, host: str = "127.0.0.1", port: int = 8099, state_dir: str) -> None:
    """Serve the authenticated endpoint on loopback (deployment-local)."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    secret = auth.secret_from_environ()
    root = Path(state_dir)

    class Handler(BaseHTTPRequestHandler):
        def _deny(self) -> None:
            body = b"unauthorized"
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server convention
            if not is_authorized(dict(self.headers), secret):
                self._deny()
                return
            if self.path == "/healthz":
                body = b"ok"
            elif self.path == "/operation":
                try:
                    record = json.loads((root / "operation.json").read_text())
                except (OSError, ValueError):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = (json.dumps(status_payload(record), sort_keys=True) + "\n").encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - http.server convention
            if not is_authorized(dict(self.headers), secret):
                self._deny()
                return
            if self.path != "/operation":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            try:
                raw = json.loads(self.rfile.read(length or 0).decode() or "{}")
                record = parse_operation_payload(raw)
            except (ValueError, OSError) as exc:
                body = (json.dumps({"error": str(exc)}) + "\n").encode()
                self.send_response(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            from moonmind_controller import state as _state

            stored = _state.reserve_record(root / "operation.json", record)
            body = (
                json.dumps(status_payload(stored), sort_keys=True) + "\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # keep logs readable
            pass

    HTTPServer((host, port), Handler).serve_forever()
