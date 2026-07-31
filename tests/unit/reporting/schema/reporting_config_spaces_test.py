"""Cross-reference validation between the ``spaces`` and ``reports`` sections.

These are checked on load, before any writes, so a typo fails the whole seed
rather than half of it.
"""

import pytest
from pydantic import ValidationError

from reporting.schema.reporting_config import Report, ReportingConfig

_VALID = {
    "spaces": {
        "security": {
            "name": "Security",
            "overview": "sec_overview",
            "subspaces": {"vulns": {"name": "Vulnerabilities"}},
        }
    },
    "reports": {
        "sec_overview": {"name": "Sec Overview", "space": "security"},
        "findings": {"name": "Findings", "space": "security", "subspace": "vulns"},
    },
}


def test_valid_space_references_load():
    config = ReportingConfig.model_validate(_VALID)

    assert config.spaces["security"].subspaces["vulns"].name == "Vulnerabilities"
    assert config.reports["findings"].subspace == "vulns"


def test_subspace_requires_a_space():
    with pytest.raises(ValidationError, match="subspace requires space"):
        Report.model_validate({"name": "R", "subspace": "vulns"})


def test_report_naming_an_unknown_space_is_rejected():
    config = {**_VALID, "reports": {"findings": {"name": "Findings", "space": "nope"}}}
    with pytest.raises(ValidationError, match="unknown space 'nope'"):
        ReportingConfig.model_validate(config)


def test_report_naming_a_subspace_of_another_space_is_rejected():
    config = {
        "spaces": {
            "security": {"name": "Security", "subspaces": {"vulns": {"name": "Vulnerabilities"}}},
            "identity": {"name": "Identity"},
        },
        "reports": {"findings": {"name": "Findings", "space": "identity", "subspace": "vulns"}},
    }
    with pytest.raises(ValidationError, match="not defined in space 'identity'"):
        ReportingConfig.model_validate(config)


def test_overview_must_name_a_known_report():
    config = {
        "spaces": {"security": {"name": "Security", "overview": "ghost"}},
        "reports": {},
    }
    with pytest.raises(ValidationError, match="unknown overview report 'ghost'"):
        ReportingConfig.model_validate(config)


def test_overview_must_be_filed_in_its_own_space():
    """Mirrors the API rule enforced by resolve_overview_report."""
    config = {
        "spaces": {"security": {"name": "Security", "overview": "elsewhere"}},
        "reports": {"elsewhere": {"name": "Elsewhere"}},
    }
    with pytest.raises(ValidationError, match="not filed in that space"):
        ReportingConfig.model_validate(config)


@pytest.mark.parametrize("key", ["Security", "sec-urity", "_security", "security__x"])
def test_space_keys_must_be_lower_snake_case(key):
    with pytest.raises(ValidationError, match="lower_snake_case"):
        ReportingConfig.model_validate({"spaces": {key: {"name": "S"}}})


@pytest.mark.parametrize("key", ["Vulns", "vuln-s", "_vulns"])
def test_subspace_keys_must_be_lower_snake_case(key):
    with pytest.raises(ValidationError, match="lower_snake_case"):
        ReportingConfig.model_validate({"spaces": {"security": {"name": "S", "subspaces": {key: {"name": "V"}}}}})


def test_space_membership_is_absent_from_a_report_config_dump():
    """The seeder strips these before saving a version.

    Membership is unversioned parent metadata: a version carrying it would
    relocate the report when restored.
    """
    report = Report.model_validate({"name": "R", "space": "security", "pinned": True})

    dumped = report.model_dump(exclude_none=True, exclude={"pinned", "space", "subspace"})

    assert dumped == {"schema_version": 1, "name": "R", "queries": {}, "inputs": [], "rows": []}
