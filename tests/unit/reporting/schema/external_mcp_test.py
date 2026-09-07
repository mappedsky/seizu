import json

import pytest
from pydantic import ValidationError

from reporting.schema.external_mcp import ExternalMCPProxy, parse_external_mcp_proxies


@pytest.mark.parametrize(
    "updates",
    [
        {"protocol_mode": "invalid"},
        {
            "auth_mode": "bearer",
            "token_env": "TOKEN",
            "user_authorization": {"reauthorize_url": "https://gateway.test/accounts"},
        },
        {
            "header_mappings": {"access_token": "X-Token"},
            "user_authorization": {"reauthorize_url": "https://gateway.test/accounts"},
        },
        {
            "header_mappings": {"email": "x-target-user-id"},
            "user_authorization": {"reauthorize_url": "https://gateway.test/accounts"},
        },
        {"user_authorization": {"reauthorize_url": "javascript:alert(1)"}},
        {
            "client_credentials": {
                "token_url": "https://idp.test/token",
                "client_id": "seizu",
                "client_secret_env": "SECRET",
            }
        },
        {
            "auth_mode": "m2m_jwt",
            "token_env": "TOKEN",
            "client_credentials": {
                "token_url": "https://idp.test/token",
                "client_id": "seizu",
                "client_secret_env": "SECRET",
            },
        },
        {
            "auth_mode": "m2m_jwt",
            "client_credentials": {
                "token_url": "https://user:secret@idp.test/token",
                "client_id": "seizu",
                "client_secret_env": "SECRET",
            },
        },
        {
            "auth_mode": "m2m_jwt",
            "header_mappings": {"subject": "authorization"},
            "client_credentials": {
                "token_url": "https://idp.test/token",
                "client_id": "seizu",
                "client_secret_env": "SECRET",
            },
        },
    ],
)
def test_invalid_gateway_configuration(updates):
    with pytest.raises(ValueError):
        ExternalMCPProxy.model_validate({"name": "gateway", "url": "https://gateway.test/mcp", **updates})


def test_parse_external_mcp_proxies_accepts_all_auth_modes() -> None:
    proxies = parse_external_mcp_proxies(
        json.dumps(
            [
                {
                    "name": "light",
                    "url": "http://proxy:8080/sse",
                    "auth_mode": "header_delegation",
                    "header_mappings": {"user_id": "X-Forwarded-User"},
                },
                {
                    "name": "gateway",
                    "url": "https://gateway.example/mcp",
                    "transport": "streamable_http",
                    "auth_mode": "m2m_jwt",
                    "token_env": "MCP_GATEWAY_TOKEN",
                },
                {
                    "name": "bearer",
                    "url": "https://proxy.example/sse",
                    "auth_mode": "bearer",
                    "header_mappings": {"access_token": "Authorization"},
                },
            ]
        )
    )

    assert [proxy.name for proxy in proxies] == ["light", "gateway", "bearer"]
    assert proxies[1].transport == "streamable_http"


@pytest.mark.parametrize(
    "config,match",
    [
        ({"name": "bad__name", "url": "https://example.test/sse"}, "lowercase"),
        ({"name": "proxy", "url": "file:///secret"}, "http"),
        (
            {
                "name": "proxy",
                "url": "https://example.test/sse",
                "header_mappings": {"user_id": "bad header"},
            },
            "header",
        ),
        (
            {"name": "proxy", "url": "https://example.test/sse", "auth_mode": "m2m_jwt"},
            "token_env",
        ),
    ],
)
def test_external_proxy_rejects_unsafe_or_incomplete_configuration(config, match) -> None:
    with pytest.raises(ValidationError, match=match):
        ExternalMCPProxy.model_validate(config)


def test_parse_external_mcp_proxies_rejects_duplicate_names() -> None:
    raw = json.dumps(
        [
            {"name": "proxy", "url": "https://one.example/sse"},
            {"name": "proxy", "url": "https://two.example/sse"},
        ]
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        parse_external_mcp_proxies(raw)
