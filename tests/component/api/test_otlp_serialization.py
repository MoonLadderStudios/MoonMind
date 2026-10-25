"""Exercise the installed OTLP exporters against credential-free collectors."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import grpc
import pytest
import requests
from opentelemetry.proto.collector.metrics.v1 import (
    metrics_service_pb2,
    metrics_service_pb2_grpc,
)
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

pytestmark = pytest.mark.component


@pytest.fixture(params=["http/protobuf", "grpc"])
def otlp_collectors(request, monkeypatch):
    # Keep process-level collector credentials and compression out of the fixture.
    for name in (
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_COMPRESSION",
        "OTEL_EXPORTER_OTLP_TRACES_COMPRESSION",
        "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION",
    ):
        monkeypatch.delenv(name, raising=False)
    received = {"traces": [], "metrics": []}

    if request.param == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        class Collector(BaseHTTPRequestHandler):
            def do_POST(self):
                assert self.headers["Content-Type"] == "application/x-protobuf"
                signal = self.path.removeprefix("/v1/")
                message_type = (
                    trace_service_pb2.ExportTraceServiceRequest
                    if signal == "traces"
                    else metrics_service_pb2.ExportMetricsServiceRequest
                )
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received[signal].append(message_type.FromString(body))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        sessions = [requests.Session(), requests.Session()]
        for session in sessions:
            session.trust_env = False
        span_exporter = OTLPSpanExporter(
            endpoint=endpoint + "/v1/traces", session=sessions[0], timeout=2
        )
        metric_exporter = OTLPMetricExporter(
            endpoint=endpoint + "/v1/metrics", session=sessions[1], timeout=2
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        class TraceCollector(trace_service_pb2_grpc.TraceServiceServicer):
            def Export(self, message, context):
                received["traces"].append(message)
                return trace_service_pb2.ExportTraceServiceResponse()

        class MetricCollector(metrics_service_pb2_grpc.MetricsServiceServicer):
            def Export(self, message, context):
                received["metrics"].append(message)
                return metrics_service_pb2.ExportMetricsServiceResponse()

        executor = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(executor)
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            TraceCollector(), server
        )
        metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(
            MetricCollector(), server
        )
        port = server.add_insecure_port("127.0.0.1:0")
        assert port
        server.start()
        endpoint = f"http://127.0.0.1:{port}"
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True, timeout=2)
        metric_exporter = OTLPMetricExporter(
            endpoint=endpoint, insecure=True, timeout=2
        )

    try:
        yield span_exporter, metric_exporter, received
    finally:
        span_exporter.shutdown()
        metric_exporter.shutdown()
        if request.param == "http/protobuf":
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        else:
            server.stop(grace=0).wait(timeout=2)
            executor.shutdown(wait=True)


def test_otlp_exporters_serialize_spans_and_metrics(otlp_collectors):
    span_exporter, metric_exporter, received = otlp_collectors
    resource = Resource.create({"service.name": "moonmind-protobuf-fixture"})
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(SimpleSpanProcessor(span_exporter))
    reader = PeriodicExportingMetricReader(
        metric_exporter, export_interval_millis=float("inf")
    )
    meters = MeterProvider(resource=resource, metric_readers=[reader])
    try:
        with traces.get_tracer("moonmind.fixture").start_as_current_span(
            "schedule.trigger", attributes={"schedule.id": "fixture-schedule"}
        ):
            pass
        meters.get_meter("moonmind.fixture").create_counter("schedule.triggers").add(
            7, {"result": "accepted"}
        )
        assert traces.force_flush(timeout_millis=2000)
        assert meters.force_flush(timeout_millis=2000)

        trace_resource = received["traces"][0].resource_spans[0]
        metric_resource = received["metrics"][0].resource_metrics[0]
        for exported in (trace_resource, metric_resource):
            attributes = {
                item.key: item.value.string_value
                for item in exported.resource.attributes
            }
            assert attributes["service.name"] == "moonmind-protobuf-fixture"
        span = trace_resource.scope_spans[0].spans[0]
        assert span.name == "schedule.trigger"
        assert len(span.trace_id) == 16 and len(span.span_id) == 8
        assert span.end_time_unix_nano >= span.start_time_unix_nano > 0
        assert span.attributes[0].value.string_value == "fixture-schedule"
        metric = metric_resource.scope_metrics[0].metrics[0]
        assert metric.name == "schedule.triggers"
        assert metric.sum.data_points[0].as_int == 7
        assert metric.sum.data_points[0].attributes[0].value.string_value == "accepted"
    finally:
        meters.shutdown()
        traces.shutdown()
