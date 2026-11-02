# Orbit Tracing: operations

`orbit-tracing` has no endpoint or exporter defaults. The application creates the exporter, passes
it to `OpenTelemetryPlugin`, registers the plugin, and uses the plugin as the tracer supplied to
Core `Runtime`. Install `orbit-tracing[otlp-http]` or `[otlp-grpc]` only when using those exporters;
other `SpanExporter` implementations can be supplied by the application.

## Lifecycle and ASGI order

The plugin owns the exporter after construction and shuts down its provider during the application
plugin lifecycle. Provider shutdown runs in a worker thread so exporter flushing does not block the
event loop. Do not independently shut down the transferred exporter or provider from another hook.

If using W3C inbound extraction, wrap the Core runtime with `plugin.wrap_asgi(runtime)`. The
propagation wrapper must be outside Core so remote context is attached before request spans begin.
It passes lifespan and non-HTTP scopes through. For outbound calls, invoke `inject_context(headers)`
inside the active span and use an application-managed client.

## Configuration and capacity

`OpenTelemetryConfig` defaults to a queue of 2,048 spans, export batches of 512, a 5-second
scheduled delay, and a 30-second export timeout. Queue size is limited to 100,000; batch size must
not exceed queue size. Choose queue and batch settings based on request volume, acceptable memory
use, exporter throughput, and shutdown budget. These settings do not guarantee that spans reach a
collector.

The adapter sets up to 128 attributes per span, 4,096 characters per string, and at most 128
sequence elements. Monitor exporter failures, queue drops, export latency, and shutdown duration
using the selected SDK/exporter diagnostics. Sampling is not customized by this package.

## Failure modes and deployment

Use verified TLS and authenticated exporter endpoints when required by the deployment. Configure
collector availability, retry behavior, sampling, and data retention in the exporter/collector
stack. Traces are diagnostic telemetry and should not be used as the durable record of business
events.

Validate startup/shutdown ordering, exporter flush on normal stop, interruption during export,
network outage behavior, queue saturation, and W3C propagation through the real ingress/proxy path
in a representative non-production environment. Unit tests use an in-memory exporter and do not
validate collector delivery or deployment topology.
