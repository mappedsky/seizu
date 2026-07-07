"""Reusable machinery for running a headless coding-agent CLI in a sandbox.

This is the provider-agnostic core shared by any workflow (or, later, a tool)
that needs to drive a coding-agent CLI — Claude Code, Codex, or opencode — inside
an ephemeral :class:`SandboxBackend`: the provider registry, credential
resolution (static key, a per-run key-mint command, or the ephemeral
credential-proxy sandbox), the agent env/run-script builders, and secret masking.

It deliberately knows nothing about *what* the agent is asked to do — cloning a
repo, opening a PR, etc. are the caller's concern (see
:mod:`reporting.services.sandbox_remediation`). Settings are the generic
``SANDBOX_AGENT_*`` family; the sandbox provider connection (``SANDBOX_API_KEY`` /
``SANDBOX_DOMAIN``) is shared with the chat sandbox tool.

Credential isolation notes carried over from the remediation flow:
- Secrets are never injected at sandbox creation; each phase is a separate
  command with only the env it needs (per-command ``envs`` on
  :meth:`SandboxBackend.run_bash_streaming`).
- The credential proxy (:func:`credential_proxy`) runs a short-lived LiteLLM
  proxy in a *separate* sandbox holding the real provider key and hands the agent
  only a budget-capped virtual key that dies when the proxy sandbox is torn down.
  The LiteLLM-in-sandbox specifics are unverified against a live CLI — confirm
  with a real run before relying on the proxy path.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from reporting.services.sandbox_backend import SandboxBackend, open_backend

logger = logging.getLogger(__name__)

# The prompt file the agent CLI reads (referenced by provider run_cmds).
PROMPT_PATH = "/home/user/prompt.md"

# Call the progress callback at least this often even when a phase is silent
# (e.g. the agent quietly running a long test suite), so upstream heartbeats
# (Temporal) never starve.
PROGRESS_TICK_SECONDS = 30.0
# Extra sandbox lifetime beyond the run deadline so E2B never reaps it mid-run.
SANDBOX_LIFETIME_SLACK_SECONDS = 120

# --- Ephemeral credential-proxy sandbox -----------------------------------
LITELLM_CONFIG_PATH = "/home/user/litellm.yaml"
_PROXY_PORT = 4000
_PROXY_TIMEOUT_SECONDS = 240
# LiteLLM config: wildcard model routing to the provider, keys from env so no
# secret lands in the file, and a global in-memory spend cap (max_budget). The
# agent authenticates with the ephemeral master key directly — LiteLLM's per-key
# minting (/key/generate) needs a Postgres database we don't provision, and a
# per-run credential is single-use anyway. NOTE: the provider namespace and the
# base-URL suffix (see SubagentProvider) are the fragile wire details — this whole
# path is unverified against a live LiteLLM + agent CLI; confirm with a real run.
_LITELLM_CONFIG = """\
model_list:
  - model_name: "*"
    litellm_params:
      model: "{namespace}/*"
      api_key: os.environ/PROXY_REAL_KEY
litellm_settings:
  max_budget: {budget}
general_settings:
  master_key: os.environ/PROXY_MASTER_KEY
"""
_PROXY_INSTALL = "set -euo pipefail\ncommand -v litellm >/dev/null 2>&1 || pip install --quiet 'litellm[proxy]'\n"
# Header E2B requires to reach a non-public sandbox's exposed ports.
_E2B_TRAFFIC_HEADER = "e2b-traffic-access-token"
# Env var the config-transport CLIs read the traffic-access token from (their
# config references it rather than baking the token into the file).
_PROXY_ACCESS_TOKEN_ENV = "SEIZU_PROXY_ACCESS_TOKEN"
# CLIs that reach a private proxy via a written config file (base URL + the
# traffic-access header), not an env var. Their config paths in the sandbox:
_CODEX_CONFIG_PATH = "/home/user/.codex/config.toml"
_OPENCODE_CONFIG_PATH = "/home/user/.config/opencode/opencode.json"
# The custom provider id the config transports register the proxy under.
_PROXY_PROVIDER_ID = "seizu_proxy"


@dataclass(frozen=True)
class ProxyAgentSetup:
    """How the agent phase reaches a running credential proxy: the extra env to
    set, and any config files to write into the agent sandbox before the run."""

    env: dict[str, str]
    files: dict[str, str] = field(default_factory=dict)


class PhaseRunner(Protocol):
    """Runs one sandbox command with output streaming + heartbeats.

    The caller owns transcript capture, masking, and the deadline, so the shared
    helpers (e.g. :func:`credential_proxy`) run their commands through the same
    path as the caller's own phases.
    """

    async def __call__(
        self, backend: SandboxBackend, name: str, script: str, envs: dict[str, str], timeout_seconds: int | None = None
    ) -> str: ...


@dataclass(frozen=True)
class SubagentProvider:
    """A headless coding-agent CLI runnable inside the sandbox.

    ``install_cmd``/``run_cmd`` are trusted constants from :data:`PROVIDERS` —
    never derived from caller input. Caller-supplied values reach the phase
    scripts only through per-command environment variables.
    """

    name: str
    # Self-guarding install command (``command -v <cli> || …``) so it is a no-op
    # when the CLI is already present (prebuilt template) and installs otherwise
    # (base image / self-hosted). Trusted constant; never caller-derived.
    install_cmd: str
    run_cmd: str
    # Env vars the CLI reads its API key from, scoped to the agent phase only.
    # A tuple because some CLIs (codex) accept more than one name.
    api_key_envs: tuple[str, ...]
    # E2B prebuilt template with this CLI preinstalled (E2B cloud only; ignored
    # on self-hosted backends, where the install command runs instead).
    default_template: str
    # Env var the CLI reads a model override from; None if unsupported.
    model_env: str | None = None
    # Env var pointing the CLI at an LLM gateway/proxy; None if unsupported.
    base_url_env: str | None = None
    # LiteLLM provider namespace + base-URL path suffix for the credential-proxy
    # mode (e.g. codex/OpenAI clients expect a "/v1" base). Empty suffix for the
    # Anthropic (Claude Code) shape.
    proxy_llm_namespace: str | None = None
    proxy_base_suffix: str = ""
    # Env var the CLI reads a literal "Name: value" request header from (used by
    # the "header" proxy transport to send E2B's traffic-access token).
    proxy_header_env: str | None = None
    # How the CLI reaches a private credential proxy (see proxy_agent_setup):
    #   "header"  — env var (proxy_header_env) carries the traffic-access header
    #   "codex"   — a ~/.codex/config.toml model_provider with env_http_headers
    #   "opencode"— an openai-compatible provider block with an options.headers
    #   "public"  — no header support; the proxy must be world-reachable (gated by
    #               the virtual key only). All built-in providers avoid this.
    proxy_transport: str = "public"
    # Multi-provider CLI (opencode): the configured model's provider prefix
    # (e.g. "deepseek" in "deepseek/deepseek-chat") selects both which standard
    # API key env var the CLI reads and which global *_API_KEY setting we fall
    # back to. The model is passed as a --model flag (via SEIZU_AGENT_MODEL)
    # rather than a provider-specific model env var.
    model_selects_key: bool = False


# opencode prompts for edit/bash approval by default, which would stall a
# headless run. This project config grants blanket approval (the sandbox
# isolation is the safety boundary, as with claude's --dangerously-skip-
# permissions). Kept out of any PR via .git/info/exclude at the call site.
# Defined as a plain (non-f) string so its braces don't collide with .format().
_OPENCODE_CONFIG_JSON = '{"$schema":"https://opencode.ai/config.json","permission":"allow"}'


# For opencode, map a model's provider prefix to (env var the CLI reads, the
# global settings attribute we fall back to — shared with the chat assistant,
# so an operator who already configured DeepSeek/etc. for chat needs no new key).
_OPENCODE_MODEL_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_API_KEY"),
    "google": ("GEMINI_API_KEY", "GEMINI_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
}


PROVIDERS: dict[str, SubagentProvider] = {
    "claude": SubagentProvider(
        name="claude",
        install_cmd=(
            "command -v claude >/dev/null 2>&1 || "
            "npm install -g @anthropic-ai/claude-code || sudo npm install -g @anthropic-ai/claude-code"
        ),
        # stream-json keeps output flowing while the agent works (heartbeats);
        # plain text would stay silent until the very end of the run.
        run_cmd=(
            f'claude -p "$(cat {PROMPT_PATH})" --dangerously-skip-permissions --output-format stream-json --verbose'
        ),
        api_key_envs=("ANTHROPIC_API_KEY",),
        default_template="claude",
        model_env="ANTHROPIC_MODEL",
        base_url_env="ANTHROPIC_BASE_URL",
        proxy_llm_namespace="anthropic",  # LiteLLM serves the Anthropic /v1/messages shape
        proxy_header_env="ANTHROPIC_CUSTOM_HEADERS",  # lets the proxy stay non-public
        proxy_transport="header",
    ),
    "codex": SubagentProvider(
        name="codex",
        install_cmd=(
            "command -v codex >/dev/null 2>&1 || npm install -g @openai/codex || sudo npm install -g @openai/codex"
        ),
        run_cmd=f'codex exec --full-auto "$(cat {PROMPT_PATH})"',
        # The codex CLI reads OPENAI_API_KEY; E2B's prebuilt template documents
        # CODEX_API_KEY. Set both so either resolution path works.
        api_key_envs=("OPENAI_API_KEY", "CODEX_API_KEY"),
        default_template="codex",
        base_url_env="OPENAI_BASE_URL",
        proxy_llm_namespace="openai",  # OpenAI clients expect a /v1 base
        proxy_base_suffix="/v1",
        proxy_transport="codex",
    ),
    "opencode": SubagentProvider(
        name="opencode",
        install_cmd=(
            "command -v opencode >/dev/null 2>&1 || npm install -g opencode-ai || sudo npm install -g opencode-ai"
        ),
        # Write the blanket-approval config (project opencode.json, read from the
        # working directory), exclude it from any PR via .git/info/exclude, then
        # run. `opencode run` is the non-interactive/headless mode; --model picks
        # the provider/model (e.g. deepseek/deepseek-chat), read from an env var
        # so the operator-configured model never lands in the .format() call.
        run_cmd=(
            "cat > opencode.json <<'OPENCODE_EOF'\n"
            f"{_OPENCODE_CONFIG_JSON}\n"
            "OPENCODE_EOF\n"
            "printf '%s\\n' 'opencode.json' '.opencode/' >> .git/info/exclude\n"
            f'opencode run --model "$SEIZU_AGENT_MODEL" "$(cat {PROMPT_PATH})"'
        ),
        # Key env is derived from the model provider prefix (see model_selects_key),
        # so no static list applies here.
        api_key_envs=(),
        default_template="opencode",
        model_selects_key=True,
        # Proxy support is via a written openai-compatible provider config; the
        # LiteLLM namespace is derived from the model prefix at runtime (see
        # proxy_namespace), so no static proxy_llm_namespace here.
        proxy_base_suffix="/v1",
        proxy_transport="opencode",
    ),
}


_AGENT_SCRIPT_TEMPLATE = """\
set -euo pipefail
export PATH="$PATH:/usr/local/bin:$HOME/.local/bin:$(npm prefix -g 2>/dev/null)/bin"
cd {workdir}
{run_cmd}
"""


def agent_run_script(provider: SubagentProvider, workdir: str) -> str:
    """The agent phase: cd into ``workdir`` and run the provider's CLI."""
    return _AGENT_SCRIPT_TEMPLATE.format(workdir=workdir, run_cmd=provider.run_cmd)


def resolve_provider() -> SubagentProvider | None:
    """The configured provider, or None if ``SANDBOX_AGENT_PROVIDER`` is unknown."""
    from reporting import settings

    return PROVIDERS.get(settings.SANDBOX_AGENT_PROVIDER)


def resolve_key_envs_and_fallback(provider: SubagentProvider) -> tuple[tuple[str, ...], str, str | None]:
    """Return ``(env var names to set, global fallback key, error)``.

    For single-provider CLIs (claude/codex) the env vars are static and the
    fallback is the global setting matching one of them (``ANTHROPIC_API_KEY`` for
    claude, ``OPENAI_API_KEY`` for codex). For opencode the configured model's
    provider prefix selects the env var and the matching global ``*_API_KEY``
    fallback; a missing or unsupported model is an error.
    """
    from reporting import settings

    if not provider.model_selects_key:
        # Fall back to the global provider key matching one of the CLI's key env
        # vars (e.g. codex → OPENAI_API_KEY), not just ANTHROPIC_API_KEY.
        fallback = next((v for env in provider.api_key_envs if (v := getattr(settings, env, ""))), "")
        return provider.api_key_envs, fallback, None

    model = settings.SANDBOX_AGENT_MODEL.strip()
    if not model:
        return (), "", f"{provider.name} requires SANDBOX_AGENT_MODEL (e.g. deepseek/deepseek-chat)"
    prefix = model.split("/", 1)[0].lower()
    mapping = _OPENCODE_MODEL_PROVIDERS.get(prefix)
    if mapping is None:
        return (), "", f"model provider {prefix!r} is not supported for {provider.name}"
    key_env, fallback_attr = mapping
    return (key_env,), getattr(settings, fallback_attr, ""), None


def resolve_template(provider: SubagentProvider) -> str | None:
    """Return the E2B template for this provider, or None to use the base image.

    ``SANDBOX_AGENT_TEMPLATE`` empty → the provider's official prebuilt template
    (recommended: CLI preinstalled, no per-run npm postinstall). A template name
    → that template. The literal ``none`` → the base image (the install command
    installs the CLI). Self-hosted backends ignore templates regardless (see
    :func:`open_backend`).
    """
    from reporting import settings

    configured = settings.SANDBOX_AGENT_TEMPLATE.strip()
    if configured.lower() == "none":
        return None
    return configured or provider.default_template


def build_agent_env(
    provider: SubagentProvider, key_envs: tuple[str, ...], key_value: str, base_url: str | None
) -> dict[str, str]:
    """Env for the agent phase: API key (under every name the CLI reads), model,
    and optional base URL — never any GitHub or sandbox-provider secret."""
    from reporting import settings

    env = {e: key_value for e in key_envs}
    if provider.model_selects_key:
        # opencode reads the model from the --model flag (SEIZU_AGENT_MODEL in run_cmd).
        env["SEIZU_AGENT_MODEL"] = settings.SANDBOX_AGENT_MODEL.strip()
    elif settings.SANDBOX_AGENT_MODEL and provider.model_env:
        env[provider.model_env] = settings.SANDBOX_AGENT_MODEL
    if base_url and provider.base_url_env:
        env[provider.base_url_env] = base_url
    return env


async def mint_agent_api_key(fallback: str) -> str:
    """Return the agent API key for one run.

    When ``SANDBOX_AGENT_API_KEY_COMMAND`` is set, run it and use its stdout —
    this is how operators hand the sandbox a *short-lived* per-run credential from
    a broker (Vault, an LLM-gateway virtual-key issuer, …) instead of the
    long-lived key attached to the Seizu process. Otherwise use the static
    ``SANDBOX_AGENT_API_KEY``, or the provider/model ``fallback`` global key.
    """
    from reporting import settings

    command = settings.SANDBOX_AGENT_API_KEY_COMMAND
    if command:
        # Shell is intentional: the command is operator-configured and often a
        # pipeline (e.g. `vault read … | jq -r .token`).
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            # Broker stderr can carry sensitive context — log it server-side only
            # and surface a generic error (it flows into the workflow result).
            logger.warning(
                "SANDBOX_AGENT_API_KEY_COMMAND failed (exit %s): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip()[:200],
            )
            raise RuntimeError(f"SANDBOX_AGENT_API_KEY_COMMAND failed with exit code {proc.returncode}")
        key = stdout.decode(errors="replace").strip()
        if not key:
            raise RuntimeError("SANDBOX_AGENT_API_KEY_COMMAND produced no output")
        return key
    return settings.SANDBOX_AGENT_API_KEY or fallback


_warned_static_key = False


def warn_long_lived_agent_key() -> None:
    """Warn once that the coding agent runs with a long-lived API key.

    The agent needs its provider key while it runs untrusted repo code/tests with
    internet on, so a static key is stealable. The production path is a
    short-lived per-run key via ``SANDBOX_AGENT_API_KEY_COMMAND`` (optionally
    through a gateway with ``SANDBOX_AGENT_BASE_URL``, or the credential proxy);
    warn when a static key is used instead.
    """
    global _warned_static_key
    if _warned_static_key:
        return
    _warned_static_key = True
    logger.warning(
        "The sandbox agent runs with a long-lived API key exposed to untrusted "
        "repo code. Prefer SANDBOX_AGENT_API_KEY_COMMAND for a short-lived per-run key.",
        extra={"type": "AUDIT"},
    )


def agent_config_error() -> str | None:
    """Return why the sandbox agent cannot run under the current settings, or None.

    Validates only the provider + credential configuration; callers add their own
    checks (e.g. the remediation flow also requires a GitHub token).
    """
    from reporting import settings

    provider = resolve_provider()
    if provider is None:
        return f"unknown sandbox agent provider {settings.SANDBOX_AGENT_PROVIDER!r}"
    _key_envs, fallback, err = resolve_key_envs_and_fallback(provider)
    if err is not None:
        return err
    if settings.SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED:
        # Proxy mode seeds a LiteLLM sandbox with the REAL key and hands the agent
        # a virtual key — so it needs a real static/global key (not the key
        # command), a routable LiteLLM namespace, and no external base URL.
        if proxy_namespace(provider) is None:
            return f"credential proxy is not supported for provider {provider.name!r}"
        if settings.SANDBOX_AGENT_BASE_URL:
            return "SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED is mutually exclusive with SANDBOX_AGENT_BASE_URL"
        if not (settings.SANDBOX_AGENT_API_KEY or fallback):
            return "credential proxy needs a real key (SANDBOX_AGENT_API_KEY or the global provider key)"
    elif not (settings.SANDBOX_AGENT_API_KEY or settings.SANDBOX_AGENT_API_KEY_COMMAND or fallback):
        return "no API key configured for the sandbox agent (SANDBOX_AGENT_API_KEY or SANDBOX_AGENT_API_KEY_COMMAND)"
    return None


def proxy_namespace(provider: SubagentProvider) -> str | None:
    """The LiteLLM provider namespace the proxy routes its wildcard model to, or
    None when the proxy doesn't apply. Static for claude/codex; for opencode it is
    the configured model's provider prefix (e.g. ``deepseek``)."""
    from reporting import settings

    if not provider.model_selects_key:
        return provider.proxy_llm_namespace
    prefix = settings.SANDBOX_AGENT_MODEL.strip().split("/", 1)[0].lower()
    return prefix or None


def use_credential_proxy(provider: SubagentProvider) -> bool:
    """Whether the credential proxy applies for this provider under the settings."""
    from reporting import settings

    return settings.SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED and proxy_namespace(provider) is not None


def tail_bytes(text: str, max_bytes: int) -> str:
    """Cap text to its *last* ``max_bytes`` UTF-8 bytes (the summary/PR URL end)."""
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return "[truncated]\n" + encoded[-max_bytes:].decode(errors="replace")


def _codex_proxy_config(base_url: str) -> str:
    """A ~/.codex/config.toml selecting a model_provider that points at the proxy
    and reads the E2B traffic token as a header from the env (never on disk)."""
    return (
        f'model_provider = "{_PROXY_PROVIDER_ID}"\n\n'
        f"[model_providers.{_PROXY_PROVIDER_ID}]\n"
        f'name = "{_PROXY_PROVIDER_ID}"\n'
        f'base_url = "{base_url}"\n'
        'env_key = "OPENAI_API_KEY"\n'
        f'env_http_headers = {{ "{_E2B_TRAFFIC_HEADER}" = "{_PROXY_ACCESS_TOKEN_ENV}" }}\n'
    )


def _opencode_proxy_config(base_url: str, vkey: str, access_token: str, model: str) -> str:
    """An opencode config registering an openai-compatible provider that points at
    the proxy, carries the traffic token as a header, and keeps blanket approval."""
    return json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "permission": "allow",
            "provider": {
                _PROXY_PROVIDER_ID: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": _PROXY_PROVIDER_ID,
                    "options": {
                        "baseURL": base_url,
                        "apiKey": vkey,
                        "headers": {_E2B_TRAFFIC_HEADER: access_token},
                    },
                    "models": {model: {}},
                }
            },
        }
    )


def proxy_agent_setup(
    provider: SubagentProvider, key_envs: tuple[str, ...], base_url: str, agent_key: str, access_token: str
) -> ProxyAgentSetup:
    """Translate a running proxy into the agent phase's env + config files, per the
    provider's ``proxy_transport``. ``agent_key`` is the (ephemeral) key the CLI
    authenticates to the proxy with; the traffic-access token additionally gates a
    private proxy — via a request header the CLI sends (env var or config)."""
    from reporting import settings

    if provider.proxy_transport == "header":
        env = build_agent_env(provider, key_envs, agent_key, base_url)
        if provider.proxy_header_env:
            env[provider.proxy_header_env] = f"{_E2B_TRAFFIC_HEADER}: {access_token}"
        return ProxyAgentSetup(env=env)
    if provider.proxy_transport == "codex":
        env = {e: agent_key for e in key_envs} | {_PROXY_ACCESS_TOKEN_ENV: access_token}
        return ProxyAgentSetup(env=env, files={_CODEX_CONFIG_PATH: _codex_proxy_config(base_url)})
    if provider.proxy_transport == "opencode":
        # Send the BARE model ("deepseek/deepseek-chat" → "deepseek-chat"): the
        # LiteLLM wildcard route ("deepseek/*") re-adds the namespace, so passing
        # the full id would reach the provider API as "deepseek/deepseek-chat".
        model = settings.SANDBOX_AGENT_MODEL.strip()
        bare_model = model.split("/", 1)[1] if "/" in model else model
        config = _opencode_proxy_config(base_url, agent_key, access_token, bare_model)
        # Route opencode at the custom provider ("<provider>/<bare model>"); the
        # CLI reads the key/header from the config above, so no key env is needed.
        return ProxyAgentSetup(
            env={"SEIZU_AGENT_MODEL": f"{_PROXY_PROVIDER_ID}/{bare_model}"},
            files={_OPENCODE_CONFIG_PATH: config},
        )
    # "public" — no header support; the proxy is world-reachable, gated by the key.
    return ProxyAgentSetup(env=build_agent_env(provider, key_envs, agent_key, base_url))


@asynccontextmanager
async def credential_proxy(
    *,
    provider: SubagentProvider,
    real_key: str,
    budget: str,
    sandbox_timeout_seconds: int,
    run_phase: PhaseRunner,
    mask_secrets: list[str],
) -> AsyncIterator[tuple[str, str, str]]:
    """Open a *separate* sandbox running a LiteLLM proxy seeded with the real
    provider key, and yield ``(base_url, agent_key, access_token)`` for the agent
    (pass these to :func:`proxy_agent_setup`). The real key never enters the agent
    sandbox; ``agent_key`` is the proxy's ephemeral master key, which dies when
    this sandbox is torn down, and the proxy enforces an in-memory spend cap.
    ``access_token`` is E2B's traffic-access token for a private proxy (``""`` for
    the public fallback). Secrets it creates (master key, traffic token) are
    appended to ``mask_secrets`` so the caller redacts them from its transcript."""
    from reporting import settings

    master_key = "sk-seizu-" + secrets.token_urlsafe(24)
    mask_secrets.append(master_key)
    start = (
        "set -euo pipefail\n"
        f"nohup litellm --config {LITELLM_CONFIG_PATH} --port {_PROXY_PORT} > /home/user/litellm.log 2>&1 &\n"
        f"for i in $(seq 1 60); do curl -sf http://localhost:{_PROXY_PORT}/health/liveliness "
        ">/dev/null 2>&1 && break; sleep 2; done\n"
        f"curl -sf http://localhost:{_PROXY_PORT}/health/liveliness >/dev/null 2>&1 "
        "|| { echo SEIZU_PROXY_UNHEALTHY; cat /home/user/litellm.log; exit 1; }\n"
    )
    # All built-in providers have a header/config transport, so the proxy stays
    # private (gated by E2B's traffic-access token); "public" is a fallback only.
    private = provider.proxy_transport != "public"
    async with open_backend(
        api_key=settings.SANDBOX_API_KEY,
        domain=settings.SANDBOX_DOMAIN,
        allow_internet=True,
        timeout_seconds=sandbox_timeout_seconds,
        allow_public_traffic=not private,
    ) as proxy:
        config = _LITELLM_CONFIG.format(namespace=proxy_namespace(provider), budget=budget)
        await proxy.write_file(LITELLM_CONFIG_PATH, config)
        proxy_env = {"PROXY_REAL_KEY": real_key, "PROXY_MASTER_KEY": master_key}
        await run_phase(proxy, "proxy_install", _PROXY_INSTALL, {}, timeout_seconds=_PROXY_TIMEOUT_SECONDS)
        await run_phase(proxy, "proxy_start", start, proxy_env, timeout_seconds=_PROXY_TIMEOUT_SECONDS)
        host = await proxy.get_host(_PROXY_PORT)

        access_token = ""
        if private:
            access_token = await proxy.get_traffic_access_token()
            if not access_token:
                raise RuntimeError("proxy sandbox exposed no traffic-access token for the private proxy")
            mask_secrets.append(access_token)
        yield f"https://{host}{provider.proxy_base_suffix}", master_key, access_token
