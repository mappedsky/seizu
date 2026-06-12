"""Distinct Cartography SyncMetadata property values.

Feeds the watch-scan autocompletes in the scheduled query and scheduled chat
editors: ``grouptype``, ``syncedtype``, and ``groupid`` are the SyncMetadata
property keys that watch_scans match against.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import Permission
from reporting.services import reporting_neo4j

logger = logging.getLogger(__name__)

router = APIRouter()

_VALUES_CYPHER = """
MATCH (s:SyncMetadata)
RETURN collect(DISTINCT s.grouptype) AS grouptypes,
       collect(DISTINCT s.syncedtype) AS syncedtypes,
       collect(DISTINCT toString(s.groupid)) AS groupids
"""

# Either of these grants access: watch_scans are configured from the scheduled
# query editor (scheduled_queries:read is Viewer+) and the scheduled chat
# editor (chat:schedule is Editor+).
_ALLOWED_PERMISSIONS = frozenset(
    {
        Permission.SCHEDULED_QUERIES_READ.value,
        Permission.CHAT_SCHEDULE.value,
    }
)


class SyncMetadataValuesResponse(BaseModel):
    grouptypes: list[str]
    syncedtypes: list[str]
    groupids: list[str]


def _clean(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


@router.get("/api/v1/sync-metadata/values", response_model=SyncMetadataValuesResponse)
async def get_sync_metadata_values(
    current: CurrentUser = Depends(get_current_user),
) -> SyncMetadataValuesResponse:
    """Return the distinct SyncMetadata grouptype/syncedtype/groupid values."""
    if not (_ALLOWED_PERMISSIONS & current.permissions):
        raise HTTPException(
            status_code=403,
            detail=f"Missing permissions: one of {', '.join(sorted(_ALLOWED_PERMISSIONS))}",
        )
    records = await reporting_neo4j.run_query(_VALUES_CYPHER)
    if not records:
        return SyncMetadataValuesResponse(grouptypes=[], syncedtypes=[], groupids=[])
    record = records[0]
    return SyncMetadataValuesResponse(
        grouptypes=_clean(record["grouptypes"]),
        syncedtypes=_clean(record["syncedtypes"]),
        groupids=_clean(record["groupids"]),
    )
