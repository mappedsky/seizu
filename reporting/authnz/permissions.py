"""RBAC permission definitions for Seizu.

Defines granular permissions, built-in roles, and permission resolution logic.
"""

from enum import StrEnum
from typing import Any


class Permission(StrEnum):
    """Granular permission strings used throughout the application."""

    # Reports
    REPORTS_READ = "reports:read"
    REPORTS_WRITE = "reports:write"
    REPORTS_DELETE = "reports:delete"
    REPORTS_SET_DASHBOARD = "reports:set_dashboard"

    # Queries
    QUERY_EXECUTE = "query:execute"
    QUERY_VALIDATE = "query:validate"
    QUERY_HISTORY_READ = "query_history:read"

    # Chat
    CHAT_USE = "chat:use"
    CHAT_TOOLS_CALL = "chat:tools:call"
    CHAT_SKILLS_CALL = "chat:skills:call"
    # Allows running chat (interactive or headless) with action confirmations
    # bypassed.
    CHAT_BYPASS_PERMISSIONS = "chat:bypass_permissions"
    # Allows managing scheduled chats (recurring headless agent runs).
    CHAT_SCHEDULE = "chat:schedule"
    # Allows viewing every user's scheduled chats (and their runs/versions),
    # not just one's own. Mutations stay owner-only.
    CHAT_SCHEDULE_READ_ALL = "chat:schedule:read_all"

    # Toolsets
    TOOLSETS_READ = "toolsets:read"
    TOOLSETS_WRITE = "toolsets:write"
    TOOLSETS_DELETE = "toolsets:delete"

    # Tools
    TOOLS_READ = "tools:read"
    TOOLS_WRITE = "tools:write"
    TOOLS_DELETE = "tools:delete"
    TOOLS_CALL = "tools:call"

    # Skillsets
    SKILLSETS_READ = "skillsets:read"
    SKILLSETS_WRITE = "skillsets:write"
    SKILLSETS_DELETE = "skillsets:delete"

    # Skills
    SKILLS_READ = "skills:read"
    SKILLS_WRITE = "skills:write"
    SKILLS_DELETE = "skills:delete"
    SKILLS_RENDER = "skills:render"

    # Scheduled queries
    SCHEDULED_QUERIES_READ = "scheduled_queries:read"
    SCHEDULED_QUERIES_WRITE = "scheduled_queries:write"
    SCHEDULED_QUERIES_DELETE = "scheduled_queries:delete"

    # Users
    USERS_READ = "users:read"

    # Roles (user-defined)
    ROLES_READ = "roles:read"
    ROLES_WRITE = "roles:write"
    ROLES_DELETE = "roles:delete"


# ---------------------------------------------------------------------------
# Built-in role permission sets (hierarchical: Admin ⊇ Editor ⊇ Viewer)
# ---------------------------------------------------------------------------

VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.REPORTS_READ,
        Permission.TOOLSETS_READ,
        Permission.TOOLS_READ,
        Permission.TOOLS_CALL,
        Permission.SKILLSETS_READ,
        Permission.SKILLS_READ,
        Permission.SKILLS_RENDER,
        Permission.SCHEDULED_QUERIES_READ,
        Permission.CHAT_USE,
        Permission.USERS_READ,
        Permission.ROLES_READ,
    }
)

EDITOR_PERMISSIONS: frozenset[Permission] = frozenset(
    VIEWER_PERMISSIONS
    | {
        Permission.REPORTS_WRITE,
        Permission.REPORTS_DELETE,
        Permission.REPORTS_SET_DASHBOARD,
        Permission.QUERY_EXECUTE,
        Permission.QUERY_VALIDATE,
        Permission.QUERY_HISTORY_READ,
        Permission.CHAT_TOOLS_CALL,
        Permission.CHAT_SKILLS_CALL,
        Permission.CHAT_BYPASS_PERMISSIONS,
        Permission.CHAT_SCHEDULE,
    }
)

ADMIN_PERMISSIONS: frozenset[Permission] = frozenset(
    EDITOR_PERMISSIONS
    | {
        Permission.SCHEDULED_QUERIES_WRITE,
        Permission.SCHEDULED_QUERIES_DELETE,
        Permission.TOOLSETS_WRITE,
        Permission.TOOLSETS_DELETE,
        Permission.TOOLS_WRITE,
        Permission.TOOLS_DELETE,
        Permission.SKILLSETS_WRITE,
        Permission.SKILLSETS_DELETE,
        Permission.SKILLS_WRITE,
        Permission.SKILLS_DELETE,
        Permission.CHAT_SCHEDULE_READ_ALL,
        Permission.ROLES_WRITE,
        Permission.ROLES_DELETE,
    }
)

# Maps built-in role names to their permission sets.
BUILTIN_ROLES: dict[str, frozenset[Permission]] = {
    "seizu-viewer": VIEWER_PERMISSIONS,
    "seizu-editor": EDITOR_PERMISSIONS,
    "seizu-admin": ADMIN_PERMISSIONS,
}

# All permissions — used in dev mode to grant full access.
ALL_PERMISSIONS: frozenset[str] = frozenset(p.value for p in Permission)


# ---------------------------------------------------------------------------
# Permission resolution
# ---------------------------------------------------------------------------


async def resolve_permissions(jwt_claims: dict[str, Any]) -> frozenset[str]:
    """Resolve a JWT payload to a set of permission strings.

    Reads RBAC_ROLE_CLAIM from the JWT claims. If the claim names a built-in
    role, returns that role's permissions directly with no store I/O. If it
    names a user-defined role, performs a single GetItem lookup by name.
    Falls back to RBAC_DEFAULT_ROLE when the claim is absent.
    """
    # Deferred imports to avoid circular dependency at module load time.
    from reporting import settings
    from reporting.services import report_store

    role_name = jwt_claims.get(settings.RBAC_ROLE_CLAIM) or settings.RBAC_DEFAULT_ROLE
    if not role_name:
        return frozenset()

    if role_name in BUILTIN_ROLES:
        return frozenset(p.value for p in BUILTIN_ROLES[role_name])

    # User-defined role: single lookup by name.
    role = await report_store.get_role_by_name(role_name)
    return frozenset(role.permissions) if role else frozenset()
