"""Write-time validation of report configs (CreateVersionRequest)."""

import pytest
from pydantic import ValidationError

from reporting.schema.report_config import CreateVersionRequest


def _config(**overrides):
    base = {
        "name": "Test Report",
        "queries": {},
        "rows": [{"name": "r", "panels": [{"type": "markdown", "w": 12, "markdown": "## Findings"}]}],
    }
    base.update(overrides)
    return base


def test_valid_config_accepted():
    CreateVersionRequest(config=_config())


def test_queries_as_list_rejected():
    with pytest.raises(ValidationError):
        CreateVersionRequest(config=_config(queries=[]))


def test_markdown_panel_without_content_rejected():
    """The live bug: content under config.content instead of the markdown field."""
    rows = [{"name": "r", "panels": [{"type": "markdown", "w": 12, "config": {"content": "## Findings"}}]}]
    with pytest.raises(ValidationError, match="markdown"):
        CreateVersionRequest(config=_config(rows=rows))


def test_missing_name_rejected():
    config = _config()
    del config["name"]
    with pytest.raises(ValidationError):
        CreateVersionRequest(config=config)
