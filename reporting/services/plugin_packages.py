"""Load and validate server-side Agent Plugins 1.0.0 packages."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import yaml
from pydantic import ValidationError

from reporting.schema.mcp_config import template_placeholders, validate_skill_template
from reporting.schema.plugins import (
    PluginDiagnostic,
    PluginFile,
    PluginSkillItem,
    PluginValidationResponse,
    SeizuPluginExtension,
)

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
EXTENSION_NAMESPACE = "com.mappedsky.seizu"
LEGACY_PROJECTION_EXTENSION_KEY = "legacySkillsetProjection"

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_BYTES = 25 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SKILL_MD_BYTES = 512 * 1024
MAX_FILES = 500

_PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KNOWN_MANIFEST_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}


@dataclass(frozen=True)
class ParsedPlugin:
    plugin_id: str
    manifest: dict[str, Any]
    files: list[PluginFile]
    skills: list[PluginSkillItem]
    diagnostics: list[PluginDiagnostic]
    package_digest: str

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    def response(self) -> PluginValidationResponse:
        return PluginValidationResponse(
            valid=self.valid,
            plugin_id=self.plugin_id or None,
            manifest=self.manifest or None,
            skills=self.skills,
            diagnostics=self.diagnostics,
            package_digest=self.package_digest or None,
        )


def diagnostic(
    severity: str,
    code: str,
    message: str,
    *,
    path: str | None = None,
    skill: str | None = None,
) -> PluginDiagnostic:
    return PluginDiagnostic(severity=severity, code=code, message=message, path=path, skill=skill)  # type: ignore[arg-type]


def _safe_path(raw: str) -> str | None:
    if not raw or "\\" in raw or "\x00" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def media_type_for(path: str) -> str:
    guessed, _encoding = mimetypes.guess_type(path)
    if guessed:
        return guessed
    if path.endswith((".md", ".txt", ".sh", ".py", ".js", ".ts", ".yaml", ".yml")):
        return "text/plain"
    return "application/octet-stream"


def files_from_zip(data: bytes) -> list[PluginFile]:
    """Extract a bounded, root-relative package from a ZIP byte string."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("archive is not a valid ZIP file") from exc

    files: list[PluginFile] = []
    seen: set[str] = set()
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = _safe_path(info.filename)
        if path is None:
            raise ValueError(f"unsafe package path: {info.filename!r}")
        if path in seen:
            raise ValueError(f"duplicate package path: {path}")
        if info.flag_bits & 0x1:
            raise ValueError(f"encrypted package member is unsupported: {path}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"symbolic links are unsupported: {path}")
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError(f"package member exceeds {MAX_FILE_BYTES} bytes: {path}")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError(f"unpacked package exceeds {MAX_UNPACKED_BYTES} bytes")
        if len(files) >= MAX_FILES:
            raise ValueError(f"package contains more than {MAX_FILES} files")
        content = archive.read(info)
        if len(content) != info.file_size:
            raise ValueError(f"package member size changed while reading: {path}")
        seen.add(path)
        files.append(
            PluginFile(
                path=path,
                content=content,
                media_type=media_type_for(path),
                executable=bool(mode & 0o111),
            )
        )
    return files


def files_from_directory(root: Path) -> list[PluginFile]:
    """Read a bounded package directory without following symbolic links."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("plugin source must be a directory")
    files: list[PluginFile] = []
    total = 0
    for candidate in sorted(root.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"symbolic links are unsupported: {candidate.relative_to(root)}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        if len(files) >= MAX_FILES:
            raise ValueError(f"package contains more than {MAX_FILES} files")
        size = candidate.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"package member exceeds {MAX_FILE_BYTES} bytes: {relative}")
        total += size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError(f"unpacked package exceeds {MAX_UNPACKED_BYTES} bytes")
        files.append(
            PluginFile(
                path=relative,
                content=candidate.read_bytes(),
                media_type=media_type_for(relative),
                executable=bool(candidate.stat().st_mode & 0o111),
            )
        )
    return files


def package_digest(files: list[PluginFile]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value.path):
        blob_digest = hashlib.sha256(item.content).hexdigest()
        digest.update(item.path.encode())
        digest.update(b"\0")
        digest.update(item.media_type.encode())
        digest.update(b"\0")
        digest.update(b"1" if item.executable else b"0")
        digest.update(b"\0")
        digest.update(blob_digest.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _json_file(files: dict[str, PluginFile], path: str) -> tuple[dict[str, Any] | None, str | None]:
    item = files.get(path)
    if item is None:
        return None, f"{path} is missing"
    try:
        value = json.loads(item.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{path} is not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, f"{path} must contain a JSON object"
    return value, None


def _validate_manifest(manifest: dict[str, Any], diagnostics: list[PluginDiagnostic]) -> bool:
    fatal = False
    if manifest.get("$schema") != PLUGIN_SCHEMA:
        diagnostics.append(
            diagnostic("error", "unsupported_plugin_schema", f"$schema must be {PLUGIN_SCHEMA}", path="plugin.json")
        )
        fatal = True
    name = manifest.get("name")
    if not isinstance(name, str) or not _PLUGIN_NAME_RE.fullmatch(name):
        diagnostics.append(
            diagnostic(
                "error", "invalid_plugin_name", "name must satisfy the Agent Plugins 1.0.0 grammar", path="plugin.json"
            )
        )
        fatal = True
    for key in sorted(set(manifest) - _KNOWN_MANIFEST_FIELDS):
        diagnostics.append(
            diagnostic(
                "warning", "unknown_manifest_field", f"Ignoring unknown top-level field {key!r}", path="plugin.json"
            )
        )
    for key in ("version", "description", "homepage", "repository", "license"):
        if key in manifest and not isinstance(manifest[key], str):
            diagnostics.append(
                diagnostic("error", "invalid_manifest_field", f"{key} must be a string", path="plugin.json")
            )
            fatal = True
    if "keywords" in manifest and not (
        isinstance(manifest["keywords"], list) and all(isinstance(item, str) for item in manifest["keywords"])
    ):
        diagnostics.append(
            diagnostic("error", "invalid_manifest_field", "keywords must be an array of strings", path="plugin.json")
        )
        fatal = True
    author = manifest.get("author")
    if author is not None and not (
        isinstance(author, dict)
        and set(author) <= {"name", "email", "url"}
        and all(isinstance(value, str) for value in author.values())
    ):
        diagnostics.append(
            diagnostic(
                "error",
                "invalid_manifest_field",
                "author must contain only string name, email, and url fields",
                path="plugin.json",
            )
        )
        fatal = True
    extensions = manifest.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        diagnostics.append(
            diagnostic("warning", "invalid_extensions", "Ignoring non-object extensions field", path="plugin.json")
        )
    elif isinstance(extensions, dict):
        for namespace, value in extensions.items():
            if not isinstance(value, dict):
                diagnostics.append(
                    diagnostic(
                        "warning",
                        "invalid_extension",
                        f"Ignoring non-object extension {namespace!r}",
                        path="plugin.json",
                    )
                )
    return not fatal


def _parse_seizu_extension(
    manifest: dict[str, Any], diagnostics: list[PluginDiagnostic]
) -> SeizuPluginExtension | None:
    extensions = manifest.get("extensions")
    raw = extensions.get(EXTENSION_NAMESPACE) if isinstance(extensions, dict) else None
    if raw is None:
        diagnostics.append(
            diagnostic(
                "error",
                "missing_seizu_extension",
                f"extensions.{EXTENSION_NAMESPACE}.skillsetId is required",
                path="plugin.json",
            )
        )
        return None
    if not isinstance(raw, dict):
        diagnostics.append(
            diagnostic(
                "error",
                "invalid_seizu_extension",
                f"extensions.{EXTENSION_NAMESPACE} must be an object",
                path="plugin.json",
            )
        )
        return None
    normalized = dict(raw)
    if "skillsetId" in normalized:
        normalized["skillset_id"] = normalized.pop("skillsetId")
    skills = normalized.get("skills")
    if isinstance(skills, dict):
        normalized["skills"] = {
            name: {("skill_id" if key == "skillId" else key): value for key, value in config.items()}
            if isinstance(config, dict)
            else config
            for name, config in skills.items()
        }
    try:
        return SeizuPluginExtension.model_validate(normalized)
    except ValidationError as exc:
        diagnostics.append(diagnostic("error", "invalid_seizu_extension", str(exc), path="plugin.json"))
        return None


def _valid_remote_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    if parsed.scheme == "https":
        return True
    return parsed.hostname == "localhost" or parsed.hostname in {"127.0.0.1", "::1"}


def _parse_mcp(files: dict[str, PluginFile], diagnostics: list[PluginDiagnostic]) -> dict[str, dict[str, Any]]:
    if "mcp.json" not in files:
        return {}
    config, error = _json_file(files, "mcp.json")
    if error or config is None:
        diagnostics.append(diagnostic("warning", "invalid_mcp_config", error or "invalid mcp.json", path="mcp.json"))
        return {}
    if (
        config.get("$schema") != MCP_SCHEMA
        or set(config) != {"$schema", "mcpServers"}
        or not isinstance(config.get("mcpServers"), dict)
    ):
        diagnostics.append(
            diagnostic(
                "warning",
                "invalid_mcp_config",
                f"mcp.json must target {MCP_SCHEMA} and contain only mcpServers",
                path="mcp.json",
            )
        )
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, server in config["mcpServers"].items():
        if not isinstance(name, str) or not name or not isinstance(server, dict):
            diagnostics.append(
                diagnostic("warning", "invalid_mcp_server", f"Skipping invalid MCP server {name!r}", path="mcp.json")
            )
            continue
        transport = server.get("type")
        valid = False
        if transport in {"streamable-http", "sse"}:
            valid = set(server) <= {"type", "url", "headers"} and _valid_remote_url(server.get("url"))
            headers = server.get("headers", {})
            valid = (
                valid
                and isinstance(headers, dict)
                and all(
                    isinstance(key, str)
                    and isinstance(value, str)
                    and "\r" not in key + value
                    and "\n" not in key + value
                    for key, value in headers.items()
                )
            )
        elif transport == "stdio":
            valid = isinstance(server.get("command"), str) and bool(server["command"])
            if valid:
                diagnostics.append(
                    diagnostic(
                        "warning",
                        "unsupported_mcp_transport",
                        f"MCP server {name!r} uses unsupported stdio transport",
                        path="mcp.json",
                    )
                )
                continue
        if not valid:
            diagnostics.append(
                diagnostic("warning", "invalid_mcp_server", f"Skipping invalid MCP server {name!r}", path="mcp.json")
            )
            continue
        result[name] = server
    return result


def _frontmatter(content: bytes, path: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if len(content) > MAX_SKILL_MD_BYTES:
        return None, None, f"{path} exceeds {MAX_SKILL_MD_BYTES} bytes"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, f"{path} must be UTF-8"
    if not text.startswith("---\n"):
        return None, None, f"{path} must begin with YAML frontmatter"
    close = text.find("\n---", 4)
    if close == -1:
        return None, None, f"{path} has unterminated YAML frontmatter"
    if close + 4 < len(text) and text[close + 4] not in {"\n", "\r"}:
        return None, None, f"{path} has an invalid frontmatter delimiter"
    try:
        metadata = yaml.safe_load(text[4:close])
    except yaml.YAMLError as exc:
        return None, None, f"{path} has invalid YAML frontmatter: {exc}"
    if not isinstance(metadata, dict):
        return None, None, f"{path} frontmatter must be an object"
    body = text[close + 4 :].lstrip("\r\n")
    return metadata, body, None


def parse_package(files: list[PluginFile]) -> ParsedPlugin:
    diagnostics: list[PluginDiagnostic] = []
    if len(files) > MAX_FILES:
        diagnostics.append(diagnostic("error", "package_too_large", f"Package contains more than {MAX_FILES} files"))
        return ParsedPlugin("", {}, [], [], diagnostics, "")
    total_size = sum(len(item.content) for item in files)
    oversized = next((item for item in files if len(item.content) > MAX_FILE_BYTES), None)
    if oversized is not None or total_size > MAX_UNPACKED_BYTES:
        message = (
            f"Package member exceeds {MAX_FILE_BYTES} bytes"
            if oversized is not None
            else f"Unpacked package exceeds {MAX_UNPACKED_BYTES} bytes"
        )
        diagnostics.append(
            diagnostic(
                "error",
                "package_too_large",
                message,
                path=oversized.path if oversized is not None else None,
            )
        )
        return ParsedPlugin("", {}, [], [], diagnostics, "")
    by_path: dict[str, PluginFile] = {}
    for item in files:
        safe = _safe_path(item.path)
        if safe is None or safe != item.path or safe in by_path:
            diagnostics.append(
                diagnostic(
                    "error", "unsafe_package_path", f"Invalid or duplicate package path {item.path!r}", path=item.path
                )
            )
            continue
        by_path[safe] = item
    digest = package_digest(list(by_path.values()))
    manifest, error = _json_file(by_path, "plugin.json")
    if error or manifest is None:
        diagnostics.append(diagnostic("error", "invalid_manifest", error or "invalid plugin.json", path="plugin.json"))
        return ParsedPlugin("", manifest or {}, list(by_path.values()), [], diagnostics, digest)
    if not _validate_manifest(manifest, diagnostics):
        return ParsedPlugin("", manifest, list(by_path.values()), [], diagnostics, digest)
    extension = _parse_seizu_extension(manifest, diagnostics)
    plugin_id = extension.skillset_id if extension else ""
    legacy_projection = bool(extension and extension.legacy_skillset_projection)
    mcp_servers = _parse_mcp(by_path, diagnostics)
    skills: list[PluginSkillItem] = []
    seen_ids: set[str] = set()
    skill_dirs = sorted(
        path.removeprefix("skills/").removesuffix("/SKILL.md")
        for path in by_path
        if path.startswith("skills/") and path.endswith("/SKILL.md") and path.count("/") == 2
    )
    for directory in skill_dirs:
        path = f"skills/{directory}/SKILL.md"
        metadata, template, skill_error = _frontmatter(by_path[path].content, path)
        if skill_error or metadata is None or template is None:
            diagnostics.append(
                diagnostic("warning", "invalid_skill", skill_error or "invalid skill", path=path, skill=directory)
            )
            continue
        name = metadata.get("name")
        description = metadata.get("description")
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not _SKILL_NAME_RE.fullmatch(name)
            or name != directory
            or not isinstance(description, str)
            or not description.strip()
            or len(description) > 1024
        ):
            diagnostics.append(
                diagnostic(
                    "warning",
                    "invalid_skill",
                    "Skill name must match its directory and description must be 1-1024 characters",
                    path=path,
                    skill=directory,
                )
            )
            continue
        raw_allowed = metadata.get("allowed-tools", "")
        if not isinstance(raw_allowed, str):
            diagnostics.append(
                diagnostic(
                    "warning",
                    "invalid_skill",
                    "allowed-tools must be a space-separated string",
                    path=path,
                    skill=directory,
                )
            )
            continue
        allowed_tools = raw_allowed.split()
        config = extension.skills.get(name) if extension else None
        skill_id = config.skill_id if config and config.skill_id else name.replace("-", "_")
        try:
            from reporting.schema.mcp_config import validate_mcp_slug_component

            validate_mcp_slug_component(skill_id)
        except ValueError as exc:
            diagnostics.append(diagnostic("warning", "invalid_skill_id", str(exc), path=path, skill=name))
            continue
        if skill_id in seen_ids:
            diagnostics.append(
                diagnostic(
                    "warning", "duplicate_skill_id", f"Duplicate effective skill ID {skill_id!r}", path=path, skill=name
                )
            )
            continue
        seen_ids.add(skill_id)
        parameters = config.parameters if config else []
        template_errors = validate_skill_template(parameters, template)
        if template_errors:
            diagnostics.extend(
                diagnostic("warning", "invalid_skill_template", message, path=path, skill=name)
                for message in template_errors
            )
            continue
        # Substitution still works, but a body carrying placeholders is not the
        # file a consumer outside Seizu can read: it sees the tags. Not raised
        # for the legacy skillset projection, whose bodies are generated from
        # records their author cannot restructure (AGT-039).
        if not legacy_projection and template_placeholders(template):
            diagnostics.append(
                diagnostic(
                    "warning",
                    "templated_skill_body",
                    "Instructions substitute argument values inline, so a consumer without Seizu's "
                    "parameter extension reads the raw tags. Prefer a static body that refers to the "
                    "values by name from the rendered Inputs block.",
                    path=path,
                    skill=name,
                )
            )
        skills.append(
            PluginSkillItem(
                plugin_id=plugin_id,
                skill_id=skill_id,
                portable_name=name,
                title=(config.title if config and config.title else name.replace("-", " ").title()),
                description=description,
                template=template,
                parameters=parameters,
                triggers=config.triggers if config else [],
                allowed_tools=allowed_tools,
                enabled=config.enabled if config else True,
                source_path=f"skills/{directory}",
                aliases=config.aliases if config else [],
                mcp_servers=mcp_servers,
                package_digest=digest,
                has_scripts=any(candidate.startswith(f"skills/{directory}/scripts/") for candidate in by_path),
            )
        )
    return ParsedPlugin(plugin_id, manifest, list(by_path.values()), skills, diagnostics, digest)


def logical_mcp_ref(value: str) -> tuple[str, str] | None:
    if not value.startswith("mcp:"):
        return None
    server, separator, tool = value[4:].partition("/")
    if not separator or not server or not tool:
        return None
    return unquote(server), unquote(tool)


def legacy_skillset_package(skillset: Any, skills: list[Any]) -> ParsedPlugin:
    """Project one legacy skillset into its canonical Agent Plugin package."""
    portable_plugin_name = skillset.skillset_id.replace("_", "-")
    manifest: dict[str, Any] = {
        "$schema": PLUGIN_SCHEMA,
        "name": portable_plugin_name,
        "version": f"0.0.{max(int(skillset.current_version), 1)}",
        "description": skillset.description or skillset.name,
        "extensions": {
            EXTENSION_NAMESPACE: {
                LEGACY_PROJECTION_EXTENSION_KEY: True,
                "skillsetId": skillset.skillset_id,
                "skills": {},
            }
        },
    }
    files: list[PluginFile] = []
    extension_skills = manifest["extensions"][EXTENSION_NAMESPACE]["skills"]
    for skill in skills:
        portable_name = skill.skill_id.replace("_", "-")
        extension_skills[portable_name] = {
            "skillId": skill.skill_id,
            "title": skill.name,
            "enabled": skill.enabled,
            "triggers": skill.triggers,
            "parameters": [parameter.model_dump() for parameter in skill.parameters],
            "aliases": [f"{skillset.skillset_id}__{skill.skill_id}"],
        }
        metadata = {
            "name": portable_name,
            "description": skill.description or skill.name,
        }
        if skill.tools_required:
            metadata["allowed-tools"] = " ".join(skill.tools_required)
        frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        content = f"---\n{frontmatter}\n---\n{skill.template}".encode()
        files.append(
            PluginFile(
                path=f"skills/{portable_name}/SKILL.md",
                content=content,
                media_type="text/markdown",
            )
        )
    files.append(
        PluginFile(
            path="plugin.json",
            content=json.dumps(manifest, indent=2, sort_keys=True).encode(),
            media_type="application/json",
        )
    )
    return parse_package(files)


def is_legacy_skillset_projection(manifest: dict[str, Any]) -> bool:
    """Whether a plugin package is owned by the legacy compatibility projection."""
    extension = manifest.get("extensions", {}).get(EXTENSION_NAMESPACE, {})
    return isinstance(extension, dict) and extension.get(LEGACY_PROJECTION_EXTENSION_KEY) is True
