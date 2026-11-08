"""Trusted IPv4 CONNECT transport; no DNS, discovery, or policy interpretation.

Run with --config and --state-directory. The directory must be job-owned,
pre-existing, persistent across process reconstruction, and unavailable to the
worker. startup.json is a durable one-shot marker, never removed by this process.
snapshot.json is bounded metadata atomically persisted before reservations. Its
bytesReserved includes attempted writes (conservatively including failed writes),
not an assertion that remote applications consumed those bytes. Cleanup must
retain both files. This module needs only stdlib and runs without privileges.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import signal
import socket
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path


def _ipv4(value):
    address = ipaddress.IPv4Address(value)
    if (
        str(address) != value
        or address.is_unspecified
        or address.is_multicast
        or value == "255.255.255.255"
    ):
        raise ValueError("specific canonical IPv4 required")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid integer limit")
    return value


def _seconds(value, high):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 < value <= high
    ):
        raise ValueError("invalid time limit")
    return value


def _authority(value):
    if not isinstance(value, str) or len(value) > 260:
        raise ValueError("invalid authority")
    match = re.fullmatch(r"([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?):([1-9][0-9]{0,4})", value)
    if not match or not 1 <= int(match[2]) <= 65535:
        raise ValueError("invalid authority")
    host = match[1]
    if all(c in "0123456789." for c in host):
        _ipv4(host)
    elif any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        raise ValueError("invalid hostname")
    return value


@dataclass(frozen=True)
class NativeTCPConfig:
    lease_id: str
    deadline_unix: float
    listen_host: str
    listen_port: int
    client_ip: str
    endpoints: tuple[tuple[str, str, int], ...]
    max_connections: int
    max_concurrent_connections: int
    max_connections_per_second: int
    max_bytes: int
    handshake_seconds: float
    connect_seconds: float
    idle_seconds: float

    @classmethod
    def from_mapping(cls, value):
        fields = {
            "leaseId",
            "deadlineUnix",
            "listenHost",
            "listenPort",
            "clientIp",
            "endpoints",
            "maxConnections",
            "maxConcurrentConnections",
            "maxConnectionsPerSecond",
            "maxBytes",
            "handshakeSeconds",
            "connectSeconds",
            "idleSeconds",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("missing or unknown gateway config field")
        lease = value["leaseId"]
        if not isinstance(lease, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", lease
        ):
            raise ValueError("invalid lease identity")
        endpoints = value["endpoints"]
        if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= 1024:
            raise ValueError("invalid endpoints")
        entries = []
        for entry in endpoints:
            if not isinstance(entry, dict) or set(entry) != {
                "authority",
                "address",
                "port",
            }:
                raise ValueError("invalid endpoint")
            authority = _authority(entry["authority"])
            port = _integer(entry["port"], 1, 65535)
            if int(authority.rsplit(":", 1)[1]) != port:
                raise ValueError("authority port mismatch")
            entries.append((authority, _ipv4(entry["address"]), port))
        if len({e[0] for e in entries}) != len(entries):
            raise ValueError("duplicate authority")
        return cls(
            lease,
            _seconds(value["deadlineUnix"], 1e12),
            _ipv4(value["listenHost"]),
            _integer(value["listenPort"], 0, 65535),
            _ipv4(value["clientIp"]),
            tuple(sorted(entries)),
            _integer(value["maxConnections"], 1, 1000000),
            _integer(value["maxConcurrentConnections"], 1, 1024),
            _integer(value["maxConnectionsPerSecond"], 1, 10000),
            _integer(value["maxBytes"], 1, 2**50),
            _seconds(value["handshakeSeconds"], 60),
            _seconds(value["connectSeconds"], 60),
            _seconds(value["idleSeconds"], 3600),
        )

    def to_mapping(self):
        return {
            "leaseId": self.lease_id,
            "deadlineUnix": self.deadline_unix,
            "listenHost": self.listen_host,
            "listenPort": self.listen_port,
            "clientIp": self.client_ip,
            "endpoints": [
                {"authority": a, "address": b, "port": c} for a, b, c in self.endpoints
            ],
            "maxConnections": self.max_connections,
            "maxConcurrentConnections": self.max_concurrent_connections,
            "maxConnectionsPerSecond": self.max_connections_per_second,
            "maxBytes": self.max_bytes,
            "handshakeSeconds": self.handshake_seconds,
            "connectSeconds": self.connect_seconds,
            "idleSeconds": self.idle_seconds,
        }

    @property
    def digest(self):
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    self.to_mapping(), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )


class NativeTCPGateway:
    def __init__(self, config, *, state_directory):
        self.config = NativeTCPConfig.from_mapping(config.to_mapping())
        self.state_directory = Path(state_directory)
        self.server = None
        self._tasks = set()
        self._writers = set()
        self._timer = None
        self._closed = False
        self._started = False
        self._attempts = 0
        self._bytes = 0
        self._denials = Counter()
        self._starts = deque()
        self._status = "created"
        self._pid = os.getpid()
        self._process_start = _process_start(self._pid)
        self._monotonic_deadline = 0

    @property
    def bound_port(self):
        return self.server.sockets[0].getsockname()[1]

    def snapshot(self):
        return {
            "schemaVersion": 1,
            "leaseId": self.config.lease_id,
            "configDigest": self.config.digest,
            "deadlineUnix": self.config.deadline_unix,
            "pid": self._pid,
            "processStart": self._process_start,
            "status": self._status,
            "attempts": self._attempts,
            "activeConnections": len(self._tasks),
            "bytesReserved": self._bytes,
            "denials": dict(self._denials),
            "endpoints": self.config.to_mapping()["endpoints"],
        }

    def _persist(self):
        directory = os.open(
            self.state_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fd = os.open(
                "snapshot.tmp",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(self.snapshot(), stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(
                    "snapshot.tmp",
                    "snapshot.json",
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                os.fsync(directory)
            finally:
                try:
                    os.unlink("snapshot.tmp", dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)

    def _stop_now(self):
        self._closed = True
        self._status = "closed"
        if self.server:
            self.server.close()
        for writer in tuple(self._writers):
            # Revocation/deadline/evidence failure is not a graceful EOF.
            # close() can flush queued bytes indefinitely to a stalled peer.
            writer.transport.abort()
        current = asyncio.current_task()
        for task in tuple(self._tasks):
            if task is not current:
                task.cancel()

    def _record(self):
        try:
            self._persist()
        except BaseException:
            self._stop_now()
            raise

    def _deny(self, reason):
        self._denials[reason] = min(self._denials[reason] + 1, 2**63 - 1)
        self._record()

    async def start(self):
        if self._started or self._closed:
            raise ValueError("gateway cannot restart")
        self._started = True
        remaining = self.config.deadline_unix - time.time()
        if remaining <= 0:
            raise ValueError("expired gateway")
        self._monotonic_deadline = asyncio.get_running_loop().time() + remaining
        directory = os.open(
            self.state_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            fd = os.open(
                "startup.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "leaseId": self.config.lease_id,
                        "configDigest": self.config.digest,
                        "deadlineUnix": self.config.deadline_unix,
                        "pid": self._pid,
                        "processStart": self._process_start,
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        finally:
            os.close(directory)
        self._status = "starting"
        self._record()
        try:
            self.server = await asyncio.start_server(
                self._accept,
                self.config.listen_host,
                self.config.listen_port,
                family=socket.AF_INET,
                limit=4096,
                backlog=self.config.max_concurrent_connections,
                start_serving=False,
            )
            await self.server.start_serving()
            self._status = "running"
            self._record()
            self._timer = asyncio.create_task(self._expiry())
        except BaseException:
            await self.close()
            raise

    def _remaining(self):
        # Wall time catches suspend/forward jumps; monotonic time prevents a
        # rollback from renewing the original allocation.
        return min(
            self.config.deadline_unix - time.time(),
            self._monotonic_deadline - asyncio.get_running_loop().time(),
        )

    def _expire_if_due(self):
        if not self._closed and self._remaining() <= 0:
            self._stop_now()
            self._record()
        return self._closed

    async def _expiry(self):
        while not self._closed:
            remaining = self._remaining()
            if remaining <= 0:
                break
            # A quiet/backpressured socket must expire after a wall-clock
            # advance even when the monotonic timer has not elapsed.
            await asyncio.sleep(min(0.2, remaining))
        await self.close()

    async def close(self):
        self._stop_now()
        if self._timer and self._timer is not asyncio.current_task():
            self._timer.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
        if self._started:
            self._record()

    def _accept(self, reader, writer):
        try:
            if self._expire_if_due():
                writer.transport.abort()
                return
            if writer.get_extra_info("peername")[0] != self.config.client_ip:
                self._deny("client")
                writer.close()
                return
            if len(self._tasks) >= self.config.max_concurrent_connections:
                self._deny("concurrency")
                writer.close()
                return
            self._writers.add(writer)
            task = asyncio.create_task(self._session(reader, writer))
            self._tasks.add(task)

            def finished(done):
                writer.close()
                self._writers.discard(writer)
                self._tasks.discard(done)
                if not done.cancelled():
                    done.exception()
                if not self._closed:
                    try:
                        self._record()
                    except OSError:
                        pass

            task.add_done_callback(finished)
        except OSError:
            writer.close()

    async def _session(self, reader, writer):
        upstream = None
        pumps = []
        graceful = False
        limited = False
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), self.config.handshake_seconds
            )
            if len(header) > 4096:
                raise ValueError("header")
            lines = header.decode("ascii").split("\r\n")
            match = re.fullmatch(r"CONNECT ([^ ]+) HTTP/1\.([01])", lines[0])
            if not match:
                raise ValueError("header")
            authority = _authority(match[1])
            # OpenSSL and curl use these fixed CONNECT preambles.
            # Header values never influence forwarding; no arbitrary headers,
            # authentication, request bodies, or duplicate Host are accepted.
            allowed = ([], [f"Host: {authority}"])
            if match[2] == "0":
                allowed = (*allowed, ["Proxy-Connection: Keep-Alive"])
            else:
                allowed = (
                    *allowed,
                    [f"Host: {authority}", "Proxy-Connection: Keep-Alive"],
                )
            if lines[1:-2] not in allowed:
                raise ValueError("header")
            endpoints = {a: (b, c) for a, b, c in self.config.endpoints}
            if authority not in endpoints:
                self._deny("destination")
                return
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._expire_if_due():
                return
            while self._starts and self._starts[0] <= now - 1:
                self._starts.popleft()
            if self._attempts >= self.config.max_connections:
                self._deny("attempts")
                return
            if len(self._starts) >= self.config.max_connections_per_second:
                self._deny("rate")
                return
            self._attempts += 1
            self._starts.append(now)
            self._record()  # Reserve the dial before even creating its socket.
            if self._expire_if_due():
                return
            host, port = endpoints[authority]
            # AI_NUMERICHOST and AF_INET forbid resolver fallback, even on errors.
            remote_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection(
                    host,
                    port,
                    family=socket.AF_INET,
                    flags=socket.AI_NUMERICHOST,
                    limit=16384,
                ),
                self.config.connect_seconds,
            )
            self._writers.add(upstream)
            if self._expire_if_due():
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(writer.drain(), self.config.idle_seconds)
            touched = loop.time()

            async def pump(source, destination):
                nonlocal touched, limited
                while chunk := await source.read(16384):
                    if limited or self._expire_if_due():
                        return
                    if len(chunk) > self.config.max_bytes - self._bytes:
                        limited = True
                        self._deny("bytes")
                        writer.transport.abort()
                        upstream.transport.abort()
                        return
                    self._bytes += len(chunk)
                    self._record()  # No await between reservation and write.
                    if self._expire_if_due():
                        return
                    destination.write(chunk)
                    await destination.drain()
                    touched = loop.time()
                if destination.can_write_eof():
                    destination.write_eof()

            async def idle():
                while True:
                    remaining = self.config.idle_seconds - (loop.time() - touched)
                    if remaining <= 0:
                        self._deny("idle")
                        return
                    await asyncio.sleep(remaining)

            pumps = [
                asyncio.create_task(pump(reader, upstream)),
                asyncio.create_task(pump(remote_reader, writer)),
                asyncio.create_task(idle()),
            ]
            transfer = asyncio.gather(*pumps[:2])
            try:
                done, _ = await asyncio.wait(
                    [transfer, pumps[2]], return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
                graceful = transfer in done and pumps[2] not in done and not limited
            finally:
                transfer.cancel()
                # Only successful bidirectional EOF may flush queued output.
                # A session limit must end reachability, not leave it flushing.
                for stream in (writer, upstream):
                    if graceful:
                        stream.close()
                    else:
                        stream.transport.abort()
                await asyncio.gather(transfer, return_exceptions=True)
        except (
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            self._deny("header")
        except TimeoutError:
            self._deny("timeout")
        except OSError:
            if not self._closed:
                self._deny("io")
        finally:
            for task in pumps:
                task.cancel()
            streams = tuple(stream for stream in (writer, upstream) if stream)
            for stream in streams:
                if graceful:
                    stream.close()
                else:
                    stream.transport.abort()
            try:
                await asyncio.gather(*pumps, return_exceptions=True)
                # Successful EOF retains revocation/concurrency ownership until
                # all output is actually closed. The original deadline still
                # aborts a stalled flush through _writers.
                await asyncio.gather(
                    *(stream.wait_closed() for stream in streams),
                    return_exceptions=True,
                )
            finally:
                for stream in streams:
                    stream.transport.abort()
                    self._writers.discard(stream)


def _process_start(pid):
    # Linux container PID identity survives neither PID reuse nor process restart.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in {"Z", "X"}:
        raise ValueError("gateway process is not live")
    return fields[19]


def check(config, state_directory):
    """Read-only readiness check; final stopped evidence remains separately readable."""
    directory = os.open(state_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        values = []
        for name in ("startup.json", "snapshot.json"):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd) as stream:
                raw = stream.read(524289)
                if len(raw) > 524288:
                    raise ValueError("oversized gateway evidence")
                values.append(json.loads(raw))
    finally:
        os.close(directory)
    startup, snapshot = values
    for value in values:
        if (
            value.get("leaseId") != config.lease_id
            or value.get("configDigest") != config.digest
            or value.get("deadlineUnix") != config.deadline_unix
        ):
            raise ValueError("gateway evidence identity mismatch")
    pid = _integer(startup.get("pid"), 1, 2**31 - 1)
    if (
        snapshot.get("pid") != pid
        or snapshot.get("processStart") != startup.get("processStart")
        or _process_start(pid) != startup.get("processStart")
        or snapshot.get("status") != "running"
        or time.time() >= config.deadline_unix
    ):
        raise ValueError("gateway is not live")
    _integer(snapshot.get("attempts"), 0, config.max_connections)
    _integer(snapshot.get("activeConnections"), 0, config.max_concurrent_connections)
    _integer(snapshot.get("bytesReserved"), 0, config.max_bytes)
    if snapshot.get("endpoints") != config.to_mapping()["endpoints"]:
        raise ValueError("gateway endpoint evidence mismatch")
    return snapshot


async def _run(config_path, state_directory):
    config = NativeTCPConfig.from_mapping(json.loads(Path(config_path).read_text()))
    gateway = NativeTCPGateway(config, state_directory=state_directory)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await gateway.start()
    try:
        while not gateway._closed and not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 0.2)
            except TimeoutError:
                pass
    finally:
        await gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-directory", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check(
            NativeTCPConfig.from_mapping(json.loads(Path(args.config).read_text())),
            args.state_directory,
        )
    else:
        asyncio.run(_run(args.config, args.state_directory))
