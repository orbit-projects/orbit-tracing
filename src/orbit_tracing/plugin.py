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
"""Lifecycle-owned OpenTelemetry implementation of Orbit's ``Tracer`` contract."""

from __future__ import annotations

import asyncio
import math
from collections.abc import MutableMapping, Sequence
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, cast

from opentelemetry import trace as otel_trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span as OTelSpan
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from orbit.asgi.types import Receive, Scope, Send
from orbit.diagnostics.tracing import Span, Tracer
from orbit.plugins import Plugin, PluginMetadata
from orbit.runtime.context import bind_span_id, bind_trace_id, reset_span_id, reset_trace_id

from orbit_tracing.config import OpenTelemetryConfig

_MAX_SPAN_ATTRIBUTES = 128
_MAX_ATTRIBUTE_LENGTH = 4_096
_MAX_ASGI_HEADERS = 1_000
_MAX_TRACE_HEADER_BYTES = 512


class OpenTelemetryPlugin(Plugin):
    """Adapt Core spans to an isolated OpenTelemetry SDK provider.

    The plugin is also the ``Tracer`` passed to ``Runtime``. It owns the exporter and shuts down
    its SDK provider when the Orbit application stops. It does not mutate OpenTelemetry's global
    provider or install automatic framework instrumentation. W3C HTTP propagation is opt-in via
    :meth:`wrap_asgi` and :meth:`inject_context`.
    """

    metadata = PluginMetadata(
        name="orbit-tracing",
        version="0.1.0a1",
        capabilities=frozenset({"tracing.export"}),
    )

    def __init__(self, config: OpenTelemetryConfig, *, exporter: SpanExporter) -> None:
        """Create a bounded batch processor around an application-owned exporter.

        The exporter is transferred to this plugin's provider lifecycle and is shut down with it.
        Install the optional OTLP extra when using the OpenTelemetry HTTP or gRPC exporters.
        """
        if not isinstance(config, OpenTelemetryConfig):
            raise TypeError("OpenTelemetryPlugin requires an OpenTelemetryConfig instance.")
        if not isinstance(exporter, SpanExporter):
            raise TypeError("exporter must implement OpenTelemetry's SpanExporter contract.")
        self.config = config
        self._provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: config.service_name}),
            shutdown_on_exit=False,
            span_limits=SpanLimits(
                max_attributes=_MAX_SPAN_ATTRIBUTES,
                max_attribute_length=_MAX_ATTRIBUTE_LENGTH,
            ),
        )
        self._provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=config.max_queue_size,
                schedule_delay_millis=config.schedule_delay_millis,
                export_timeout_millis=config.export_timeout_millis,
                max_export_batch_size=config.max_export_batch_size,
            )
        )
        self._tracer = self._provider.get_tracer("orbit-tracing", "0.1.0a1")
        self._propagator = TraceContextTextMapPropagator()
        self._closing = False
        self._shutdown_task: asyncio.Task[None] | None = None

    @property
    def tracer(self) -> Tracer:
        """Return this plugin through Core's provider-neutral tracer protocol."""
        return cast(Tracer, self)

    def start_span(self, name: str) -> AbstractAsyncContextManager[Span]:
        """Create a Core-compatible async span scope with OpenTelemetry context propagation."""
        if self._closing:
            raise RuntimeError("OpenTelemetry tracing plugin is shutting down.")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("Span names must be bounded printable strings.")
        return _SpanScope(self._tracer, name)

    def wrap_asgi(self, app: Any) -> Any:
        """Wrap an ASGI callable to extract one bounded W3C ``traceparent`` per HTTP request.

        This wrapper must sit outside the Core ``Runtime`` so extracted context becomes the
        parent of Core's request span. It forwards lifespan and non-HTTP scopes unchanged.
        Duplicate or malformed propagation headers are ignored rather than trusted.
        """
        if not callable(app):
            raise TypeError("The ASGI application must be callable.")
        return _TraceContextASGI(app, self._propagator)

    def inject_context(self, carrier: MutableMapping[str, str]) -> None:
        """Inject the current W3C trace context into an outbound text carrier.

        Call this when constructing an outbound request's headers. Orbit does not install or
        configure an HTTP client, so transport and header ownership remain with the application.
        """
        if not isinstance(carrier, MutableMapping):
            raise TypeError("Trace propagation carrier must be a mutable string mapping.")
        self._propagator.inject(carrier)

    async def activate(self) -> None:
        """Ensure the provider has not already been shut down before application startup."""
        if self._closing:
            raise RuntimeError("A stopped OpenTelemetry tracing plugin cannot be reactivated.")

    async def deactivate(self) -> None:
        """Flush and shut down the SDK off-loop, retaining the task across cancellation."""
        self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(asyncio.to_thread(self._provider.shutdown))
        task = self._shutdown_task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise


class _SpanScope(AbstractAsyncContextManager[Span]):
    """Bridge OpenTelemetry's synchronous context manager into Core's async span protocol."""

    def __init__(self, tracer: otel_trace.Tracer, name: str) -> None:
        """Prepare an OTel current-span scope without entering it yet."""
        self._scope = tracer.start_as_current_span(
            name,
            record_exception=False,
            set_status_on_exception=True,
        )
        self._span: OTelSpan | None = None
        self._trace_token: Any = None
        self._span_token: Any = None

    async def __aenter__(self) -> Span:
        """Enter the SDK span and bind its IDs into Orbit's task-local context."""
        span = self._scope.__enter__()
        context = span.get_span_context()
        if not context.is_valid:
            self._scope.__exit__(None, None, None)
            raise RuntimeError("OpenTelemetry returned an invalid span context.")
        self._span = span
        self._trace_token = bind_trace_id(f"{context.trace_id:032x}")
        self._span_token = bind_span_id(f"{context.span_id:016x}")
        return _SpanAdapter(span)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        """Restore Orbit context and end the OpenTelemetry span with the original outcome."""
        if self._span_token is not None:
            reset_span_id(self._span_token)
            self._span_token = None
        if self._trace_token is not None:
            reset_trace_id(self._trace_token)
            self._trace_token = None
        return self._scope.__exit__(exc_type, exc, tb)


class _SpanAdapter:
    """Validate Core span mutations before passing them to the OpenTelemetry SDK."""

    def __init__(self, span: OTelSpan) -> None:
        """Bind one SDK span for the lifetime of its Core context manager."""
        self._span = span
        self._attributes: set[str] = set()

    @property
    def trace_id(self) -> str:
        """Return the active OpenTelemetry trace ID as fixed-width lowercase hex."""
        return f"{self._span.get_span_context().trace_id:032x}"

    @property
    def span_id(self) -> str:
        """Return the active OpenTelemetry span ID as fixed-width lowercase hex."""
        return f"{self._span.get_span_context().span_id:016x}"

    def set_attribute(self, name: str, value: Any) -> None:
        """Set one bounded OpenTelemetry-compatible attribute on the active span."""
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("Span attribute names must be bounded printable strings.")
        if name not in self._attributes and len(self._attributes) >= _MAX_SPAN_ATTRIBUTES:
            raise ValueError("Span attributes exceed the configured cardinality limit.")
        normalized = _attribute_value(value)
        self._span.set_attribute(name, normalized)
        self._attributes.add(name)

    def set_status(self, status: str) -> None:
        """Map Orbit's status vocabulary to the OpenTelemetry status enum."""
        status_codes = {
            "unset": StatusCode.UNSET,
            "ok": StatusCode.OK,
            "error": StatusCode.ERROR,
        }
        if not isinstance(status, str) or status not in status_codes:
            raise ValueError("Span status must be 'unset', 'ok', or 'error'.")
        self._span.set_status(Status(status_codes[status]))


class _TraceContextASGI:
    """Extract W3C trace context before delegating an ASGI request to Orbit Core."""

    def __init__(self, app: Any, propagator: TraceContextTextMapPropagator) -> None:
        """Retain the wrapped ASGI application and this plugin's local propagator."""
        self._app = app
        self._propagator = propagator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach valid remote context only around HTTP handling, then always detach it."""
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        carrier = _trace_carrier(scope)
        if carrier is None:
            await self._app(scope, receive, send)
            return
        extracted = self._propagator.extract(carrier=carrier, context=Context())
        token = attach(extracted)
        try:
            await self._app(scope, receive, send)
        finally:
            detach(token)


def _trace_carrier(scope: Scope) -> dict[str, str] | None:
    """Read only bounded, unique W3C request headers from a conventional ASGI scope."""
    headers = scope.get("headers", ())
    if not isinstance(headers, (tuple, list)) or len(headers) > _MAX_ASGI_HEADERS:
        return {}
    values: dict[bytes, list[bytes]] = {b"traceparent": [], b"tracestate": []}
    for header in headers:
        if (
            not isinstance(header, (tuple, list))
            or len(header) != 2
            or not isinstance(header[0], bytes)
            or not isinstance(header[1], bytes)
        ):
            return {}
        name = header[0].lower()
        if name in values:
            collected = values[name]
            if len(collected) == 1 or len(header[1]) > _MAX_TRACE_HEADER_BYTES:
                return {}
            collected.append(header[1])
    if not values[b"traceparent"]:
        return None
    carrier: dict[str, str] = {}
    for name, entries in values.items():
        if not entries:
            continue
        try:
            carrier[name.decode("ascii")] = entries[0].decode("ascii")
        except UnicodeDecodeError:
            return {}
    return carrier


def _attribute_value(value: Any) -> bool | int | float | str | Sequence[bool | int | float | str]:
    """Accept only bounded scalar or homogeneous sequence values supported by OTel."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Span float attributes must be finite.")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_ATTRIBUTE_LENGTH or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Span string attributes must be bounded printable text.")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_SPAN_ATTRIBUTES:
            raise ValueError("Span sequence attributes exceed the configured size limit.")
        items = tuple(_attribute_value(item) for item in value)
        if any(isinstance(item, (list, tuple)) for item in items):
            raise TypeError("Span sequence attributes cannot contain nested sequences.")
        if items and any(type(item) is not type(items[0]) for item in items):
            raise TypeError("Span sequence attributes must be homogeneous primitive values.")
        return cast(Sequence[bool | int | float | str], items)
    raise TypeError("Span attributes must use a supported scalar or homogeneous sequence type.")


__all__ = ["OpenTelemetryConfig", "OpenTelemetryPlugin"]
