# Orbit Tracing: architecture and boundaries

## Responsibility

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

## Declared dependencies

The following dependency declarations come from the checked-in manifests. Optional groups and development dependencies are called out separately.

### `pyproject.toml`
- `orbit-core>=0.1.0a1,<0.2`
- `opentelemetry-api>=1.45,<2`
- `opentelemetry-sdk>=1.45,<2`
- Optional `otlp-http` group: `opentelemetry-exporter-otlp-proto-http>=1.45,<2`.
- Optional `otlp-grpc` group: `opentelemetry-exporter-otlp-proto-grpc>=1.45,<2`.
- Optional `dev` group: `pytest>=8,<10`, `pytest-asyncio>=0.24,<2`, `ruff>=0.8,<1`, `mypy>=1.13,<2`, `orbit-testing>=0.1.0a1,<0.2`.

Declared dependencies do not mean that optional providers or services are bundled with this package.

## Implementation layout

Representative implementation files in this checkout:

- `src/orbit_tracing/__init__.py`
- `src/orbit_tracing/config.py`
- `src/orbit_tracing/plugin.py`

## Public contract and scope

## Status

This package is pre-alpha; its API is not stable and it is not yet published. It supports Python
3.11 through 3.14. Licensed under Apache-2.0.

## Boundary rules

Keep provider SDKs, credentials, transports, and provider-specific error translation in provider adapters. Keep reusable capability contracts in the matching capability package and lifecycle orchestration in Core. Apply the relevant layer for this repository and preserve the dependency direction shown above.
