import pytest
from pydantic import ValidationError

from reporting.schema.reporting_config import ReportingConfig, Workflow


def test_workflow_references_named_input():
    workflow = Workflow.model_validate(
        {
            "name": "Notify",
            "inputs": {"findings": {"type": "query", "cypher": "RETURN 1"}},
            "activities": [{"type": "log", "input": "findings", "parameters": {}}],
        }
    )

    assert workflow.activities[0].input == "findings"


def test_workflow_rejects_unknown_input_reference():
    with pytest.raises(ValidationError, match="unknown input"):
        Workflow.model_validate(
            {
                "name": "Notify",
                "inputs": {"findings": {"type": "query", "cypher": "RETURN 1"}},
                "activities": [{"type": "log", "input": "missing", "parameters": {}}],
            }
        )


def test_config_rejects_canonical_and_legacy_sections_together():
    with pytest.raises(ValidationError, match="cannot both"):
        ReportingConfig.model_validate(
            {
                "workflows": [{"name": "Canonical"}],
                "scheduled_queries": [
                    {
                        "name": "Legacy",
                        "cypher": "RETURN 1",
                        "frequency": 60,
                        "actions": [],
                    }
                ],
            }
        )
