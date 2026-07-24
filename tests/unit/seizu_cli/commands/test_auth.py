"""Tests for seizu_cli.commands.auth (login / logout / whoami)."""

from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from seizu_cli.client import APIError
from seizu_cli.commands.auth import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def set_state_api_url() -> None:
    """Set a deterministic API URL in state for all auth tests."""
    import seizu_cli.state as state_mod

    original = state_mod.api_url
    state_mod.api_url = "http://localhost:8080"
    yield
    state_mod.api_url = original


@pytest.fixture
def mock_client(mocker: pytest.MonkeyPatch) -> MagicMock:
    mc = MagicMock()
    mocker.patch("seizu_cli.state.get_client", return_value=mc)
    return mc


# ---------------------------------------------------------------------------
# whoami
# ---------------------------------------------------------------------------


def test_whoami_shows_user_info(mock_client: MagicMock) -> None:
    mock_client.get.return_value = {
        "user": {
            "user_id": "u1",
            "email": "alice@example.com",
            "display_name": "Alice",
            "last_login": "2024-01-01T00:00:00Z",
            "role": "seizu-admin",
        },
        "permissions": ["workflows:read", "workflows:write"],
    }
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0
    assert "u1" in result.output
    assert "alice@example.com" in result.output
    assert "Alice" in result.output
    assert "seizu-admin" in result.output
    assert "workflows:write" in result.output
    mock_client.get.assert_called_once_with("/api/v1/me")


def test_whoami_without_display_name(mock_client: MagicMock) -> None:
    mock_client.get.return_value = {
        "user": {
            "user_id": "u2",
            "email": "bob@example.com",
            "last_login": "2024-01-01T00:00:00Z",
        },
        "permissions": [],
    }
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0
    assert "bob@example.com" in result.output
    assert "(none)" in result.output


def test_whoami_api_error(mock_client: MagicMock) -> None:
    mock_client.get.side_effect = APIError(401, "unauthorized")
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


def test_login_success(mocker: pytest.MonkeyPatch) -> None:
    mock_store = MagicMock()
    mock_store.description.return_value = "OS keyring"
    mocker.patch("seizu_cli.commands.auth.auth.get_store", return_value=mock_store)
    mocker.patch("seizu_cli.commands.auth.auth.device_authorize", return_value="access-token-123")

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "Logged in" in result.output
    mock_store.save_token.assert_called_once_with("http://localhost:8080", "access-token-123")


def test_login_device_authorize_error(mocker: pytest.MonkeyPatch) -> None:
    mock_store = MagicMock()
    mocker.patch("seizu_cli.commands.auth.auth.get_store", return_value=mock_store)
    mocker.patch(
        "seizu_cli.commands.auth.auth.device_authorize",
        side_effect=RuntimeError("auth server unreachable"),
    )

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 1
    mock_store.save_token.assert_not_called()


def test_login_get_store_error(mocker: pytest.MonkeyPatch) -> None:
    mocker.patch(
        "seizu_cli.commands.auth.auth.get_store",
        side_effect=RuntimeError("no keyring available"),
    )

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


def test_logout_with_stored_credentials(mocker: pytest.MonkeyPatch) -> None:
    mock_store = MagicMock()
    mock_store.clear_token.return_value = True
    mocker.patch("seizu_cli.commands.auth.auth.get_store", return_value=mock_store)

    result = runner.invoke(app, ["logout"])

    assert result.exit_code == 0
    assert "Logged out" in result.output
    mock_store.clear_token.assert_called_once_with("http://localhost:8080")


def test_logout_no_stored_credentials(mocker: pytest.MonkeyPatch) -> None:
    mock_store = MagicMock()
    mock_store.clear_token.return_value = False
    mocker.patch("seizu_cli.commands.auth.auth.get_store", return_value=mock_store)

    result = runner.invoke(app, ["logout"])

    assert result.exit_code == 0
    assert "No stored credentials" in result.output
