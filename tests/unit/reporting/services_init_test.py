"""Tests for reporting.services (boto session/client helpers)."""

from unittest.mock import MagicMock

from reporting.services import get_boto_client, get_boto_session


def test_get_boto_session_uses_region():
    session = get_boto_session("us-east-1")
    assert session.region_name == "us-east-1"


def test_get_boto_client_returns_client(mocker):
    fake_client = MagicMock()
    fake_session = MagicMock()
    fake_session.client.return_value = fake_client
    mocker.patch("reporting.services.get_boto_session", return_value=fake_session)

    result = get_boto_client("s3", region="us-east-1", endpoint_url="http://localhost:9000")

    fake_session.client.assert_called_once_with("s3", endpoint_url="http://localhost:9000", config=None)
    assert result is fake_client
