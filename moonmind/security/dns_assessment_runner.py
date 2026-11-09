"""Fixed, bounded RFC 8484 runner through the deployment's HTTP CONNECT proxy.

The execution checkpoint owns every reservation, including uncertain outcomes.
The persistent start marker never grants a fresh attempt: relaunch only publishes
existing progress. No DNS resolver, raw DNS socket, or direct fallback is used.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import ssl
import sys
import tempfile
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from moonmind.security.dns_assessments import (
    DNS_OUTPUT_DIRECTORY,
    DNS_OUTPUT_FILES,
    DNSExecution,
    DNSObservation,
    DNSQueryPlan,
    DNSResponse,
    build_dns_query_plan,
    build_dns_query_wire,
    decode_dns_query_plan,
    followup_questions,
    parse_dns_response,
    serialize_dns_artifacts,
    validate_dns_execution,
)
from moonmind.security.egress_policy import tool_proxy_options

_START_MARKER = ".started"
_MAX_QUESTION_ATTEMPTS = 2


def _now() -> datetime:
    return datetime.now(UTC)


def _lease_deadline(environ: dict) -> datetime:
    value = environ.get("MOONMIND_EGRESS_DEADLINE")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 12
        or not value.isascii()
        or not value.isdecimal()
        or not 0 < int(value) <= 253402300799
    ):
        raise ValueError("deployment egress deadline is unavailable or invalid")
    return datetime.fromtimestamp(int(value), UTC)


def _atomic_write(path: Path, body: bytes) -> None:
    """Replace one complete checkpoint and persist it before dispatching."""
    fd, temporary = tempfile.mkstemp(prefix=".dns-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _Checkpoint:
    def __init__(self, output: Path, plan: DNSQueryPlan):
        self.output, self.plan = output, plan
        self.current: DNSExecution | None = None
        self.fd: int | None = None
        self.fresh = False

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=True)
        if self.output.is_symlink():
            raise ValueError("DNS output directory must not be a symlink")
        marker = self.output / _START_MARKER
        try:
            self.fd = os.open(
                marker, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            self.fresh = True
        except FileExistsError:
            self.fd = os.open(marker, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.fresh:
                # No start time or counters here: execution.json alone owns them.
                os.write(self.fd, self.plan.plan_key.encode("ascii"))
                os.fsync(self.fd)
                if any(
                    (self.output / name).exists() for name in DNS_OUTPUT_FILES.values()
                ):
                    raise ValueError("DNS artifacts exist without their start marker")
            elif os.read(self.fd, 65) != self.plan.plan_key.encode("ascii"):
                raise ValueError("DNS start marker belongs to another or unknown plan")
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)

    def publish(self, execution: DNSExecution, *, reserve_terminal: bool) -> None:
        bodies = serialize_dns_artifacts(
            self.plan,
            execution,
            DNS_OUTPUT_FILES["execution"],
            reserve_terminal=reserve_terminal,
        )
        # Readers recover from execution.json if publication is interrupted
        # between the two replacements. Never create a second reservation log.
        # Keep the proposed state in memory if replacement/fsync fails. A
        # failed publish never dispatches, and terminal publication must retain
        # any known response rather than overwrite it with an older checkpoint.
        self.current = execution
        _atomic_write(
            self.output / DNS_OUTPUT_FILES["execution"], bodies["execution.json"]
        )
        _atomic_write(self.output / DNS_OUTPUT_FILES["summary"], bodies["summary.json"])

    def recover(self, lease_deadline: datetime) -> DNSExecution:
        fd = os.open(
            self.output / DNS_OUTPUT_FILES["execution"], os.O_RDONLY | os.O_NOFOLLOW
        )
        with os.fdopen(fd, "rb") as stream:
            body = stream.read(self.plan.assessment.max_output_bytes + 1)
        if len(body) > self.plan.assessment.max_output_bytes:
            raise ValueError("DNS checkpoint exceeds the original output budget")
        self.current = validate_dns_execution(
            self.plan, DNSExecution.model_validate_json(body)
        )
        if self.current.stop_reason == "in_progress":
            deadline = min(
                lease_deadline,
                self.current.started_at
                + timedelta(seconds=self.plan.assessment.timeout_seconds),
            )
            return self.finish("duration_limit" if _now() >= deadline else "failed")
        self.publish(self.current, reserve_terminal=False)
        return self.current

    def finish(self, reason: str) -> DNSExecution:
        current = self.current
        if current is None:
            raise ValueError("DNS invocation has no canonical checkpoint")
        observations = tuple(
            (
                row.model_copy(update={"transport_outcome": "interrupted"})
                if row.transport_outcome == "in_flight"
                else row
            )
            for row in current.observations
        )
        finished = max(_now(), current.checkpoint_at)
        result = current.model_copy(
            update={
                "observations": observations,
                "stop_reason": reason,
                "checkpoint_at": finished,
                "finished_at": finished,
            }
        )
        self.publish(result, reserve_terminal=False)
        return result


def _age(headers: httpx.Headers) -> int | None:
    values = headers.get_list("age")
    if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
        return None
    value = values[0]
    return int(value) if len(value) <= 10 and int(value) <= 4294967295 else None


def _tls_failure(error: BaseException) -> bool:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLError):
            return True
        error = error.__cause__ or error.__context__
    return False


async def _query(client, plan, reservation, deadline):
    """One counted HTTP request; never retain headers, wire or exception text."""
    limits = plan.assessment
    retryable = False
    fields = {}
    completed_at = None
    canceled = False
    try:
        async with asyncio.timeout_at(deadline):
            # httpx extracts Set-Cookie even with cookies=None. Clear before
            # every request so a resolver cannot turn it into another channel.
            client.cookies.clear()
            async with client.stream(
                "POST",
                plan.resolver.endpoint,
                content=build_dns_query_wire(reservation.question),
                headers={
                    "Accept": "application/dns-message",
                    "Content-Type": "application/dns-message",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                status = response.status_code
                fields["http_status"] = status
                if status in {401, 403, 407}:
                    fields["transport_outcome"] = "denied"
                elif status == 429:
                    fields["transport_outcome"] = "rate_limited"
                elif status != 200:
                    fields["transport_outcome"] = "http_error"
                    retryable = status in {502, 503, 504}
                elif (
                    response.headers.get_list("content-type")
                    != ["application/dns-message"]
                    or response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    fields["transport_outcome"] = "http_error"
                else:
                    lengths = response.headers.get_list("content-length")
                    oversized = (
                        len(lengths) == 1
                        and lengths[0].isascii()
                        and lengths[0].isdecimal()
                        and (
                            len(lengths[0]) > 10
                            or int(lengths[0]) > limits.max_response_bytes
                        )
                    )
                    if oversized:
                        # The header alone rejects admission. Do not fabricate
                        # received bytes from the untrusted advertised length.
                        parsed = DNSResponse(dns_outcome="oversized", response_bytes=0)
                    else:
                        body = bytearray()
                        async for chunk in response.aiter_raw(
                            chunk_size=limits.max_response_bytes + 1
                        ):
                            remaining = limits.max_response_bytes + 1 - len(body)
                            body.extend(chunk[:remaining])
                            if len(body) > limits.max_response_bytes:
                                break
                        parsed = parse_dns_response(
                            reservation.question,
                            bytes(body),
                            http_age_seconds=_age(response.headers),
                            max_response_bytes=limits.max_response_bytes,
                        )
                    fields.update(transport_outcome="response", response=parsed)
                    retryable = parsed.dns_outcome == "servfail"
                # Completion is observed before asynchronous connection cleanup.
                # Later cleanup failures/cancellation cannot erase this result.
                completed_at = _now()
    except asyncio.CancelledError:
        canceled = True
        retryable = False
    except (TimeoutError, httpx.TimeoutException):
        if completed_at is None:
            fields = {"transport_outcome": "timeout"}
            retryable = True
    except httpx.ProxyError:
        if completed_at is None:
            fields = {"transport_outcome": "denied"}
    except httpx.HTTPError as exc:
        if completed_at is None:
            fields = {"transport_outcome": "network_error"}
            retryable = isinstance(
                exc, (httpx.ConnectError, httpx.ReadError, httpx.WriteError)
            ) and not _tls_failure(exc)
    if canceled and completed_at is None:
        return (
            reservation.model_copy(update={"transport_outcome": "interrupted"}),
            False,
            True,
        )
    finished = completed_at or _now()
    # Scheduling or cancellation can delay bookkeeping, but cannot manufacture
    # a within-budget completion time for an unknown outcome.
    if finished > reservation.started_at + timedelta(
        seconds=limits.query_timeout_seconds + 1
    ):
        return (
            reservation.model_copy(update={"transport_outcome": "interrupted"}),
            False,
            canceled,
        )
    return (
        reservation.model_copy(update={**fields, "finished_at": finished}),
        retryable,
        canceled,
    )


async def inspect_dns(
    plan: DNSQueryPlan,
    output: Path,
    *,
    environ: dict | None = None,
    started_at: datetime | None = None,
) -> DNSExecution:
    """Execute once; subsequent invocations only recover canonical evidence."""
    if build_dns_query_plan(plan.assessment, plan.resolver) != plan:
        raise ValueError("DNS plan differs from its canonical identity")
    environ = dict(os.environ if environ is None else environ)
    proxy = tool_proxy_options("httpx", environ=environ)
    lease_deadline = _lease_deadline(environ)
    with _Checkpoint(Path(output), plan) as store:
        if not store.fresh:
            return store.recover(lease_deadline)
        started_at = started_at or _now()
        initial = DNSExecution(
            plan_key=plan.plan_key,
            resolver=plan.resolver,
            started_at=started_at,
            checkpoint_at=max(started_at, _now()),
            stop_reason="in_progress",
        )
        try:
            store.publish(initial, reserve_terminal=True)
        except ValueError:
            store.current = initial
            return store.finish("output_limit")
        limits = plan.assessment
        absolute_deadline = min(
            lease_deadline, started_at + timedelta(seconds=limits.timeout_seconds)
        )
        monotonic_deadline = (
            time.monotonic() + (absolute_deadline - _now()).total_seconds()
        )
        pending = deque((q, 0, None) for q in limits.questions)
        seen = set(limits.questions)
        counts = {}
        next_request = time.monotonic()
        reason = "complete"
        try:
            if _now() >= absolute_deadline:
                return store.finish("duration_limit")
            context = ssl.create_default_context()
            context.keylog_filename = None
            async with httpx.AsyncClient(
                **proxy,
                verify=context,
                http1=True,
                http2=False,
                follow_redirects=False,
                auth=None,
                timeout=httpx.Timeout(limits.query_timeout_seconds),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                while pending:
                    if len(store.current.observations) >= limits.max_attempts:
                        reason = "attempt_limit"
                        break
                    previous = store.current.observations
                    if previous:
                        delay = max(
                            next_request - time.monotonic(),
                            (
                                previous[-1].started_at
                                + timedelta(seconds=1 / limits.queries_per_second)
                                - _now()
                            ).total_seconds(),
                        )
                        if delay > 0:
                            await asyncio.sleep(
                                min(
                                    delay, max(0, monotonic_deadline - time.monotonic())
                                )
                            )
                    if (
                        _now() >= absolute_deadline
                        or time.monotonic() >= monotonic_deadline
                    ):
                        reason = "duration_limit"
                        break
                    question, depth, parent = pending.popleft()
                    reservation_clock = time.monotonic()
                    next_request = reservation_clock + 1 / limits.queries_per_second
                    query_deadline = min(
                        monotonic_deadline,
                        reservation_clock + limits.query_timeout_seconds,
                    )
                    reservation = DNSObservation(
                        question=question,
                        attempt=len(previous) + 1,
                        depth=depth,
                        parent_attempt=parent,
                        started_at=_now(),
                        transport_outcome="in_flight",
                    )
                    candidate = store.current.model_copy(
                        update={
                            "observations": (*previous, reservation),
                            "checkpoint_at": reservation.started_at,
                        }
                    )
                    try:
                        store.publish(candidate, reserve_terminal=True)
                    except ValueError:
                        reason = "output_limit"
                        break
                    # Serialization/fsync may have consumed the remaining time.
                    # Keep the reservation counted, even if never dispatched.
                    query_wall_deadline = min(
                        absolute_deadline,
                        reservation.started_at
                        + timedelta(seconds=limits.query_timeout_seconds),
                    )
                    if (
                        _now() >= query_wall_deadline
                        or time.monotonic() >= query_deadline
                    ):
                        reason = "duration_limit"
                        break
                    row, retryable, canceled = await _query(
                        client, plan, reservation, query_deadline
                    )
                    if (
                        row.finished_at is not None
                        and row.finished_at > absolute_deadline + timedelta(seconds=1)
                    ):
                        row = reservation.model_copy(
                            update={"transport_outcome": "interrupted"}
                        )
                        retryable = False
                    candidate = store.current.model_copy(
                        update={
                            "observations": (*previous, row),
                            "checkpoint_at": max(
                                _now(), row.finished_at or row.started_at
                            ),
                        }
                    )
                    try:
                        store.publish(candidate, reserve_terminal=True)
                    except ValueError:
                        if row.transport_outcome == "response":
                            row = row.model_copy(
                                update={
                                    "response": None,
                                    "response_omitted": "output_limit",
                                }
                            )
                        candidate = candidate.model_copy(
                            update={"observations": (*previous, row)}
                        )
                        store.publish(candidate, reserve_terminal=False)
                        reason = "canceled" if canceled else "output_limit"
                        break
                    if canceled:
                        reason = "canceled"
                        break
                    if row.transport_outcome == "interrupted":
                        reason = "duration_limit"
                        break
                    counts[question] = counts.get(question, 0) + 1
                    if retryable and counts[question] < _MAX_QUESTION_ATTEMPTS:
                        pending.append((question, depth, parent))
                    for followup in followup_questions(plan, row):
                        if followup not in seen:
                            seen.add(followup)
                            pending.append((followup, depth + 1, row.attempt))
        except asyncio.CancelledError:
            return store.finish("canceled")
        except Exception:  # noqa: BLE001 - terminal evidence boundary
            # Preserve committed observations and unresolved reservations. Do
            # not expose exception strings, proxy details, paths or peer bytes.
            return store.finish("failed")
        return store.finish(reason)


async def _main(plan: DNSQueryPlan) -> DNSExecution:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await inspect_dns(plan, Path(DNS_OUTPUT_DIRECTORY))
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> int:
    try:
        result = asyncio.run(_main(decode_dns_query_plan(sys.argv[1:])))
    except (ValueError, OSError):
        return 2
    return 0 if result.stop_reason == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
