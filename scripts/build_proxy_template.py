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
    # The template is only as good as the lock it is built from, so hold it to
    # the same rule a templateless run does.
    if (invalid := sandbox_agent.proxy_lock_error()) is not None:
        return _fail(invalid)
    plan = sandbox_agent.proxy_install_plan()
    if plan is None:  # unreachable: proxy_lock_error() just passed
        return _fail("the credential proxy requirement lock is unusable")

    name = (
        os.environ.get("TEMPLATE_NAME", "").strip()
        or settings.SANDBOX_AGENT_CREDENTIAL_PROXY_TEMPLATE.strip()
        or DEFAULT_TEMPLATE_NAME
    )
    lock = Path(plan.lock.path)
    print(
        f"Building E2B template {name!r} from {lock.name}: {' '.join(plan.lock.requirements)} "
        f"(python {plan.lock.python}, {plan.lock.machine})\n"
    )
    # Build from the lock the run would install, so a template built by this
    # script cannot contain a different dependency set than a templateless run.
    #
    # …and from the lock's own python, not e2bdev/base: a lock's hashes cover one
    # ABI, and the base image (3.11) is not the same interpreter as the E2B
    # default sandbox (3.13) the lock targets. Deriving the image from the lock
    # is what keeps a template and a templateless run on the same runtime.
    template = (
        Template(file_context_path=str(lock.parent))
        .from_python_image(plan.lock.python)
        .copy(lock.name, sandbox_agent._PROXY_LOCK_SANDBOX_PATH)
        .run_cmd(sandbox_agent.PROXY_LOCKED_INSTALL_CMD)
        # Build-time proof that the set can actually serve — the same check the
        # run's install phase makes, moved to where a failure is cheap.
        .run_cmd(sandbox_agent.PROXY_IMPORT_CHECK)
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
