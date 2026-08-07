"""Build the E2B sandbox template the credential proxy runs on.

Without a template, every remediation run that uses the credential proxy
(``SANDBOX_AGENT_CREDENTIAL_PROXY_ENABLED``) installs LiteLLM's whole proxy
dependency tree from PyPI before the proxy can boot. This bakes **the same
requirement set the run would install** into a reusable template, after which
runs skip the install entirely.

Measured on E2B cloud, proxy sandbox create → serving: ~33s from the base image
vs ~12s from a template. The bigger win is that a run no longer depends on PyPI
resolving correctly while it is running — which is what broke it.

    make build_proxy_template                      # name from the settings/default
    make build_proxy_template TEMPLATE_NAME=my-proxy

Then point the workers at it::

    SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE=seizu-litellm-proxy

The build imports ``litellm.proxy.proxy_server`` as its last step, so a
requirement set that cannot actually serve fails here rather than during a
remediation run — that import is exactly what broke when the requirements were
unpinned.

Runs with a template configured install **nothing** — the image is used as built.
That keeps the two setups separate concerns (you lock the requirements and bake
an image; templateless runs do a best-effort install instead), and it means a
template is only as current as the last build: nothing at run time re-checks it,
so re-run this after every `make lock_proxy_requirements`.

Requires ``SANDBOX_API_KEY`` (E2B). Templates are an E2B-cloud feature — on a
self-hosted backend (``SANDBOX_DOMAIN`` set) sandbox creation ignores templates,
so there is nothing to build.
"""

import os
import sys
from pathlib import Path
from typing import Any

# Work both as a module (python -m scripts.build_proxy_template) and as a plain
# script: the workspace package is not pip-installed in the dev image.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting import settings  # noqa: E402
from reporting.services import sandbox_agent  # noqa: E402

DEFAULT_TEMPLATE_NAME = "seizu-litellm-proxy"


def _fail(message: str) -> int:
    print(f"TEMPLATE BUILD FAILED: {message}", file=sys.stderr)
    return 2


def _build() -> int:
    from e2b import Template, default_build_logger

    if not settings.SANDBOX_API_KEY:
        return _fail("SANDBOX_API_KEY is not configured")
    if settings.SANDBOX_DOMAIN:
        return _fail(
            f"SANDBOX_DOMAIN is set ({settings.SANDBOX_DOMAIN}) — templates are an E2B-cloud feature and "
            "self-hosted sandboxes ignore them; the run-time install phase covers those"
        )
    # The template is only as good as the pins it is built from, so hold them to
    # the same rule the run does.
    if (invalid := sandbox_agent.proxy_requirements_error()) is not None:
        return _fail(invalid)
    requirements = sandbox_agent.proxy_requirements()

    name = (
        os.environ.get("TEMPLATE_NAME", "").strip()
        or settings.SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE.strip()
        or DEFAULT_TEMPLATE_NAME
    )
    print(f"Building E2B template {name!r} with: {' '.join(requirements)}\n")

    # Build from the same plan the run uses, so the template cannot contain a
    # different dependency set than a run would install — the hash-locked file
    # by default, the bare pins when an operator configured their own.
    plan = sandbox_agent.proxy_install_plan()
    lock = Path(sandbox_agent.PROXY_LOCK_PATH)
    builder = Template(file_context_path=str(lock.parent)).from_base_image()
    if plan.locked:
        print(f"Using the hash-locked requirement set ({lock.name}).")
        builder = builder.copy(lock.name, sandbox_agent._PROXY_LOCK_SANDBOX_PATH).run_cmd(
            sandbox_agent.PROXY_LOCKED_INSTALL_CMD
        )
    else:
        print("No hash lock for these requirements — installing the top-level pins only.")
        builder = builder.pip_install(requirements)
    template = (
        # Build-time proof that these pins can actually serve — the same check
        # the run's install phase makes, moved to where a failure is cheap.
        builder.run_cmd(sandbox_agent.PROXY_IMPORT_CHECK)
    )
    # The SDK would read E2B_API_KEY from the environment; pass the configured
    # key explicitly so the template is built against the same account the runs
    # use, whatever the shell happens to export.
    info: Any = Template.build(
        template, name=name, on_build_logs=default_build_logger(), api_key=settings.SANDBOX_API_KEY
    )
    print(f"\nBuilt template {name!r} (build {getattr(info, 'build_id', '?')})")
    print(f"Set SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE={name} on the temporal worker to use it.")
    return 0


def main() -> None:
    raise SystemExit(_build())


if __name__ == "__main__":
    main()
