import pytest

from reporting.schema.mcp_config import (
    MAX_SLUG_COMPONENT_LEN,
    ToolParamDef,
    cypher_parameter_names,
    render_skill_template,
    template_placeholders,
    undeclared_cypher_parameters,
    validate_lower_snake_id,
    validate_skill_template,
)


def test_cypher_parameter_names_extracts_refs_ignoring_strings_and_comments():
    cypher = (
        "MATCH (n:CVE) WHERE n.id = $cve_id // not a param: $nope\n"
        "AND n.note = 'mentions $ignored' AND n.x = $`odd name` RETURN n LIMIT $limit"
    )
    assert cypher_parameter_names(cypher) == {"cve_id", "odd name", "limit"}


def test_undeclared_cypher_parameters_flags_missing_declarations():
    cypher = "MATCH (n) WHERE n.id = $cve_id RETURN n LIMIT $limit"
    only_limit = [ToolParamDef(name="limit", type="integer")]
    assert undeclared_cypher_parameters(cypher, only_limit) == ["cve_id"]

    both = [ToolParamDef(name="cve_id", type="string"), ToolParamDef(name="limit", type="integer")]
    assert undeclared_cypher_parameters(cypher, both) == []
    # A declared-but-unused parameter is fine (not flagged).
    assert undeclared_cypher_parameters("MATCH (n) RETURN n", both) == []


_PARAM_REQUEST = ToolParamDef(name="request", type="string", required=True)
_PARAM_DRY_RUN = ToolParamDef(name="dry_run", type="boolean", required=False, default=True)


def test_validate_lower_snake_id_enforces_length_cap():
    # At the cap is fine; two capped components fit the 64-char provider limit.
    at_cap = "a" * MAX_SLUG_COMPONENT_LEN
    assert validate_lower_snake_id(at_cap) == at_cap
    assert len(f"{at_cap}__{at_cap}") == 64

    with pytest.raises(ValueError, match="at most 31 characters"):
        validate_lower_snake_id("a" * (MAX_SLUG_COMPONENT_LEN + 1))

    # Pattern errors still take precedence.
    with pytest.raises(ValueError, match="lower_snake_case"):
        validate_lower_snake_id("Not_Snake")


def test_template_placeholders_finds_vars():
    assert template_placeholders("Hello {% $request %}") == {"request"}


def test_template_placeholders_ignores_escaped():
    assert template_placeholders(r"Use \{% $name %} syntax") == set()


def test_template_placeholders_mixed():
    tmpl = r"Value: {% $request %} — escaped: \{% $example %}"
    assert template_placeholders(tmpl) == {"request"}


def test_validate_skill_template_ok():
    errors = validate_skill_template([_PARAM_REQUEST], "Request: {% $request %}")
    assert errors == []


def test_validate_skill_template_unknown_placeholder():
    errors = validate_skill_template([_PARAM_REQUEST], "{% $unknown %}")
    assert any("unknown" in e for e in errors)


def test_validate_skill_template_escaped_not_validated():
    # \{% $name %} is literal text — no parameter named 'name' is required
    errors = validate_skill_template([_PARAM_REQUEST], r"Use \{% $name %} syntax. Value: {% $request %}")
    assert errors == []


def test_render_skill_template_substitutes_vars():
    rendered, errors = render_skill_template(
        [_PARAM_REQUEST],
        "Request: {% $request %}",
        {"request": "hello"},
    )
    assert errors == []
    assert rendered == "Request: hello"


def test_render_skill_template_unescapes_escaped_vars():
    rendered, errors = render_skill_template(
        [_PARAM_REQUEST],
        r"Syntax: \{% $name %}. Value: {% $request %}",
        {"request": "world"},
    )
    assert errors == []
    assert rendered == "Syntax: {% $name %}. Value: world"


def test_render_skill_template_escaped_only():
    rendered, errors = render_skill_template(
        [],
        r"Use \{% $param %} for variables.",
        {},
    )
    assert errors == []
    assert rendered == "Use {% $param %} for variables."


def test_render_skill_template_boolean_coercion():
    rendered, errors = render_skill_template(
        [_PARAM_DRY_RUN],
        "dry_run={% $dry_run %}",
        {"dry_run": True},
    )
    assert errors == []
    assert rendered == "dry_run=True"
