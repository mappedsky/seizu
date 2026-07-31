"""Tests for the spaces half of ``seizu_cli.commands.seed`` (seed + export)."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from seizu_cli.commands import seed


@pytest.fixture
def mock_client(mocker: pytest.MonkeyPatch) -> MagicMock:
    mc = MagicMock()
    mocker.patch("seizu_cli.state.get_client", return_value=mc)
    return mc


def _report_config(name: str) -> dict[str, Any]:
    return {"schema_version": 1, "name": name, "queries": {}, "inputs": [], "rows": []}


def _space_row(space_id: str = "s1", name: str = "Security") -> dict[str, Any]:
    return {
        "space_id": space_id,
        "name": name,
        "description": "d",
        "overview_report_id": None,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "updated_by": None,
    }


def _report_row(report_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "name": name,
        "current_version": 1,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "created_by": "u1",
        "updated_by": "u1",
        "access": {"scope": "public"},
        "pinned": False,
        **extra,
    }


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

_SPACE_CONFIG = """
spaces:
  security:
    name: Security
    description: d
    overview: sec_overview
    subspaces:
      vulns:
        name: Vulnerabilities
reports:
  sec_overview:
    name: Sec Overview
    space: security
    subspace: vulns
""".lstrip()


def test_seed_creates_space_subspace_membership_and_overview(
    mock_client: MagicMock,
    tmp_path: Path,
) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/reports": {"reports": []},
    }[path]
    mock_client.post.side_effect = [
        {"space_id": "s1"},
        {"subspace_id": "ss1"},
        {"report_id": "r1"},
        {},  # version save
    ]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    posts = [call.args[0] for call in mock_client.post.call_args_list]
    # Spaces first: filing a report needs its space to already exist.
    assert posts[:3] == ["/api/v1/spaces", "/api/v1/spaces/s1/subspaces", "/api/v1/reports"]

    puts = {call.args[0]: call.kwargs.get("json") for call in mock_client.put.call_args_list}
    assert puts["/api/v1/reports/r1/space"] == {"space_id": "s1", "subspace_id": "ss1"}
    assert puts["/api/v1/spaces/s1/overview"] == {"report_id": "r1"}


def test_seed_keeps_membership_out_of_the_stored_version(mock_client: MagicMock, tmp_path: Path) -> None:
    """space/subspace are parent metadata: a version must never carry them.

    Otherwise restoring an old version would relocate the report.
    """
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/reports": {"reports": []},
    }[path]
    mock_client.post.side_effect = [
        {"space_id": "s1"},
        {"subspace_id": "ss1"},
        {"report_id": "r1"},
        {},
    ]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    version_call = next(c for c in mock_client.post.call_args_list if c.args[0] == "/api/v1/reports/r1/versions")
    stored = version_call.kwargs["json"]["config"]
    assert "space" not in stored
    assert "subspace" not in stored
    assert "pinned" not in stored


def test_seed_matches_an_existing_space_by_name(mock_client: MagicMock, tmp_path: Path) -> None:
    """Space ids are server-generated, so the YAML key is a local handle only."""
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/subspaces": {
            "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}]
        },
        # The space already existed, so its overview pointer is read before
        # being written.
        "/api/v1/spaces/s1/tree": {"space": _space_row(), "subspaces": [], "reports": []},
        "/api/v1/reports": {"reports": []},
    }[path]
    mock_client.post.side_effect = [{"report_id": "r1"}, {}]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    posts = [call.args[0] for call in mock_client.post.call_args_list]
    assert "/api/v1/spaces" not in posts
    assert "/api/v1/spaces/s1/subspaces" not in posts
    puts = {call.args[0]: call.kwargs.get("json") for call in mock_client.put.call_args_list}
    assert puts["/api/v1/reports/r1/space"] == {"space_id": "s1", "subspace_id": "ss1"}


def test_reseeding_an_unchanged_config_writes_nothing(mock_client: MagicMock, tmp_path: Path) -> None:
    """Filing and the overview pointer are metadata writes that stamp updated_by.

    Rewriting them on every seed would churn every filed report and every
    configured space for no change.
    """
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    stored = _report_config("Sec Overview")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/subspaces": {
            "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}]
        },
        "/api/v1/spaces/s1/tree": {
            "space": {**_space_row(), "overview_report_id": "r1"},
            "subspaces": [],
            "reports": [],
        },
        "/api/v1/reports": {"reports": [_report_row("r1", "Sec Overview", space_id="s1", subspace_id="ss1")]},
        "/api/v1/reports/r1": {"config": stored},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.post.assert_not_called()
    mock_client.put.assert_not_called()


def test_reseed_refiles_a_report_that_moved(mock_client: MagicMock, tmp_path: Path) -> None:
    """Only a membership that actually differs is rewritten."""
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/subspaces": {
            "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}]
        },
        "/api/v1/spaces/s1/tree": {
            "space": {**_space_row(), "overview_report_id": "r1"},
            "subspaces": [],
            "reports": [],
        },
        # Same space, but the sub-space was cleared out of band.
        "/api/v1/reports": {"reports": [_report_row("r1", "Sec Overview", space_id="s1", subspace_id=None)]},
        "/api/v1/reports/r1": {"config": _report_config("Sec Overview")},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.put.assert_called_once_with(
        "/api/v1/reports/r1/space",
        json={"space_id": "s1", "subspace_id": "ss1"},
    )


def test_reseed_resets_an_overview_pointing_elsewhere(mock_client: MagicMock, tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/subspaces": {
            "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}]
        },
        "/api/v1/spaces/s1/tree": {"space": _space_row(), "subspaces": [], "reports": []},
        "/api/v1/reports": {"reports": [_report_row("r1", "Sec Overview", space_id="s1", subspace_id="ss1")]},
        "/api/v1/reports/r1": {"config": _report_config("Sec Overview")},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.put.assert_called_once_with("/api/v1/spaces/s1/overview", json={"report_id": "r1"})


def test_force_rewrites_membership_and_overview(mock_client: MagicMock, tmp_path: Path) -> None:
    """--force means "write even if unchanged", consistently across the seeder."""
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/subspaces": {
            "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}]
        },
        "/api/v1/reports": {"reports": [_report_row("r1", "Sec Overview", space_id="s1", subspace_id="ss1")]},
        "/api/v1/reports/r1": {"config": _report_config("Sec Overview")},
    }[path]

    seed.seed_cmd(str(config), force=True, dry_run=False)

    puts = {call.args[0] for call in mock_client.put.call_args_list}
    assert "/api/v1/reports/r1/space" in puts
    assert "/api/v1/spaces/s1/overview" in puts
    # The tree read is skipped entirely: force writes without comparing.
    assert "/api/v1/spaces/s1/tree" not in {call.args[0] for call in mock_client.get.call_args_list}


def test_seed_matches_space_names_exactly(mock_client: MagicMock, tmp_path: Path) -> None:
    """A case-only difference is a different space, as the API also treats it."""
    config = tmp_path / "c.yaml"
    config.write_text("spaces:\n  security:\n    name: Security\n    description: d\n")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row(name="security")]},
        "/api/v1/reports": {"reports": []},
    }[path]
    mock_client.post.return_value = {"space_id": "s2"}

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.post.assert_called_once_with("/api/v1/spaces", json={"name": "Security", "description": "d"})
    mock_client.put.assert_not_called()


def test_seed_updates_a_space_whose_description_changed(mock_client: MagicMock, tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text("spaces:\n  security:\n    name: Security\n    description: new\n")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/reports": {"reports": []},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.put.assert_called_once_with(
        "/api/v1/spaces/s1",
        json={"name": "Security", "description": "new"},
    )


def test_seed_leaves_an_undeclared_report_in_its_space(mock_client: MagicMock, tmp_path: Path) -> None:
    """Omitting ``space`` means "not declared", not "remove from its space".

    Same rule ``pinned`` follows — a config written before spaces existed must
    not silently pull reports out of them.
    """
    config = tmp_path / "c.yaml"
    config.write_text("reports:\n  plain:\n    name: Plain\n")
    stored = _report_config("Plain")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/reports": {"reports": [_report_row("r1", "Plain", space_id="s1")]},
        "/api/v1/reports/r1": {"config": stored},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    space_puts = [c for c in mock_client.put.call_args_list if c.args[0].endswith("/space")]
    assert space_puts == []


def test_seed_dry_run_writes_nothing(mock_client: MagicMock, tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(_SPACE_CONFIG)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/reports": {"reports": []},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=True)

    mock_client.post.assert_not_called()
    mock_client.put.assert_not_called()


# ---------------------------------------------------------------------------
# Exporting
# ---------------------------------------------------------------------------


def test_export_round_trips_spaces_membership_and_overview(mock_client: MagicMock, tmp_path: Path) -> None:
    config = tmp_path / "c.yaml"
    config.write_text("reports: {}\n")
    tree = {
        "space": {**_space_row(), "overview_report_id": "r1"},
        "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}],
        "reports": [],
    }
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/tree": tree,
        "/api/v1/reports": {
            "reports": [
                _report_row("r1", "Sec Overview", space_id="s1", subspace_id=None),
                _report_row("r2", "Findings", space_id="s1", subspace_id="ss1"),
            ]
        },
        "/api/v1/reports/r1": {"config": _report_config("Sec Overview")},
        "/api/v1/reports/r2": {"config": _report_config("Findings")},
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {"toolsets": []},
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config), dry_run=False)

    written = seed.schema.load_file(str(config))
    assert written.spaces["security"].name == "Security"
    assert written.spaces["security"].subspaces["vulnerabilities"].name == "Vulnerabilities"
    assert written.spaces["security"].overview == "sec_overview"
    assert written.reports["sec_overview"].space == "security"
    assert written.reports["findings"].subspace == "vulnerabilities"


def test_export_reuses_existing_yaml_keys_for_spaces(mock_client: MagicMock, tmp_path: Path) -> None:
    """A hand-written key survives an export, like report keys do."""
    config = tmp_path / "c.yaml"
    config.write_text(
        "spaces:\n  sec:\n    name: Security\n    subspaces:\n      v:\n        name: Vulnerabilities\nreports: {}\n"
    )
    tree = {
        "space": _space_row(),
        "subspaces": [{"subspace_id": "ss1", "space_id": "s1", "name": "Vulnerabilities"}],
        "reports": [],
    }
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/tree": tree,
        "/api/v1/reports": {"reports": []},
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {"toolsets": []},
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config), dry_run=False)

    written = seed.schema.load_file(str(config))
    assert set(written.spaces) == {"sec"}
    assert set(written.spaces["sec"].subspaces) == {"v"}


def test_export_skips_builtin_toolsets(mock_client: MagicMock, tmp_path: Path) -> None:
    """Built-in ids fail the YAML key validators and are not seedable (#240)."""
    config = tmp_path / "c.yaml"
    config.write_text("reports: {}\n")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/reports": {"reports": []},
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {
            "toolsets": [
                {"toolset_id": "__builtin_graph__", "name": "graph", "enabled": True},
                {"toolset_id": "cve_analysis", "name": "CVE Analysis", "enabled": True},
            ]
        },
        "/api/v1/toolsets/cve_analysis/tools": {
            "tools": [
                {
                    "tool_id": "get_cve",
                    "name": "Get CVE",
                    "cypher": "MATCH (c:CVE) RETURN c",
                    "parameters": [],
                    "enabled": True,
                }
            ]
        },
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config), dry_run=False)

    # The built-in toolset's tools are never even fetched.
    fetched = [call.args[0] for call in mock_client.get.call_args_list]
    assert "/api/v1/toolsets/__builtin_graph__/tools" not in fetched
    written = seed.schema.load_file(str(config))
    assert set(written.toolsets) == {"cve_analysis"}


def test_export_skips_one_bad_toolset_without_aborting(mock_client: MagicMock, tmp_path: Path) -> None:
    """A validation failure costs one toolset, not the whole run (#240)."""
    config = tmp_path / "c.yaml"
    config.write_text("reports: {}\n")
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/reports": {"reports": []},
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {
            "toolsets": [
                {"toolset_id": "Bad-Id", "name": "Aaa Bad", "enabled": True},
                {"toolset_id": "good", "name": "Zzz Good", "enabled": True},
            ]
        },
        "/api/v1/toolsets/Bad-Id/tools": {
            "tools": [
                {
                    "tool_id": "Not-Snake-Case",
                    "name": "T",
                    "cypher": "MATCH (n) RETURN n",
                    "parameters": [],
                    "enabled": True,
                }
            ]
        },
        "/api/v1/toolsets/good/tools": {"tools": []},
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config), dry_run=False)

    written = seed.schema.load_file(str(config))
    assert set(written.toolsets) == {"good"}


def test_export_drops_an_overview_whose_report_failed_to_export(mock_client: MagicMock, tmp_path: Path) -> None:
    """A dangling overview would make the emitted config fail to load."""
    config = tmp_path / "c.yaml"
    config.write_text("reports: {}\n")
    tree = {
        "space": {**_space_row(), "overview_report_id": "r1"},
        "subspaces": [],
        "reports": [],
    }
    mock_client.get.side_effect = lambda path: {
        "/api/v1/spaces": {"spaces": [_space_row()]},
        "/api/v1/spaces/s1/tree": tree,
        "/api/v1/reports": {"reports": [_report_row("r1", "Sec Overview", space_id="s1")]},
        # No version: the report is skipped, so nothing can point at it.
        "/api/v1/reports/r1": None,
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {"toolsets": []},
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config), dry_run=False)

    written = seed.schema.load_file(str(config))
    assert written.spaces["security"].overview is None
    assert written.reports == {}
