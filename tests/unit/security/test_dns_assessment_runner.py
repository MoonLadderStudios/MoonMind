"""The fixed DNS runner must never bypass the deployment CONNECT proxy."""

import asyncio
import os
import socket
import ssl
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import dns.message
import dns.query
import dns.rcode
import dns.resolver
import dns.rrset
import httpx
import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from moonmind.security import dns_assessment_runner as runner
from moonmind.security.dns_assessments import (
    DNS_TERMINAL_RESERVE_BYTES,
    DNSAssessment,
    DNSExecution,
    DNSObservation,
    DNSQuestion,
    DNSResolverConfig,
    build_dns_query_plan,
    build_dns_query_wire,
    encode_dns_query_plan,
    serialize_dns_artifacts,
    validate_dns_execution,
)


def plan(**limits):
    return build_dns_query_plan(
        DNSAssessment(
            questions=(DNSQuestion(name="example.test", record_type="A"),),
            **limits,
        ),
        DNSResolverConfig(endpoint="https://resolver.test/dns-query"),
    )


def test_missing_explicit_proxy_fails_before_client_or_network(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("client must not be created without the explicit proxy")

    monkeypatch.setattr(runner.httpx, "AsyncClient", forbidden)
    with pytest.raises(ValueError, match="proxy"):
        asyncio.run(runner.inspect_dns(plan(), tmp_path, environ={}))
    assert not (tmp_path / "execution.json").exists()


def answer(wire, *, rcode=0, target=None, text=None):
    query = dns.message.from_wire(wire)
    response = dns.message.make_response(query)
    response.origin = dns.name.root
    response.set_rcode(rcode)
    q = query.question[0]
    if not rcode:
        if target:
            response.answer.append(
                dns.rrset.from_text(q.name, 120, "IN", "CNAME", target)
            )
        elif text:
            response.answer.append(dns.rrset.from_text(q.name, 120, "IN", "TXT", text))
        else:
            response.answer.append(
                dns.rrset.from_text(q.name, 120, "IN", "A", "192.0.2.1")
            )
    return response.to_wire(max_size=65535)


@dataclass
class Fixture:
    endpoint: str = ""
    lease_deadline: int = field(
        default_factory=lambda: int(datetime.now(UTC).timestamp()) + 120
    )
    proxy: str = "http://127.0.0.1:3128"
    requests: list = field(default_factory=list)
    connects: list = field(default_factory=list)
    direct: list = field(default_factory=list)
    tasks: set = field(default_factory=set)
    errors: list = field(default_factory=list)
    status: int = 200
    proxy_status: int = 200
    headers: dict = field(
        default_factory=lambda: {"Content-Type": "application/dns-message", "Age": "0"}
    )
    body: object = answer
    hang: bool = False
    chunked: bool = False
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    def make_plan(self, **limits):
        questions = limits.pop(
            "questions", (DNSQuestion(name="example.test", record_type="A"),)
        )
        return build_dns_query_plan(
            DNSAssessment(questions=questions, queries_per_second=20, **limits),
            DNSResolverConfig(endpoint=self.endpoint),
        )

    @property
    def environ(self):
        return {
            "MOONMIND_EGRESS_PROXY": self.proxy,
            "MOONMIND_EGRESS_DEADLINE": str(self.lease_deadline),
            "HTTPS_PROXY": "http://forbidden.test:9999",
            "ALL_PROXY": "http://forbidden.test:9999",
            "NO_PROXY": "*",
        }


# Real TLS, HTTP/1.1 and CONNECT, with the resolver name deliberately unavailable
# to local DNS. All traffic stays on loopback; no public resolvers are queried.
@pytest_asyncio.fixture
async def doh(tmp_path, monkeypatch):
    fixture = Fixture()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "resolver.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("resolver.test")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "certificate.pem").write_bytes(pem)
    (tmp_path / "private.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        tmp_path / "certificate.pem", tmp_path / "private.key"
    )
    client_context = ssl.create_default_context(cadata=pem.decode())
    monkeypatch.setattr(runner.ssl, "create_default_context", lambda: client_context)

    async def resolver(reader, writer):
        fixture.tasks.add(asyncio.current_task())
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            first, *lines = head.split("\r\n")
            headers = dict(line.lower().split(": ", 1) for line in lines if line)
            wire = await reader.readexactly(int(headers["content-length"]))
            query = dns.message.from_wire(wire)
            fixture.requests.append((first, headers, query))
            fixture.requested.set()
            if fixture.hang:
                await fixture.release.wait()
            body = fixture.body(wire) if callable(fixture.body) else fixture.body
            response_headers = {**fixture.headers, "Connection": "close"}
            if fixture.chunked:
                response_headers["Transfer-Encoding"] = "chunked"
            else:
                response_headers["Content-Length"] = str(len(body))
            response = (
                f"HTTP/1.1 {fixture.status} Fixture\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in response_headers.items())
                + "\r\n"
            )
            writer.write(response.encode())
            if fixture.chunked:
                for start in range(0, len(body), 7):
                    chunk = body[start : start + 7]
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                writer.write(b"0\r\n\r\n")
            else:
                writer.write(body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # noqa: BLE001 - surface background fixture failures
            fixture.errors.append(exc)
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            fixture.tasks.discard(asyncio.current_task())

    resolver_server = await asyncio.start_server(
        resolver, "127.0.0.1", 0, ssl=server_context
    )
    port = resolver_server.sockets[0].getsockname()[1]
    fixture.endpoint = f"https://resolver.test:{port}/dns-query"

    async def proxy(reader, writer):
        fixture.tasks.add(asyncio.current_task())
        upstream = None
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            first = head.split(b"\r\n", 1)[0].decode("ascii")
            fixture.connects.append(first)
            assert first == f"CONNECT resolver.test:{port} HTTP/1.1"
            if fixture.proxy_status != 200:
                writer.write(
                    f"HTTP/1.1 {fixture.proxy_status} Refused\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
                return
            upstream_reader, upstream = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def copy(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
                target.close()

            await asyncio.gather(copy(reader, upstream), copy(upstream_reader, writer))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # noqa: BLE001 - surface background fixture failures
            fixture.errors.append(exc)
        finally:
            if upstream:
                upstream.close()
            writer.close()
            fixture.tasks.discard(asyncio.current_task())

    # A worker-specific loopback address permits xdist without changing the
    # production proxy's mandatory port or monkeypatching policy validation.
    proxy_host = f"127.0.{os.getpid() // 250 % 250 + 1}.{os.getpid() % 250 + 1}"
    proxy_server = await asyncio.start_server(proxy, proxy_host, 3128)
    fixture.proxy = f"http://{proxy_host}:3128"
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in {
            "127.0.0.1",
            b"127.0.0.1",
            proxy_host,
            proxy_host.encode(),
            None,
        }:
            fixture.direct.append(host)
            raise AssertionError("forbidden direct name resolution")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    def no_dns(*args, **kwargs):
        fixture.direct.append("raw-dns")
        raise AssertionError("raw DNS transport is forbidden")

    for name in ("udp", "tcp", "tls", "https", "udp_with_fallback"):
        monkeypatch.setattr(dns.query, name, no_dns)
    monkeypatch.setattr(dns.resolver, "resolve", no_dns)
    try:
        yield fixture
    finally:
        proxy_server.close()
        resolver_server.close()
        await proxy_server.wait_closed()
        await resolver_server.wait_closed()
        for task in list(fixture.tasks):
            task.cancel()
        await asyncio.gather(*fixture.tasks, return_exceptions=True)
        assert fixture.direct == []
        assert fixture.errors == []


def read_execution(output):
    return DNSExecution.model_validate_json((output / "execution.json").read_bytes())


def assert_budget(output, p):
    assert (
        sum(
            (output / name).stat().st_size
            for name in ("execution.json", "summary.json")
        )
        <= p.assessment.max_output_bytes
    )
    validate_dns_execution(p, read_execution(output))


@pytest.mark.asyncio
async def test_actual_tls_connect_question_and_initial_reservation(doh, tmp_path):
    output = tmp_path / "output"
    p = doh.make_plan()
    original = doh.body

    def inspect_reservation(wire):
        checkpoint = read_execution(output)
        assert checkpoint.stop_reason == "in_progress"
        assert len(checkpoint.observations) == 1
        assert checkpoint.observations[0].transport_outcome == "in_flight"
        assert checkpoint.observations[0].finished_at is None
        return original(wire)

    doh.body = inspect_reservation
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert result.stop_reason == "complete"
    assert result.observations[0].response.dns_outcome == "answer"
    assert result.observations[0].response.freshness == "fresh"
    assert len(doh.connects) == len(doh.requests) == 1
    first, headers, query = doh.requests[0]
    assert first == "POST /dns-query HTTP/1.1"
    assert headers["accept-encoding"] == "identity"
    assert "authorization" not in headers and "cookie" not in headers
    assert query.id == 0
    assert query.question[0].name.to_text() == "example.test."
    assert query.question[0].rdtype == 1
    assert_budget(output, p)
    artifacts = (output / "execution.json").read_text() + (
        output / "summary.json"
    ).read_text()
    assert "wire" not in artifacts and "base64" not in artifacts


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, "invalid", "-1", "4294967296", "0, 0"])
async def test_missing_or_invalid_age_never_claims_fresh(doh, tmp_path, age):
    if age is None:
        doh.headers.pop("Age")
    else:
        doh.headers["Age"] = age
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert result.observations[0].response.freshness == "unknown"
    assert result.observations[0].response.http_age_seconds is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,outcome,count",
    [
        (403, "denied", 1),
        (401, "denied", 1),
        (407, "denied", 1),
        (429, "rate_limited", 1),
        (302, "http_error", 1),
        (500, "http_error", 1),
        (503, "http_error", 2),
    ],
)
async def test_http_failures_have_bounded_retries_no_redirects(
    doh, tmp_path, status, outcome, count
):
    doh.status = status
    doh.headers.update(
        Location="https://forbidden.test/", **{"Set-Cookie": "secret=value"}
    )
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert len(result.observations) == count
    assert len(doh.requests) == count
    assert all(row.transport_outcome == outcome for row in result.observations)
    assert all("cookie" not in headers for _, headers, _ in doh.requests)
    if count > 1:
        assert (
            result.observations[1].started_at - result.observations[0].started_at
        ).total_seconds() >= 0.049
    assert "forbidden.test" not in (tmp_path / "out" / "execution.json").read_text()


@pytest.mark.asyncio
async def test_proxy_denial_never_falls_back(doh, tmp_path):
    doh.proxy_status = 403
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert len(doh.connects) == 1 and doh.requests == []
    assert result.observations[0].transport_outcome == "denied"


@pytest.mark.asyncio
async def test_tls_verification_failure_never_retries_unverified(
    doh, tmp_path, monkeypatch
):
    # Deliberately remove fixture trust, never disable verification.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(runner.ssl, "create_default_context", lambda: context)
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert len(doh.connects) == 1 and doh.requests == []
    assert result.observations[0].transport_outcome == "network_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/dns-message; charset=utf-8"},
        {"Content-Encoding": "gzip"},
    ],
)
async def test_wrong_media_or_compression_rejected_before_parse(
    doh, tmp_path, monkeypatch, headers
):
    doh.headers.update(headers)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid HTTP envelope must not reach DNS parser")

    monkeypatch.setattr(runner, "parse_dns_response", forbidden)
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert result.observations[0].transport_outcome == "http_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_response_is_bounded_before_parsing(doh, tmp_path, chunked):
    doh.body = b"private response bytes" * 500
    doh.chunked = chunked
    result = await runner.inspect_dns(
        doh.make_plan(max_response_bytes=64), tmp_path / "out", environ=doh.environ
    )
    response = result.observations[0].response
    assert response.dns_outcome == "oversized"
    assert response.response_bytes == (65 if chunked else 0)
    assert response.records == ()
    assert "private response" not in (tmp_path / "out" / "execution.json").read_text()


@pytest.mark.asyncio
async def test_malformed_response_is_not_retried(doh, tmp_path):
    doh.body = b"a secret malformed DNS response"
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert len(doh.requests) == 1
    assert result.observations[0].response.dns_outcome == "malformed"


@pytest.mark.asyncio
async def test_servfail_retried_once_and_every_request_is_counted(doh, tmp_path):
    doh.body = lambda wire: answer(wire, rcode=dns.rcode.SERVFAIL)
    output = tmp_path / "out"
    p = doh.make_plan()
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert len(doh.requests) == len(result.observations) == 2
    assert all(row.response.dns_outcome == "servfail" for row in result.observations)
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_attempt_limit_applies_to_retries(doh, tmp_path):
    doh.status = 503
    result = await runner.inspect_dns(
        doh.make_plan(max_attempts=1), tmp_path / "out", environ=doh.environ
    )
    assert len(doh.requests) == 1
    assert result.stop_reason == "attempt_limit"


@pytest.mark.asyncio
async def test_original_deadline_not_reset_by_timeout_retry(doh, tmp_path):
    doh.hang = True
    p = doh.make_plan(timeout_seconds=1, query_timeout_seconds=1)
    result = await runner.inspect_dns(p, tmp_path / "out", environ=doh.environ)
    assert len(doh.requests) == 1
    assert result.stop_reason == "duration_limit"
    assert result.observations[0].transport_outcome == "timeout"
    assert (result.finished_at - result.started_at).total_seconds() < 2


@pytest.mark.asyncio
async def test_expired_invocation_never_creates_client(doh, tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("expired invocation must not create a client")

    monkeypatch.setattr(runner.httpx, "AsyncClient", forbidden)
    p = doh.make_plan(timeout_seconds=1, query_timeout_seconds=1)
    result = await runner.inspect_dns(
        p,
        tmp_path / "out",
        environ=doh.environ,
        started_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    assert result.stop_reason == "duration_limit" and result.observations == ()


@pytest.mark.asyncio
async def test_cancellation_preserves_unknown_reservation_and_forbids_relaunch(
    doh, tmp_path
):
    doh.hang = True
    output = tmp_path / "out"
    p = doh.make_plan()
    task = asyncio.create_task(runner.inspect_dns(p, output, environ=doh.environ))
    await asyncio.wait_for(doh.requested.wait(), 3)
    task.cancel()
    result = await task
    assert result.stop_reason == "canceled"
    assert result.observations[0].transport_outcome == "interrupted"
    assert result.observations[0].finished_at is None
    again = await runner.inspect_dns(p, output, environ=doh.environ)
    assert again == result
    assert len(doh.requests) == 1
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_concurrent_launch_cannot_change_live_checkpoint(doh, tmp_path):
    doh.hang = True
    output = tmp_path / "out"
    p = doh.make_plan()
    task = asyncio.create_task(runner.inspect_dns(p, output, environ=doh.environ))
    await asyncio.wait_for(doh.requested.wait(), 3)
    before = (output / "execution.json").read_bytes()
    with pytest.raises(BlockingIOError):
        await runner.inspect_dns(p, output, environ=doh.environ)
    assert (output / "execution.json").read_bytes() == before
    task.cancel()
    await task
    assert len(doh.requests) == 1


@pytest.mark.asyncio
async def test_completed_checkpoint_relaunch_does_not_requery(doh, tmp_path):
    output = tmp_path / "out"
    p = doh.make_plan()
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    (output / "summary.json").unlink()
    again = await runner.inspect_dns(p, output, environ=doh.environ)
    assert again == result and len(doh.requests) == 1
    assert (output / "summary.json").is_file()


@pytest.mark.asyncio
async def test_recovery_retains_completed_and_unknown_outcomes(doh, tmp_path):
    output = tmp_path / "out"
    q1 = DNSQuestion(name="a.test", record_type="A")
    q2 = DNSQuestion(name="b.test", record_type="A")
    p = doh.make_plan(questions=(q1, q2))
    start = datetime.now(UTC) - timedelta(seconds=3)
    first = DNSObservation(
        question=q1,
        attempt=1,
        started_at=start,
        finished_at=start + timedelta(milliseconds=1),
        transport_outcome="response",
        response=runner.parse_dns_response(
            q1, answer(build_dns_query_wire(q1)), http_age_seconds=0
        ),
    )
    second = DNSObservation(
        question=q2,
        attempt=2,
        started_at=start + timedelta(seconds=1),
        transport_outcome="in_flight",
    )
    current = DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=start,
        checkpoint_at=start + timedelta(seconds=1),
        observations=(first, second),
        stop_reason="in_progress",
    )
    with runner._Checkpoint(output, p) as store:
        store.publish(current, reserve_terminal=True)
    recovered = await runner.inspect_dns(p, output, environ=doh.environ)
    assert recovered.stop_reason == "failed"
    assert recovered.observations[0] == first
    assert recovered.observations[1].finished_at is None
    assert recovered.observations[1].transport_outcome == "interrupted"
    assert doh.requests == [] and doh.connects == []
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_changed_plan_or_missing_checkpoint_never_restarts(doh, tmp_path):
    output = tmp_path / "out"
    p = doh.make_plan()
    with runner._Checkpoint(output, p):
        pass
    with pytest.raises(OSError):
        await runner.inspect_dns(p, output, environ=doh.environ)
    with pytest.raises(ValueError, match="another or unknown plan"):
        await runner.inspect_dns(
            doh.make_plan(max_attempts=3), output, environ=doh.environ
        )
    assert doh.connects == []


@pytest.mark.asyncio
async def test_only_explicitly_admitted_alias_followup_is_queried(doh, tmp_path):
    admitted = DNSQuestion(name="target.test", record_type="A")
    doh.body = lambda wire: (
        answer(wire, target="target.test")
        if dns.message.from_wire(wire).question[0].name.to_text() == "example.test."
        else answer(wire, target="unadmitted.test")
    )
    p = doh.make_plan(allowed_followups=(admitted,))
    result = await runner.inspect_dns(p, tmp_path / "out", environ=doh.environ)
    assert [q.question[0].name.to_text() for _, _, q in doh.requests] == [
        "example.test.",
        "target.test.",
    ]
    assert result.observations[1].parent_attempt == 1
    assert result.observations[1].depth == 1


@pytest.mark.asyncio
async def test_followup_depth_zero_never_follows_answer(doh, tmp_path):
    doh.body = lambda wire: answer(wire, target="target.test")
    p = doh.make_plan(
        allowed_followups=(DNSQuestion(name="target.test", record_type="A"),),
        max_followup_depth=0,
    )
    result = await runner.inspect_dns(p, tmp_path / "out", environ=doh.environ)
    assert len(doh.requests) == len(result.observations) == 1


@pytest.mark.asyncio
async def test_initial_output_reserve_checked_before_client(doh, tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("unreservable output must not create a client")

    monkeypatch.setattr(runner.httpx, "AsyncClient", forbidden)
    p = doh.make_plan(max_output_bytes=4096)
    output = tmp_path / "out"
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert result.stop_reason == "output_limit" and result.observations == ()
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_received_response_exceeding_output_is_omitted_with_actual_finish(
    doh, tmp_path
):
    q = DNSQuestion(name="example.test", record_type="TXT")

    # Each RR is bounded, while all projections together exceed output capacity.
    def large_answer(wire):
        response = dns.message.make_response(dns.message.from_wire(wire))
        response.origin = dns.name.root
        response.answer.append(
            dns.rrset.from_text(
                "example.test",
                120,
                "IN",
                "TXT",
                *[f'"{index:03d}' + "x" * 220 + '"' for index in range(50)],
            )
        )
        return response.to_wire(max_size=65535)

    doh.body = large_answer
    output = tmp_path / "out"
    p = doh.make_plan(questions=(q,), max_output_bytes=10000)
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert len(doh.requests) == 1
    assert result.stop_reason == "output_limit"
    row = result.observations[0]
    assert row.transport_outcome == "response"
    assert row.response is None and row.response_omitted == "output_limit"
    assert row.finished_at is not None
    assert row.started_at <= row.finished_at <= result.finished_at
    assert_budget(output, p)


def test_canonical_cli_has_fixed_output_no_extra_flags(monkeypatch):
    p = plan()
    seen = []

    async def fake_inspect(actual, output):
        seen.append((actual, output))
        now = datetime.now(UTC)
        return DNSExecution(
            plan_key=actual.plan_key,
            resolver=actual.resolver,
            started_at=now,
            checkpoint_at=now,
            finished_at=now,
            stop_reason="complete",
        )

    monkeypatch.setattr(runner, "inspect_dns", fake_inspect)
    monkeypatch.setattr(runner.sys, "argv", ["runner", *encode_dns_query_plan(p)])
    assert runner.main() == 0
    assert str(seen[0][1]) == "/outputs/dns-assessment" and seen[0][0] == p
    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["runner", *encode_dns_query_plan(p), "--output=/tmp/elsewhere"],
    )
    assert runner.main() == 2 and len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_write,request_count,outcome",
    [
        (3, 0, "interrupted"),
        (4, 0, "interrupted"),
        (5, 1, "response"),
        (6, 1, "response"),
    ],
)
async def test_partial_publication_failure_preserves_latest_progress(
    doh, tmp_path, monkeypatch, failed_write, request_count, outcome
):
    actual = runner._atomic_write
    writes = 0

    def fail_once(path, body):
        nonlocal writes
        writes += 1
        if writes == failed_write:
            raise OSError("private filesystem error must not be retained")
        actual(path, body)

    monkeypatch.setattr(runner, "_atomic_write", fail_once)
    output = tmp_path / "out"
    p = doh.make_plan()
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert result.stop_reason == "failed"
    assert len(doh.requests) == request_count
    assert len(result.observations) == 1
    row = result.observations[0]
    assert row.transport_outcome == outcome
    assert (row.finished_at is None) == (outcome == "interrupted")
    again = await runner.inspect_dns(p, output, environ=doh.environ)
    assert again == result and len(doh.requests) == request_count
    assert "private filesystem" not in (output / "execution.json").read_text()
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_reservation_that_cannot_fit_never_dispatches(doh, tmp_path):
    p = doh.make_plan()
    now = datetime.now(UTC)
    empty = DNSExecution(
        plan_key=p.plan_key,
        resolver=p.resolver,
        started_at=now,
        checkpoint_at=now,
        stop_reason="in_progress",
    )
    # Leave enough space for the initial checkpoint and its terminal reserve,
    # but less than one counted observation's projection into both artifacts.
    budget = (
        sum(map(len, serialize_dns_artifacts(p, empty, "execution.json").values()))
        + DNS_TERMINAL_RESERVE_BYTES
        + 100
    )
    p = doh.make_plan(max_output_bytes=budget)
    output = tmp_path / "out"
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert result.stop_reason == "output_limit"
    assert result.observations == () and doh.requests == []
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_cancellation_does_not_erase_an_earlier_success(doh, tmp_path):
    output = tmp_path / "out"
    p = doh.make_plan(
        questions=(
            DNSQuestion(name="a.test", record_type="A"),
            DNSQuestion(name="b.test", record_type="A"),
        )
    )
    first_observed = None
    original = doh.body

    def next_hangs(wire):
        nonlocal first_observed
        # This takes effect for the next request, after the first response.
        doh.hang = True
        doh.requested.clear()
        first_observed = dns.message.from_wire(wire).question[0].name.to_text()
        return original(wire)

    doh.body = next_hangs
    task = asyncio.create_task(runner.inspect_dns(p, output, environ=doh.environ))
    while len(doh.requests) < 2:
        await asyncio.sleep(0.01)
    task.cancel()
    result = await task
    assert first_observed == "a.test."
    assert result.stop_reason == "canceled"
    assert result.observations[0].transport_outcome == "response"
    assert result.observations[0].response.dns_outcome == "answer"
    assert result.observations[1].transport_outcome == "interrupted"
    assert result.observations[1].finished_at is None
    assert_budget(output, p)


@pytest.mark.asyncio
async def test_timeout_retry_keeps_per_query_bound(doh, tmp_path):
    doh.hang = True
    p = doh.make_plan(timeout_seconds=5, query_timeout_seconds=1)
    result = await runner.inspect_dns(p, tmp_path / "out", environ=doh.environ)
    assert len(doh.requests) == len(result.observations) == 2
    for row in result.observations:
        assert row.transport_outcome == "timeout"
        assert 0.9 <= (row.finished_at - row.started_at).total_seconds() < 2


@pytest.mark.asyncio
async def test_client_security_options_are_explicit(doh, tmp_path, monkeypatch):
    real_client = httpx.AsyncClient
    seen = []

    def client(**kwargs):
        seen.append(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(runner.httpx, "AsyncClient", client)
    await runner.inspect_dns(doh.make_plan(), tmp_path / "out", environ=doh.environ)
    assert len(seen) == 1
    options = seen[0]
    assert options["proxy"] == doh.proxy
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["http1"] is True and options["http2"] is False
    assert options["auth"] is None
    assert options["verify"].check_hostname is True
    assert options["verify"].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.asyncio
async def test_checkpoint_time_consumes_query_budget_before_dispatch(
    doh, tmp_path, monkeypatch
):
    real_publish = runner._Checkpoint.publish
    real_now = runner._now
    elapsed = timedelta()
    dispatched = []

    def slow_reservation(store, execution, **kwargs):
        nonlocal elapsed
        real_publish(store, execution, **kwargs)
        if (
            execution.observations
            and execution.observations[-1].transport_outcome == "in_flight"
        ):
            elapsed = timedelta(seconds=2)

    async def forbidden(*args, **kwargs):
        dispatched.append(True)
        raise AssertionError("expired per-query reservation must not dispatch")

    monkeypatch.setattr(runner._Checkpoint, "publish", slow_reservation)
    monkeypatch.setattr(runner, "_now", lambda: real_now() + elapsed)
    monkeypatch.setattr(runner, "_query", forbidden)
    result = await runner.inspect_dns(
        doh.make_plan(query_timeout_seconds=1), tmp_path / "out", environ=doh.environ
    )
    assert dispatched == []
    assert result.stop_reason == "duration_limit"
    assert len(result.observations) == 1
    assert result.observations[0].transport_outcome == "interrupted"
    assert result.observations[0].finished_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deadline", [None, "", "NaN", "1.5", "-1", "99999999999999999999", "253402300800"]
)
async def test_missing_or_invalid_lease_deadline_prevents_network(
    doh, tmp_path, deadline
):
    environ = doh.environ
    if deadline is None:
        environ.pop("MOONMIND_EGRESS_DEADLINE")
    else:
        environ["MOONMIND_EGRESS_DEADLINE"] = deadline
    with pytest.raises(ValueError, match="deadline"):
        await runner.inspect_dns(doh.make_plan(), tmp_path / "out", environ=environ)
    assert doh.requests == [] and doh.connects == []


@pytest.mark.asyncio
async def test_expired_deployment_lease_does_not_get_fresh_duration(
    doh, tmp_path, monkeypatch
):
    def forbidden(**kwargs):
        pytest.fail("expired lease must not create a client")

    monkeypatch.setattr(runner.httpx, "AsyncClient", forbidden)
    doh.lease_deadline = int(datetime.now(UTC).timestamp()) - 1
    result = await runner.inspect_dns(
        doh.make_plan(), tmp_path / "out", environ=doh.environ
    )
    assert result.stop_reason == "duration_limit" and result.observations == ()


@pytest.mark.asyncio
async def test_lease_deadline_wins_over_longer_query_and_job_budgets(doh, tmp_path):
    doh.hang = True
    doh.lease_deadline = int(datetime.now(UTC).timestamp()) + 2
    result = await runner.inspect_dns(
        doh.make_plan(timeout_seconds=120, query_timeout_seconds=10),
        tmp_path / "out",
        environ=doh.environ,
    )
    assert len(doh.requests) == 1
    assert result.stop_reason == "duration_limit"
    assert result.observations[0].transport_outcome == "timeout"
    assert result.observations[0].finished_at.timestamp() <= doh.lease_deadline + 1


@pytest.mark.asyncio
async def test_cancel_during_cleanup_keeps_already_parsed_response(
    doh, tmp_path, monkeypatch
):
    actual_parse = runner.parse_dns_response
    actual_close = httpx.Response.aclose
    parsed = False
    canceled = False

    def parse(*args, **kwargs):
        nonlocal parsed
        result = actual_parse(*args, **kwargs)
        parsed = True
        return result

    async def close(response):
        nonlocal canceled
        if parsed and not canceled:
            canceled = True
            raise asyncio.CancelledError
        await actual_close(response)

    monkeypatch.setattr(runner, "parse_dns_response", parse)
    monkeypatch.setattr(httpx.Response, "aclose", close)
    output = tmp_path / "out"
    p = doh.make_plan()
    result = await runner.inspect_dns(p, output, environ=doh.environ)
    assert result.stop_reason == "canceled" and len(doh.requests) == 1
    assert result.observations[0].transport_outcome == "response"
    assert result.observations[0].response.dns_outcome == "answer"
    assert result.observations[0].finished_at is not None
    assert_budget(output, p)
