"""Resolve a stored user's identity and permissions outside an HTTP request.

Used by headless callers (e.g. Temporal workflow activities) that act on
behalf of a stored user — typically the creator of a scheduled query — and
must enforce that user's RBAC permissions without a bearer token. Permissions
are derived from the last role claim observed on an authenticated request
(``User.role``, synced by ``get_or_create_user``), resolved through the same
``resolve_permissions`` path the request flow uses.
"""

import logging

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, resolve_permissions

logger = logging.getLogger(__name__)


class HeadlessIdentityError(Exception):
    """The stored user cannot be resolved to an active identity."""


async def resolve_stored_user(user_id: str) -> CurrentUser:
    """Return a CurrentUser for a stored user, enforcing their RBAC role.

    Raises HeadlessIdentityError when the user does not exist or is archived;
    headless work must hard-stop for deactivated identities.
    """
    from reporting.services import report_store

    user = await report_store.get_user(user_id)
    if user is None:
        raise HeadlessIdentityError(f"User {user_id!r} not found")
    if user.archived_at:
        raise HeadlessIdentityError(f"User {user_id!r} is archived")

    if not settings.DEVELOPMENT_ONLY_REQUIRE_AUTH:
        # Dev mode grants every permission on web requests; mirror that here.
        logger.warning(
            "Authentication is disabled; headless identity granted all permissions",
            extra={"type": "AUDIT", "user": user.email or user.user_id},
        )
        permissions = ALL_PERMISSIONS
    else:
        permissions = await resolve_permissions({settings.RBAC_ROLE_CLAIM: user.role})

    return CurrentUser(user=user, jwt_claims={}, permissions=permissions)
