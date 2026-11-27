"""The ``seizu-sandbox/1`` protocol: what a sandbox image must serve.

A provider that runs a container Seizu does not build (Amazon Bedrock AgentCore
Runtime) reaches it through this protocol: one JSON request per operation,
``POST /invocations``, one JSON response back. These models are the single
source of truth for it -- ``python -m reporting.schema.cli export-sandbox-protocol``
renders them as the JSON Schema an image repository builds against, and
``scripts/sandbox_image_conformance.py`` checks an image against the same
contract. The normative prose (environment, ``/ping``, versioning) is
``docs/root/dev/sandbox-image-protocol.md``.

Compatibility rules:

- Responses are parsed leniently: an image may add fields, and Seizu ignores
  what it does not know. Requests are the same in the other direction -- an
  image must ignore request fields it does not know.
- Anything that would break an existing client is a new protocol version,
  announced in :class:`HelloResult.protocol_versions`. Seizu refuses an image
  that does not list :data:`PROTOCOL_VERSION`.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: The protocol identifier every request carries.
PROTOCOL = "seizu-sandbox/1"

#: The protocol version this client speaks; an image must list it in ``hello``.
PROTOCOL_VERSION = 1

#: File, relative to ``$HOME``, holding the :class:`SandboxMarker` as JSON.
#: Written by ``hello`` when asked, read back by every later ``hello``; its
#: absence on a sandbox Seizu created means the storage behind it was lost.
MARKER_FILENAME = ".seizu-sandbox"

#: Where the image's persistent storage is mounted, and where ``$HOME`` must
#: resolve to. Fixed rather than negotiated: every path Seizu writes is under
#: ``/home/user``, and this is what makes those paths persistent.
WORKSPACE_MOUNT = "/mnt/workspace"
HOME = "/home/user"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Op(StrEnum):
    """Every operation an image must implement."""

    HELLO = "hello"
    RUN_PYTHON = "run_python"
    RUN_BASH = "run_bash"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_FILES = "list_files"
    WIPE = "wipe"


class ErrorCode(StrEnum):
    """Why an operation failed, as opposed to what the code it ran printed.

    A Python exception or a non-zero shell exit is a *successful* operation
    reporting an outcome; these codes are for the operation itself failing.
    """

    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNSUPPORTED_OP = "unsupported_op"
    INVALID_ARGS = "invalid_args"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class SandboxMarker(_Lenient):
    """Who created a sandbox and for what. Written once, at creation.

    ``created_at`` is supplied by Seizu, never generated in the image: a
    snapshotted container restores the same clock reading into every session.
    """

    deployment: str = Field(description="SEIZU_DEPLOYMENT_ID of the installation that created the sandbox.")
    purpose: str = Field(default="", description="What the sandbox is for; diagnostics only.")
    thread: str = Field(default="", description="Namespaced chat thread (user:<id>:thread:<id>), or empty.")
    created_at: str = Field(description="ISO 8601 UTC timestamp of creation.")


class HelloArgs(_Lenient):
    create_marker: SandboxMarker | None = Field(
        default=None,
        description="Write this marker if none exists yet. An existing marker is never overwritten.",
    )


class HelloResult(_Lenient):
    protocol_versions: list[int] = Field(description="Every protocol version the image speaks.")
    capabilities: list[str] = Field(default_factory=list, description="Optional features; none are defined in v1.")
    home: str = Field(description="The resolved $HOME. Must be inside the workspace mount.")
    storage_semantics: Literal["posix", "object"] = Field(
        default="posix",
        description=(
            "'object' when the workspace is an object-store mount that cannot append, edit in place, "
            "rename or symlink; 'posix' otherwise."
        ),
    )
    marker: SandboxMarker | None = Field(description="The marker after this call, or null if none exists.")


class RunPythonArgs(_Lenient):
    code: str


class PythonError(_Lenient):
    name: str
    value: str = ""
    traceback: str = ""


class RunPythonResult(_Lenient):
    stdout: str = ""
    stderr: str = ""
    text: str = Field(default="", description="repr() of a trailing expression, as a notebook cell shows it.")
    error: PythonError | None = None
    restarted: bool = Field(
        default=False,
        description="True when the interpreter was restarted by this call (timeout), losing its state.",
    )


class RunBashArgs(_Lenient):
    command: str
    cwd: str | None = Field(default=None, description="Working directory; $HOME when omitted.")


class RunBashResult(_Lenient):
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False


class ReadFileArgs(_Lenient):
    path: str


class ReadFileResult(_Lenient):
    content_b64: str
    size: int


class WriteFileArgs(_Lenient):
    path: str
    content_b64: str


class WriteFileResult(_Lenient):
    size: int


class ListFilesArgs(_Lenient):
    path: str


class FileEntry(_Lenient):
    name: str
    type: Literal["file", "dir"]


class ListFilesResult(_Lenient):
    entries: list[FileEntry]


class WipeArgs(_Lenient):
    pass


class WipeResult(_Lenient):
    removed: int = Field(default=0, description="Top-level entries removed from the workspace.")


#: Argument and result model for each operation.
OPS: dict[Op, tuple[type[BaseModel], type[BaseModel]]] = {
    Op.HELLO: (HelloArgs, HelloResult),
    Op.RUN_PYTHON: (RunPythonArgs, RunPythonResult),
    Op.RUN_BASH: (RunBashArgs, RunBashResult),
    Op.READ_FILE: (ReadFileArgs, ReadFileResult),
    Op.WRITE_FILE: (WriteFileArgs, WriteFileResult),
    Op.LIST_FILES: (ListFilesArgs, ListFilesResult),
    Op.WIPE: (WipeArgs, WipeResult),
}


class SandboxRequest(_Lenient):
    protocol: Literal["seizu-sandbox/1"] = "seizu-sandbox/1"
    op: Op
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0, description="How long the operation may run inside the image.")


class SandboxError(_Lenient):
    code: ErrorCode
    message: str = ""


class SandboxResponse(_Lenient):
    ok: bool
    result: dict[str, Any] | None = None
    error: SandboxError | None = None


def json_schema() -> dict[str, Any]:
    """The whole protocol as one JSON Schema document, for image repositories."""
    defs: dict[str, Any] = {}

    def _ref(model: type[BaseModel]) -> dict[str, str]:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
        return {"$ref": f"#/$defs/{model.__name__}"}

    ops = {op.value: {"args": _ref(args), "result": _ref(result)} for op, (args, result) in OPS.items()}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": PROTOCOL,
        "description": (
            "Request/response contract for a Seizu sandbox image. See docs/root/dev/sandbox-image-protocol.md."
        ),
        "x-seizu-protocol-version": PROTOCOL_VERSION,
        "x-seizu-marker-file": MARKER_FILENAME,
        "x-seizu-home": HOME,
        "x-seizu-workspace-mount": WORKSPACE_MOUNT,
        "x-seizu-ops": ops,
        "oneOf": [_ref(SandboxRequest), _ref(SandboxResponse)],
        "$defs": defs,
    }
