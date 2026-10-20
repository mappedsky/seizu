"""Lint has to type-check the tree against the libraries the app runs.

``mypy``'s pre-commit hook resolves its own environment from
``additional_dependencies``, independently of ``uv.lock``. An entry left
unpinned there resolves to whatever is newest each time that environment is
built -- which the linter job does on every run, holding no pre-commit cache --
so the version can move with no commit anywhere in the repository, and lint
then reports on a library the app does not install. These assert the two
properties that prevent it: every entry is pinned, and the ``pydantic`` pin is
the version ``uv.lock`` resolves.
"""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_PRE_COMMIT = _ROOT / ".pre-commit-config.yaml"
_UV_LOCK = _ROOT / "uv.lock"


def _mypy_additional_dependencies() -> list[str]:
    """The mypy hook's declared dependencies, read as text.

    Deliberately not parsed as YAML: this asserts on what the file says, and
    the repository does not depend on a YAML reader for its own lint config.
    """
    text = _PRE_COMMIT.read_text()
    start = text.index("mirrors-mypy")
    rest = text[start:]
    end = re.search(r"\n-   repo:", rest)
    block = rest[: end.start()] if end else rest
    deps_start = block.index("additional_dependencies:")
    return re.findall(r"^\s+-\s+(\S+)$", block[deps_start:], flags=re.MULTILINE)


def _locked_version(package: str) -> str:
    text = _UV_LOCK.read_text()
    match = re.search(
        rf'^name = "{re.escape(package)}"\nversion = "([^"]+)"$',
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"{package} is not resolved in uv.lock"
    return match.group(1)


def test_mypy_hook_dependencies_are_pinned() -> None:
    deps = _mypy_additional_dependencies()
    assert deps, "the mypy hook declares no additional dependencies"
    unpinned = [dep for dep in deps if "==" not in dep]
    assert not unpinned, f"pin these mypy hook dependencies to an exact version: {unpinned}"


def test_mypy_hook_pydantic_matches_the_lockfile() -> None:
    pins = dict(dep.split("==", 1) for dep in _mypy_additional_dependencies())
    assert pins.get("pydantic") == _locked_version("pydantic"), (
        "the mypy hook's pydantic pin and uv.lock disagree; a pydantic bump has "
        "to move both, or lint type-checks against a version the app does not run"
    )
