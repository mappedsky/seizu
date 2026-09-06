"""The checked-in API contract must describe the application FastAPI builds."""

import json
from pathlib import Path

from reporting.app import create_app


def test_checked_in_openapi_schema_is_current() -> None:
    expected = json.loads(Path("schema/openapi.json").read_text())

    assert create_app().openapi() == expected, "run `make generate_openapi` and regenerate the frontend API types"
