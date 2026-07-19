import pytest
from pydantic import ValidationError

from reporting.schema.reporting_config import ReportingConfig, Workflow


def test_workflow_references_output_from_earlier_stage():
    workflow = Workflow.model_validate(
        {
            "name": "Notify",
            "stages": [
                {
                    "activities": [
                        {
                            "type": "query",
                            "output": "findings",
                            "parameters": {"cypher": "RETURN 1"},
                        }
                    ]
                },
                {
                    "activities": [
                        {
                            "type": "log",
                            "input": "findings",
                            "output": "notification",
                        }
                    ]
                },
            ],
        }
    )

    assert workflow.stages[1].activities[0].input == "findings"


def test_workflow_rejects_same_stage_input_reference():
    with pytest.raises(ValidationError, match="earlier stage"):
        Workflow.model_validate(
            {
                "name": "Notify",
                "stages": [
                    {
                        "activities": [
                            {"type": "query", "output": "findings"},
                            {"type": "log", "input": "findings", "output": "notification"},
                        ]
                    }
                ],
            }
        )


def test_config_rejects_canonical_and_legacy_sections_together():
    with pytest.raises(ValidationError, match="cannot both"):
        ReportingConfig.model_validate(
            {
                "workflows": [
                    {
                        "name": "Canonical",
                        "stages": [{"activities": [{"type": "query", "output": "query"}]}],
                    }
                ],
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
