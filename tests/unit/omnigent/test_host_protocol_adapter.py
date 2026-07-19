"""Hermetic conformance tests for the pinned stock-host frame profile."""

from pathlib import Path

import pytest

from moonmind.omnigent.host_protocol_adapter import (
    MAX_HOST_FRAME_BYTES,
    OmnigentHostProtocolAdapter,
    UpstreamHostProtocolError,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "omnigent"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_decodes_stock_host_hello_and_result_fixtures() -> None:
    adapter = OmnigentHostProtocolAdapter()

    hello = adapter.decode_host_frame(_fixture("host_hello.json"))
    result = adapter.decode_host_frame(_fixture("host_launch_result.json"))
    exited = adapter.decode_host_frame(_fixture("host_runner_exited.json"))

    assert hello.name == "stock-host"
    assert hello.configured_harnesses == {"codex-native": True}
    assert result.runner_id == "runner_token_expected"
    assert exited.error == "runner process exited with code 1"


def test_reuses_one_pinned_frames_module() -> None:
    assert OmnigentHostProtocolAdapter().frames is OmnigentHostProtocolAdapter().frames


def test_encodes_exact_stock_host_launch_and_stop_commands() -> None:
    adapter = OmnigentHostProtocolAdapter()
    frames = adapter.frames

    launch = adapter.encode_server_frame(
        frames.HostLaunchRunnerFrame(
            request_id="req_launch_1",
            binding_token="redacted-test-binding",
            workspace="/workspace/repo",
            session_id="sess_1",
            harness="codex-native",
        )
    )
    stop = adapter.encode_server_frame(
        frames.HostStopRunnerFrame(
            request_id="req_stop_1", runner_id="runner_token_expected"
        )
    )

    assert '"kind":"host.launch_runner"' in launch
    assert '"binding_token":"redacted-test-binding"' in launch
    assert '"kind":"host.stop_runner"' in stop


def test_rejects_incompatible_misdirected_and_oversized_frames() -> None:
    adapter = OmnigentHostProtocolAdapter()
    incompatible = _fixture("host_hello.json").replace(
        '"frame_protocol_version":1', '"frame_protocol_version":2'
    )

    with pytest.raises(UpstreamHostProtocolError, match="incompatible"):
        adapter.decode_host_frame(incompatible)
    with pytest.raises(UpstreamHostProtocolError, match="server-to-host"):
        adapter.decode_host_frame(
            '{"kind":"host.stop_runner","request_id":"r","runner_id":"x"}'
        )
    with pytest.raises(UpstreamHostProtocolError, match="byte limit"):
        adapter.decode_host_frame("x" * (MAX_HOST_FRAME_BYTES + 1))


def test_rejects_host_result_on_outbound_direction() -> None:
    adapter = OmnigentHostProtocolAdapter()
    frame = adapter.decode_host_frame(_fixture("host_launch_result.json"))

    with pytest.raises(UpstreamHostProtocolError, match="cannot be sent"):
        adapter.encode_server_frame(frame)
