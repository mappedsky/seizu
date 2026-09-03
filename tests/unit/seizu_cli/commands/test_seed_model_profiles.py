from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seizu_cli.commands import seed


@pytest.fixture
def mock_client(mocker) -> MagicMock:
    client = MagicMock()
    mocker.patch("seizu_cli.state.get_client", return_value=client)
    return client


def _definition(name: str = "Balanced", *, is_default: bool = True, enabled: bool = True):
    return seed.schema.ModelProfileDef(
        name=name,
        description=f"{name} profile",
        enabled=enabled,
        is_default=is_default,
        primary={"model_id": "anthropic/claude-sonnet-4-6"},
        economy={"model_id": "openai/gpt-5-mini", "reasoning_effort": "low"},
        stage_overrides={"planner": {"reasoning_effort": "high"}},
        user_reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="medium",
        run_cost_budget_usd=2.0,
    )


def _stored(profile_id: str, definition, **updates):
    value = {"profile_id": profile_id, **definition.model_dump(mode="json")}
    value.update(updates)
    return value


def test_seed_model_profiles_creates_missing_and_skips_unchanged(mock_client: MagicMock) -> None:
    balanced = _definition()
    economy = _definition("Economy", is_default=False)
    config = seed.schema.ReportingConfig(model_profiles={"balanced": balanced, "economy": economy})
    mock_client.get.return_value = {"profiles": [_stored("existing", economy)]}
    mock_client.post.return_value = {"profile_id": "created"}

    seed._seed_model_profiles(config, force=False, dry_run=False)

    mock_client.post.assert_called_once_with("/api/v1/model-profiles", json=balanced.model_dump(mode="json"))
    mock_client.put.assert_not_called()


def test_seed_model_profiles_updates_changed_profile_with_force(mock_client: MagicMock) -> None:
    definition = _definition()
    config = seed.schema.ReportingConfig(model_profiles={"balanced": definition})
    mock_client.get.return_value = {"profiles": [_stored("p1", definition)]}

    seed._seed_model_profiles(config, force=True, dry_run=False)

    mock_client.put.assert_called_once_with(
        "/api/v1/model-profiles/p1",
        json={**definition.model_dump(mode="json"), "comment": seed.SEED_UPDATE_COMMENT},
    )


def test_seed_model_profiles_switches_default_before_updating_old_default(mock_client: MagicMock) -> None:
    new_default = _definition("New default")
    old_default = _definition("Old default", is_default=False, enabled=False)
    config = seed.schema.ReportingConfig(model_profiles={"old": old_default, "new": new_default})
    mock_client.get.return_value = {
        "profiles": [
            _stored("old-id", old_default, enabled=True, is_default=True),
            _stored("new-id", new_default, is_default=False),
        ]
    }

    seed._seed_model_profiles(config, force=False, dry_run=False)

    assert [call.args[0] for call in mock_client.put.call_args_list] == [
        "/api/v1/model-profiles/new-id",
        "/api/v1/model-profiles/old-id",
    ]


def test_seed_model_profiles_dry_run_performs_no_writes(mock_client: MagicMock) -> None:
    definition = _definition()
    config = seed.schema.ReportingConfig(model_profiles={"balanced": definition})
    mock_client.get.return_value = {"profiles": []}

    seed._seed_model_profiles(config, force=False, dry_run=True)

    mock_client.post.assert_not_called()
    mock_client.put.assert_not_called()


def test_export_model_profiles_round_trips_and_preserves_yaml_key(mock_client: MagicMock, tmp_path: Path) -> None:
    definition = _definition()
    config_path = tmp_path / "reporting.yaml"
    config_path.write_text(
        seed.schema.dump_yaml(seed.schema.ReportingConfig(model_profiles={"hand_written": definition}))
    )
    stored = _stored("profile-id", definition)
    mock_client.get.side_effect = lambda path: {
        "/api/v1/model-profiles": {"profiles": [stored]},
        "/api/v1/reports": {"reports": []},
        "/api/v1/reports/dashboard": {"report_id": None},
        "/api/v1/spaces": {"spaces": []},
        "/api/v1/workflows": {"workflows": []},
        "/api/v1/toolsets": {"toolsets": []},
        "/api/v1/skillsets": {"skillsets": []},
    }[path]

    seed.export_cmd(str(config_path), dry_run=False)

    exported = seed.schema.load_file(str(config_path))
    assert exported.model_profiles == {"hand_written": definition}

    mock_client.reset_mock()
    mock_client.get.return_value = {"profiles": [stored]}
    seed._seed_model_profiles(exported, force=False, dry_run=False)
    mock_client.post.assert_not_called()
    mock_client.put.assert_not_called()
