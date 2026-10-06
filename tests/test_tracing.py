# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contract and lifecycle tests for the OpenTelemetry tracing adapter."""

import math

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orbit import Application, ApplicationConfig
from orbit.asgi import Response
from orbit.diagnostics import Tracer
from orbit.runtime import Runtime
from orbit.runtime.context import current_span_id, current_trace_id
from orbit_testing import TestClient
from pydantic import ValidationError

from orbit_tracing import OpenTelemetryConfig, OpenTelemetryPlugin


def _plugin(exporter: InMemorySpanExporter) -> OpenTelemetryPlugin:
    """Create a fast, bounded plugin for tests without network export."""
    return OpenTelemetryPlugin(
        OpenTelemetryConfig(
            service_name="orbit-tests",
            schedule_delay_millis=100,
            export_timeout_millis=1_000,
            max_queue_size=64,
            max_export_batch_size=32,
        ),
        exporter=exporter,
    )


def test_configuration_rejects_unbounded_or_inconsistent_batches() -> None:
    """Reject invalid service identity and batches larger than the bounded queue."""
    with pytest.raises(ValidationError):
        OpenTelemetryConfig(service_name="\n")
    with pytest.raises(ValidationError):
        OpenTelemetryConfig(service_name="api", max_queue_size=2, max_export_batch_size=3)


async def test_plugin_records_core_http_spans_and_shuts_down_with_lifespan() -> None:
    """Exercise Core's tracer contract, route attributes, and plugin-owned exporter cleanup."""
    exporter = InMemorySpanExporter()
    plugin = _plugin(exporter)
    application = Application(ApplicationConfig(name="tracing-test"))
    application.register_plugin(plugin)

    @application.router.route("/orders/{order_id}", name="get-order")
    async def get_order(request):
        """Return one route response so Core creates an HTTP span."""
        return Response.text("found")

    runtime = Runtime(application, tracer=plugin)
    assert isinstance(plugin, Tracer)
    async with TestClient(runtime) as client:
        response = await client.request("GET", "/orders/42")

    assert response.status == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "http.get"
    assert span.attributes["http.method"] == "GET"
    assert span.attributes["http.route"] == "/orders/{order_id}"
    assert span.attributes["http.status_code"] == 200
    assert span.status.status_code.name == "OK"
    assert current_trace_id() is None
    assert current_span_id() is None
    with pytest.raises(RuntimeError, match="shutting down"):
        plugin.start_span("after-shutdown")


async def test_asgi_wrapper_uses_valid_traceparent_as_core_request_parent() -> None:
    """Extract inbound W3C context before Core creates its request span."""
    exporter = InMemorySpanExporter()
    plugin = _plugin(exporter)
    application = Application(ApplicationConfig(name="trace-context-test"))
    application.register_plugin(plugin)

    @application.router.route("/trace", name="trace")
    async def trace(request):
        """Return a response while the inbound trace context is active."""
        return Response.text("traced")

    runtime = Runtime(application, tracer=plugin)
    wrapped_runtime = plugin.wrap_asgi(runtime)
    remote_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    remote_parent_span_id = "00f067aa0ba902b7"
    async with TestClient(wrapped_runtime) as client:
        response = await client.request(
            "GET",
            "/trace",
            headers={
                "traceparent": f"00-{remote_trace_id}-{remote_parent_span_id}-01",
            },
        )

    assert response.status == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert f"{spans[0].context.trace_id:032x}" == remote_trace_id
    assert spans[0].parent is not None
    assert f"{spans[0].parent.span_id:016x}" == remote_parent_span_id


async def test_manual_injection_uses_current_core_compatible_span() -> None:
    """Expose explicit W3C header injection without choosing an outbound HTTP client."""
    plugin = _plugin(InMemorySpanExporter())
    await plugin.activate()

    async with plugin.start_span("outbound-call") as span:
        carrier: dict[str, str] = {}
        plugin.inject_context(carrier)

    traceparent = carrier["traceparent"]
    version, trace_id, span_id, flags = traceparent.split("-")
    assert version == "00"
    assert trace_id == span.trace_id
    assert span_id == span.span_id
    assert len(flags) == 2
    await plugin.deactivate()


async def test_nested_spans_share_trace_id_and_restore_core_context() -> None:
    """Preserve nested OpenTelemetry parentage and Orbit task-local identifiers."""
    exporter = InMemorySpanExporter()
    plugin = _plugin(exporter)
    await plugin.activate()

    async with plugin.start_span("parent") as parent:
        parent_trace_id = parent.trace_id
        parent_span_id = parent.span_id
        assert current_trace_id() == parent_trace_id
        assert current_span_id() == parent_span_id
        async with plugin.start_span("child") as child:
            child.set_attribute("items.count", 3)
            child.set_status("ok")
            assert child.trace_id == parent_trace_id
            assert child.span_id != parent_span_id
        assert current_span_id() == parent_span_id

    await plugin.deactivate()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["child"].parent.span_id == int(parent_span_id, 16)
    assert spans["child"].attributes["items.count"] == 3
    assert spans["parent"].parent is None
    assert current_trace_id() is None
    assert current_span_id() is None


async def test_plugin_does_not_change_process_global_tracer_provider() -> None:
    """Keep adapter registration local rather than mutating OpenTelemetry global state."""
    global_provider = otel_trace.get_tracer_provider()
    plugin = _plugin(InMemorySpanExporter())
    await plugin.activate()
    await plugin.deactivate()
    assert otel_trace.get_tracer_provider() is global_provider


async def test_attributes_are_bounded_and_reject_unsupported_values() -> None:
    """Prevent unsupported, non-finite, and oversized attributes reaching exporters."""
    plugin = _plugin(InMemorySpanExporter())
    async with plugin.start_span("bounded") as span:
        with pytest.raises(ValueError, match="finite"):
            span.set_attribute("bad.float", math.inf)
        with pytest.raises(TypeError, match="homogeneous"):
            span.set_attribute("bad.sequence", [1, "mixed"])
        with pytest.raises(TypeError, match="supported"):
            span.set_attribute("bad.object", object())
        with pytest.raises(ValueError, match="printable"):
            span.set_attribute("bad.text", "line\nbreak")
    await plugin.deactivate()
