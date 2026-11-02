# Orbit Tracing: architecture and contracts

## Package boundary

`orbit-tracing` adapts Orbit Core's provider-neutral `Tracer` and `Span` interfaces to one isolated
OpenTelemetry SDK `TracerProvider`. Core retains its bounded in-memory tracer and does not depend
on OpenTelemetry. This adapter does not set OpenTelemetry's process-global provider, install
automatic instrumentation, or choose an exporter backend.

```text
Orbit Runtime ── Core Tracer/Span ── OpenTelemetryPlugin ── SDK TracerProvider
                                                           │
                                                 owned BatchSpanProcessor
                                                           │
                                                application exporter
```

The plugin owns the SDK provider and the exporter passed to it. Application lifecycle shutdown
flushes and shuts down the provider off the event loop. The application constructs an exporter
(for example with the optional OTLP HTTP or gRPC extra) and registers the plugin. No network
exporter is installed by default.

## Span mapping and bounds

Core span names map to OpenTelemetry spans. IDs exposed by the Core adapter are fixed-width
lowercase hexadecimal. Orbit's task-local trace and span IDs are bound while a Core span is active
and restored at exit. Core statuses map to the OpenTelemetry status enum; unsupported values fail
at the adapter boundary.

Span attribute names are limited to 255 characters; each span accepts at most 128 distinct
attributes. String values are limited to 4,096 characters and control-free. Sequence values have
at most 128 homogeneous primitive entries; nested arrays are rejected. Booleans are kept distinct
from integers, floats must be finite, and integers must fit the signed 64-bit OpenTelemetry
attribute type. The [OpenTelemetry common specification](https://opentelemetry.io/docs/specs/otel/common/)
defines attribute primitive types and collection limits. The SDK provider also applies span limits.

Exception objects and messages are not copied to spans. Exceptions still propagate through the
application normally; status is set by the span context manager according to its configured
exception behavior.

## Context propagation

Inbound W3C Trace Context extraction is opt-in through `wrap_asgi()`. Place the wrapper outside the
Core `Runtime` so remote context is attached before Core creates its request span. Only HTTP scopes
are parsed; lifespan and other scopes pass through directly. The adapter inspects at most 1,000
header entries and accepts at most one `traceparent` and one `tracestate`, each bounded to 512
bytes. Duplicate, malformed-shape, or non-ASCII fields are ignored. Invalid trace semantics are
handled by the OpenTelemetry propagator.

Outbound propagation is explicit. Call `inject_context()` into the caller-owned request-header
mapping while the span is active. This package does not create or instrument HTTP, database, or
messaging clients.

## Sampling, queueing, and export

The SDK uses a bounded batch processor. Queue size, scheduled export delay, export timeout, and
batch size are configured by `OpenTelemetryConfig`; the batch cannot exceed the queue. Queue
overflow behavior and sampling are OpenTelemetry SDK concerns and should be configured and
monitored according to the selected exporter/backend. This package does not add a custom sampling
policy, persistent queue, retry store, collector, metrics, logs, or dashboard.

## Source layout and status

- `src/orbit_tracing/config.py`: strict bounded provider and batch settings.
- `src/orbit_tracing/plugin.py`: isolated provider, Core span adapter, ASGI extraction, and
  explicit outbound injection.
- `src/orbit_tracing/__init__.py`: public package exports.

The distribution declares Python `>=3.11,<3.15`, Orbit Core `>=0.1.0a1,<0.2`, and OpenTelemetry
API/SDK `>=1.45,<2`; exporters are optional extras. The package is alpha.
