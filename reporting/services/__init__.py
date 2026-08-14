import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def get_boto_session(
    region: str | None,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
) -> boto3.Session:
    """Get a boto3 session."""
    return boto3.Session(
        region_name=region,
        aws_secret_access_key=aws_secret_access_key,
        aws_access_key_id=aws_access_key_id,
        aws_session_token=aws_session_token,
    )


def get_boto_client(
    client: str,
    region: str | None = None,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
    endpoint_url: str | None = None,
    config: Any = None,
) -> Any:
    """Return a boto3 client."""
    session = get_boto_session(region, aws_access_key_id, aws_secret_access_key, aws_session_token)
    return session.client(client, endpoint_url=endpoint_url, config=config)
