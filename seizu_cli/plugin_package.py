"""Build Agent Plugin ZIP uploads from local package sources."""

import io
import zipfile
from pathlib import Path
from typing import Any

#: Seizu's own extension namespace inside an Agent Plugin manifest.
SEIZU_EXTENSION = "com.mappedsky.seizu"

#: Marks a package Seizu serialized from a legacy skillset rather than one an
#: author wrote. Mirrors ``LEGACY_PROJECTION_EXTENSION_KEY`` in
#: reporting/services/plugin_packages.py.
LEGACY_PROJECTION_KEY = "legacySkillsetProjection"


def is_legacy_projection(manifest: dict[str, Any]) -> bool:
    """Whether a package manifest belongs to the legacy skillset projection."""
    extension = (manifest or {}).get("extensions", {}).get(SEIZU_EXTENSION, {})
    return isinstance(extension, dict) and extension.get(LEGACY_PROJECTION_KEY) is True


def build_plugin_package(source: Path) -> tuple[str, bytes]:
    """Return an upload filename and ZIP bytes for a package directory or ZIP."""
    if source.is_file():
        return source.name, source.read_bytes()
    if not source.is_dir():
        raise ValueError("source must be a plugin directory or ZIP file")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symbolic links are unsupported: {path}")
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    return f"{source.name}.zip", output.getvalue()
