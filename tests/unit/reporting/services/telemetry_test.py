"""Tracing must be invisible when off and never able to break a run (AGT-026)."""

from typing import Any

import pytest

from reporting import settings
from reporting.services import telemetry


@pytest.fixture(autouse=True)
def _reset_tracer():
    """Tracing is process-global, so a test that enables it must not leak."""
    saved = (telemetry._tracer, telemetry._configured)
    yield
    telemetry._tracer, telemetry._configured = saved


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def __enter__(self) -> "_Span":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _Span]] = []

    def start_as_current_span(self, name: str) -> _Span:
        span = _Span()
        self.spans.append((name, span))
        return span


def _enable(mocker) -> _Tracer:
    tracer = _Tracer()
    mocker.patch.object(telemetry, "_tracer", tracer)
    return tracer


def test_a_span_is_a_no_op_when_tracing_is_off(mocker):
    mocker.patch.object(telemetry, "_tracer", None)

    with telemetry.span("chat step", step_id="s1") as current:
        assert current is None
    assert telemetry.enabled() is False
    # Attributes on nothing are also a no-op, so call sites need no guard.
    telemetry.set_attributes(None, step_id="s1")


def test_attributes_are_namespaced_and_empties_dropped(mocker):
    tracer = _enable(mocker)

    with telemetry.span("chat step", step_id="s1", map_item="", tool_calls=0, missing=None):
        pass

    _name, span = tracer.spans[0]
    assert span.attributes == {"seizu.step_id": "s1", "seizu.tool_calls": 0}


def test_an_exception_is_recorded_by_type_and_re_raised(mocker):
    """The message can carry tool output, and content is opt-in."""
    tracer = _enable(mocker)

    with pytest.raises(ValueError):
        with telemetry.span("chat step"):
            raise ValueError("CVE-2024-3094 found in acme/private-repo")

    _name, span = tracer.spans[0]
    assert span.attributes["seizu.error"] == "ValueError"
    assert "acme/private-repo" not in str(span.attributes)


def test_content_is_withheld_unless_it_is_asked_for(mocker):
    mocker.patch.object(settings, "TELEMETRY_RECORD_CONTENT", False)
    assert telemetry.content("the user's question") == ""

    mocker.patch.object(settings, "TELEMETRY_RECORD_CONTENT", True)
    mocker.patch.object(settings, "TELEMETRY_CONTENT_MAX_CHARS", 10)
    assert telemetry.content("the user's question") == "the user's"
    assert telemetry.content("x" * 5000) == "x" * 10


def test_prompts_have_a_separate_opt_in(mocker):
    mocker.patch.object(settings, "TELEMETRY_CONTENT_MAX_CHARS", 20)
    mocker.patch.object(settings, "TELEMETRY_RECORD_CONTENT", True)
    mocker.patch.object(settings, "TELEMETRY_RECORD_PROMPTS", False)
    assert telemetry.prompt("system prompt") == ""

    mocker.patch.object(settings, "TELEMETRY_RECORD_PROMPTS", True)
    assert telemetry.prompt("system prompt") == "system prompt"


def test_skill_scope_is_inherited_by_spans_and_restored(mocker):
    tracer = _enable(mocker)

    with telemetry.skill_scope(skill_id="review", skill_name="Review", skill_version=3):
        with telemetry.span("chat step"):
            pass
    with telemetry.span("unrelated"):
        pass

    assert tracer.spans[0][1].attributes == {
        "seizu.skill_id": "review",
        "seizu.skill_name": "Review",
        "seizu.skill_version": 3,
    }
    assert tracer.spans[1][1].attributes == {}


def test_configure_does_nothing_without_an_endpoint(mocker):
    mocker.patch.object(settings, "TELEMETRY_ENABLED", True)
    mocker.patch.object(settings, "TELEMETRY_OTLP_ENDPOINT", "  ")
    mocker.patch.object(telemetry, "_configured", False)
    mocker.patch.object(telemetry, "_tracer", None)

    telemetry.configure()

    assert telemetry.enabled() is False


def test_a_broken_exporter_leaves_the_run_alone(mocker):
    """The run is the product; the trace is not."""
    mocker.patch.object(settings, "TELEMETRY_ENABLED", True)
    mocker.patch.object(settings, "TELEMETRY_OTLP_ENDPOINT", "http://collector.invalid/v1/traces")
    mocker.patch.object(telemetry, "_configured", False)
    mocker.patch.object(telemetry, "_tracer", None)
    mocker.patch("opentelemetry.sdk.trace.TracerProvider", side_effect=RuntimeError("no exporter for you"))

    telemetry.configure()  # must not raise

    assert telemetry.enabled() is False


def test_headers_parse_into_the_form_an_otlp_endpoint_takes():
    assert telemetry._parse_headers("x-api-key=abc123, x-tenant = t1 ") == {"x-api-key": "abc123", "x-tenant": "t1"}
    assert telemetry._parse_headers("") == {}
    assert telemetry._parse_headers("malformed") == {}


def test_no_temporal_interceptor_while_tracing_is_off(mocker):
    """Without tracing the interceptor is pure overhead on every workflow call."""
    mocker.patch.object(telemetry, "_tracer", None)

    assert telemetry.temporal_interceptors() == []


def test_the_temporal_interceptor_is_installed_when_tracing_is_on(mocker):
    _enable(mocker)

    interceptors = telemetry.temporal_interceptors()

    assert len(interceptors) == 1
    assert type(interceptors[0]).__name__ == "TracingInterceptor"
