# Orbit Tracing: development

This repository adapts Core tracing interfaces to the OpenTelemetry SDK. Keep OpenTelemetry's
provider local to this plugin and preserve Core's public `Tracer`/`Span` boundary.

## Local setup and checks

From the repository root:

```bash
python -m pip install -e ../orbit-core
python -m pip install -e ../orbit-testing
python -m pip install -e '.[dev,otlp-http]' build
pytest
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

The test suite uses an in-memory OpenTelemetry exporter and Orbit's test client. It does not
require an OTLP collector, remote exporter credentials, or live ingress.

## Change guidance

- Keep one isolated `TracerProvider`; do not install a process-global provider implicitly.
- Maintain lifecycle ownership of the exporter and test shutdown after normal use and cancellation.
- Keep attribute validation aligned with the Core span protocol and OpenTelemetry primitive types;
  test signed integer limits, non-finite floats, homogeneous arrays, and size bounds.
- Validate propagation header count/size and restore attached context in `finally` blocks.
- Do not add exception messages, request bodies, credentials, or arbitrary Core data to spans.
- Update README examples and architecture, operations, and security guidance when changing limits,
  provider behavior, or propagation.

Follow the repository contribution guide, governance, code of conduct, and security policy. Keep
tests deterministic and report the exact OpenTelemetry SDK/exporter versions validated.
