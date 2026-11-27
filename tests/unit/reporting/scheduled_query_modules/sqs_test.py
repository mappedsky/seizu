from unittest.mock import AsyncMock

from reporting.scheduled_query_modules import sqs
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import ScheduledQueryAction


def test_action_name():
    assert sqs.action_name() == "sqs"


def test_get_client_uses_configured_timeout(mocker):
    client_mock = mocker.MagicMock()
    client_ctor = mocker.patch("reporting.scheduled_query_modules.sqs.get_boto_client", return_value=client_mock)
    mocker.patch("reporting.settings.AWS_CONNECT_TIMEOUT", 11)
    mocker.patch("reporting.settings.AWS_READ_TIMEOUT", 23)
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_URL", "http://sqs.local")

    result = sqs._get_client()

    assert result == client_mock
    client_ctor.assert_called_once()
    kwargs = client_ctor.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://sqs.local"
    assert kwargs["config"].connect_timeout == 11
    assert kwargs["config"].read_timeout == 23


def test_get_client_uses_its_own_credentials_only_when_set(mocker):
    client_ctor = mocker.patch("reporting.scheduled_query_modules.sqs.get_boto_client")
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_URL", "")
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_ACCESS_KEY_ID", "")
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_SECRET_ACCESS_KEY", "")

    sqs._get_client()
    kwargs = client_ctor.call_args.kwargs
    assert kwargs["endpoint_url"] is None
    assert kwargs["aws_access_key_id"] is None and kwargs["aws_secret_access_key"] is None

    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_ACCESS_KEY_ID", "fake")
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_SECRET_ACCESS_KEY", "fake")
    sqs._get_client()
    kwargs = client_ctor.call_args.kwargs
    assert kwargs["aws_access_key_id"] == "fake" and kwargs["aws_secret_access_key"] == "fake"


async def test_setup(mocker):
    mocker.patch("reporting.scheduled_query_modules.sqs._SQS_CREATE_SCHEDULED_QUERY_QUEUES", True)
    client_mock = mocker.MagicMock()
    client_mock.create_queue = mocker.MagicMock()
    mocker.patch("reporting.scheduled_query_modules.sqs._get_client", return_value=client_mock)
    items = [
        ScheduledQueryItem(
            scheduled_query_id="sq1",
            name="test_query",
            cypher="test",
            enabled=True,
            params=[],
            watch_scans=[],
            actions=[{"action_type": "sqs", "action_config": {"sqs_queue": "test"}}],
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            created_by="seed-script",
        ),
        ScheduledQueryItem(
            scheduled_query_id="sq2",
            name="test_query2",
            cypher="",
            enabled=True,
            params=[],
            watch_scans=[],
            actions=[],
            stages=[
                {
                    "activities": [
                        {
                            "type": "sqs",
                            "input": "query",
                            "output": "sent",
                            "parameters": {"sqs_queue": "test2"},
                        }
                    ]
                }
            ],
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            created_by="seed-script",
        ),
    ]
    mocker.patch(
        "reporting.scheduled_query_modules.sqs.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=items),
    )
    await sqs.setup()
    assert client_mock.create_queue.call_count == 2


def test_handle_results(mocker):
    action = ScheduledQueryAction(action_type="sqs", action_config={"sqs_queue": "test"})
    results = [{"details": {"param1": "value1"}}, {"details": {"param2": "value2"}}]
    client_mock = mocker.MagicMock()
    client_mock.send_message = mocker.MagicMock()
    mocker.patch("reporting.scheduled_query_modules.sqs._get_client", return_value=client_mock)

    sqs.handle_results("test_query", action, results)
    assert client_mock.send_message.call_count == 2
