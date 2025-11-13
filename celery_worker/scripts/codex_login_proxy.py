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
from dataclasses import dataclass
from functools import partial

BUFFER_SIZE = 65536


@dataclass(frozen=True)
class ProxyConfig:
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()


def _parse_port(raw: str, env_name: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise ValueError(f"{env_name} must be an integer") from exc
    if not 0 < port < 65536:
        raise ValueError(f"{env_name} must be between 1 and 65535")
    return port


def _parse_log_level(raw: str) -> int:
    stripped = raw.strip()
    try:
        return int(stripped)
    except ValueError:
        normalized = stripped.upper()
    if normalized in logging._nameToLevel:  # type: ignore[attr-defined]
        return logging._nameToLevel[normalized]  # type: ignore[attr-defined]
    valid = ", ".join(sorted(logging._nameToLevel))  # type: ignore[attr-defined]
    raise ValueError(
        f"CODEX_LOGIN_PROXY_LOG_LEVEL must be one of {valid} or a numeric value"
    )


def _load_config() -> ProxyConfig:
    listen_host = os.getenv("CODEX_LOGIN_PROXY_LISTEN_HOST", "0.0.0.0")
    target_host = os.getenv("CODEX_LOGIN_PROXY_TARGET_HOST", "127.0.0.1")
    listen_port = _parse_port(
        os.getenv("CODEX_LOGIN_PROXY_LISTEN_PORT", "51455"),
        "CODEX_LOGIN_PROXY_LISTEN_PORT",
    )
    target_port = _parse_port(
        os.getenv("CODEX_LOGIN_PROXY_TARGET_PORT", "1455"),
        "CODEX_LOGIN_PROXY_TARGET_PORT",
    )
    return ProxyConfig(
        listen_host=listen_host,
        listen_port=listen_port,
        target_host=target_host,
        target_port=target_port,
    )


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        await _close_writer(writer)


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    config: ProxyConfig,
) -> None:
    peer = writer.get_extra_info("peername")
    logging.debug("Received connection from %s", peer)
    try:
        target_reader, target_writer = await asyncio.open_connection(
            config.target_host, config.target_port
        )
    except OSError as exc:  # pragma: no cover - best effort logging
        logging.error(
            "codex-login-proxy: failed to reach target %s:%s", config.target_host, config.target_port
        )
        logging.debug("Target connection failure detail: %s", exc)
        await _close_writer(writer)
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


async def _run_server(config: ProxyConfig) -> None:
    server = await asyncio.start_server(
        partial(_handle_client, config=config), config.listen_host, config.listen_port
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info(
        "codex-login-proxy listening on %s → forwarding to %s:%s",
        sockets,
        config.target_host,
        config.target_port,
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
    try:
        log_level = _parse_log_level(
            os.getenv("CODEX_LOGIN_PROXY_LOG_LEVEL", "INFO")
        )
    except ValueError as exc:
        print(f"Invalid log level configuration: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    logging.basicConfig(level=log_level, stream=sys.stdout, format="%(message)s")
    try:
        config = _load_config()
    except ValueError as exc:
        logging.error("Invalid proxy configuration: %s", exc)
        raise SystemExit(1) from exc

    try:
        asyncio.run(_run_server(config))
    except Exception as exc:  # pragma: no cover - startup failures printed to stderr
        logging.exception("codex-login-proxy terminated due to unexpected error: %s", exc)
        raise


if __name__ == "__main__":
    main()
