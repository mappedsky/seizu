"""Tests for seizu_cli.commands.seed helpers."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seizu_cli.commands import seed


@pytest.fixture
def mock_client(mocker: pytest.MonkeyPatch) -> MagicMock:
    mc = MagicMock()
    mocker.patch("seizu_cli.state.get_client", return_value=mc)
    return mc


def test_publish_report_uses_visibility_endpoint(mock_client: MagicMock) -> None:
    seed._publish_report("r1")

    mock_client.put.assert_called_once_with(
        "/api/v1/reports/r1/visibility",
        json={"access": {"scope": "public"}},
    )


def test_pin_report_uses_pin_endpoint(mock_client: MagicMock) -> None:
    seed._pin_report("r1", True)

    mock_client.put.assert_called_once_with(
        "/api/v1/reports/r1/pin",
        json={"pinned": True},
    )


def test_seed_pins_unchanged_report_without_storing_pinned_in_config(
    mock_client: MagicMock,
    tmp_path: Path,
) -> None:
    config = tmp_path / "reporting-dashboard.yaml"
    config.write_text(
        """
reports:
  pinned-report:
    name: Pinned Report
    pinned: true
""".lstrip()
    )
    stored_config = {
        "schema_version": 1,
        "name": "Pinned Report",
        "queries": {},
        "inputs": [],
        "rows": [],
    }
    mock_client.get.side_effect = lambda path: {
        "/api/v1/reports": {
            "reports": [
                {
                    "report_id": "r1",
                    "name": "Pinned Report",
                    "access": {"scope": "public"},
                    "pinned": False,
                }
            ]
        },
        "/api/v1/reports/r1": {"config": stored_config},
    }[path]

    seed.seed_cmd(str(config), force=False, dry_run=False)

    mock_client.post.assert_not_called()
    mock_client.put.assert_called_once_with("/api/v1/reports/r1/pin", json={"pinned": True})


def test_seed_plugins_resolves_relative_source_and_installs_package(
    mock_client: MagicMock,
    tmp_path: Path,
) -> None:
    package = tmp_path / "plugins" / "security"
    package.mkdir(parents=True)
    (package / "plugin.json").write_text("{}")
    config_path = tmp_path / "reporting.yaml"
    config_path.write_text("plugins: {}\n")
    config = seed.schema.ReportingConfig(
        plugins={"security": seed.schema.PluginDef(source="plugins/security", enabled=False)}
    )
    validation = {
        "valid": True,
        "plugin_id": "security",
        "package_digest": "digest-1",
        "diagnostics": [],
    }
    installed = {"plugin_id": "security", "package_digest": "digest-1", "enabled": True}
    disabled = {**installed, "enabled": False}
    mock_client.get.return_value = {"plugins": []}
    mock_client.post.side_effect = [validation, installed]
    mock_client.put.return_value = disabled

    seed._seed_plugins(config, config_path=config_path, force=False, dry_run=False)

    assert [call.args[0] for call in mock_client.post.call_args_list] == [
        "/api/v1/plugins/validate",
        "/api/v1/plugins/install",
    ]
    mock_client.put.assert_called_once_with("/api/v1/plugins/security", json={"enabled": False})


def test_seed_plugins_skips_unchanged_digest(mock_client: MagicMock, tmp_path: Path) -> None:
    package = tmp_path / "security.zip"
    package.write_bytes(b"package")
    config = seed.schema.ReportingConfig(plugins={"security": seed.schema.PluginDef(source=str(package), enabled=True)})
    existing = {"plugin_id": "security", "package_digest": "digest-1", "enabled": True}
    mock_client.get.return_value = {"plugins": [existing]}
    mock_client.post.return_value = {
        "valid": True,
        "plugin_id": "security",
        "package_digest": "digest-1",
        "diagnostics": [],
    }

    seed._seed_plugins(config, config_path=tmp_path / "reporting.yaml", force=True, dry_run=False)

    assert [call.args[0] for call in mock_client.post.call_args_list] == ["/api/v1/plugins/validate"]
    mock_client.put.assert_not_called()
