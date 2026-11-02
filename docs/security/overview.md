# Orbit Tracing: security

Trace data leaves the process through the configured exporter and may be retained by collectors,
backends, and third-party observability services. Attribute validation limits type and size; it
does not determine whether a value is safe to export.

## Sensitive attributes

Never attach passwords, access tokens, cookies, authorization headers, encryption keys, request or
response bodies, or unnecessary personal/regulated data. Prefer bounded, low-cardinality
operational attributes. Avoid using user IDs, request IDs, email addresses, or raw URLs as metric
labels or span attributes unless their collection and retention are approved. Span names and
attribute names should not include caller-supplied secrets.

The adapter deliberately does not copy exception messages or exception objects onto spans. Values
you explicitly pass to `set_attribute` are still exported. String/control/sequence/numeric limits
prevent some malformed or unbounded data but are not redaction.

## Propagation trust

W3C headers are untrusted input. Inbound extraction is opt-in and ignores duplicate or malformed
header shapes. A valid remote trace ID is correlation context, not an authenticated identity. Do
not use trace headers for authorization or tenant selection. Configure ingress limits and TLS at the
network boundary.

Outbound injection writes trace context into the supplied carrier. Ensure the destination is
permitted to receive that correlation data and do not reuse a carrier containing credentials or
unrelated headers across trust boundaries.

## Exporter and collector

Protect exporter endpoints with verified TLS and suitable authentication. Keep endpoint credentials
out of logs and configuration examples. Apply least-privilege access, retention, deletion, and
region controls in the collector/backend. Review the OpenTelemetry SDK/exporter diagnostics for
whether failed exports can expose endpoint details.

Follow the repository [security policy](../../SECURITY.md) for private vulnerability reporting.
This page describes the adapter boundary and is not a collector or deployment security audit.
