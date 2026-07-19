"""Tests for the phase-isolated sandbox remediation service."""

import logging
from contextlib import ExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting.services import sandbox_agent, sandbox_remediation
from reporting.services.sandbox_remediation import (
    RemediationRunResult,
    config_error,
    run_ci_fix,
    run_remediation,
    validate_target,
)

_GH_TOKEN = "ghp_supersecret123"
_AGENT_KEY = "sk-ant-agentkey456"

_TARGET: dict[str, Any] = {
    "repo": "org/app",
    "base_branch": "main",
    "branch_name": "seizu/dependency-update/pip-requests",
    "prompt": "Update requests to 2.32.4",
    "pr_title": "Bump requests",
    "pr_body_fallback": "Bumps `requests` to a newer version.",
}


class _FakeBackend:
    """Records every phase command with the envs it received."""

    def __init__(self, outputs: dict[str, str] | None = None, fail_phase: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.files: dict[str, str] = {}
        self._outputs = outputs or {}
        self._fail_phase = fail_phase

    async def write_file(self, path: str, content: str) -> str:
        self.files[path] = content
        return f"Wrote {path}"

    async def read_file(self, path: str) -> str:
        # Handoff files the agent sandbox reads for the push sandbox.
        defaults = {
            sandbox_remediation.CHANGES_B64_PATH: "cGF0Y2g=",  # base64("patch")
            sandbox_remediation.PR_TITLE_PATH: "Bump requests",
            sandbox_remediation.PR_BODY_PATH: "Bumps requests.",
        }
        return self.files.get(path, defaults.get(path, ""))

    async def get_host(self, port: int) -> str:
        return f"{port}-fakesandbox.e2b.app"

    async def get_traffic_access_token(self) -> str:
        return "e2b-traffic-tok"

    async def run_bash_streaming(
        self, cmd: str, *, timeout_seconds: int, on_output: Any, envs: dict[str, str] | None = None
    ) -> str:
        phase = _phase_of(cmd)
        self.calls.append((phase, dict(envs or {})))
        output = self._outputs.get(phase, f"{phase} ok\n")
        on_output(output)
        if self._fail_phase == phase:
            raise RuntimeError(f"command exited with code 1 in {phase}")
        return output


def _phase_of(cmd: str) -> str:
    # Order matters: both the setup and push scripts clone, so match the more
    # specific markers (agent CLI, gh pr create) before the generic clone check.
    if "litellm --config" in cmd:
        return "proxy_start"
    if "litellm[proxy]" in cmd:
        return "proxy_install"
    if "npm install -g" in cmd:
        return "install"  # agent-sandbox install (gh + provider CLI)
    if "gh pr list --head" in cmd:
        return "guard"
    if "claude -p" in cmd or "codex exec" in cmd or "opencode run" in cmd:
        return "agent"
    if "git diff --binary" in cmd:
        return "extract"  # create- and fix-mode extract both emit a binary diff
    if "gh pr create" in cmd:
        return "push"  # push-sandbox script (clone + apply + commit + push + PR)
    if "gh pr view" in cmd and "git push origin" in cmd:
        return "push"  # fix-mode push (existing PR: fast-forward push, no create)
    if "git clone" in cmd or "git checkout -b" in cmd:
        return "setup"  # agent-sandbox clone + branch (no PR)
    if "cli/cli/releases" in cmd:
        return "push_install"  # push-sandbox install (gh only, no npm/git)
    return "unknown"


def _patched_backend(backend: _FakeBackend, captured: dict[str, Any] | None = None) -> Any:
    """Yield the SAME backend for every open_backend() call (both sandboxes),
    accumulating all phases on one .calls list — for tests that only inspect the
    agent sandbox. ``captured`` records the LAST open's kwargs."""

    @asynccontextmanager
    async def _ctx(**kwargs: Any):  # type: ignore[misc]
        if captured is not None:
            captured.update(kwargs)
        yield backend

    return _patch_open_backend(_ctx)


def _patched_open(backends: list[_FakeBackend], opens: list[dict[str, Any]]) -> Any:
    """Yield a distinct backend per open_backend() call and record each open's
    kwargs — for tests that assert the sandbox split (proxy/agent/push)."""
    state = {"n": 0}

    @asynccontextmanager
    async def _ctx(**kwargs: Any):  # type: ignore[misc]
        opens.append(kwargs)
        idx = state["n"]
        state["n"] += 1
        yield backends[idx] if idx < len(backends) else _FakeBackend()

    return _patch_open_backend(_ctx)


def _patch_open_backend(ctx: Any) -> ExitStack:
    """Patch open_backend in BOTH namespaces that call it (the agent/push
    sandboxes via sandbox_remediation, the credential proxy via sandbox_agent)
    with one shared context factory, so opens stay in a single ordered sequence."""
    stack = ExitStack()
    stack.enter_context(patch("reporting.services.sandbox_remediation.open_backend", new=ctx))
    stack.enter_context(patch("reporting.services.sandbox_agent.open_backend", new=ctx))
    return stack


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "SANDBOX_AGENT_PROVIDER": "claude",
        "SANDBOX_AGENT_API_KEY": _AGENT_KEY,
        "SANDBOX_AGENT_API_KEY_COMMAND": "",
        "SANDBOX_AGENT_BASE_URL": "",
        "SANDBOX_AGENT_MODEL": "",
        "REMEDIATION_TIMEOUT_SECONDS": 100,
        "REMEDIATION_GH_VERSION": "2.62.0",
        "REMEDIATION_GH_SHA256": "",
        "SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED": False,
        "SANDBOX_AGENT_CREDENTIAL_PROXY_MAX_BUDGET": "5",
        "SANDBOX_AGENT_TEMPLATE": "",
        "REMEDIATION_GITHUB_HOST": "github.com",
        "REMEDIATION_GITHUB_TOKEN": _GH_TOKEN,
        "REMEDIATION_USE_FORK": False,
        "REMEDIATION_FORK_ORG": "",
        "REMEDIATION_GIT_USER": "bot",
        "REMEDIATION_GIT_EMAIL": "bot@localhost",
        "SANDBOX_API_KEY": "e2b-key",
        "SANDBOX_DOMAIN": "",
        "SANDBOX_MAX_OUTPUT_BYTES": 50_000,
    }
    values.update(overrides)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(patch(f"reporting.settings.{name}", value))
    return stack


# ---------------------------------------------------------------------------
# Credential phase isolation — the core security property
# ---------------------------------------------------------------------------


async def test_two_sandboxes_isolate_the_token_from_the_agent() -> None:
    # THE core property: the untrusted agent runs in one sandbox (never with the
    # GitHub token); the push runs in a SEPARATE fresh sandbox that never ran the
    # agent — so a persistence attack planted during the agent phase can't reach
    # the token.
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    opens: list[dict[str, Any]] = []
    with _settings(), _patched_open([agent, push], opens):
        result = await run_remediation(**_TARGET)

    # Two distinct sandboxes.
    assert len(opens) == 2
    assert [p for p, _ in agent.calls] == ["install", "setup", "guard", "agent", "extract"]
    assert [p for p, _ in push.calls] == ["push_install", "push"]

    envs = dict(agent.calls)
    # Install runs with no secrets (only the non-secret pinned gh version); the
    # setup/guard phases have the token (pre-agent, trusted).
    assert envs["install"] == {"SEIZU_GH_VERSION": "2.62.0"}
    assert envs["setup"]["GH_TOKEN"] == _GH_TOKEN
    assert envs["guard"]["GH_TOKEN"] == _GH_TOKEN
    # THE invariant — the agent never sees the GitHub token, and neither does
    # the extract phase that runs after it in the same (now-tainted) sandbox.
    assert envs["agent"] == {"ANTHROPIC_API_KEY": _AGENT_KEY}
    assert "GH_TOKEN" not in envs["agent"]
    assert "GH_TOKEN" not in envs["extract"]

    # The push sandbox holds the token, but it never ran the agent.
    push_env = dict(push.calls)["push"]
    assert push_env["GH_TOKEN"] == _GH_TOKEN
    assert push_env["GH_ENTERPRISE_TOKEN"] == _GH_TOKEN
    assert push_env["SEIZU_PR_TITLE"] == _TARGET["pr_title"]
    assert "ANTHROPIC_API_KEY" not in push_env

    # No secrets at creation for either sandbox; the agent sandbox uses the
    # provider template, the push sandbox uses the base image (no agent CLI).
    assert all("envs" not in o for o in opens)
    assert opens[0]["template"] == "claude"
    assert opens[0]["allow_internet"] is True
    assert opens[1]["template"] is None

    assert result.status == "completed"
    assert result.pr_url == "https://github.com/org/app/pull/42"


async def test_credential_proxy_keeps_real_key_out_of_the_agent_sandbox() -> None:
    # Proxy mode: a THIRD sandbox runs LiteLLM with the real key; the agent
    # sandbox gets only a virtual key + the proxy's base URL. The real key never
    # touches the agent VM.
    proxy = _FakeBackend()
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    opens: list[dict[str, Any]] = []
    with (
        _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True, SANDBOX_AGENT_API_KEY="real-anthropic-key"),
        _patched_open([proxy, agent, push], opens),
    ):
        result = await run_remediation(**_TARGET)

    # Three sandboxes: proxy, agent, push. claude can send a custom header, so
    # the proxy stays PRIVATE (not publicly reachable) and uses no agent template.
    assert len(opens) == 3
    assert opens[0]["allow_public_traffic"] is False
    assert opens[0].get("template") is None
    assert [p for p, _ in proxy.calls] == ["proxy_install", "proxy_start"]

    # The REAL key only ever seeds the proxy sandbox; the config caps spend.
    proxy_start_env = dict(proxy.calls)["proxy_start"]
    assert proxy_start_env["PROXY_REAL_KEY"] == "real-anthropic-key"
    assert "max_budget" in proxy.files[sandbox_agent.LITELLM_CONFIG_PATH]

    # The agent gets the proxy's ephemeral master key + the proxy base URL — never
    # the real key — plus the E2B traffic token as a header to reach the private proxy.
    agent_env = next(envs for phase, envs in agent.calls if phase == "agent")
    assert agent_env["ANTHROPIC_API_KEY"].startswith("sk-seizu-")
    assert agent_env["ANTHROPIC_BASE_URL"] == "https://4000-fakesandbox.e2b.app"
    assert agent_env["ANTHROPIC_CUSTOM_HEADERS"] == "e2b-traffic-access-token: e2b-traffic-tok"
    assert "real-anthropic-key" not in agent_env.values()
    assert result.status == "completed"


async def test_credential_proxy_codex_writes_a_private_config() -> None:
    # codex reaches the private proxy via a written ~/.codex/config.toml with an
    # env-referenced traffic-token header — no public fallback.
    proxy = _FakeBackend()
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    opens: list[dict[str, Any]] = []
    with (
        _settings(
            SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True,
            SANDBOX_AGENT_PROVIDER="codex",
            SANDBOX_AGENT_API_KEY="real-openai-key",
        ),
        _patched_open([proxy, agent, push], opens),
    ):
        result = await run_remediation(**_TARGET)

    assert opens[0]["allow_public_traffic"] is False  # private, not the public fallback
    agent_env = next(envs for phase, envs in agent.calls if phase == "agent")
    assert agent_env["OPENAI_API_KEY"].startswith("sk-seizu-")
    assert agent_env["SEIZU_PROXY_ACCESS_TOKEN"] == "e2b-traffic-tok"
    # The codex config selects the proxy provider and reads the header from env.
    config = agent.files[sandbox_agent._CODEX_CONFIG_PATH]
    assert 'model_provider = "seizu_proxy"' in config
    assert "e2b-traffic-access-token" in config and "real-openai-key" not in config
    assert result.status == "completed"


async def test_credential_proxy_opencode_writes_a_private_config() -> None:
    # opencode reaches the private proxy via an openai-compatible provider config.
    proxy = _FakeBackend()
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    opens: list[dict[str, Any]] = []
    with (
        _settings(
            SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True,
            SANDBOX_AGENT_PROVIDER="opencode",
            SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro",
            SANDBOX_AGENT_API_KEY="real-deepseek-key",
        ),
        _patched_open([proxy, agent, push], opens),
    ):
        result = await run_remediation(**_TARGET)

    assert opens[0]["allow_public_traffic"] is False
    # LiteLLM routes the wildcard model to the deepseek namespace (from the model).
    assert 'model: "deepseek/*"' in proxy.files[sandbox_agent.LITELLM_CONFIG_PATH]
    agent_env = next(envs for phase, envs in agent.calls if phase == "agent")
    # The bare model is routed at the custom provider (no doubled namespace).
    assert agent_env["SEIZU_AGENT_MODEL"] == "seizu_proxy/deepseek-v4-pro"
    config = agent.files[sandbox_agent._OPENCODE_CONFIG_PATH]
    assert '"deepseek-v4-pro"' in config and "deepseek/deepseek-v4-pro" not in config
    assert "e2b-traffic-access-token" in config and "sk-seizu-" in config  # the ephemeral key
    assert "real-deepseek-key" not in config
    assert result.status == "completed"


async def test_credential_proxy_conflicts_with_base_url() -> None:
    with _settings(SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED=True, SANDBOX_AGENT_BASE_URL="https://gw"):
        assert "mutually exclusive" in (config_error() or "")


async def test_guard_skips_agent_and_push_when_open_pr_exists() -> None:
    # The guard finds an existing open PR → the agent, extract, and the entire
    # push sandbox never run; the result reports the existing PR as skipped.
    agent = _FakeBackend(outputs={"guard": "SEIZU_PR_EXISTS=https://github.com/org/app/pull/9\n"})
    opens: list[dict[str, Any]] = []
    with _settings(), _patched_open([agent], opens):
        result = await run_remediation(**_TARGET)

    assert len(opens) == 1  # push sandbox never opened
    assert [p for p, _ in agent.calls] == ["install", "setup", "guard"]
    assert result.status == "skipped"
    assert result.pr_url == "https://github.com/org/app/pull/9"


def test_guard_script_checks_for_open_pr() -> None:
    guard = sandbox_remediation._GUARD_SCRIPT
    assert "gh pr list --head" in guard
    assert "--state open" in guard
    assert "SEIZU_PR_EXISTS=" in guard


async def test_no_secrets_written_to_sandbox_files() -> None:
    backend = _FakeBackend()
    with _settings(), _patched_backend(backend):
        await run_remediation(**_TARGET)

    prompt = backend.files[sandbox_agent.PROMPT_PATH]
    assert "Security boundary" in prompt
    assert "Update requests to 2.32.4" in prompt
    assert "do NOT push" in prompt
    assert _GH_TOKEN not in prompt
    assert _AGENT_KEY not in prompt
    assert _GH_TOKEN not in backend.files[sandbox_remediation.PR_BODY_PATH]


async def test_target_values_reach_scripts_via_env_not_interpolation() -> None:
    backend = _FakeBackend()
    with _settings(), _patched_backend(backend):
        await run_remediation(**_TARGET)

    setup_env = next(envs for phase, envs in backend.calls if phase == "setup")
    assert setup_env["SEIZU_REPO"] == "org/app"
    assert setup_env["SEIZU_BASE_BRANCH"] == "main"
    assert setup_env["SEIZU_BRANCH"] == "seizu/dependency-update/pip-requests"
    # The scripts themselves are fixed constants referencing only env vars.
    assert "$SEIZU_REPO" in sandbox_remediation._SETUP_SCRIPT
    assert "org/app" not in sandbox_remediation._SETUP_SCRIPT


async def test_tokens_masked_in_output_tail() -> None:
    backend = _FakeBackend(outputs={"agent": f"leaked {_GH_TOKEN} and {_AGENT_KEY}\n"})
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert _GH_TOKEN not in result.output_tail
    assert _AGENT_KEY not in result.output_tail
    assert "***" in result.output_tail


async def test_model_env_only_in_agent_phase_when_configured() -> None:
    backend = _FakeBackend()
    with _settings(SANDBOX_AGENT_MODEL="claude-sonnet-4-6"), _patched_backend(backend):
        await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


async def test_agent_key_falls_back_to_anthropic_key_for_claude() -> None:
    backend = _FakeBackend()
    with (
        _settings(SANDBOX_AGENT_API_KEY=""),
        patch("reporting.settings.ANTHROPIC_API_KEY", "anthropic-fallback"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    assert result.status == "completed"
    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_API_KEY"] == "anthropic-fallback"


async def test_agent_key_command_mints_per_run_key() -> None:
    """SANDBOX_AGENT_API_KEY_COMMAND stdout becomes the run's key — the
    short-lived-credential path; it takes precedence over the static key."""
    backend = _FakeBackend()
    with (
        _settings(SANDBOX_AGENT_API_KEY_COMMAND="printf per-run-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    assert result.status == "completed"
    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_API_KEY"] == "per-run-key"


async def test_agent_key_command_failure_fails_the_run() -> None:
    backend = _FakeBackend()
    with _settings(SANDBOX_AGENT_API_KEY_COMMAND="exit 1"), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "agent API key unavailable" in (result.error or "")
    assert backend.calls == []


async def test_agent_base_url_exported_to_agent_phase() -> None:
    backend = _FakeBackend()
    with (
        _settings(SANDBOX_AGENT_BASE_URL="https://llm-gateway.internal"),
        _patched_backend(backend),
    ):
        await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_BASE_URL"] == "https://llm-gateway.internal"


async def test_template_none_uses_base_image() -> None:
    opens: list[dict[str, Any]] = []
    with _settings(SANDBOX_AGENT_TEMPLATE="none"), _patched_open([], opens):
        await run_remediation(**_TARGET)
    # "none" → base image for the agent sandbox; install installs the CLI itself.
    assert opens[0]["template"] is None


async def test_template_explicit_override() -> None:
    opens: list[dict[str, Any]] = []
    with _settings(SANDBOX_AGENT_TEMPLATE="my-pinned-claude"), _patched_open([], opens):
        await run_remediation(**_TARGET)
    # The agent sandbox uses the operator's template; the push sandbox stays base.
    assert opens[0]["template"] == "my-pinned-claude"
    assert opens[1]["template"] is None


async def test_codex_provider_sets_both_api_key_envs() -> None:
    backend = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/9\n"})
    with _settings(SANDBOX_AGENT_PROVIDER="codex"), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    # codex reads OPENAI_API_KEY; E2B's template documents CODEX_API_KEY — set both.
    assert agent_env["OPENAI_API_KEY"] == _AGENT_KEY
    assert agent_env["CODEX_API_KEY"] == _AGENT_KEY
    # Still no GitHub token in the agent phase.
    assert "GH_TOKEN" not in agent_env
    assert result.status == "completed"


async def test_codex_falls_back_to_global_openai_key() -> None:
    # codex must fall back to the global OPENAI_API_KEY (not ANTHROPIC), matching
    # the provider-global fallback the docs promise.
    backend = _FakeBackend()
    with (
        _settings(SANDBOX_AGENT_PROVIDER="codex", SANDBOX_AGENT_API_KEY=""),
        patch("reporting.settings.OPENAI_API_KEY", "global-openai-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["OPENAI_API_KEY"] == "global-openai-key"
    assert result.status == "completed"


async def test_codex_provider_uses_codex_template() -> None:
    opens: list[dict[str, Any]] = []
    with _settings(SANDBOX_AGENT_PROVIDER="codex"), _patched_open([], opens):
        await run_remediation(**_TARGET)
    assert opens[0]["template"] == "codex"


async def test_install_command_is_idempotent() -> None:
    # Guarded with `command -v` so a prebuilt-template CLI is not reinstalled.
    for provider in sandbox_agent.PROVIDERS.values():
        assert provider.install_cmd.startswith("command -v ")


# ---------------------------------------------------------------------------
# opencode (multi-provider — e.g. DeepSeek)
# ---------------------------------------------------------------------------


async def test_opencode_deepseek_sets_provider_key_env_and_model() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/5\n"})
    opens: list[dict[str, Any]] = []
    with (
        _settings(SANDBOX_AGENT_PROVIDER="opencode", SANDBOX_AGENT_MODEL="deepseek/deepseek-v4-pro"),
        _patched_open([agent, push], opens),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in agent.calls if phase == "agent")
    # The model provider prefix selects the key env opencode reads.
    assert agent_env["DEEPSEEK_API_KEY"] == _AGENT_KEY
    # opencode takes the model as a --model flag, passed via env.
    assert agent_env["SEIZU_AGENT_MODEL"] == "deepseek/deepseek-v4-pro"
    # Still no GitHub token in the agent phase, and no unrelated provider keys.
    assert "GH_TOKEN" not in agent_env
    assert "ANTHROPIC_API_KEY" not in agent_env
    assert opens[0]["template"] == "opencode"
    assert result.status == "completed"


async def test_opencode_falls_back_to_global_provider_key() -> None:
    # Parity with the chat assistant: an operator who already set DEEPSEEK_API_KEY
    # for chat needs no remediation-specific key.
    backend = _FakeBackend()
    with (
        _settings(
            SANDBOX_AGENT_PROVIDER="opencode",
            SANDBOX_AGENT_MODEL="deepseek/deepseek-reasoner",
            SANDBOX_AGENT_API_KEY="",
        ),
        patch("reporting.settings.DEEPSEEK_API_KEY", "global-deepseek-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["DEEPSEEK_API_KEY"] == "global-deepseek-key"
    assert result.status == "completed"


def test_opencode_requires_a_model() -> None:
    with _settings(SANDBOX_AGENT_PROVIDER="opencode", SANDBOX_AGENT_MODEL=""):
        assert "requires SANDBOX_AGENT_MODEL" in (config_error() or "")


def test_opencode_rejects_unsupported_model_provider() -> None:
    with _settings(SANDBOX_AGENT_PROVIDER="opencode", SANDBOX_AGENT_MODEL="frobnicator/x"):
        assert "not supported" in (config_error() or "")


async def test_opencode_run_command_is_headless() -> None:
    provider = sandbox_agent.PROVIDERS["opencode"]
    run_cmd = provider.run_cmd
    assert 'opencode run --model "$SEIZU_AGENT_MODEL"' in run_cmd
    assert sandbox_agent.PROMPT_PATH in run_cmd
    # Blanket-approval config so the headless run never stalls on a prompt...
    assert '"permission":"allow"' in run_cmd
    assert "opencode.json" in run_cmd
    # ...written to the repo but kept out of the PR.
    assert ".git/info/exclude" in run_cmd


async def test_github_enterprise_host() -> None:
    backend = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.example.com/org/app/pull/7\n"})
    with _settings(REMEDIATION_GITHUB_HOST="github.example.com"), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    setup_env = next(envs for phase, envs in backend.calls if phase == "setup")
    push_env = next(envs for phase, envs in backend.calls if phase == "push")
    assert setup_env["SEIZU_GITHUB_HOST"] == "github.example.com"
    # setup runs `gh auth setup-git` for the clone, so it needs the GHES gh env too.
    assert setup_env["GH_HOST"] == "github.example.com"
    assert setup_env["GH_ENTERPRISE_TOKEN"] == _GH_TOKEN
    assert push_env["GH_HOST"] == "github.example.com"
    assert push_env["GH_ENTERPRISE_TOKEN"] == _GH_TOKEN
    # The PR URL marker is parsed host-agnostically.
    assert result.pr_url == "https://github.example.com/org/app/pull/7"


# ---------------------------------------------------------------------------
# Fork mode (REMEDIATION_USE_FORK)
# ---------------------------------------------------------------------------


def _patched_fork(mock: AsyncMock) -> Any:
    return patch("reporting.services.github_checks.ensure_fork", mock)


async def test_fork_mode_pushes_to_the_fork_and_opens_a_cross_repo_pr() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/8\n"})
    ensure = AsyncMock(return_value="seizu-bot/app")
    with _settings(REMEDIATION_USE_FORK=True), _patched_fork(ensure), _patched_open([agent, push], []):
        result = await run_remediation(**_TARGET)

    # The fork is ensured worker-side (never from a sandbox), under the token
    # user's account when no org is configured.
    ensure.assert_awaited_once_with("org/app", org="")
    push_env = dict(push.calls)["push"]
    assert push_env["SEIZU_FORK_REPO"] == "seizu-bot/app"
    assert push_env["SEIZU_HEAD_REPO"] == "seizu-bot/app"
    assert push_env["SEIZU_REPO"] == "org/app"  # the PR still targets upstream
    # The agent phase is unchanged — still no GitHub token, no fork knowledge.
    assert dict(agent.calls)["agent"] == {"ANTHROPIC_API_KEY": _AGENT_KEY}
    assert result.status == "completed"
    assert result.pr_url == "https://github.com/org/app/pull/8"


async def test_fork_org_is_forwarded_to_ensure_fork() -> None:
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/8\n"})
    ensure = AsyncMock(return_value="bots-inc/app")
    with (
        _settings(REMEDIATION_USE_FORK=True, REMEDIATION_FORK_ORG="bots-inc"),
        _patched_fork(ensure),
        _patched_open([_FakeBackend(), push], []),
    ):
        result = await run_remediation(**_TARGET)
    ensure.assert_awaited_once_with("org/app", org="bots-inc")
    assert result.status == "completed"


async def test_direct_mode_never_touches_forks() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/8\n"})
    ensure = AsyncMock()
    with _settings(), _patched_fork(ensure), _patched_open([agent, push], []):
        await run_remediation(**_TARGET)

    ensure.assert_not_awaited()
    push_env = dict(push.calls)["push"]
    assert "SEIZU_FORK_REPO" not in push_env
    assert push_env["SEIZU_HEAD_REPO"] == "org/app"  # PR branch lives upstream


async def test_fork_failure_fails_the_run_before_any_sandbox() -> None:
    opens: list[dict[str, Any]] = []
    ensure = AsyncMock(side_effect=RuntimeError(f"403 forbidden for {_GH_TOKEN}"))
    with _settings(REMEDIATION_USE_FORK=True), _patched_fork(ensure), _patched_open([], opens):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "could not ensure a fork of org/app" in (result.error or "")
    assert _GH_TOKEN not in (result.error or "")  # masked like all run errors
    assert opens == []


async def test_fork_name_from_the_api_is_validated_before_reaching_shell() -> None:
    opens: list[dict[str, Any]] = []
    ensure = AsyncMock(return_value="bot/app; rm -rf /")
    with _settings(REMEDIATION_USE_FORK=True), _patched_fork(ensure), _patched_open([], opens):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "invalid fork repo" in (result.error or "")
    assert opens == []


async def test_fork_mode_fix_clones_and_pushes_the_pr_branch_on_the_fork() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    ensure = AsyncMock(return_value="seizu-bot/app")
    with _settings(REMEDIATION_USE_FORK=True), _patched_fork(ensure), _patched_open([agent, push], []):
        result = await run_ci_fix(**_FIX_TARGET)

    # Both the agent-sandbox clone and the fresh push-sandbox clone target the
    # fork — that is where the PR branch lives in fork mode.
    assert dict(agent.calls)["setup"]["SEIZU_HEAD_REPO"] == "seizu-bot/app"
    push_env = dict(push.calls)["push"]
    assert push_env["SEIZU_HEAD_REPO"] == "seizu-bot/app"
    assert push_env["SEIZU_REPO"] == "org/app"  # PR lookup stays on upstream
    assert result.status == "completed"
    assert result.pushed is True


async def test_ci_fix_explicit_head_repo_overrides_current_fork_setting() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    ensure = AsyncMock()
    with _settings(REMEDIATION_USE_FORK=False), _patched_fork(ensure), _patched_open([agent, push], []):
        result = await run_ci_fix(**_FIX_TARGET, head_repo="seizu-bot/app")

    assert dict(agent.calls)["setup"]["SEIZU_HEAD_REPO"] == "seizu-bot/app"
    assert dict(push.calls)["push"]["SEIZU_HEAD_REPO"] == "seizu-bot/app"
    ensure.assert_not_awaited()
    assert result.status == "completed"


def test_push_script_fork_branch_is_cross_repo() -> None:
    push = sandbox_remediation._PUSH_SCRIPT
    # Fork mode pushes the branch to the fork remote and opens the PR on the
    # target repo with a fork-owner-qualified head.
    assert 'git push --force -u fork "$SEIZU_BRANCH"' in push
    assert "${SEIZU_FORK_REPO%%/*}:$SEIZU_BRANCH" in push
    # The fork's base branch is synced first so the shallow push finds its
    # ancestor objects server-side.
    assert "merge-upstream" in push
    # PR creation and the exists-fallback always address the target repo
    # explicitly (two remotes exist in fork mode).
    assert 'gh pr create --repo "$SEIZU_REPO"' in push
    assert 'gh pr view "$SEIZU_BRANCH" --repo "$SEIZU_REPO"' in push
    assert "$GH_TOKEN" not in push


def test_fix_scripts_clone_the_head_repo_and_look_up_the_pr_on_the_target() -> None:
    assert "$SEIZU_HEAD_REPO" in sandbox_remediation._FIX_SETUP_SCRIPT
    assert "$SEIZU_HEAD_REPO" in sandbox_remediation._FIX_PUSH_SCRIPT
    assert '--repo "$SEIZU_REPO"' in sandbox_remediation._FIX_PUSH_SCRIPT


# ---------------------------------------------------------------------------
# Configuration and target validation
# ---------------------------------------------------------------------------


def test_config_error_cases() -> None:
    # There is no enable flag: fully configured means enabled.
    with _settings():
        assert config_error() is None
    with _settings(SANDBOX_AGENT_PROVIDER="gemini-cli"):
        assert "unknown sandbox agent provider" in (config_error() or "")
    with _settings(SANDBOX_AGENT_API_KEY=""), patch("reporting.settings.ANTHROPIC_API_KEY", ""):
        assert "no API key" in (config_error() or "")
    # A key-mint command satisfies the API key requirement on its own.
    with (
        _settings(SANDBOX_AGENT_API_KEY="", SANDBOX_AGENT_API_KEY_COMMAND="printf k"),
        patch("reporting.settings.ANTHROPIC_API_KEY", ""),
    ):
        assert config_error() is None
    with _settings(REMEDIATION_GITHUB_TOKEN=""):
        assert "REMEDIATION_GITHUB_TOKEN" in (config_error() or "")


@pytest.mark.parametrize(
    "repo,base_branch,branch_name",
    [
        ("x; rm -rf /", "main", "fix"),
        ("org/app/extra", "main", "fix"),
        ("$(whoami)/app", "main", "fix"),
        ("org/app", "main; curl evil", "fix"),
        ("org/app", "-oProxyCommand=evil", "fix"),
        ("org/app", "main", "a..b"),
        ("org/app", "main", "fix`id`"),
    ],
)
def test_validate_target_rejects_unsafe_values(repo: str, base_branch: str, branch_name: str) -> None:
    assert validate_target(repo, base_branch, branch_name) is not None


async def test_run_fails_closed_when_unconfigured() -> None:
    backend = _FakeBackend()
    with _settings(REMEDIATION_GITHUB_TOKEN=""), _patched_backend(backend):
        result = await run_remediation(**_TARGET)
    assert result.status == "failed"
    assert "REMEDIATION_GITHUB_TOKEN" in (result.error or "")
    assert backend.calls == []


async def test_run_fails_closed_on_invalid_target() -> None:
    backend = _FakeBackend()
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**{**_TARGET, "repo": "x; rm -rf /"})
    assert result.status == "failed"
    assert "invalid repo" in (result.error or "")
    assert backend.calls == []


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_phase_failure_returns_masked_output() -> None:
    backend = _FakeBackend(outputs={"agent": f"partial with {_GH_TOKEN}\n"}, fail_phase="agent")
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert _GH_TOKEN not in (result.error or "")
    assert _GH_TOKEN not in result.output_tail
    assert "partial with" in result.output_tail
    # The push phase never ran after the agent failed.
    assert [phase for phase, _ in backend.calls] == ["install", "setup", "guard", "agent"]


async def test_no_changes_committed_is_a_distinct_error() -> None:
    # The extract phase (agent sandbox) detects an empty diff and exits; the
    # push sandbox never opens.
    agent = _FakeBackend(outputs={"extract": "SEIZU_NO_CHANGES\n"}, fail_phase="extract")
    opens: list[dict[str, Any]] = []
    with _settings(), _patched_open([agent], opens):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "committed no changes" in (result.error or "")
    assert len(opens) == 1  # no push sandbox


async def test_timeout_returns_failed_result() -> None:
    backend = _FakeBackend()
    backend.run_bash_streaming = AsyncMock(side_effect=TimeoutError())  # type: ignore[method-assign]
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "timed out" in (result.error or "")


async def test_on_progress_called_for_output_chunks() -> None:
    backend = _FakeBackend()
    progress = MagicMock()
    with _settings(), _patched_backend(backend):
        await run_remediation(**_TARGET, on_progress=progress)
    # One chunk per phase at minimum.
    assert progress.call_count >= 4


# ---------------------------------------------------------------------------
# Script invariants
# ---------------------------------------------------------------------------


def test_setup_authenticates_via_gh_with_no_token_in_url() -> None:
    setup = sandbox_remediation._SETUP_SCRIPT
    # Auth is delegated to gh's own credential helper (correctly quoted by gh),
    # not a hand-rolled inline helper.
    assert "gh auth setup-git" in setup
    assert "credential.helper='!f" not in setup
    # The token is never interpolated into the clone URL or the script.
    assert "$GH_TOKEN" not in setup
    assert "GH_TOKEN@" not in setup
    # Host comes from env (GitHub Enterprise support), never hardcoded.
    assert "$SEIZU_GITHUB_HOST" in setup
    assert "github.com" not in setup


def test_extract_script_detects_no_changes_and_emits_patch() -> None:
    extract = sandbox_remediation._EXTRACT_SCRIPT
    assert "SEIZU_NO_CHANGES" in extract
    assert "git diff --binary" in extract
    assert "base64" in extract
    assert sandbox_remediation.CHANGES_B64_PATH in extract
    # The extract phase must never carry the token.
    assert "$GH_TOKEN" not in extract


def test_push_script_applies_patch_in_a_fresh_clone_and_reports_pr_url() -> None:
    push = sandbox_remediation._PUSH_SCRIPT
    # The push sandbox clones FRESH (never ran the agent) and applies the patch.
    assert "git clone" in push
    assert "git apply" in push
    assert sandbox_remediation.CHANGES_B64_PATH in push
    # Squash into one deterministic commit (enforces no-CVE in the commit message).
    assert "git commit" in push
    assert "SEIZU_PR_URL=" in push
    assert "gh pr create" in push
    assert "gh auth setup-git" in push
    assert "git push --force" in push
    # gh's helper supplies auth; the token is never in the push command.
    assert "$GH_TOKEN" not in push
    # The agent-written title file wins over the fallback env title.
    assert sandbox_remediation.PR_TITLE_PATH in push
    assert '"$SEIZU_PR_TITLE"' in push
    # No CVE scrubbing — the policy is left to the agent prompt (CVEs are public).
    assert "sed" not in push


def test_push_install_has_no_agent_cli_or_npm() -> None:
    # The push sandbox installs only gh — never npm/agent/test code runs there.
    assert "npm" not in sandbox_remediation._PUSH_INSTALL
    assert "gh" in sandbox_remediation._PUSH_INSTALL
    # Pinned, checksum-verified gh (no "latest").
    assert "sha256sum -c" in sandbox_remediation._GH_INSTALL
    assert "releases/latest" not in sandbox_remediation._GH_INSTALL


def test_gh_install_supports_independent_sha256_pin() -> None:
    # When REMEDIATION_GH_SHA256 is set, gh is verified against that out-of-band
    # digest; otherwise against the release's own checksums.
    assert "SEIZU_GH_SHA256" in sandbox_remediation._GH_INSTALL
    assert "gh_checksums.txt" in sandbox_remediation._GH_INSTALL  # fallback path


async def test_gh_version_is_a_setting_that_reaches_the_install_phases() -> None:
    # The version is env-driven (REMEDIATION_GH_VERSION), not hardcoded in the script.
    assert "${SEIZU_GH_VERSION}" in sandbox_remediation._GH_INSTALL
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    with _settings(REMEDIATION_GH_VERSION="2.99.9"), _patched_open([agent, push], []):
        await run_remediation(**_TARGET)
    assert dict(agent.calls)["install"]["SEIZU_GH_VERSION"] == "2.99.9"
    assert dict(push.calls)["push_install"]["SEIZU_GH_VERSION"] == "2.99.9"


async def test_gh_sha256_digest_reaches_the_install_phases() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    opens: list[dict[str, Any]] = []
    with _settings(REMEDIATION_GH_SHA256="abc123"), _patched_open([agent, push], opens):
        await run_remediation(**_TARGET)

    assert dict(agent.calls)["install"]["SEIZU_GH_SHA256"] == "abc123"
    assert dict(push.calls)["push_install"]["SEIZU_GH_SHA256"] == "abc123"


async def test_gh_sha256_absent_by_default() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    with _settings(), _patched_open([agent, push], []):
        await run_remediation(**_TARGET)
    # Empty setting → no SEIZU_GH_SHA256 (falls back to release checksums).
    assert "SEIZU_GH_SHA256" not in dict(agent.calls)["install"]


async def test_warns_when_using_a_long_lived_agent_key(caplog: pytest.LogCaptureFixture) -> None:
    # A static key (no key-mint command) is exposed to untrusted repo code — warn.
    sandbox_agent._warned_static_key = False
    try:
        agent = _FakeBackend()
        push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
        with (
            _settings(SANDBOX_AGENT_API_KEY_COMMAND=""),
            _patched_open([agent, push], []),
            caplog.at_level(logging.WARNING),
        ):
            await run_remediation(**_TARGET)
        assert any("long-lived API key" in r.message for r in caplog.records)
    finally:
        sandbox_agent._warned_static_key = False


async def test_each_phase_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/1\n"})
    with _settings(), _patched_open([agent, push], []), caplog.at_level(logging.INFO):
        await run_remediation(**_TARGET)

    records = [r for r in caplog.records if r.message == "Remediation phase starting"]
    assert [r.phase for r in records] == ["install", "setup", "guard", "agent", "extract", "push_install", "push"]
    assert all(r.mode == "create" and r.repo == "org/app" for r in records)


async def test_fix_mode_phases_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    with _settings(), _patched_open([agent, push], []), caplog.at_level(logging.INFO):
        await run_ci_fix(**_FIX_TARGET)

    records = [r for r in caplog.records if r.message == "Remediation phase starting"]
    assert [r.phase for r in records] == ["install", "setup", "agent", "extract", "push_install", "push"]
    assert all(r.mode == "fix" for r in records)


def test_agent_scripts_read_prompt_from_file() -> None:
    for provider in sandbox_agent.PROVIDERS.values():
        assert sandbox_agent.PROMPT_PATH in provider.run_cmd


def test_result_dataclass_defaults() -> None:
    result = RemediationRunResult(status="failed", error="x")
    assert result.pr_url is None
    assert result.output_tail == ""
    assert result.pushed is False
    assert result.comment_body == ""


# ---------------------------------------------------------------------------
# PR body prompt (create mode)
# ---------------------------------------------------------------------------


async def test_pr_body_instructions_require_verification_and_change_rationale() -> None:
    # The PR body must say how compatibility was verified, state explicitly when
    # no code changes were needed (and why), and justify each code change made.
    backend = _FakeBackend()
    with _settings(), _patched_backend(backend):
        await run_remediation(**_TARGET)

    prompt = " ".join(backend.files[sandbox_agent.PROMPT_PATH].split())
    assert "How you verified compatibility" in prompt
    assert "searched this codebase for usages" in prompt
    assert "If NO code changes were needed, say so explicitly and why" in prompt
    assert "If code changes WERE needed, explain each one" in prompt
    assert "anything you could not verify" in prompt


# ---------------------------------------------------------------------------
# CI fix mode (run_ci_fix against an existing PR)
# ---------------------------------------------------------------------------

_FIX_TARGET: dict[str, Any] = {
    "repo": "org/app",
    "base_branch": "main",
    "branch_name": "seizu/dependency-update/pip-requests-2.32.4",
    "prompt": "Triage the failing CI checks",
    "commit_title": "Fix CI failures for the requests update",
}


class _CommentingBackend(_FakeBackend):
    """Simulates an agent that writes the PR-comment file during its phase."""

    def __init__(self, comment: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._comment = comment

    async def run_bash_streaming(
        self, cmd: str, *, timeout_seconds: int, on_output: Any, envs: dict[str, str] | None = None
    ) -> str:
        output = await super().run_bash_streaming(cmd, timeout_seconds=timeout_seconds, on_output=on_output, envs=envs)
        if _phase_of(cmd) == "agent":
            self.files[sandbox_remediation.PR_COMMENT_PATH] = self._comment
        return output


async def test_ci_fix_pushes_new_commits_with_same_token_isolation() -> None:
    # Fix mode keeps THE invariant: agent phase never sees the GitHub token;
    # the push happens from a separate fresh sandbox. No guard phase (the PR
    # is known to exist), and the push is a fast-forward with a commit title.
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    opens: list[dict[str, Any]] = []
    with _settings(), _patched_open([agent, push], opens):
        result = await run_ci_fix(**_FIX_TARGET)

    assert len(opens) == 2
    assert [p for p, _ in agent.calls] == ["install", "setup", "agent", "extract"]  # no guard
    assert [p for p, _ in push.calls] == ["push_install", "push"]
    envs = dict(agent.calls)
    assert envs["setup"]["GH_TOKEN"] == _GH_TOKEN  # pre-agent clone, trusted
    assert envs["agent"] == {"ANTHROPIC_API_KEY": _AGENT_KEY}
    assert "GH_TOKEN" not in envs["extract"]
    push_env = dict(push.calls)["push"]
    assert push_env["GH_TOKEN"] == _GH_TOKEN
    assert push_env["SEIZU_COMMIT_TITLE"] == _FIX_TARGET["commit_title"]
    assert opens[1]["template"] is None  # fresh base-image push sandbox

    assert result.status == "completed"
    assert result.pushed is True
    assert result.comment_body == ""
    assert result.pr_url == "https://github.com/org/app/pull/42"


async def test_ci_fix_comment_only_skips_the_push_sandbox() -> None:
    # No new commits + a comment file → the failures were judged unrelated;
    # nothing to push, the comment text is returned for the WORKER to post.
    agent = _CommentingBackend(
        "The failing check is flaky and also fails on main.",
        outputs={"extract": "SEIZU_NO_NEW_COMMITS\n"},
    )
    opens: list[dict[str, Any]] = []
    with _settings(), _patched_open([agent], opens):
        result = await run_ci_fix(**_FIX_TARGET)

    assert len(opens) == 1  # push sandbox never opened
    assert result.status == "completed"
    assert result.pushed is False
    assert result.comment_body == "The failing check is flaky and also fails on main."
    assert result.pr_url is None


async def test_ci_fix_fails_when_agent_produces_nothing() -> None:
    agent = _FakeBackend(outputs={"extract": "SEIZU_NO_NEW_COMMITS\n"})
    with _settings(), _patched_backend(agent):
        result = await run_ci_fix(**_FIX_TARGET)

    assert result.status == "failed"
    assert "neither committed fixes nor wrote a PR comment" in (result.error or "")


async def test_ci_fix_comment_is_masked() -> None:
    agent = _CommentingBackend(f"see {_GH_TOKEN}", outputs={"extract": "SEIZU_NO_NEW_COMMITS\n"})
    with _settings(), _patched_backend(agent):
        result = await run_ci_fix(**_FIX_TARGET)
    assert _GH_TOKEN not in result.comment_body
    assert "***" in result.comment_body


async def test_ci_fix_prompt_has_fix_footer_and_no_pr_body_instructions() -> None:
    agent = _FakeBackend()
    push = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    with _settings(), _patched_open([agent, push], []):
        await run_ci_fix(**_FIX_TARGET)

    prompt = " ".join(agent.files[sandbox_agent.PROMPT_PATH].split())
    assert "Security boundary" in prompt
    assert "Triage the failing CI checks" in prompt
    assert "pull request branch" in prompt
    assert sandbox_remediation.PR_COMMENT_PATH in prompt
    assert "do NOT push" in prompt
    assert "do not try to comment on the pull request yourself" in prompt
    # Create-mode PR title/body instructions do not apply to a fix run.
    assert sandbox_remediation.PR_TITLE_PATH not in prompt
    assert sandbox_remediation.PR_BODY_PATH not in prompt
    # The comment placeholder was written so reading it back never errors.
    assert agent.files[sandbox_remediation.PR_COMMENT_PATH] == ""


async def test_ci_fix_fails_closed_when_unconfigured_or_invalid() -> None:
    backend = _FakeBackend()
    with _settings(REMEDIATION_GITHUB_TOKEN=""), _patched_backend(backend):
        result = await run_ci_fix(**_FIX_TARGET)
    assert result.status == "failed"
    assert backend.calls == []
    with _settings(), _patched_backend(backend):
        result = await run_ci_fix(**{**_FIX_TARGET, "branch_name": "a..b"})
    assert result.status == "failed"
    assert backend.calls == []
    with _settings(), _patched_backend(backend):
        result = await run_ci_fix(**_FIX_TARGET, head_repo="bot/app; touch /tmp/pwned")
    assert "invalid PR head repo" in (result.error or "")
    assert backend.calls == []


def test_fix_setup_script_checks_out_the_existing_pr_branch() -> None:
    setup = sandbox_remediation._FIX_SETUP_SCRIPT
    # Clone the PR branch itself — never create a new branch, never touch base.
    assert '--branch "$SEIZU_BRANCH"' in setup
    assert "git checkout -b" not in setup
    assert "$SEIZU_BASE_BRANCH" not in setup
    assert "gh auth setup-git" in setup
    assert "github.com" not in setup


def test_fix_extract_script_diffs_only_new_commits() -> None:
    extract = sandbox_remediation._FIX_EXTRACT_SCRIPT
    # Diff against the remote PR-branch tip (only the agent's commits) — NOT
    # against base, so a base branch that advanced can't leak reverted commits.
    assert "origin/$SEIZU_BRANCH..HEAD" in extract
    assert "SEIZU_NO_NEW_COMMITS" in extract
    assert "$GH_TOKEN" not in extract


def test_fix_push_script_fast_forwards_without_force_or_pr_create() -> None:
    push = sandbox_remediation._FIX_PUSH_SCRIPT
    assert "git clone" in push and "git apply" in push
    assert "gh pr create" not in push  # the PR already exists
    assert "--force" not in push  # fast-forward only; a race fails loudly
    assert "gh pr view" in push and "SEIZU_PR_URL=" in push
    assert "$GH_TOKEN" not in push
