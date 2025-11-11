#!/usr/bin/env python3

"""TCP proxy for Codex CLI login callback.

Expose a TCP listener reachable from outside the container and forward traffic to
``localhost:1455`` inside the worker, allowing ``codex login`` to run inside the
container while the browser callback hits the forwarded port.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

LISTEN_HOST = os.getenv("CODEX_LOGIN_PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("CODEX_LOGIN_PROXY_LISTEN_PORT", "51455"))
TARGET_HOST = os.getenv("CODEX_LOGIN_PROXY_TARGET_HOST", "127.0.0.1")
TARGET_PORT = int(os.getenv("CODEX_LOGIN_PROXY_TARGET_PORT", "1455"))
LOG_LEVEL = os.getenv("CODEX_LOGIN_PROXY_LOG_LEVEL", "INFO").upper()


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while not reader.at_eof():
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    logging.debug("Received connection from %s", peer)
    try:
        target_reader, target_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception as exc:  # pragma: no cover - best effort logging
        logging.error(
            "codex-login-proxy: failed to reach target %s:%s: %s", TARGET_HOST, TARGET_PORT, exc
        )
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
        return

    async def _bridge() -> None:
        await asyncio.gather(
            _relay(reader, target_writer),
            _relay(target_reader, writer),
            return_exceptions=True,
        )

    try:
        await _bridge()
    finally:
        logging.debug("Connection from %s closed", peer)


async def _run_server() -> None:
    server = await asyncio.start_server(_handle_client, LISTEN_HOST, LISTEN_PORT)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info(
        "codex-login-proxy listening on %s → forwarding to %s:%s",
        sockets,
        TARGET_HOST,
        TARGET_PORT,
    )

    stop_event = asyncio.Event()

    def _request_shutdown(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_shutdown)

    await stop_event.wait()
    server.close()
    await server.wait_closed()


def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, stream=sys.stdout, format="%(message)s")
    try:
        asyncio.run(_run_server())
    except Exception as exc:  # pragma: no cover - startup failures printed to stderr
        logging.exception("codex-login-proxy terminated due to unexpected error: %s", exc)
        raise


if __name__ == "__main__":
    main()
