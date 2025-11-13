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


def _parse_port(
    name: str, raw_value: str, *, minimum: int = 1, maximum: int = 65535
) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:  # pragma: no cover - depends on environment
        raise ValueError(f"{name} must be an integer (received {raw_value!r})") from exc

    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum} (received {value})"
        )

    return value


@dataclass(frozen=True)
class ProxyConfig:
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int
    log_level: str


def _load_config() -> ProxyConfig:
    listen_host = os.getenv("CODEX_LOGIN_PROXY_LISTEN_HOST", "0.0.0.0")
    target_host = os.getenv("CODEX_LOGIN_PROXY_TARGET_HOST", "127.0.0.1")

    listen_port = _parse_port(
        "CODEX_LOGIN_PROXY_LISTEN_PORT",
        os.getenv("CODEX_LOGIN_PROXY_LISTEN_PORT", "51455"),
    )
    target_port = _parse_port(
        "CODEX_LOGIN_PROXY_TARGET_PORT",
        os.getenv("CODEX_LOGIN_PROXY_TARGET_PORT", "1455"),
    )

    log_level = os.getenv("CODEX_LOGIN_PROXY_LOG_LEVEL", "INFO").upper()

    return ProxyConfig(
        listen_host=listen_host,
        listen_port=listen_port,
        target_host=target_host,
        target_port=target_port,
        log_level=log_level,
    )


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while not reader.at_eof():
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception as exc:  # pragma: no cover - defensive logging
        logging.debug("Relay terminated due to exception: %s", exc)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


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
    except Exception as exc:  # pragma: no cover - best effort logging
        logging.error(
            "codex-login-proxy: failed to reach target %s:%s",
            config.target_host,
            config.target_port,
        )
        logging.debug("Target connection failure details: %s", exc)
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
        return

    async def _bridge() -> None:
        try:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(_relay(reader, target_writer))
                task_group.create_task(_relay(target_reader, writer))
        except* Exception as exc_group:  # pragma: no cover - rare edge cases
            logging.debug("Bridge terminated with exception group: %s", exc_group)

    try:
        await _bridge()
    finally:
        logging.debug("Connection from %s closed", peer)


async def _run_server(config: ProxyConfig) -> None:
    async def _client_handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _handle_client(reader, writer, config=config)

    server = await asyncio.start_server(
        _client_handler,
        config.listen_host,
        config.listen_port,
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
        config = _load_config()
    except ValueError as exc:
        print(f"codex-login-proxy configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    logging.basicConfig(level=config.log_level, stream=sys.stdout, format="%(message)s")
    try:
        asyncio.run(_run_server(config))
    except Exception as exc:  # pragma: no cover - startup failures printed to stderr
        logging.exception(
            "codex-login-proxy terminated due to unexpected error: %s", exc
        )
        raise


if __name__ == "__main__":
    main()
