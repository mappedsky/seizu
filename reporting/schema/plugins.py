"""Agent Plugins 1.0.0 package and authoring API models."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reporting.schema.mcp_config import ToolParamDef, validate_mcp_slug_component


class PluginDiagnostic(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str | None = None
    skill: str | None = None


class PluginFile(BaseModel):
    path: str
    content: bytes
    media_type: str = "application/octet-stream"
    executable: bool = False


class PluginSkillItem(BaseModel):
    plugin_id: str
    skill_id: str
    portable_name: str
    title: str
    description: str
    template: str
    parameters: list[ToolParamDef] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    source_path: str
    aliases: list[str] = Field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    revision: int = 0
    package_digest: str = ""
    has_scripts: bool = False
    disabled_reason: str | None = None


class PluginListItem(BaseModel):
    plugin_id: str
    name: str
    package_version: str | None = None
    description: str = ""
    enabled: bool = True
    current_revision: int = 0
    package_digest: str = ""
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    diagnostics: list[PluginDiagnostic] = Field(default_factory=list)


class PluginVersion(BaseModel):
    plugin_id: str
    revision: int
    manifest: dict[str, Any]
    package_digest: str
    created_at: str
    created_by: str
    comment: str | None = None
    diagnostics: list[PluginDiagnostic] = Field(default_factory=list)


class PluginValidationResponse(BaseModel):
    valid: bool
    plugin_id: str | None = None
    manifest: dict[str, Any] | None = None
    skills: list[PluginSkillItem] = Field(default_factory=list)
    diagnostics: list[PluginDiagnostic] = Field(default_factory=list)
    package_digest: str | None = None


class PluginListResponse(BaseModel):
    plugins: list[PluginListItem]


class PluginVersionListResponse(BaseModel):
    versions: list[PluginVersion]


class PluginSkillListResponse(BaseModel):
    skills: list[PluginSkillItem]


class PluginFileInfo(BaseModel):
    path: str
    media_type: str
    size: int
    sha256: str
    executable: bool = False
    etag: str


class PluginFileListResponse(BaseModel):
    files: list[PluginFileInfo]


class PluginFileContent(BaseModel):
    path: str
    media_type: str
    content_base64: str
    executable: bool = False
    etag: str


class PluginUpdateRequest(BaseModel):
    enabled: bool


class PluginCreateRequest(BaseModel):
    plugin_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""

    @field_validator("plugin_id")
    @classmethod
    def valid_plugin_id(cls, value: str) -> str:
        return validate_mcp_slug_component(value)


class PluginFilePayload(BaseModel):
    """One file in a staged package.

    A file the client changed carries its bytes as ``content_base64``. A file it
    left alone carries only ``sha256``, which the server resolves against the
    blobs this plugin already stores -- so republishing a package whose only
    edit is one line of Markdown does not re-upload its binary assets.
    """

    path: str
    content_base64: str | None = None
    sha256: str | None = None
    media_type: str | None = None
    executable: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> "PluginFilePayload":
        if (self.content_base64 is None) == (self.sha256 is None):
            raise ValueError("each file must carry either content_base64 or sha256")
        return self


class PluginPackageRequest(BaseModel):
    """A complete package, staged in the client and submitted in one request."""

    files: list[PluginFilePayload]


class PluginPublishRequest(PluginPackageRequest):
    """A staged package offered as the plugin's next revision.

    ``base_revision`` is required, not optional: it is the revision the editor
    loaded, and publishing against a stale one is refused with a 409 rather than
    reverting whatever was published in the meantime.
    """

    base_revision: int = Field(ge=1)
    comment: str | None = None


class PluginRestoreRequest(BaseModel):
    revision: int = Field(ge=1)
    # The revision the caller believed was current. Restoring is a publish like
    # any other, so it takes the same stale-base refusal.
    base_revision: int = Field(ge=1)
    comment: str | None = None


class SeizuSkillExtension(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    skill_id: str | None = None
    title: str | None = None
    enabled: bool = True
    triggers: list[str] = Field(default_factory=list)
    parameters: list[ToolParamDef] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str | None) -> str | None:
        return validate_mcp_slug_component(value) if value is not None else None


class SeizuPluginExtension(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    skillset_id: str
    skills: dict[str, SeizuSkillExtension] = Field(default_factory=dict)
    legacy_skillset_projection: bool = Field(default=False, alias="legacySkillsetProjection")

    @field_validator("skillset_id")
    @classmethod
    def valid_skillset_id(cls, value: str) -> str:
        return validate_mcp_slug_component(value)
