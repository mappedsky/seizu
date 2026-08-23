"""Agent Plugins 1.0.0 installation, versioning, and package authoring APIs."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.plugins import (
    PluginCreateRequest,
    PluginFile,
    PluginFileContent,
    PluginFileListResponse,
    PluginListItem,
    PluginListResponse,
    PluginPackageRequest,
    PluginPublishRequest,
    PluginRestoreRequest,
    PluginSkillListResponse,
    PluginUpdateRequest,
    PluginValidationResponse,
    PluginVersion,
    PluginVersionListResponse,
)
from reporting.services import plugin_packages, report_store
from reporting.services.report_store.base import PluginRevisionConflict

router = APIRouter()


async def _uploaded_files(package: UploadFile) -> list[PluginFile]:
    # Checked before reading: an oversized body is spooled to disk by the ASGI
    # layer before this runs, so the declared size is the only cheap refusal.
    if package.size is not None and package.size > plugin_packages.MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail=f"Archive exceeds {plugin_packages.MAX_ARCHIVE_BYTES} bytes")
    data = await package.read(plugin_packages.MAX_ARCHIVE_BYTES + 1)
    try:
        return plugin_packages.files_from_zip(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_path(path: str) -> str:
    parsed = PurePosixPath(path)
    if not path or "\\" in path or parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise HTTPException(status_code=400, detail="Invalid plugin-relative path")
    return parsed.as_posix()


async def _publish(
    files: list[PluginFile],
    current: CurrentUser,
    comment: str | None = None,
    expected_revision: int | None = None,
) -> PluginListItem:
    parsed = plugin_packages.parse_package(files)
    if not parsed.valid:
        raise HTTPException(status_code=400, detail=parsed.response().model_dump(mode="json"))
    existing = await report_store.get_plugin(parsed.plugin_id)
    # Checked before the no-op short circuit so a stale base is always refused,
    # never quietly reported as success. The store re-checks it under the row
    # lock; this only spares the write when the answer is already known.
    if expected_revision is not None and (existing is None or existing.current_revision != expected_revision):
        raise PluginRevisionConflict(parsed.plugin_id)
    if existing and existing.package_digest == parsed.package_digest:
        return existing
    diagnostics = list(parsed.diagnostics)
    declared_version = parsed.manifest.get("version")
    if existing is not None and declared_version and declared_version == existing.package_version:
        # Not an error: identity inside Seizu is the revision and the package
        # digest, both server-assigned. It matters once the package leaves --
        # an exported ZIP carries only the manifest version.
        diagnostics.append(
            plugin_packages.diagnostic(
                "warning",
                "unchanged_package_version",
                f"Package contents changed but version is still {declared_version!r}."
                " Seizu tracks this revision by its digest, but once this package is"
                " exported and installed elsewhere, tools outside Seizu have only the"
                " version to compare and cannot tell it apart from the previous one.",
                path="plugin.json",
            )
        )
    return await report_store.publish_plugin(
        plugin_id=parsed.plugin_id,
        manifest=parsed.manifest,
        files=parsed.files,
        skills=parsed.skills,
        diagnostics=[item.model_dump() for item in diagnostics],
        package_digest=parsed.package_digest,
        created_by=current.user.user_id,
        comment=comment,
        expected_revision=expected_revision,
    )


async def _staged_files(plugin_id: str, body: PluginPackageRequest) -> list[PluginFile]:
    """Resolve a staged package into concrete files.

    Retained entries are read back from the plugin's own blobs, so the caller
    never re-uploads content the server already holds, and never introduces
    content it does not.
    """
    if len(body.files) > plugin_packages.MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Package contains more than {plugin_packages.MAX_FILES} files")
    files: list[PluginFile] = []
    seen: set[str] = set()
    total = 0
    for payload in body.files:
        path = _validate_path(payload.path)
        if path in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate package path: {path}")
        seen.add(path)
        if payload.content_base64 is not None:
            try:
                content = base64.b64decode(payload.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{path} carries invalid base64") from exc
            media_type = payload.media_type or plugin_packages.media_type_for(path)
            executable = payload.executable
        else:
            retained = await report_store.read_plugin_blob(plugin_id, payload.sha256 or "")
            if retained is None:
                raise HTTPException(status_code=400, detail=f"{path} references content this plugin does not store")
            content = retained.content
            media_type = payload.media_type or retained.media_type
            executable = payload.executable or retained.executable
        if len(content) > plugin_packages.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{path} exceeds {plugin_packages.MAX_FILE_BYTES} bytes")
        total += len(content)
        if total > plugin_packages.MAX_UNPACKED_BYTES:
            raise HTTPException(status_code=413, detail=f"Package exceeds {plugin_packages.MAX_UNPACKED_BYTES} bytes")
        files.append(PluginFile(path=path, content=content, media_type=media_type, executable=executable))
    return files


@router.post("/api/v1/plugins/validate", response_model=PluginValidationResponse)
async def validate_plugin(
    package: UploadFile = File(...),
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginValidationResponse:
    del current
    return plugin_packages.parse_package(await _uploaded_files(package)).response()


@router.post("/api/v1/plugins/install", response_model=PluginListItem, status_code=201)
async def install_plugin(
    package: UploadFile = File(...),
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    try:
        return await _publish(await _uploaded_files(package), current, "Installed package")
    except PluginRevisionConflict as exc:
        raise HTTPException(status_code=409, detail="This plugin was published concurrently; retry") from exc


@router.get("/api/v1/plugins", response_model=PluginListResponse)
async def list_plugins(
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginListResponse:
    del current
    return PluginListResponse(plugins=await report_store.list_plugins())


@router.post("/api/v1/plugins", response_model=PluginListItem, status_code=201)
async def create_plugin(
    body: PluginCreateRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    plugin_id = plugin_packages.derive_seizu_id(body.name)
    if plugin_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Package name does not derive a Seizu id. Use lower-case words separated by single "
                "hyphens, starting with a letter and at most 31 characters."
            ),
        )
    if await report_store.get_plugin(plugin_id):
        raise HTTPException(status_code=409, detail="Plugin already exists")
    manifest = {
        "$schema": plugin_packages.PLUGIN_SCHEMA,
        "name": body.name,
        "version": body.version,
        "description": body.description,
    }
    files = [
        PluginFile(
            path="plugin.json",
            content=json.dumps(manifest, indent=2).encode(),
            media_type="application/json",
        )
    ]
    return await _publish(files, current, "Created plugin")


@router.get("/api/v1/plugins/{plugin_id}", response_model=PluginListItem)
async def get_plugin(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginListItem:
    del current
    plugin = await report_store.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.put("/api/v1/plugins/{plugin_id}", response_model=PluginListItem)
async def update_plugin(
    plugin_id: str,
    body: PluginUpdateRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    plugin = await report_store.set_plugin_enabled(plugin_id, body.enabled, current.user.user_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.delete("/api/v1/plugins/{plugin_id}", status_code=204)
async def delete_plugin(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_DELETE)),
) -> Response:
    del current
    if not await report_store.delete_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    return Response(status_code=204)


@router.get("/api/v1/plugins/{plugin_id}/skills", response_model=PluginSkillListResponse)
async def list_plugin_skills(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginSkillListResponse:
    """The skills indexed from the plugin's current revision, disabled ones included."""
    del current
    if not await report_store.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    return PluginSkillListResponse(skills=await report_store.list_plugin_skills(plugin_id))


@router.get("/api/v1/plugins/{plugin_id}/versions/{revision}/skills", response_model=PluginSkillListResponse)
async def list_plugin_version_skills(
    plugin_id: str,
    revision: int,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginSkillListResponse:
    """The skills a past revision declared, parsed from that revision's files.

    ``plugin_skills`` indexes only the current revision, so an older one has to
    be read back and re-parsed. That is a whole-package read, which is why it is
    a per-revision request a person triggers rather than something a listing
    does for every revision it shows.
    """
    del current
    files = await _revision_files(plugin_id, revision)
    if not files:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    return PluginSkillListResponse(skills=plugin_packages.parse_package(files).skills)


@router.get("/api/v1/plugins/{plugin_id}/versions", response_model=PluginVersionListResponse)
async def list_plugin_versions(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginVersionListResponse:
    del current
    if not await report_store.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    return PluginVersionListResponse(versions=await report_store.list_plugin_versions(plugin_id))


@router.get("/api/v1/plugins/{plugin_id}/versions/{revision}", response_model=PluginVersion)
async def get_plugin_version(
    plugin_id: str,
    revision: int,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginVersion:
    del current
    versions = await report_store.list_plugin_versions(plugin_id)
    version = next((item for item in versions if item.revision == revision), None)
    if not version:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    return version


@router.get("/api/v1/plugins/{plugin_id}/versions/{revision}/files", response_model=PluginFileListResponse)
async def list_published_plugin_files(
    plugin_id: str,
    revision: int,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginFileListResponse:
    del current
    files = await report_store.list_plugin_files(plugin_id, revision)
    if not files:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    return PluginFileListResponse(files=files)


@router.get(
    "/api/v1/plugins/{plugin_id}/versions/{revision}/files/{path:path}",
    response_model=PluginFileContent,
)
async def read_published_plugin_file(
    plugin_id: str,
    revision: int,
    path: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginFileContent:
    del current
    path = _validate_path(path)
    file = await report_store.read_plugin_file(plugin_id, path, revision)
    if not file:
        raise HTTPException(status_code=404, detail="Plugin file not found")
    digest = hashlib.sha256(file.content).hexdigest()
    return PluginFileContent(
        path=path,
        media_type=file.media_type,
        content_base64=base64.b64encode(file.content).decode(),
        executable=file.executable,
        etag=f'"{digest}"',
    )


def _zip_response(plugin_id: str, revision: int, files: list[PluginFile]) -> Response:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files, key=lambda item: item.path):
            info = zipfile.ZipInfo(file.path)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.external_attr = (0o755 if file.executable else 0o644) << 16
            archive.writestr(info, file.content)
    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{plugin_id}-{revision}.zip"'},
    )


async def _revision_files(plugin_id: str, revision: int | None = None) -> list[PluginFile]:
    return await report_store.read_plugin_files(plugin_id, revision)


@router.get("/api/v1/plugins/{plugin_id}/download")
async def download_plugin(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> Response:
    del current
    plugin = await report_store.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return _zip_response(plugin_id, plugin.current_revision, await _revision_files(plugin_id))


@router.get("/api/v1/plugins/{plugin_id}/versions/{revision}/download")
async def download_plugin_version(
    plugin_id: str,
    revision: int,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> Response:
    del current
    files = await _revision_files(plugin_id, revision)
    if not files:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    return _zip_response(plugin_id, revision, files)


@router.post("/api/v1/plugins/{plugin_id}/restore", response_model=PluginListItem)
async def restore_plugin(
    plugin_id: str,
    body: PluginRestoreRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    files = await _revision_files(plugin_id, body.revision)
    if not files:
        raise HTTPException(status_code=404, detail="Plugin version not found")
    try:
        return await _publish(
            files,
            current,
            body.comment or f"Restored revision {body.revision}",
            body.base_revision,
        )
    except PluginRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="This plugin changed since this history was loaded; reload and restore again",
        ) from exc


@router.post("/api/v1/plugins/{plugin_id}/validate", response_model=PluginValidationResponse)
async def validate_plugin_package(
    plugin_id: str,
    body: PluginPackageRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginValidationResponse:
    del current
    if not await report_store.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    parsed = plugin_packages.parse_package(await _staged_files(plugin_id, body))
    if parsed.plugin_id and parsed.plugin_id != plugin_id:
        parsed.diagnostics.append(
            plugin_packages.diagnostic(
                "error", "immutable_skillset_id", "A published plugin's skillsetId cannot change", path="plugin.json"
            )
        )
    return parsed.response()


@router.post("/api/v1/plugins/{plugin_id}/publish", response_model=PluginListItem)
async def publish_plugin_package(
    plugin_id: str,
    body: PluginPublishRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    if not await report_store.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    files = await _staged_files(plugin_id, body)
    parsed = plugin_packages.parse_package(files)
    if parsed.valid and parsed.plugin_id != plugin_id:
        raise HTTPException(status_code=400, detail="A published plugin's skillsetId cannot change")
    try:
        return await _publish(files, current, body.comment, body.base_revision)
    except PluginRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="This plugin changed since these edits began; reload and reapply them",
        ) from exc
