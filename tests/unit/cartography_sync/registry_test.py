import pytest

from cartography_sync.registry import (
    MODULE_REGISTRY,
    build_module_argv,
    validate_module_params,
)


def test_cve_argv_contains_selected_modules_and_fixed_flags():
    argv = build_module_argv("cve", {})
    assert argv == ["--selected-modules=create-indexes,cve,analysis", "--cve-enabled"]


def test_aws_defaults_apply_sync_all_profiles():
    argv = build_module_argv("aws", {})
    assert "--aws-sync-all-profiles" in argv
    assert "--permission-relationships-file=/etc/cartography/permission_relationships.yaml" in argv


def test_aws_boolean_false_omits_flag():
    argv = build_module_argv("aws", {"aws_sync_all_profiles": False})
    assert "--aws-sync-all-profiles" not in argv


def test_string_value_joins_flag_in_single_token():
    argv = build_module_argv("aws", {"aws_requested_syncs": "ec2,s3"})
    assert "--aws-requested-syncs=ec2,s3" in argv
    # The value never becomes its own argv token.
    assert "ec2,s3" not in argv


def test_credential_env_var_names_are_fixed_constants():
    argv = build_module_argv("crowdstrike", {})
    assert "--crowdstrike-client-id-env-var=CROWDSTRIKE_CLIENT_ID" in argv
    assert "--crowdstrike-client-secret-env-var=CROWDSTRIKE_CLIENT_SECRET" in argv


def test_unknown_module_rejected():
    errors = validate_module_params("gcp", {})
    assert errors and "unknown cartography module" in errors[0]


def test_unknown_param_rejected():
    errors = validate_module_params("cve", {"neo4j_uri": "bolt://evil:7687"})
    assert errors and "does not allow param" in errors[0]


@pytest.mark.parametrize(
    "value",
    [
        "ec2 --bogus",  # space → separate-token attempt
        "ec2\n--bogus",  # newline
        "ec2;rm",  # shell metachar (belt and suspenders; there is no shell)
        'ec2"',  # quote
        "ec2$HOME",  # env expansion chars
        "",  # empty
        123,  # wrong type
        True,  # wrong type
    ],
)
def test_string_param_injection_values_rejected(value):
    errors = validate_module_params("aws", {"aws_requested_syncs": value})
    assert errors


def test_okta_org_id_pattern_enforced():
    assert validate_module_params("okta", {"okta_org_id": "acme.okta-1"}) == []
    assert validate_module_params("okta", {"okta_org_id": "acme org"})
    assert validate_module_params("okta", {"okta_org_id": "acme/../etc"})


def test_boolean_param_requires_bool():
    errors = validate_module_params("aws", {"aws_sync_all_profiles": "true"})
    assert errors and "must be a boolean" in errors[0]


def test_build_module_argv_raises_on_invalid_config():
    with pytest.raises(ValueError):
        build_module_argv("aws", {"aws_requested_syncs": "ec2 --bogus"})
    with pytest.raises(ValueError):
        build_module_argv("nope", {})


def test_registry_entries_are_well_formed():
    for name, spec in MODULE_REGISTRY.items():
        assert spec.name == name
        assert spec.description
        assert all(token.startswith("--") for token in spec.fixed_argv)
        assert all(env and env.upper() == env for env in spec.required_env)
        for flag in spec.flags:
            assert flag.flag.startswith("--")
            assert flag.type in ("boolean", "number", "string", "enum")
        # Every module builds a valid default argv.
        argv = build_module_argv(name, {})
        assert argv[0] == f"--selected-modules=create-indexes,{name},analysis"
