"""Shape of the identifiers the report store mints.

``report_store.generate_id`` produced Snowflake integers until UUIDv7 replaced
them, and no data migration rewrote the rows created before that. Both shapes
therefore address live records, and anything validating an id at the API edge
has to accept either -- a UUID-only rule would strand every pre-migration
thread, and a digits-only rule strands every thread minted since.
"""

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

STORE_ID_PATTERN = rf"^(?:[0-9]{{1,20}}|{_UUID})$"
# The canonical UUID string, which is the longer of the two forms.
STORE_ID_MAX_LENGTH = 36
