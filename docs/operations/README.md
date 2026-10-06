# Orbit Tracing: operations and security

This guide organizes runtime behavior documented by the package. It does not certify production readiness. Verify provider/client versions, permissions, transport security, limits, and failure behavior in the target environment before release.

## Configuration surface

Environment names found in the package README:

The package README does not name `ORBIT_*` variables. Use its typed constructors and application configuration, and confirm exact runtime inputs in the implementation before deployment.

Use the package README's constructor and deployment examples. Store credentials in a secret manager and avoid logging credentials, raw provider errors, request data, or opaque cursors.

## Lifecycle, failure behavior, and limits

`orbit-tracing` adapts Orbit Core's provider-neutral `Tracer` contract to an isolated
OpenTelemetry SDK provider. The package is optional: Core keeps its bounded `InMemoryTracer` and
does not depend on OpenTelemetry.

Install the adapter and, for OTLP over HTTP, its optional exporter extra:

```bash
pip install orbit-core 'orbit-tracing[otlp-http]'
```

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from orbit import Application, ApplicationConfig
from orbit.runtime import Runtime
from orbit_tracing import OpenTelemetryConfig, OpenTelemetryPlugin

application = Application(ApplicationConfig(name="orders"))
tracing = OpenTelemetryPlugin(
    OpenTelemetryConfig(service_name="orders-api"),
    exporter=OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces"),
)
application.register_plugin(tracing)
runtime = Runtime(application, tracer=tracing)
# Wrap outside Runtime so remote context is active before Core starts its request span.
runtime = tracing.wrap_asgi(runtime)
```

The configured exporter is owned by the plugin's SDK provider. Orbit's application lifecycle shuts
the provider down off the event loop, flushing its batch processor. The adapter never changes
OpenTelemetry's process-global provider, so applications can compose its provider explicitly.
Span names, identifiers, statuses, and attributes are translated to the Core `Tracer`/`Span`
contract; attributes are type-checked and bounded. Exception objects and messages are deliberately
not copied into spans. Use `orbit-tracing[otlp-grpc]` for the optional OTLP gRPC exporter, or pass
another OpenTelemetry `SpanExporter` from the application.

Inbound W3C Trace Context extraction is opt-in: `wrap_asgi()` reads one bounded, unique
`traceparent` (and optional `tracestate`) from HTTP scopes and attaches valid context while Core
handles the request. Invalid or duplicate propagation headers are ignored. The wrapper must be
outside `Runtime`; lifespan and non-HTTP ASGI scopes pass through unchanged. For an outbound request,
call `tracing.inject_context(headers)` while a span is active, then let the application-owned client
send those headers. This package does not install automatic HTTP instrumentation or choose a client.
It does not provide metrics, logs, a sampling policy, or an observability dashboard.

OpenTelemetry's [Python instrumentation guide](https://opentelemetry.io/docs/languages/python/instrumentation/)
describes tracer-provider and span setup. Its [exporter guide](https://opentelemetry.io/docs/languages/python/exporters/)
covers OTLP HTTP/gRPC exporters and batch processors. No collector or live backend is required by
the unit tests, and none was exercised for this package.

## Status

This package is pre-alpha; its API is not stable and it is not yet published. It supports Python
3.11 through 3.14. Licensed under Apache-2.0.

## Production validation

Validate startup/shutdown cleanup, timeout and cancellation behavior, concurrency and payload bounds where applicable, secret rotation and least-privilege access, data durability, backup/restore, and failover against the selected provider. Do not infer distributed or durable guarantees from an in-process API or fake-client tests.
