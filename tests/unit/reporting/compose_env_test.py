"""The composed deployment has to carry the settings its services read.

A setting that exists in ``settings.py`` but never reaches the container that
acts on it is indistinguishable from a setting that does not work: the operator
sets it, nothing changes, and nothing says why. Session reaping is the case that
went wrong -- it runs in the Temporal worker, whose ``environment`` block did
not list any of it.
"""

import pathlib
import re

_COMPOSE = pathlib.Path(__file__).resolve().parents[3] / "docker-compose.yml"

# Where the sweep runs; nothing else reads these.
_REAPER_SETTINGS = (
    "CHAT_SESSION_REAP_ENABLED",
    "CHAT_SESSION_REAP_IDLE_SECONDS",
    "CHAT_SESSION_REAP_INTERVAL_SECONDS",
    "SANDBOX_REAP_UNTAGGED",
    "SANDBOX_SESSION_TIMEOUT_SECONDS",
)


def _service_environment(service: str) -> set[str]:
    """Env var names in one service's block, without a YAML dependency.

    Read as text on purpose: this asserts on what the file says, and a parser
    would happily normalize away the difference between a variable that is
    passed through and one that is missing.
    """
    text = _COMPOSE.read_text()
    start = text.index(f"\n  {service}:\n")
    rest = text[start + 1 :]
    end = re.search(r"\n  [a-z0-9-]+:\n", rest)
    block = rest[: end.start()] if end else rest
    env_start = block.index("\n    environment:\n")
    env_block = block[env_start + 1 :]
    env_end = re.search(r"\n    [a-z_]+:", env_block)
    env_lines = (env_block[: env_end.start()] if env_end else env_block).splitlines()
    names: set[str] = set()
    for line in env_lines:
        entry = line.strip()
        if not entry.startswith("- "):
            continue
        names.add(entry[2:].split("=", 1)[0].strip())
    return names


def test_the_worker_receives_every_reaper_setting() -> None:
    """The sweep runs only here, so this is the one service that must have them."""
    env = _service_environment("seizu-temporal-worker")
    assert set(_REAPER_SETTINGS) <= env


def test_both_sandbox_creating_services_agree_on_the_deployment_id() -> None:
    """Interactive chat creates the sandboxes the worker reaps. If the id is set
    in one service and not the other, every sandbox is tagged for a deployment
    the reaper does not recognize, and nothing is ever collected."""
    for service in ("seizu", "seizu-temporal-worker"):
        assert "SEIZU_DEPLOYMENT_ID" in _service_environment(service), service
