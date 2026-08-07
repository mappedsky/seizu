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
        "SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE": "",
        "SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE": "",
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
        SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro",
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
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro", DEEPSEEK_API_KEY="sk-d"):
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
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro"):
        env = sandbox_agent.build_agent_env(opencode, ("DEEPSEEK_API_KEY",), "sk-d", None)
    # opencode passes the model via a --model flag env, not a provider model env.
    assert env == {"DEEPSEEK_API_KEY": "sk-d", "SEIZU_AGENT_MODEL": "deepseek/deepseek-v4-pro"}


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
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True, SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro"):
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["opencode"]) is True
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=False):
        assert sandbox_agent.use_credential_proxy(sandbox_agent.PROVIDERS["claude"]) is False


def test_checked_in_lock_is_usable() -> None:
    # The shipped lock is what a templateless run installs, so it must parse:
    # hashes, the requirements it came from, and the runtime it was resolved for
    # (without which the sandbox cannot be checked against it).
    with _settings():
        assert sandbox_agent.proxy_lock_error() is None
        lock = sandbox_agent.read_proxy_lock()
        assert lock is not None
        assert lock.requirements and all("==" in r for r in lock.requirements)
        assert lock.python and lock.machine
        assert "--hash=sha256:" in lock.text


def test_unusable_lock_files_are_rejected(tmp_path: Any) -> None:
    # Each of these would otherwise fail inside the sandbox, after it had been
    # created and handed the real provider key.
    missing = tmp_path / "nope.txt"
    no_hashes = tmp_path / "no_hashes.txt"
    no_hashes.write_text(f"{sandbox_agent.PROXY_LOCK_RUNTIME_MARKER} python=3.11 machine=x86_64\nlitellm==1.87.0\n")
    no_runtime = tmp_path / "no_runtime.txt"
    no_runtime.write_text("litellm==1.87.0 \\\n    --hash=sha256:abc\n")
    for bad in (missing, no_hashes, no_runtime):
        with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE=str(bad)):
            assert sandbox_agent.read_proxy_lock() is None
            assert sandbox_agent.proxy_lock_error() is not None
            assert sandbox_agent.proxy_install_plan() is None


def test_lock_selection_carries_the_runtime_it_was_resolved_for(tmp_path: Any) -> None:
    # The install refuses on a sandbox whose python/architecture differs, so the
    # plan has to hand those values to the script.
    lock = tmp_path / "lock.txt"
    lock.write_text(
        f"{sandbox_agent.PROXY_LOCK_INPUT_MARKER} litellm[proxy]==1.90.0\n"
        f"{sandbox_agent.PROXY_LOCK_RUNTIME_MARKER} python=3.12 machine=aarch64 platform=aarch64-unknown-linux-gnu\n"
        "litellm==1.90.0 \\\n    --hash=sha256:abc\n"
    )
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE=str(lock)):
        plan = sandbox_agent.proxy_install_plan()
        assert plan is not None
        assert plan.env == {"SEIZU_LOCK_PYTHON": "3.12", "SEIZU_LOCK_MACHINE": "aarch64"}
        assert plan.lock.requirements == ["litellm[proxy]==1.90.0"]
        # The full uv target is kept too: the sandbox compares `uname -m`, but
        # re-locking has to reproduce the libc, which the machine cannot carry.
        assert plan.lock.platform == "aarch64-unknown-linux-gnu"
        # The lock itself is what gets written into the sandbox.
        assert plan.files[sandbox_agent._PROXY_LOCK_SANDBOX_PATH] == lock.read_text()


def test_install_script_refuses_a_runtime_the_lock_was_not_resolved_for() -> None:
    script = sandbox_agent._PROXY_LOCKED_INSTALL
    # Compared in the sandbox, before pip runs: a mismatched lock otherwise
    # fails as a wall of "no matching distribution" nobody is watching.
    assert "SEIZU_PROXY_RUNTIME_MISMATCH" in script
    assert "uname -m" in script and "sys.version_info" in script
    assert script.index("SEIZU_LOCK_PYTHON") < script.index(sandbox_agent.PROXY_LOCKED_INSTALL_CMD)


def test_lock_is_irrelevant_when_a_template_is_configured() -> None:
    # With a template nothing is installed, so an unusable lock must not block
    # a run that never reads it.
    with _settings(
        SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True,
        SANDBOX_AGENT_API_KEY="real-key",
        SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE="/nonexistent/lock.txt",
        SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE="my-proxy",
    ):
        assert sandbox_agent.agent_config_error() is None
    with _settings(
        SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True,
        SANDBOX_AGENT_API_KEY="real-key",
        SANDBOX_AGENT_CREDENTIAL_PROXY_REQUIREMENTS_FILE="/nonexistent/lock.txt",
    ):
        assert "lock" in (sandbox_agent.agent_config_error() or "")


def test_proxy_namespace() -> None:
    with _settings():
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["claude"]) == "anthropic"
        assert sandbox_agent.proxy_namespace(sandbox_agent.PROVIDERS["codex"]) == "openai"
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro"):
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
    with _settings(SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro"):
        setup = sandbox_agent.proxy_agent_setup(
            sandbox_agent.PROVIDERS["opencode"], ("DEEPSEEK_API_KEY",), "https://p/v1", "vk", "tok"
        )
    # The provider prefix is stripped so LiteLLM's wildcard doesn't double it.
    assert setup.env == {"SEIZU_AGENT_MODEL": "seizu_proxy/deepseek-v4-pro"}
    config = setup.files[sandbox_agent._OPENCODE_CONFIG_PATH]
    assert '"deepseek-v4-pro"' in config and "deepseek/deepseek-v4-pro" not in config


def test_agent_run_script_cds_into_the_workdir() -> None:
    script = sandbox_agent.agent_run_script(sandbox_agent.PROVIDERS["claude"], "/home/user/repo")
    assert "cd /home/user/repo" in script
    assert "claude -p" in script


def test_lock_records_the_full_platform_not_just_the_machine() -> None:
    # A musl and a gnu lock share a machine but not an ABI, so re-locking has to
    # read the recorded platform rather than deriving one.
    with _settings():
        lock = sandbox_agent.read_proxy_lock()
        assert lock is not None
        assert lock.platform.startswith(lock.machine + "-")
