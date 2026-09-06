"""The checked-in API contract must describe the application FastAPI builds."""

import json
from pathlib import Path

import pytest

from reporting import settings
from reporting.app import create_app


def test_checked_in_openapi_schema_is_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_SCHEDULES_ENABLED", True)
    expected = json.loads(Path("schema/openapi.json").read_text())

    assert create_app().openapi() == expected, "run `make generate_openapi` and regenerate the frontend API types"
