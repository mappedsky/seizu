"""OpenTelemetry tracing for chat runs, off unless an endpoint is configured.

A turn is spread across at least three processes -- the web service admits it,
one worker activity drives it, and its plan steps run as further activities that
may land on other replicas (AGT-018) -- so the question "where did this turn
spend its twenty minutes" has no answer inside any one of them. Spans exported
with propagated context put the turn, its batches, its steps and every model
call into one tree.

Diagnosis only. Budget decisions read the ledger (`chat_budget`), never this: a
run must spend the same whether or not an exporter is reachable. Rationale:
AGT-026.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from reporting import settings

logger = logging.getLogger(__name__)

#: Set once by :func:`configure`, then read by every span helper.
_tracer: Any | None = None
_configured = False

#: Attribute prefix for everything Seizu adds, so a backend can tell our
#: attributes from the semantic-convention ones.
_NS = "seizu"


def configure() -> None:
    """Install the tracer provider, if tracing is switched on and importable.

    Idempotent and never fatal: a deployment that cannot reach its collector
    should lose its traces, not its chat.
    """
    global _tracer, _configured
    if _configured:
        return
    _configured = True
    if not settings.TELEMETRY_ENABLED or not settings.TELEMETRY_OTLP_ENDPOINT.strip():
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.TELEMETRY_SERVICE_NAME,
                    "deployment.environment.name": settings.SEIZU_DEPLOYMENT_ID or "dev",
                }
            )
        )
        headers = _parse_headers(settings.TELEMETRY_OTLP_HEADERS)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.TELEMETRY_OTLP_ENDPOINT, headers=headers or None))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("seizu.chat")
        logger.info("Tracing enabled, exporting to %s", settings.TELEMETRY_OTLP_ENDPOINT)
    except Exception:
        # An unimportable or misconfigured exporter must not take the process
        # with it; the run is the product, the trace is not.
        logger.warning("Tracing could not be configured; continuing without it", exc_info=True)
        _tracer = None


def _parse_headers(raw: str) -> dict[str, str]:
    """``k=v,k2=v2`` into a dict, the form OTLP endpoints take API keys in."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        if key.strip() and value.strip():
            headers[key.strip()] = value.strip()
    return headers


def enabled() -> bool:
    return _tracer is not None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """A span, or a no-op context manager when tracing is off.

    Every call site pays one attribute lookup when tracing is disabled, which is
    what lets these sit on hot paths without a guard at each one.
    """
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as current:
        set_attributes(current, **attributes)
        try:
            yield current
        except BaseException as exc:
            # Recorded without the message: an exception string can carry tool
            # output, and content is opt-in (AGT-026).
            current.set_attribute(f"{_NS}.error", exc.__class__.__name__)
            raise


def current_span() -> Any:
    """The span in scope, for attributes learned after it was opened."""
    if _tracer is None:
        return None
    from opentelemetry import trace

    return trace.get_current_span()


def set_attributes(current: Any, **attributes: Any) -> None:
    """Attach namespaced attributes, dropping empties so a trace stays readable."""
    if current is None:
        return
    for key, value in attributes.items():
        if value is None or value == "":
            continue
        try:
            current.set_attribute(f"{_NS}.{key}", value)
        except Exception:  # pragma: no cover - a backend rejecting one attribute
            logger.debug("could not set span attribute %s", key)


def content(text: str, limit: int = 2000) -> str:
    """Text for a span, or ``""`` unless recording content is switched on.

    Off by default and deliberately: a trace of this system carries graph rows,
    tool output and the user's own words, and exporting it is data leaving the
    deployment. An operator opting in is a different decision from an operator
    wanting timings (AGT-026).
    """
    if not settings.TELEMETRY_RECORD_CONTENT or not text:
        return ""
    return text[:limit]


def temporal_interceptors() -> list[Any]:
    """The Temporal interceptor that carries trace context between processes.

    Without it a turn's spans and its steps' spans are unrelated trees, and the
    one view worth having -- the whole turn -- cannot be assembled.
    """
    if not enabled():
        return []
    try:
        from temporalio.contrib.opentelemetry import TracingInterceptor

        return [TracingInterceptor()]
    except Exception:
        logger.warning("Temporal tracing interceptor unavailable", exc_info=True)
        return []
