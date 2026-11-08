"""Bounded connect inventory using only the admitted native CONNECT gateway.

No target resolver, direct socket, retry, redirect, TLS or caller-defined payload.
Gateway receipts remain the authority for actual transport reservations; these
records describe what this one client observed, not delivered byte guarantees.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import signal
import sys
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from moonmind.security.native_tcp_gateway import (
    CONNECT_CLOSE_ON_EOF,
    CONNECT_FAILURES,
    CONNECT_SUCCESS,
)
from moonmind.security.service_inventory import (
    MAX_SERVICE_INVENTORY_OUTPUT_BYTES,
    ServiceInventoryPlan,
    sanitize_service_banner,
)

# Upstream dialing has its own immutable selected timeout in the gateway.
# Allow its persisted result to traverse CONNECT setup/response scheduling,
# without extending that dial, the original lease, or the run deadline.
_CONNECT_RESPONSE_GRACE_SECONDS = 2
_TUNNEL_CLOSE_SECONDS = 2
_TUNNEL_CLOSE_DRAIN_BYTES = 64 * 1024
_DRAIN_STOP_REASONS = frozenset(
    {"connection_budget_exhausted", "byte_budget_exhausted"}
)


def _now():
    return datetime.now(UTC).isoformat()


def _proxy(environ):
    value = environ.get("MOONMIND_EGRESS_PROXY", "")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname or "")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port is None
        or value != f"http://{parsed.hostname}:{parsed.port}"
    ):
        raise ValueError("admitted native gateway is unavailable")
    return parsed.hostname, parsed.port


def _checkpoint(path, value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    if len(raw) > MAX_SERVICE_INVENTORY_OUTPUT_BYTES:
        raise ValueError("inventory evidence exceeds byte bound")
    # An interrupted replacement leaves the last complete checkpoint intact.
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class _Inventory:
    def __init__(self, plan, output, environ):
        self.plan = plan
        self.output = output
        self.proxy = _proxy(environ)
        deadline = float(environ.get("MOONMIND_EGRESS_DEADLINE", "nan"))
        if not math.isfinite(deadline):
            raise ValueError("original native deadline is required")
        self.deadline = min(
            deadline, time.time() + plan.native_network.limits.max_duration_seconds
        )
        self.monotonic_deadline = time.monotonic() + max(0, self.deadline - time.time())
        self.starts = deque()
        self.start_lock = asyncio.Lock()
        self.bytes_spent = 0
        self.bytes_pending = 0
        self.byte_capacity_changed = asyncio.Event()
        self.attempts = 0
        self.next_index = 0
        self.stop_reason = None
        self.writers = set()
        self.execution = {
            "schemaVersion": "service-inventory-execution/v1",
            "planKey": plan.plan_key,
            "startedAt": _now(),
            "finishedAt": None,
            "stopReason": None,
            "records": [
                {
                    "authority": endpoint.authority,
                    "probe": endpoint.probe,
                    "reachability": "untested",
                    "probeStatus": (
                        "not_requested" if endpoint.probe == "connect" else "untested"
                    ),
                    "bytesSent": 0,
                    "bytesReceived": 0,
                    "truncated": False,
                }
                for endpoint in plan.endpoints
            ],
        }

    def remaining(self):
        return min(
            self.deadline - time.time(), self.monotonic_deadline - time.monotonic()
        )

    def checkpoint(self):
        self.execution["stopReason"] = (
            self.stop_reason if self.execution["finishedAt"] is not None else None
        )
        try:
            _checkpoint(self.output / "execution.json", self.execution)
        except OSError as exc:
            # Storage is not a socket/probe observation. In particular it must
            # never turn an already-open connect-only record into invalid probe
            # evidence or let the run claim completion after losing a write.
            raise RuntimeError("inventory checkpoint unavailable") from exc

    def stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    async def reserve_start(self):
        limits = self.plan.native_network.limits
        async with self.start_lock:
            while not self.stop_reason:
                if self.remaining() <= 0:
                    self.stop("duration_exhausted")
                    return False
                if self.attempts >= limits.max_connections:
                    self.stop("connection_budget_exhausted")
                    return False
                now = time.monotonic()
                while self.starts and self.starts[0] <= now - 1.01:
                    self.starts.popleft()
                # Space starts as well as enforcing the shared sliding window.
                # Small slack avoids consuming the gateway's exact edge due to
                # scheduling jitter. A gateway denial still stops, never retries.
                delay = (
                    max(
                        0,
                        self.starts[-1]
                        + 1 / limits.max_connections_per_second
                        + 0.01
                        - now,
                    )
                    if self.starts
                    else 0
                )
                if len(self.starts) >= limits.max_connections_per_second:
                    delay = max(delay, self.starts[0] + 1.01 - now)
                if delay:
                    await asyncio.sleep(min(delay, max(0, self.remaining())))
                    continue
                self.starts.append(now)
                self.attempts += 1
                return True
        return False

    async def reserve_bytes(self, requested, *, exact=False):
        minimum = requested if exact else 1
        while True:
            remaining = self.plan.native_network.limits.max_bytes - self.bytes_spent
            available = remaining - self.bytes_pending
            if available >= minimum:
                count = requested if exact else min(requested, available)
                self.bytes_pending += count
                return count
            if remaining < minimum:
                return 0  # Spent bytes, not another probe's unused reservation.
            # An active read owns this capacity temporarily. Its bounded probe
            # completion or cancellation releases unused bytes. The caller's
            # probe timeout and the original run deadline bound this wait too.
            self.byte_capacity_changed.clear()
            await self.byte_capacity_changed.wait()

    def finish_bytes(self, reserved, spent):
        # Synchronous: no write/read may escape its reservation, including during
        # cancellation. Wake pending probes only after the unused bytes return.
        self.bytes_pending -= reserved
        self.bytes_spent += spent
        self.byte_capacity_changed.set()

    async def probe(self, endpoint, record, reader, writer):
        if endpoint.probe == "connect":
            return
        request = (
            self.plan.http_head_request(endpoint)
            if endpoint.probe == "http-head"
            else b""
        )
        # Reserve before writing. Never transmit a partial protocol request.
        if request:
            count = await self.reserve_bytes(len(request), exact=True)
            if not count:
                record["probeStatus"] = "budget_exhausted"
                self.stop("byte_budget_exhausted")
                return
            self.finish_bytes(count, count)
            record["bytesSent"] = count
            writer.write(request)
            await writer.drain()
        cap = self.plan.capture_bytes_per_endpoint
        count = await self.reserve_bytes(cap)
        if not count:
            record["probeStatus"] = "budget_exhausted"
            self.stop("byte_budget_exhausted")
            return
        raw = bytearray()
        try:
            while len(raw) < count:
                chunk = await reader.read(count - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
                record["bytesReceived"] = len(raw)
                if (endpoint.probe == "banner" and b"\n" in raw) or (
                    endpoint.probe == "http-head" and b"\r\n\r\n" in raw
                ):
                    break
        finally:
            self.finish_bytes(count, len(raw))
            record["truncated"] = len(raw) == count
            # Apply the byte bound before decoding or storing any peer text.
            if raw:
                text = sanitize_service_banner(bytes(raw), max_bytes=cap)
                if text:
                    record["observation"] = {
                        "text": text,
                        "source": "peer_banner",
                        "confidence": "unverified",
                    }
        if not raw:
            record["probeStatus"] = "empty"
        elif endpoint.probe == "http-head":
            status = re.fullmatch(
                rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e]*)?",
                bytes(raw).split(b"\r\n", 1)[0],
            )
            header_lines = bytes(raw).partition(b"\r\n\r\n")[0].split(b"\r\n")[1:]
            valid_headers = all(
                re.fullmatch(
                    rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e\x80-\xff]*", line
                )
                for line in header_lines
            )
            if status and b"\r\n\r\n" in raw and valid_headers:
                record["httpStatus"] = int(status[1])
                record["probeStatus"] = "observed"
            else:
                record["probeStatus"] = "malformed"
        else:
            record["probeStatus"] = (
                "observed" if record.get("observation") else "malformed"
            )

    async def close_tunnel(self, reader, writer):
        """Finish the opt-in tunnel before reusing its gateway concurrency slot."""
        if writer.is_closing():
            return
        try:
            async with asyncio.timeout(min(_TUNNEL_CLOSE_SECONDS, self.remaining())):
                writer.write_eof()
                # Only discard bounded, in-flight data while waiting for the
                # gateway's EOF. It is never decoded or added to observations;
                # the gateway already reserves all target payload bytes. No
                # further probe is sent and no target is contacted here.
                remaining = _TUNNEL_CLOSE_DRAIN_BYTES
                while remaining:
                    chunk = await reader.read(min(16384, remaining))
                    if not chunk:
                        return
                    remaining -= len(chunk)
                if not reader.at_eof():
                    self.stop("transport_unavailable")
        except (OSError, TimeoutError):
            if self.remaining() <= 0:
                self.stop_reason = "duration_exhausted"
            else:
                self.stop("transport_unavailable")

    async def observe(self, endpoint, record):
        if not await self.reserve_start():
            return
        writer = None
        try:
            async with asyncio.timeout(
                min(
                    self.plan.options.connect_timeout_seconds
                    + _CONNECT_RESPONSE_GRACE_SECONDS,
                    self.remaining(),
                )
            ):
                # Only the configured gateway is resolved/dialed by this client.
                reader, writer = await asyncio.open_connection(*self.proxy, limit=512)
                self.writers.add(writer)
                writer.write(
                    f"CONNECT {endpoint.authority} HTTP/1.1\r\nHost: {endpoint.authority}\r\n{CONNECT_CLOSE_ON_EOF}\r\n\r\n".encode(
                        "ascii"
                    )
                )
                await writer.drain()
                header = await reader.readuntil(b"\r\n\r\n")
                if header == CONNECT_SUCCESS:
                    record["reachability"] = "open"
                    # Preserve confirmed reachability before any probe can fail.
                    self.checkpoint()
                else:
                    outcome = next(
                        (
                            key
                            for key, response in CONNECT_FAILURES.items()
                            if header == response
                        ),
                        "transport_error",
                    )
                    record["reachability"] = outcome
                    if outcome in {"denied", "transport_error"}:
                        self.stop("transport_unavailable")
                    return
            async with asyncio.timeout(
                min(self.plan.options.probe_timeout_seconds, self.remaining())
            ):
                await self.probe(endpoint, record, reader, writer)
        except TimeoutError:
            if record["reachability"] == "open":
                record["probeStatus"] = "timed_out"
            else:
                # A client-side timeout cannot attest the gateway's dial result.
                record["reachability"] = "transport_error"
                self.stop("transport_unavailable")
            if self.remaining() <= 0:
                self.stop_reason = "duration_exhausted"
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if record["reachability"] == "open":
                record["probeStatus"] = "transport_error"
            else:
                record["reachability"] = "transport_error"
                self.stop("transport_unavailable")
        except asyncio.CancelledError:
            if record["reachability"] == "open" and endpoint.probe != "connect":
                record["probeStatus"] = "canceled"
            raise
        finally:
            if writer:
                try:
                    if record["reachability"] != "transport_error":
                        await self.close_tunnel(reader, writer)
                finally:
                    # Unconfirmed closure stops successors rather than retrying
                    # into a potentially still-owned gateway concurrency slot.
                    writer.transport.abort()
                    self.writers.discard(writer)
            self.checkpoint()

    async def worker(self):
        while not self.stop_reason and self.next_index < len(self.plan.endpoints):
            index = self.next_index
            self.next_index += 1
            await self.observe(
                self.plan.endpoints[index], self.execution["records"][index]
            )

    async def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        # Refuse reconstruction rather than repeat completed observations or
        # renew reservations. The gateway separately owns its durable dial lock.
        fd = os.open(
            self.output / ".service-inventory-started",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(self.plan.plan_key)
            stream.flush()
            os.fsync(stream.fileno())
        self.checkpoint()
        tasks = [
            asyncio.create_task(self.worker())
            for _ in range(
                min(
                    len(self.plan.endpoints),
                    self.plan.native_network.limits.max_concurrent_connections,
                )
            )
        ]
        try:
            while any(not task.done() for task in tasks):
                if self.remaining() <= 0:
                    self.stop_reason = "duration_exhausted"
                if self.stop_reason and self.stop_reason not in _DRAIN_STOP_REASONS:
                    break
                await asyncio.sleep(min(0.05, max(0, self.remaining())))
            if not self.stop_reason or self.stop_reason in _DRAIN_STOP_REASONS:
                await asyncio.gather(*tasks)
                self.stop("complete")
        except asyncio.CancelledError:
            self.stop("canceled")
            raise
        except Exception:
            self.stop("runner_error")
            raise
        finally:
            for writer in tuple(self.writers):
                writer.transport.abort()
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if any(isinstance(value, Exception) for value in results):
                self.stop_reason = "runner_error"
            self.execution["finishedAt"] = _now()
            self.checkpoint()
        return self.execution


async def inspect_service_inventory(
    plan: ServiceInventoryPlan, output: Path, *, environ=None
):
    """Execute once and retain completed observations even on cancellation."""
    return await _Inventory(
        plan, Path(output), dict(os.environ if environ is None else environ)
    ).run()


def main() -> int:
    from moonmind.security.service_inventory import (
        SERVICE_INVENTORY_OUTPUT_DIRECTORY,
        parse_service_inventory_plan_arguments,
    )

    try:
        plan = parse_service_inventory_plan_arguments(sys.argv[1:])

        async def run():
            task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, task.cancel)
            return await inspect_service_inventory(
                plan, Path(SERVICE_INVENTORY_OUTPUT_DIRECTORY)
            )

        asyncio.run(run())
    except asyncio.CancelledError:
        return 130
    except Exception:  # noqa: BLE001 - retain private checkpoint, never print peer data
        # No peer text or raw exceptions belong in process output. The latest
        # atomic execution checkpoint is the diagnostic artifact.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
