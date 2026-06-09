import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from CyVer import PropertiesValidator, SchemaValidator

from reporting import settings
from reporting.schema.mcp_config import ToolParamDef, undeclared_cypher_parameters
from reporting.services.reporting_neo4j import _get_async_neo4j_client, _get_sync_neo4j_client

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

# Built-in Neo4j procedures that are read-only, side-effect-free, and perform no
# network or filesystem I/O. These are the only procedures permitted by default.
# Operators can allow more CALL procedures (apoc.*, gds.*, custom plugins) via
# the QUERY_VALIDATOR_ALLOWED_PROCEDURES setting. Names are normalized lowercase.
#
# Procedure guarding is an allowlist rather than a denylist: any installed
# plugin procedure that Neo4j classifies as read-only but performs I/O (e.g.
# neosemantics `n10s.rdf.import.fetch`, custom HTTP procedures) would otherwise
# bypass both the EXPLAIN write check and the SSRF regex.
_DEFAULT_ALLOWED_PROCEDURES = frozenset(
    {
        "db.labels",
        "db.relationshiptypes",
        "db.propertykeys",
        "db.schema.visualization",
        "db.schema.nodetypeproperties",
        "db.schema.reltypeproperties",
        "db.ping",
    }
)

# A procedure invocation: the CALL keyword followed by a dotted procedure name.
# Each segment is a bare identifier or a backtick-quoted segment, with optional
# whitespace around the dots (comments are stripped to spaces before scanning).
# `CALL {...}` and `CALL (...) {...}` subqueries have no name token here and so
# are not matched.
_PROCEDURE_CALL_RE = re.compile(
    r"\bCALL\s+((?:`[^`]*`|[A-Za-z_]\w*)(?:\s*\.\s*(?:`[^`]*`|[A-Za-z_]\w*))*)",
    re.IGNORECASE,
)

_BACKTICK_OR_SPACE_RE = re.compile(r"[`\s]")

# Built-in LOAD CSV — an SSRF/exfiltration vector (also covers file://).
_LOAD_CSV_RE = re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE)

# The USE clause routes a query to a specific graph in the DBMS, letting a
# caller escape Seizu's configured graph and read any other database in the
# DBMS (including `system`) via `USE other`, `USE composite.constituent`, or
# `USE graph.byName(...)`. Seizu queries always target the default graph, so
# every USE clause is blocked. It is matched only at clause-start positions —
# query start, after UNION/NEXT/conditional-branch keywords, or at the start of
# a CALL {} subquery — optionally after a CYPHER version prefix.
#
# The detector has two branches so that the keyword USE is told apart from an
# identifier named `use`, without depending on a closed list of the clauses
# that may follow a USE clause (Cypher keeps adding query-initial clauses):
#
#  1. `USE` followed by an ordinary graph reference — anything not starting
#     with a CASE/branch keyword. This is unambiguous: nothing other than a USE
#     clause has that shape at a clause-start position, so no following clause
#     needs to be matched.
#  2. `USE` followed by a reference whose name collides with ELSE/END/WHEN/THEN
#     (a database literally named that). Here the negative lookahead in branch 1
#     cannot fire, so the reference is disambiguated from a CASE result variable
#     (`... THEN use ELSE alt END`) by requiring a real clause keyword next.
#
_USE_FOLLOWING_CLAUSE = (
    r"(?:"
    r"OPTIONAL\s+(?:MATCH|CALL)|LOAD\s+CSV|DETACH\s+DELETE|"
    r"MATCH|CALL|RETURN|WITH|UNWIND|CREATE|MERGE|DELETE|SET|REMOVE|FOREACH|"
    r"SHOW|START|STOP|DROP|ALTER|GRANT|DENY|REVOKE|TERMINATE|"
    r"NEXT|UNION|FINISH|INSERT|FOR|LET|WHEN"
    r")\b"
)
_USE_CLAUSE_BODY = (
    r"USE\s+(?:"
    r"(?!(?:ELSE|END|WHEN|THEN)\b)(?:graph\s*\.|`|[A-Za-z_])"
    r"|"
    rf"(?:ELSE|END|WHEN|THEN)\b\s+{_USE_FOLLOWING_CLAUSE}"
    r")"
)
# An importing WITH in an old-style subquery: a bare variable list or `WITH *`
# (Neo4j accepts both as the import boundary). No projections/aliasing allowed.
_IMPORTING_WITH = r"WITH\s+(?:\*|(?:`[^`]*`|[A-Za-z_]\w*)(?:\s*,\s*(?:`[^`]*`|[A-Za-z_]\w*))*)\s+"
_USE_CLAUSE_RE = re.compile(
    r"(?:^|\b(?:UNION|NEXT|THEN|ELSE)\b|\{)\s*"
    r"(?:CYPHER\s+[\w.]+(?:\s+\w+\s*=\s*\w+)*\s+)?"
    rf"{_USE_CLAUSE_BODY}",
    re.IGNORECASE,
)
_USE_AFTER_IMPORTING_WITH_RE = re.compile(
    r"\{\s*"
    r"(?:CYPHER\s+[\w.]+(?:\s+\w+\s*=\s*\w+)*\s+)?"
    rf"{_IMPORTING_WITH}{_USE_CLAUSE_BODY}",
    re.IGNORECASE,
)

# Neo4j administration/catalog commands can be classified as read-only by
# EXPLAIN while still exposing operational metadata or causing side effects
# such as terminating transactions. Block SHOW as a top-level admin/catalog
# entry point rather than enumerating every Cypher 5 modifier form
# (SHOW ALL INDEXES, SHOW RANGE INDEXES, SHOW CONSTRAINTS, etc.).
#
# Administration *procedures* (`CALL dbms.*`, `CALL db.createLabel`,
# `CALL tx.setMetaData`, etc.) are not listed here — they are blocked by the
# EXPLAIN write check (DBMS/WRITE/SCHEMA modes are non-read) and, as a backstop,
# by the procedure allowlist below.
_ADMIN_COMMAND_RE = re.compile(
    r"\bSHOW\b|"
    r"\bTERMINATE\s+TRANSACTIONS?\b|"
    r"\b(?:START|STOP)\s+DATABASE\b|"
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:DATABASE|COMPOSITE\s+DATABASE|ALIAS|USER|ROLE)\b|"
    r"\bDROP\s+(?:DATABASE|COMPOSITE\s+DATABASE|ALIAS|USER|ROLE|SERVER)\b|"
    r"\bALTER\s+(?:DATABASE|ALIAS|USER|CURRENT\s+USER|SERVER)\b|"
    r"\bALTER\s+CURRENT\s+GRAPH(?:\s+TYPE)?\b|"
    r"\bRENAME\s+(?:USER|ROLE|SERVER)\b|"
    r"\b(?:GRANT|DENY|REVOKE)\b|"
    r"\b(?:ENABLE|DEALLOCATE)\s+(?:SERVER|DATABASES?)\b|"
    r"\bREALLOCATE\s+DATABASES\b",
    re.IGNORECASE,
)

# Dangerous function namespaces invoked WITHOUT a CALL keyword. `apoc.cypher.*`
# executes arbitrary inner Cypher; `gds.*` functions create graph-catalog
# state; `ai.*` / `genai.*` functions send graph-derived data to external model
# providers. These cannot be allowlisted per-procedure because they are
# functions, not procedures, so they are blocked by namespace.
#
# Procedure-call (`CALL ...`) name spans are masked out before these patterns
# run, so an allowlisted `CALL` into one of these namespaces is not re-flagged
# here. QUERY_VALIDATOR_ALLOWED_PROCEDURES only affects procedure calls; it does
# not permit dangerous functions in the same namespace.
#
# apoc is guarded only at the `apoc.cypher.` namespace: APOC's pure-computation
# functions (`apoc.text.*`, `apoc.convert.*`, ...) are safe and widely used.
_DANGEROUS_FUNCTION_NAMESPACES: tuple[tuple[str, str], ...] = (
    (
        "apoc.cypher.",
        r"(?:\bapoc\b|`apoc`)\s*\.\s*(?:cypher\b|`cypher`)\s*\.|`apoc\.cypher\.",
    ),
    (
        "gds.",
        r"\bgds\b\s*\.|`gds`\s*\.|`gds\.",
    ),
    (
        "ai.",
        r"(?:\bai\b|`ai`)\s*\.\s*(?:[A-Za-z_]\w*|`[^`]*`)\s*\.|`ai\.[^`]*\.",
    ),
    (
        "genai.",
        r"(?:\bgenai\b|`genai`)\s*\.\s*(?:[A-Za-z_]\w*|`[^`]*`)\s*\.|`genai\.[^`]*\.",
    ),
)


def _decode_cypher_unicode_escapes(query: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    return _UNICODE_ESCAPE_RE.sub(replace, query)


def _strip_cypher_comments(query: str) -> str:
    """Replace Cypher comments with spaces using a linear scan.

    This deliberately mirrors the previous conservative behavior: `//` inside a
    string literal is still treated as a comment in the stripped form. The
    validator also scans the original query, so URLs in strings cannot hide
    dangerous tokens that appear later in the raw text.
    """

    stripped: list[str] = []
    index = 0
    query_len = len(query)
    while index < query_len:
        current = query[index]
        next_char = query[index + 1] if index + 1 < query_len else ""

        if current == "/" and next_char == "*":
            stripped.append(" ")
            index += 2
            while index + 1 < query_len and not (query[index] == "*" and query[index + 1] == "/"):
                index += 1
            index = min(index + 2, query_len)
            continue

        if current == "/" and next_char == "/":
            stripped.append(" ")
            index += 2
            while index < query_len and query[index] != "\n":
                index += 1
            continue

        stripped.append(current)
        index += 1

    return "".join(stripped)


def _keyword_scan_targets(query: str) -> tuple[str, ...]:
    decoded = _decode_cypher_unicode_escapes(query)
    stripped = _strip_cypher_comments(query)
    stripped_decoded = _strip_cypher_comments(decoded)
    decoded_stripped = _decode_cypher_unicode_escapes(stripped)
    return query, stripped, decoded, stripped_decoded, decoded_stripped


def _normalize_procedure(raw_name: str) -> str:
    """Normalize a matched procedure name: drop backticks/whitespace, lowercase.

    `\\`apoc\\`.\\`load\\`.\\`json\\`` and `apoc . load . json` both normalize
    to `apoc.load.json`.
    """

    return _BACKTICK_OR_SPACE_RE.sub("", raw_name).lower()


def _resolve_allowed_procedures() -> tuple[frozenset[str], tuple[str, ...]]:
    """Combine the default safe procedures with the configured extras.

    Returns (exact_names, namespace_prefixes). A configured entry ending in `.`
    is treated as a namespace prefix (e.g. `apoc.` allows every apoc procedure);
    any other entry is an exact procedure name.
    """

    exact = set(_DEFAULT_ALLOWED_PROCEDURES)
    prefixes: list[str] = []
    for entry in settings.QUERY_VALIDATOR_ALLOWED_PROCEDURES:
        normalized = _normalize_procedure(entry)
        if not normalized:
            continue
        if normalized.endswith("."):
            prefixes.append(normalized)
        else:
            exact.add(normalized)
    return frozenset(exact), tuple(prefixes)


def _procedure_allowed(name: str, allowed_exact: frozenset[str], allowed_prefixes: tuple[str, ...]) -> bool:
    if name in allowed_exact:
        return True
    return any(name.startswith(prefix) for prefix in allowed_prefixes)


def _build_dangerous_function_re() -> re.Pattern[str]:
    """Compile the dangerous-function-namespace regex."""

    return re.compile(
        "|".join(pattern for _, pattern in _DANGEROUS_FUNCTION_NAMESPACES),
        re.IGNORECASE,
    )


def _scan_for_dangerous_constructs(query: str) -> str | None:
    """Scan a read-classified query for SSRF, admin, and disallowed-procedure
    constructs that Neo4j does not block on its own.

    Returns an error message if the query must be blocked, otherwise None. The
    original, comment-stripped, and unicode-decoded forms are all scanned (see
    validate_query for the rationale).
    """

    allowed_exact, allowed_prefixes = _resolve_allowed_procedures()
    dangerous_function_re = _build_dangerous_function_re()

    for target in _keyword_scan_targets(query):
        if (
            _ADMIN_COMMAND_RE.search(target)
            or _USE_CLAUSE_RE.search(target)
            or _USE_AFTER_IMPORTING_WITH_RE.search(target)
        ):
            return "Write queries are not allowed"
        if _LOAD_CSV_RE.search(target):
            return "Write queries are not allowed"

        # Enforce the procedure allowlist, masking each allowed procedure name
        # so an allowlisted CALL is not re-flagged by the function-namespace
        # check below.
        masked_segments: list[str] = []
        cursor = 0
        for match in _PROCEDURE_CALL_RE.finditer(target):
            name = _normalize_procedure(match.group(1))
            if not _procedure_allowed(name, allowed_exact, allowed_prefixes):
                return f"Procedure '{name}' is not permitted"
            masked_segments.append(target[cursor : match.start(1)])
            masked_segments.append(" " * (match.end(1) - match.start(1)))
            cursor = match.end(1)
        masked_segments.append(target[cursor:])
        masked = "".join(masked_segments)

        if dangerous_function_re.search(masked):
            return "Write queries are not allowed"

    return None


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


async def validate_query(query: str, params: dict[str, Any] | None = None) -> ValidationResult:
    result = ValidationResult()
    driver = _get_async_neo4j_client()

    # Syntax validation and write detection via EXPLAIN.
    # EXPLAIN never executes the query (no side effects).  The returned summary
    # includes query_type: 'r' (read), 'w' (write), 'rw' (read-write), or 's'
    # (schema).  We only permit 'r'.  Params are forwarded so Neo4j can plan
    # parameterized queries correctly, avoiding false ParameterNotProvided errors.
    try:
        _, summary, _ = await driver.execute_query(
            f"EXPLAIN {query}",
            parameters_=params or {},
        )
        if summary.query_type != "r":
            result.errors.append("Write queries are not allowed")
            return result
        if summary.notifications:
            for notification in summary.notifications:
                code = notification.get("code", "")
                description = notification.get("description", str(notification))
                if code == "Neo.ClientNotification.Statement.ParameterNotProvided":
                    # When params are omitted (e.g. /api/v1/validate called
                    # without params), surface as a warning rather than
                    # blocking the caller.
                    result.warnings.append(description)
                elif code in (
                    "Neo.DatabaseError.Statement.ExecutionFailed",
                    "Neo.ClientError.Statement.SyntaxError",
                    "Neo.ClientNotification.Statement.UnsatisfiableRelationshipTypeExpression",
                ):
                    result.errors.append(description)
    except Exception as e:
        error_msg = getattr(e, "message", str(e))
        if "EXPLAIN" in error_msg:
            error_msg = error_msg.split("EXPLAIN")[0].strip('"')
        result.errors.append(error_msg)
        return result

    if result.has_errors:
        return result

    # SSRF / exfiltration / admin / procedure guard — Neo4j classifies LOAD CSV,
    # APOC HTTP procedures, and many catalog commands as query_type='r', so they
    # pass the check above.
    #
    # We check original, comment-stripped, and unicode-decoded forms:
    # - Stripped catches comment injection (CALL /* x */ apoc., LOAD /* x */ CSV)
    # - Original catches patterns that appear after a '//' inside a string
    #   literal (e.g. a URL like http://...) that the comment stripper would
    #   incorrectly consume, hiding a dangerous token that follows it.
    # - Unicode-decoded catches Cypher escapes in keywords and procedure names
    #   (e.g. SHOW, CALL apoc., apoc.cypher.).
    dangerous = _scan_for_dangerous_constructs(query)
    if dangerous is not None:
        result.errors.append(dangerous)
        return result

    # Schema validation — warning, query still executes
    # CyVer validators are synchronous; run them in a thread pool.
    sync_driver = _get_sync_neo4j_client()
    schema_validator = SchemaValidator(sync_driver)
    schema_is_valid, schema_metadata = await asyncio.to_thread(schema_validator.validate, query)
    if not schema_is_valid:
        result.warnings.extend(schema_metadata)

    # Property validation — warning, query still executes
    properties_validator = PropertiesValidator(sync_driver)
    properties_is_valid, properties_metadata = await asyncio.to_thread(properties_validator.validate, query)
    if not properties_is_valid:
        result.warnings.extend(properties_metadata)

    return result


async def validate_tool_cypher(cypher: str, parameters: list[ToolParamDef]) -> ValidationResult:
    """Validate a toolset tool's query for create/update.

    Runs the standard query validation, then adds the tool-specific rule that
    every ``$parameter`` the query references must be declared in the tool's
    parameters — otherwise the tool fails every call with a Neo4j
    ParameterMissing error.
    """
    result = await validate_query(cypher)
    undeclared = undeclared_cypher_parameters(cypher, parameters)
    if undeclared:
        names = ", ".join(f"${name}" for name in undeclared)
        result.errors.append(
            f"Cypher references undeclared parameter(s): {names}. "
            "Declare them in the tool's parameters or remove them from the query."
        )
    return result
