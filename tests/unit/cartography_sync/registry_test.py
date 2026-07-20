import pytest

from cartography_sync.registry import (
    ALWAYS_ENABLED_MODULES,
    MODULE_REGISTRY,
    build_module_argv,
    parse_enabled_modules,
    validate_module_params,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("aws,github", ["aws", "github"]),
        (" aws, github ,,aws ", ["aws", "github"]),
    ],
)
def test_parse_enabled_modules(raw, expected):
    assert parse_enabled_modules(raw) == expected


def test_cve_argv_contains_selected_modules_and_fixed_flags():
    # Exactly one sync stage per subprocess: create-indexes and analysis are
    # separate registry modules placed as their own stages, never repeated
    # per module.
    argv = build_module_argv("cve", {})
    assert argv == [
        "--selected-modules=cve",
        "--cve-enabled",
        "--cve-api-key-env-var=CARTOGRAPHY_NIST_NVD_TOKEN",
        "--nist-cve-url=https://services.nvd.nist.gov/rest/json/cves/2.0/",
    ]


def test_structural_stages_are_registered_and_always_enabled():
    assert ALWAYS_ENABLED_MODULES <= set(MODULE_REGISTRY)
    assert build_module_argv("create-indexes", {}) == ["--selected-modules=create-indexes"]
    assert build_module_argv("analysis", {}) == [
        "--selected-modules=analysis",
        "--analysis-job-directory=/etc/cartography/analysis",
    ]


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
    assert "--crowdstrike-client-id-env-var=CARTOGRAPHY_CROWDSTRIKE_CLIENT_ID" in argv
    assert "--crowdstrike-client-secret-env-var=CARTOGRAPHY_CROWDSTRIKE_CLIENT_SECRET" in argv


def test_repeatable_string_list_emits_one_flag_per_value():
    argv = build_module_argv("cve_metadata", {"cve_metadata_src": ["nvd", "epss"]})
    assert "--cve-metadata-src=nvd" in argv
    assert "--cve-metadata-src=epss" in argv
    assert validate_module_params("cve_metadata", {"cve_metadata_src": ["invalid"]})


def test_unknown_module_rejected():
    errors = validate_module_params("not-a-module", {})
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
    assert len(MODULE_REGISTRY) == 52
    for name, spec in MODULE_REGISTRY.items():
        assert spec.name == name
        assert spec.description
        assert all(token.startswith("--") for token in spec.fixed_argv)
        assert all(env.startswith("CARTOGRAPHY_") and env.upper() == env for env in spec.required_env)
        assert all(env.startswith("CARTOGRAPHY_") and env.upper() == env for env in spec.optional_env)
        declared_env = set(spec.required_env) | set(spec.optional_env)
        mappings = dict(spec.env_mappings)
        assert set(mappings) <= declared_env
        assert all(source.startswith("CARTOGRAPHY_") for source in mappings)
        aliases = dict(spec.env_aliases)
        assert set(aliases) <= declared_env
        assert all(alias.startswith("CARTOGRAPHY_") for names in aliases.values() for alias in names)
        for token in spec.fixed_argv:
            flag, _, value = token.partition("=")
            if flag.endswith("-env-var"):
                assert value in declared_env
            if any(part in flag for part in ("-file", "-kubeconfig", "-source", "-directory")):
                assert value.startswith("/")
        for flag in spec.flags:
            assert flag.flag.startswith("--")
            assert flag.type in ("boolean", "number", "string", "enum", "string_list")
        # Every module builds a valid default argv.
        argv = build_module_argv(name, {})
        assert argv[0] == f"--selected-modules={name}"
