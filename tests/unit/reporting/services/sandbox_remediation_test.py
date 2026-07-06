"""Tests for the phase-isolated sandbox remediation service."""

from contextlib import ExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting.services import sandbox_remediation
from reporting.services.sandbox_remediation import (
    RemediationRunResult,
    config_error,
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
    if "npm install -g" in cmd or "cli/cli/releases" in cmd:
        return "install"
    if "git checkout -b" in cmd or " clone " in cmd:
        return "setup"
    if "gh pr list --head" in cmd:
        return "guard"
    if "claude -p" in cmd or "codex exec" in cmd or "opencode run" in cmd:
        return "agent"
    if "gh pr create" in cmd:
        return "push"
    return "unknown"


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "REMEDIATION_AGENT_PROVIDER": "claude",
        "REMEDIATION_AGENT_API_KEY": _AGENT_KEY,
        "REMEDIATION_AGENT_API_KEY_COMMAND": "",
        "REMEDIATION_AGENT_BASE_URL": "",
        "REMEDIATION_AGENT_MODEL": "",
        "REMEDIATION_TIMEOUT_SECONDS": 100,
        "REMEDIATION_SANDBOX_TEMPLATE": "",
        "REMEDIATION_GITHUB_HOST": "github.com",
        "REMEDIATION_GITHUB_TOKEN": _GH_TOKEN,
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


def _patched_backend(backend: _FakeBackend, captured: dict[str, Any] | None = None) -> Any:
    @asynccontextmanager
    async def _ctx(**kwargs: Any):  # type: ignore[misc]
        if captured is not None:
            captured.update(kwargs)
        yield backend

    return patch("reporting.services.sandbox_remediation._open_backend", new=_ctx)


# ---------------------------------------------------------------------------
# Credential phase isolation — the core security property
# ---------------------------------------------------------------------------


async def test_phases_run_in_order_with_isolated_envs() -> None:
    backend = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/42\n"})
    captured: dict[str, Any] = {}
    with _settings(), _patched_backend(backend, captured):
        result = await run_remediation(**_TARGET)

    assert [phase for phase, _ in backend.calls] == ["install", "setup", "guard", "agent", "push"]
    install_env, setup_env, guard_env, agent_env, push_env = (envs for _, envs in backend.calls)

    # Phase 1: third-party install scripts run with no secrets at all.
    assert install_env == {}
    # Phase 2: GitHub token present for the clone; no agent key.
    assert setup_env["GH_TOKEN"] == _GH_TOKEN
    assert "ANTHROPIC_API_KEY" not in setup_env
    # Guard: GitHub token (to query PRs), no agent key.
    assert guard_env["GH_TOKEN"] == _GH_TOKEN
    assert "ANTHROPIC_API_KEY" not in guard_env
    # Agent phase: THE invariant — the coding agent never sees the GitHub token.
    assert agent_env == {"ANTHROPIC_API_KEY": _AGENT_KEY}
    assert "GH_TOKEN" not in agent_env
    assert "GITHUB_TOKEN" not in agent_env
    # Push: GitHub token returns for push/PR; no agent key.
    assert push_env["GH_TOKEN"] == _GH_TOKEN
    assert push_env["GH_ENTERPRISE_TOKEN"] == _GH_TOKEN
    assert push_env["GH_HOST"] == "github.com"
    assert push_env["SEIZU_PR_TITLE"] == _TARGET["pr_title"]
    assert "ANTHROPIC_API_KEY" not in push_env
    # Host reaches the clone via env too.
    assert setup_env["SEIZU_GITHUB_HOST"] == "github.com"

    # No secrets at sandbox creation either.
    assert "envs" not in captured
    assert captured["allow_internet"] is True
    assert captured["timeout_seconds"] == 100 + 120
    # Empty setting → the provider's official prebuilt template.
    assert captured["template"] == "claude"

    assert result.status == "completed"
    assert result.pr_url == "https://github.com/org/app/pull/42"


async def test_guard_skips_agent_and_push_when_open_pr_exists() -> None:
    # The guard phase finds an existing open PR → the expensive agent and the
    # push never run, and the result reports the existing PR as skipped.
    backend = _FakeBackend(outputs={"guard": "SEIZU_PR_EXISTS=https://github.com/org/app/pull/9\n"})
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert [phase for phase, _ in backend.calls] == ["install", "setup", "guard"]
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

    prompt = backend.files[sandbox_remediation.PROMPT_PATH]
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
    with _settings(REMEDIATION_AGENT_MODEL="claude-sonnet-4-6"), _patched_backend(backend):
        await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


async def test_agent_key_falls_back_to_anthropic_key_for_claude() -> None:
    backend = _FakeBackend()
    with (
        _settings(REMEDIATION_AGENT_API_KEY=""),
        patch("reporting.settings.ANTHROPIC_API_KEY", "anthropic-fallback"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    assert result.status == "completed"
    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_API_KEY"] == "anthropic-fallback"


async def test_agent_key_command_mints_per_run_key() -> None:
    """REMEDIATION_AGENT_API_KEY_COMMAND stdout becomes the run's key — the
    short-lived-credential path; it takes precedence over the static key."""
    backend = _FakeBackend()
    with (
        _settings(REMEDIATION_AGENT_API_KEY_COMMAND="printf per-run-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    assert result.status == "completed"
    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_API_KEY"] == "per-run-key"


async def test_agent_key_command_failure_fails_the_run() -> None:
    backend = _FakeBackend()
    with _settings(REMEDIATION_AGENT_API_KEY_COMMAND="exit 1"), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "agent API key unavailable" in (result.error or "")
    assert backend.calls == []


async def test_agent_base_url_exported_to_agent_phase() -> None:
    backend = _FakeBackend()
    with (
        _settings(REMEDIATION_AGENT_BASE_URL="https://llm-gateway.internal"),
        _patched_backend(backend),
    ):
        await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["ANTHROPIC_BASE_URL"] == "https://llm-gateway.internal"


async def test_template_none_uses_base_image() -> None:
    backend = _FakeBackend()
    captured: dict[str, Any] = {}
    with _settings(REMEDIATION_SANDBOX_TEMPLATE="none"), _patched_backend(backend, captured):
        await run_remediation(**_TARGET)
    # "none" → base image; the install phase installs the CLI itself.
    assert captured["template"] is None


async def test_template_explicit_override() -> None:
    backend = _FakeBackend()
    captured: dict[str, Any] = {}
    with _settings(REMEDIATION_SANDBOX_TEMPLATE="my-pinned-claude"), _patched_backend(backend, captured):
        await run_remediation(**_TARGET)
    assert captured["template"] == "my-pinned-claude"


async def test_codex_provider_sets_both_api_key_envs() -> None:
    backend = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/9\n"})
    with _settings(REMEDIATION_AGENT_PROVIDER="codex"), _patched_backend(backend):
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
        _settings(REMEDIATION_AGENT_PROVIDER="codex", REMEDIATION_AGENT_API_KEY=""),
        patch("reporting.settings.OPENAI_API_KEY", "global-openai-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["OPENAI_API_KEY"] == "global-openai-key"
    assert result.status == "completed"


async def test_codex_provider_uses_codex_template() -> None:
    backend = _FakeBackend()
    captured: dict[str, Any] = {}
    with _settings(REMEDIATION_AGENT_PROVIDER="codex"), _patched_backend(backend, captured):
        await run_remediation(**_TARGET)
    assert captured["template"] == "codex"


async def test_install_command_is_idempotent() -> None:
    # Guarded with `command -v` so a prebuilt-template CLI is not reinstalled.
    for provider in sandbox_remediation.PROVIDERS.values():
        assert provider.install_cmd.startswith("command -v ")


# ---------------------------------------------------------------------------
# opencode (multi-provider — e.g. DeepSeek)
# ---------------------------------------------------------------------------


async def test_opencode_deepseek_sets_provider_key_env_and_model() -> None:
    backend = _FakeBackend(outputs={"push": "SEIZU_PR_URL=https://github.com/org/app/pull/5\n"})
    captured: dict[str, Any] = {}
    with (
        _settings(REMEDIATION_AGENT_PROVIDER="opencode", REMEDIATION_AGENT_MODEL="deepseek/deepseek-chat"),
        _patched_backend(backend, captured),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    # The model provider prefix selects the key env opencode reads.
    assert agent_env["DEEPSEEK_API_KEY"] == _AGENT_KEY
    # opencode takes the model as a --model flag, passed via env.
    assert agent_env["SEIZU_AGENT_MODEL"] == "deepseek/deepseek-chat"
    # Still no GitHub token in the agent phase, and no unrelated provider keys.
    assert "GH_TOKEN" not in agent_env
    assert "ANTHROPIC_API_KEY" not in agent_env
    assert captured["template"] == "opencode"
    assert result.status == "completed"


async def test_opencode_falls_back_to_global_provider_key() -> None:
    # Parity with the chat assistant: an operator who already set DEEPSEEK_API_KEY
    # for chat needs no remediation-specific key.
    backend = _FakeBackend()
    with (
        _settings(
            REMEDIATION_AGENT_PROVIDER="opencode",
            REMEDIATION_AGENT_MODEL="deepseek/deepseek-reasoner",
            REMEDIATION_AGENT_API_KEY="",
        ),
        patch("reporting.settings.DEEPSEEK_API_KEY", "global-deepseek-key"),
        _patched_backend(backend),
    ):
        result = await run_remediation(**_TARGET)

    agent_env = next(envs for phase, envs in backend.calls if phase == "agent")
    assert agent_env["DEEPSEEK_API_KEY"] == "global-deepseek-key"
    assert result.status == "completed"


def test_opencode_requires_a_model() -> None:
    with _settings(REMEDIATION_AGENT_PROVIDER="opencode", REMEDIATION_AGENT_MODEL=""):
        assert "requires REMEDIATION_AGENT_MODEL" in (config_error() or "")


def test_opencode_rejects_unsupported_model_provider() -> None:
    with _settings(REMEDIATION_AGENT_PROVIDER="opencode", REMEDIATION_AGENT_MODEL="frobnicator/x"):
        assert "not supported" in (config_error() or "")


async def test_opencode_run_command_is_headless() -> None:
    provider = sandbox_remediation.PROVIDERS["opencode"]
    run_cmd = provider.run_cmd
    assert 'opencode run --model "$SEIZU_AGENT_MODEL"' in run_cmd
    assert sandbox_remediation.PROMPT_PATH in run_cmd
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
# Configuration and target validation
# ---------------------------------------------------------------------------


def test_config_error_cases() -> None:
    # There is no enable flag: fully configured means enabled.
    with _settings():
        assert config_error() is None
    with _settings(REMEDIATION_AGENT_PROVIDER="gemini-cli"):
        assert "unknown remediation provider" in (config_error() or "")
    with _settings(REMEDIATION_AGENT_API_KEY=""), patch("reporting.settings.ANTHROPIC_API_KEY", ""):
        assert "no API key" in (config_error() or "")
    # A key-mint command satisfies the API key requirement on its own.
    with (
        _settings(REMEDIATION_AGENT_API_KEY="", REMEDIATION_AGENT_API_KEY_COMMAND="printf k"),
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
    backend = _FakeBackend(outputs={"push": "SEIZU_NO_CHANGES\n"}, fail_phase="push")
    with _settings(), _patched_backend(backend):
        result = await run_remediation(**_TARGET)

    assert result.status == "failed"
    assert "committed no changes" in (result.error or "")


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


def test_push_uses_gh_auth_and_reports_pr_url() -> None:
    push = sandbox_remediation._PUSH_SCRIPT
    assert "SEIZU_NO_CHANGES" in push
    assert "SEIZU_PR_URL=" in push
    assert "gh pr create" in push
    assert "gh auth setup-git" in push
    assert "git push --force" in push
    # gh's helper supplies auth; the token is never in the push command.
    assert "$GH_TOKEN" not in push
    # The agent-written title file wins over the fallback env title.
    assert sandbox_remediation.PR_TITLE_PATH in push
    assert '"$SEIZU_PR_TITLE"' in push
    # CVE identifiers are scrubbed from the PR title/body deterministically,
    # not just requested in the prompt.
    assert "CVE-[0-9]" in push
    assert push.index("sed") < push.index("gh pr create")


def test_agent_scripts_read_prompt_from_file() -> None:
    for provider in sandbox_remediation.PROVIDERS.values():
        assert sandbox_remediation.PROMPT_PATH in provider.run_cmd


def test_result_dataclass_defaults() -> None:
    result = RemediationRunResult(status="failed", error="x")
    assert result.pr_url is None
    assert result.output_tail == ""
