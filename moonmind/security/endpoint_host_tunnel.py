"""SSH ProxyCommand: one byte stream through the admitted native gateway.

``python -I -m moonmind.security.endpoint_host_tunnel <host> <port>`` opens a
CONNECT tunnel to exactly that endpoint at ``MOONMIND_EGRESS_PROXY`` and
relays standard input and output. It resolves no name and dials nothing else;
the gateway decides reach and budgets. A refused tunnel prints one fixed line
naming the gateway's outcome, never peer text. Standard library only.
"""

from __future__ import annotations

import os
import re
import selectors
import socket
import sys
from urllib.parse import urlsplit

from moonmind.security.native_tcp_gateway import CONNECT_FAILURES, CONNECT_SUCCESS

#: Prefix of the one diagnostic line the endpoint runner classifies.
DIAGNOSTIC = "moonmind-endpoint-tunnel:"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_CONNECT_SECONDS = 30


def proxy_address(environ) -> tuple[str, int]:
    """The job's own gateway, exactly as the worker configured it."""
    value = environ.get("MOONMIND_EGRESS_PROXY", "")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not _HOST.fullmatch(parsed.hostname or "")
        or parsed.port is None
        or value != f"http://{parsed.hostname}:{parsed.port}"
    ):
        raise ValueError("admitted native gateway is unavailable")
    return parsed.hostname, parsed.port


def _fail(outcome: str) -> int:
    sys.stderr.write(f"{DIAGNOSTIC} {outcome}\n")
    sys.stderr.flush()
    return 1


def _relay(sock: socket.socket) -> None:
    stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
    selector = selectors.DefaultSelector()
    selector.register(stdin, selectors.EVENT_READ)
    selector.register(sock, selectors.EVENT_READ)
    open_streams = 2
    while open_streams:
        for key, _ in selector.select():
            if key.fileobj == stdin:
                chunk = os.read(stdin, 65536)
                if chunk:
                    sock.sendall(chunk)
                    continue
                selector.unregister(stdin)
                open_streams -= 1
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    return
            else:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                view = memoryview(chunk)
                while view:
                    view = view[os.write(stdout, view) :]


def main(argv: list[str]) -> int:
    try:
        host, port = argv[1], int(argv[2])
        if len(argv) != 3 or not _HOST.fullmatch(host) or not 1 <= port <= 65535:
            raise ValueError("endpoint")
        proxy = proxy_address(os.environ)
    except (IndexError, ValueError):
        return _fail("configuration_error")
    authority = f"{host}:{port}"
    try:
        sock = socket.create_connection(proxy, timeout=_CONNECT_SECONDS)
    except OSError:
        return _fail("gateway_unavailable")
    with sock:
        try:
            sock.sendall(
                f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()
            )
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                chunk = sock.recv(1)
                if not chunk or len(header) >= 4096:
                    return _fail("transport_error")
                header += chunk
        except TimeoutError:
            return _fail("timed_out")
        except OSError:
            return _fail("transport_error")
        if header != CONNECT_SUCCESS:
            outcome = next(
                (name for name, body in CONNECT_FAILURES.items() if body == header),
                "transport_error",
            )
            return _fail(outcome)
        sock.settimeout(None)
        try:
            _relay(sock)
        except OSError:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
