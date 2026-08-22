"""Agent Plugins 1.0.0 installation, versioning, and draft authoring APIs."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import Response

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.plugins import (
    PluginCreateRequest,
    PluginFile,
    PluginFileContent,
    PluginFileInfo,
    PluginFileListResponse,
    PluginFileWriteRequest,
    PluginListItem,
    PluginListResponse,
    PluginPublishRequest,
    PluginRestoreRequest,
    PluginUpdateRequest,
    PluginValidationResponse,
    PluginVersion,
    PluginVersionListResponse,
)
from reporting.services import plugin_packages, report_store
from reporting.services.report_store.base import PluginRevisionConflict

router = APIRouter()


async def _uploaded_files(package: UploadFile) -> list[PluginFile]:
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
    if existing and existing.package_digest == parsed.package_digest:
        return existing
    return await report_store.publish_plugin(
        plugin_id=parsed.plugin_id,
        manifest=parsed.manifest,
        files=parsed.files,
        skills=parsed.skills,
        diagnostics=[item.model_dump() for item in parsed.diagnostics],
        package_digest=parsed.package_digest,
        created_by=current.user.user_id,
        comment=comment,
        expected_revision=expected_revision,
    )


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
    return await _publish(await _uploaded_files(package), current, "Installed package")


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
    if await report_store.get_plugin(body.plugin_id):
        raise HTTPException(status_code=409, detail="Plugin already exists")
    manifest = {
        "$schema": plugin_packages.PLUGIN_SCHEMA,
        "name": body.name,
        "version": body.version,
        "description": body.description,
        "extensions": {
            plugin_packages.EXTENSION_NAMESPACE: {
                "skillsetId": body.plugin_id,
                "skills": {},
            }
        },
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
    digest = __import__("hashlib").sha256(file.content).hexdigest()
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
    infos = await report_store.list_plugin_files(plugin_id, revision)
    files = [await report_store.read_plugin_file(plugin_id, item.path, revision) for item in infos]
    return [file for file in files if file is not None]


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
    return await _publish(files, current, body.comment or f"Restored revision {body.revision}")


@router.post("/api/v1/plugins/{plugin_id}/draft", status_code=201)
async def create_plugin_draft(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> dict[str, str]:
    if not await report_store.create_plugin_draft(plugin_id, current.user.user_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"plugin_id": plugin_id, "status": "draft"}


@router.delete("/api/v1/plugins/{plugin_id}/draft", status_code=204)
async def discard_plugin_draft(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> Response:
    del current
    if not await report_store.delete_plugin_draft(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin draft not found")
    return Response(status_code=204)


@router.get("/api/v1/plugins/{plugin_id}/draft/files", response_model=PluginFileListResponse)
async def list_plugin_draft_files(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginFileListResponse:
    del current
    return PluginFileListResponse(files=await report_store.list_plugin_draft_files(plugin_id))


@router.post("/api/v1/plugins/{plugin_id}/draft/validate", response_model=PluginValidationResponse)
async def validate_plugin_draft(
    plugin_id: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginValidationResponse:
    del current
    files = await _draft_files(plugin_id)
    if files is None:
        raise HTTPException(status_code=404, detail="Plugin draft not found")
    parsed = plugin_packages.parse_package(files)
    if parsed.plugin_id and parsed.plugin_id != plugin_id:
        parsed.diagnostics.append(
            plugin_packages._diagnostic(  # noqa: SLF001
                "error", "immutable_skillset_id", "A published plugin's skillsetId cannot change", path="plugin.json"
            )
        )
    return parsed.response()


async def _draft_files(plugin_id: str) -> list[PluginFile] | None:
    infos = await report_store.list_plugin_draft_files(plugin_id)
    if not infos:
        return None
    files = [await report_store.read_plugin_draft_file(plugin_id, info.path) for info in infos]
    return [file for file in files if file is not None]


@router.post("/api/v1/plugins/{plugin_id}/draft/publish", response_model=PluginListItem)
async def publish_plugin_draft(
    plugin_id: str,
    body: PluginPublishRequest,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginListItem:
    files = await _draft_files(plugin_id)
    if files is None:
        raise HTTPException(status_code=404, detail="Plugin draft not found")
    parsed = plugin_packages.parse_package(files)
    if parsed.plugin_id != plugin_id:
        raise HTTPException(status_code=400, detail="A published plugin's skillsetId cannot change")
    base_revision = await report_store.get_plugin_draft_base_revision(plugin_id)
    if base_revision is None:
        raise HTTPException(status_code=404, detail="Plugin draft not found")
    try:
        result = await _publish(files, current, body.comment, base_revision)
    except PluginRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Plugin changed after this draft was created; recreate the draft",
        ) from exc
    await report_store.delete_plugin_draft(plugin_id)
    return result


@router.get("/api/v1/plugins/{plugin_id}/draft/files/{path:path}", response_model=PluginFileContent)
async def read_plugin_draft_file(
    plugin_id: str,
    path: str,
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_READ)),
) -> PluginFileContent:
    del current
    path = _validate_path(path)
    file = await report_store.read_plugin_draft_file(plugin_id, path)
    if not file:
        raise HTTPException(status_code=404, detail="Plugin draft file not found")
    digest = __import__("hashlib").sha256(file.content).hexdigest()
    return PluginFileContent(
        path=path,
        media_type=file.media_type,
        content_base64=base64.b64encode(file.content).decode(),
        executable=file.executable,
        etag=f'"{digest}"',
    )


@router.put("/api/v1/plugins/{plugin_id}/draft/files/{path:path}", response_model=PluginFileInfo)
async def write_plugin_draft_file(
    plugin_id: str,
    path: str,
    body: PluginFileWriteRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> PluginFileInfo:
    path = _validate_path(path)
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="content_base64 is invalid") from exc
    if len(content) > plugin_packages.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Plugin file is too large")
    file = PluginFile(
        path=path,
        content=content,
        media_type=body.media_type or plugin_packages._media_type(path),  # noqa: SLF001
        executable=body.executable,
    )
    result = await report_store.write_plugin_draft_file(plugin_id, file, current.user.user_id, if_match)
    if result is None:
        raise HTTPException(status_code=412 if if_match else 404, detail="Draft missing or file changed")
    return result


@router.delete("/api/v1/plugins/{plugin_id}/draft/files/{path:path}", status_code=204)
async def delete_plugin_draft_file(
    plugin_id: str,
    path: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    current: CurrentUser = Depends(require_permission(Permission.PLUGINS_WRITE)),
) -> Response:
    path = _validate_path(path)
    if not await report_store.delete_plugin_draft_file(plugin_id, path, current.user.user_id, if_match):
        raise HTTPException(status_code=412 if if_match else 404, detail="Draft file missing or changed")
    return Response(status_code=204)
