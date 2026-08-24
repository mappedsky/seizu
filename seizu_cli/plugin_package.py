"""Build Agent Plugin ZIP uploads from local package sources."""

import io
import zipfile
from pathlib import Path


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
