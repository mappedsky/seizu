"""Unit tests for the shared sandbox coding-agent helpers.

The remediation tests exercise these through ``run_remediation``; these cover the
reusable module's own contract in isolation (it is meant to back other callers).
"""

from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from reporting.services import sandbox_agent


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "SANDBOX_AGENT_PROVIDER": "claude",
        "SANDBOX_AGENT_API_KEY": "",
        "SANDBOX_AGENT_API_KEY_COMMAND": "",
        "SANDBOX_AGENT_BASE_URL": "",
        "SANDBOX_AGENT_MODEL": "",
        "SANDBOX_AGENT_TEMPLATE": "",
        "SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED": False,
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
    }
    values.update(overrides)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(patch(f"reporting.settings.{name}", value))
    return stack


def test_resolve_provider_unknown() -> None:
    with _settings(SANDBOX_AGENT_PROVIDER="gemini-cli"):
        assert sandbox_agent.resolve_provider() is None


def test_agent_config_error_needs_a_key() -> None:
    with _settings():  # no key anywhere
        assert "no API key configured" in (sandbox_agent.agent_config_error() or "")
    with _settings(SANDBOX_AGENT_API_KEY="sk-x"):
        assert sandbox_agent.agent_config_error() is None
    # The global provider key is an acceptable fallback.
    with _settings(ANTHROPIC_API_KEY="sk-global"):
        assert sandbox_agent.agent_config_error() is None


def test_agent_config_error_proxy_constraints() -> None:
    with _settings(
        SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True, SANDBOX_AGENT_API_KEY="sk-x", SANDBOX_AGENT_BASE_URL="u"
    ):
        assert "mutually exclusive" in (sandbox_agent.agent_config_error() or "")
    # opencode is supported in proxy mode (routes via a written config); it needs
    # a model to derive the namespace and a real key to seed the proxy.
    with _settings(
        SANDBOX_AGENT_PROVIDER="opencode",
        SANDBOX_AGENT_MODEL="deepseek/deepseek-chat",
        SANDBOX_AGENT_API_KEY="sk-d",
        SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True,
    ):
        assert sandbox_agent.agent_config_error() is None
    # …but with no model it can't derive a namespace → key error surfaces first.
    with _settings(SANDBOX_AGENT_PROVIDER="opencode", SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True):
        assert sandbox_agent.agent_config_error() is not None


def test_resolve_key_envs_fallback_matches_the_provider() -> None:
    codex = sandbox_agent.PROVIDERS["codex"]
    with _settings(OPENAI_API_KEY="sk-openai", ANTHROPIC_API_KEY="sk-anthropic"):
        key_envs, fallback, err = sandbox_agent.resolve_key_envs_and_fallback(codex)
    assert err is None
    assert key_envs == ("OPENAI_API_KEY", "CODEX_API_KEY")
    assert fallback == "sk-openai"  # not the anthropic key


def test_resolve_key_envs_opencode_selects_by_model_prefix() -> None:
    opencode = sandbox_agent.PROVIDERS["opencode"]
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-chat", DEEPSEEK_API_KEY="sk-d"):
        key_envs, fallback, err = sandbox_agent.resolve_key_envs_and_fallback(opencode)
    assert err is None and key_envs == ("DEEPSEEK_API_KEY",) and fallback == "sk-d"
    with _settings(SANDBOX_AGENT_MODEL=""):
        assert "requires SANDBOX_AGENT_MODEL" in (sandbox_agent.resolve_key_envs_and_fallback(opencode)[2] or "")
    with _settings(SANDBOX_AGENT_MODEL="mystery/model"):
        assert "not supported" in (sandbox_agent.resolve_key_envs_and_fallback(opencode)[2] or "")


def test_build_agent_env_claude_and_opencode() -> None:
    claude = sandbox_agent.PROVIDERS["claude"]
    with _settings(SANDBOX_AGENT_MODEL="claude-sonnet-4-6"):
        env = sandbox_agent.build_agent_env(claude, ("ANTHROPIC_API_KEY",), "sk-key", "https://proxy")
    assert env == {
        "ANTHROPIC_API_KEY": "sk-key",
        "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        "ANTHROPIC_BASE_URL": "https://proxy",
    }
    opencode = sandbox_agent.PROVIDERS["opencode"]
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-chat"):
        env = sandbox_agent.build_agent_env(opencode, ("DEEPSEEK_API_KEY",), "sk-d", None)
    # opencode passes the model via a --model flag env, not a provider model env.
    assert env == {"DEEPSEEK_API_KEY": "sk-d", "SEIZU_AGENT_MODEL": "deepseek/deepseek-chat"}


def test_resolve_template() -> None:
    claude = sandbox_agent.PROVIDERS["claude"]
    with _settings():
        assert sandbox_agent.resolve_template(claude) == "claude"  # provider default
    with _settings(SANDBOX_AGENT_TEMPLATE="my-image"):
        assert sandbox_agent.resolve_template(claude) == "my-image"
    with _settings(SANDBOX_AGENT_TEMPLATE="none"):
        assert sandbox_agent.resolve_template(claude) is None


def test_use_credential_proxy_depends_on_a_routable_namespace() -> None:
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True):
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["claude"]) is True
        # opencode needs a model to derive its LiteLLM namespace.
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["opencode"]) is False
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True, SANDBOX_AGENT_MODEL="deepseek/deepseek-chat"):
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["opencode"]) is True
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=False):
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["claude"]) is False


def test_proxy_namespace() -> None:
    with _settings():
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["claude"]) == "anthropic"
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["codex"]) == "openai"
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-chat"):
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["opencode"]) == "deepseek"
    with _settings(SANDBOX_AGENT_MODEL=""):  # opencode with no model → no namespace
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["opencode"]) is None


def test_proxy_agent_setup_per_transport() -> None:
    # header (claude): env carries the base URL + the traffic-token header.
    with _settings():
        setup = sandbox_agent.proxy_agent_setup(
            sandbox_agent.PROVIDERS["claude"], ("ANTHROPIC_API_KEY",), "https://p", "vk", "tok"
        )
    assert setup.files == {}
    assert setup.env["ANTHROPIC_BASE_URL"] == "https://p"
    assert setup.env["ANTHROPIC_CUSTOM_HEADERS"] == "e2b-traffic-access-token: tok"

    # codex: a config file + the token in an env var it references (not on disk).
    with _settings():
        setup = sandbox_agent.proxy_agent_setup(
            sandbox_agent.PROVIDERS["codex"], ("OPENAI_API_KEY", "CODEX_API_KEY"), "https://p/v1", "vk", "tok"
        )
    assert setup.env["OPENAI_API_KEY"] == "vk" and setup.env["SEIZU_PROXY_ACCESS_TOKEN"] == "tok"
    assert "vk" not in setup.files[sandbox_agent._CODEX_CONFIG_PATH]  # key stays in env

    # opencode: an openai-compatible provider config + a namespaced model.
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-chat"):
        setup = sandbox_agent.proxy_agent_setup(
            sandbox_agent.PROVIDERS["opencode"], ("DEEPSEEK_API_KEY",), "https://p/v1", "vk", "tok"
        )
    assert setup.env == {"SEIZU_AGENT_MODEL": "seizu_proxy/deepseek/deepseek-chat"}
    assert "deepseek/deepseek-chat" in setup.files[sandbox_agent._OPENCODE_CONFIG_PATH]


def test_agent_run_script_cds_into_the_workdir() -> None:
    script = sandbox_agent.agent_run_script(sandbox_agent.PROVIDERS["claude"], "/home/user/repo")
    assert "cd /home/user/repo" in script
    assert "claude -p" in script
