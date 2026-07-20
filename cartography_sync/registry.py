"""Allowlist registry of cartography intel modules and their settable flags.

This is the security boundary for user-configured cartography runs: a module
run may only ever produce argv the registry approves. Users pick a module and
set typed params; every credential flag hardcodes the env-var *name* (the
``--*-env-var`` indirection cartography supports) and every file path is a
fixed container path — user input never names an env var, a path, or the
Neo4j URI. Values are validated per flag and against a global character
allowlist, and always emitted as a single ``--flag=value`` token, so a value
can never become its own argv token.

Validated on both sides: the reporting app rejects bad configs at save time,
and the sync worker's activity re-validates and rebuilds argv itself, so a
forged Temporal payload still can't escape the allowlist.
"""

import re
from dataclasses import dataclass

# Global character allowlist for string values (defense in depth on top of the
# per-flag pattern): no whitespace, quotes, or control characters. Some flags
# intentionally accept regex/URL punctuation; argv is never passed to a shell.
_VALUE_RE = re.compile(r"[A-Za-z0-9._/@:,+=?&%#{}\[\]\\^$|()*!-]+")

BOOLEAN = "boolean"
NUMBER = "number"
STRING = "string"
ENUM = "enum"
STRING_LIST = "string_list"

_FLAG_TYPES = (BOOLEAN, NUMBER, STRING, ENUM, STRING_LIST)


def parse_enabled_modules(value: str | None) -> list[str]:
    """Parse the shared comma-separated module allowlist.

    Empty input means every registered module is enabled. Whitespace around
    entries is insignificant, empty entries are ignored, and duplicates are
    removed while preserving their first occurrence. Both the reporting
    process and the credential-bearing sync worker use this parser so their
    enforcement cannot diverge.
    """
    if not value:
        return []
    return list(dict.fromkeys(name for item in value.split(",") if (name := item.strip())))


@dataclass(frozen=True)
class FlagSpec:
    """One user-settable cartography CLI flag."""

    name: str  # param name in scheduled-query config, e.g. "aws_sync_all_profiles"
    flag: str  # argv flag it maps to, e.g. "--aws-sync-all-profiles"
    type: str  # one of BOOLEAN | NUMBER | STRING | ENUM | STRING_LIST
    description: str = ""
    # Applied when the param is absent from the run's params.
    default: bool | int | str | None = None
    pattern: str | None = None  # re.fullmatch for STRING values
    choices: tuple[str, ...] = ()  # for ENUM and STRING_LIST item values
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True)
class CartographyModuleSpec:
    """One cartography sync stage runnable from a scheduled sync."""

    name: str  # the --selected-modules name
    description: str
    flags: tuple[FlagSpec, ...] = ()
    # Constant argv appended for every run: credential env-var-NAME flags and
    # fixed container paths. Never derived from user input.
    fixed_argv: tuple[str, ...] = ()
    # Env vars the activity must copy into the subprocess (missing → config
    # error before the subprocess starts).
    required_env: tuple[str, ...] = ()
    # Env vars copied into the subprocess only when present (e.g. AWS SDK
    # credential/region configuration supplied by the operator).
    optional_env: tuple[str, ...] = ()


# Credential-free structural stages that must never be blocked by the operator
# intel-module allowlist: existing CARTOGRAPHY_ENABLED_MODULES values don't
# list them, yet every useful pipeline needs indexes first and analysis after
# ingestion.
ALWAYS_ENABLED_MODULES: frozenset[str] = frozenset({"create-indexes", "analysis"})


_AWS_OPTIONAL_ENV = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_EC2_METADATA_SERVICE_ENDPOINT",
    "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)


def _flag(
    name: str,
    *,
    type: str = STRING,
    default: bool | int | str | None = None,
    pattern: str | None = None,
    choices: tuple[str, ...] = (),
    min_value: int | None = None,
    max_value: int | None = None,
    description: str = "",
) -> FlagSpec:
    """Build a conventional ``--kebab-case`` option from a config name."""
    return FlagSpec(
        name=name,
        flag=f"--{name.replace('_', '-')}",
        type=type,
        default=default,
        pattern=pattern,
        choices=choices,
        min_value=min_value,
        max_value=max_value,
        description=description,
    )


def _module(
    name: str,
    *,
    flags: tuple[FlagSpec, ...] = (),
    fixed_argv: tuple[str, ...] = (),
    required_env: tuple[str, ...] = (),
    optional_env: tuple[str, ...] = (),
    description: str | None = None,
) -> CartographyModuleSpec:
    return CartographyModuleSpec(
        name=name,
        description=description or f"Cartography {name.replace('_', ' ')} intelligence module.",
        flags=flags,
        fixed_argv=fixed_argv,
        required_env=required_env,
        optional_env=optional_env,
    )


# This is deliberately exhaustive for Cartography 0.139.0. Options whose value
# is an environment-variable name or a filesystem path are fixed here rather
# than exposed to schedule authors. Deprecated aliases (the ``entra`` names and
# legacy report-source flags) are omitted in favor of their canonical option.
MODULE_REGISTRY: dict[str, CartographyModuleSpec] = {
    "create-indexes": _module(
        "create-indexes",
        description="Creates Cartography's Neo4j indexes; run before ingestion modules.",
    ),
    "airbyte": _module(
        "airbyte",
        flags=(
            _flag("airbyte_client_id"),
            _flag("airbyte_api_url", default="https://api.airbyte.com/v1"),
        ),
        fixed_argv=("--airbyte-client-secret-env-var=AIRBYTE_CLIENT_SECRET",),
        required_env=("AIRBYTE_CLIENT_SECRET",),
    ),
    "databricks": _module(
        "databricks",
        flags=(
            _flag("databricks_workspace_url"),
            _flag("databricks_client_id"),
            _flag("databricks_account_id"),
            _flag("databricks_account_host", default="https://accounts.cloud.databricks.com"),
            _flag("databricks_account_client_id"),
        ),
        fixed_argv=(
            "--databricks-token-env-var=DATABRICKS_TOKEN",
            "--databricks-client-secret-env-var=DATABRICKS_CLIENT_SECRET",
            "--databricks-account-client-secret-env-var=DATABRICKS_ACCOUNT_CLIENT_SECRET",
        ),
        optional_env=("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_ACCOUNT_CLIENT_SECRET"),
    ),
    "anthropic": _module(
        "anthropic",
        fixed_argv=("--anthropic-apikey-env-var=ANTHROPIC_API_KEY",),
        required_env=("ANTHROPIC_API_KEY",),
    ),
    "aws": _module(
        "aws",
        flags=(
            _flag(
                "aws_sync_all_profiles",
                type=BOOLEAN,
                default=True,
                description="Sync every discovered named AWS profile.",
            ),
            _flag("aws_regions", pattern=r"[a-z0-9,-]+"),
            _flag("aws_organization_account_ids", pattern=r"[0-9,]+"),
            _flag("aws_best_effort_mode", type=BOOLEAN),
            _flag("aws_cloudtrail_management_events_lookback_hours", type=NUMBER),
            _flag("aws_requested_syncs", pattern=r"[a-z0-9_,:-]+"),
            _flag(
                "aws_guardduty_severity_threshold",
                type=ENUM,
                choices=("LOW", "MEDIUM", "HIGH", "CRITICAL"),
            ),
            _flag("experimental_aws_inspector_batch", type=NUMBER, default=1000),
            _flag("aws_tagging_api_cleanup_batch", type=NUMBER, default=1000),
        ),
        fixed_argv=("--permission-relationships-file=/etc/cartography/permission_relationships.yaml",),
        optional_env=_AWS_OPTIONAL_ENV,
        description="AWS asset inventory using the mounted AWS configuration or AWS environment credentials.",
    ),
    "azure": _module(
        "azure",
        flags=(
            _flag("azure_sync_all_subscriptions", type=BOOLEAN),
            _flag("azure_sp_auth", type=BOOLEAN),
            _flag("azure_tenant_id"),
            _flag("azure_client_id"),
            _flag("azure_subscription_id"),
        ),
        fixed_argv=(
            "--azure-client-secret-env-var=AZURE_CLIENT_SECRET",
            "--azure-permission-relationships-file=/etc/cartography/azure_permission_relationships.yaml",
        ),
        optional_env=("AZURE_CLIENT_SECRET",),
    ),
    "microsoft": _module(
        "microsoft",
        flags=(_flag("microsoft_tenant_id"), _flag("microsoft_client_id")),
        fixed_argv=("--microsoft-client-secret-env-var=MICROSOFT_CLIENT_SECRET",),
        required_env=("MICROSOFT_CLIENT_SECRET",),
    ),
    "cloudflare": _module(
        "cloudflare",
        fixed_argv=("--cloudflare-token-env-var=CLOUDFLARE_TOKEN",),
        required_env=("CLOUDFLARE_TOKEN",),
    ),
    "crowdstrike": _module(
        "crowdstrike",
        flags=(_flag("crowdstrike_api_url"),),
        fixed_argv=(
            "--crowdstrike-client-id-env-var=CROWDSTRIKE_CLIENT_ID",
            "--crowdstrike-client-secret-env-var=CROWDSTRIKE_CLIENT_SECRET",
        ),
        required_env=("CROWDSTRIKE_CLIENT_ID", "CROWDSTRIKE_CLIENT_SECRET"),
    ),
    "gcp": _module(
        "gcp",
        flags=(_flag("gcp_requested_syncs", pattern=r"[a-z0-9_,:-]+"),),
        fixed_argv=("--gcp-permission-relationships-file=/etc/cartography/gcp_permission_relationships.yaml",),
        optional_env=("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CONFIG"),
    ),
    "googleworkspace": _module(
        "googleworkspace",
        flags=(
            _flag(
                "googleworkspace_auth_method",
                type=ENUM,
                default="delegated",
                choices=("delegated", "oauth", "default"),
            ),
        ),
        fixed_argv=("--googleworkspace-tokens-env-var=GOOGLEWORKSPACE_GOOGLE_APPLICATION_CREDENTIALS",),
        required_env=("GOOGLEWORKSPACE_GOOGLE_APPLICATION_CREDENTIALS",),
    ),
    "gsuite": _module(
        "gsuite",
        flags=(
            _flag(
                "gsuite_auth_method",
                type=ENUM,
                default="delegated",
                choices=("delegated", "oauth", "default"),
            ),
        ),
        fixed_argv=("--gsuite-tokens-env-var=GSUITE_GOOGLE_APPLICATION_CREDENTIALS",),
        required_env=("GSUITE_GOOGLE_APPLICATION_CREDENTIALS",),
    ),
    "cve": _module(
        "cve",
        flags=(_flag("nist_cve_url", default="https://services.nvd.nist.gov/rest/json/cves/2.0/"),),
        fixed_argv=("--cve-enabled", "--cve-api-key-env-var=NIST_NVD_TOKEN"),
        optional_env=("NIST_NVD_TOKEN",),
        description="NIST NVD CVE feed.",
    ),
    "oci": _module(
        "oci",
        flags=(_flag("oci_sync_all_profiles", type=BOOLEAN),),
        optional_env=("OCI_CONFIG_FILE", "OCI_CONFIG_PROFILE", "OCI_CLI_AUTH", "OCI_CLI_REGION"),
    ),
    "okta": _module(
        "okta",
        flags=(
            _flag("okta_org_id", pattern=r"[A-Za-z0-9.-]+"),
            _flag("okta_base_domain", default="okta.com"),
            _flag("okta_saml_role_regex", default=r"^aws\#\S+\#(?{{role}}[\w\-]+)\#(?{{accountid}}\d+)$"),
        ),
        fixed_argv=("--okta-api-key-env-var=OKTA_API_KEY",),
        required_env=("OKTA_API_KEY",),
    ),
    "openai": _module(
        "openai",
        flags=(_flag("openai_org_id"),),
        fixed_argv=("--openai-apikey-env-var=OPENAI_API_KEY",),
        required_env=("OPENAI_API_KEY",),
    ),
    "github": _module(
        "github",
        flags=(_flag("github_commit_lookback_days", type=NUMBER, default=30),),
        fixed_argv=("--github-config-env-var=GITHUB_TOKEN",),
        required_env=("GITHUB_TOKEN",),
    ),
    "gitlab": _module(
        "gitlab",
        flags=(
            _flag("gitlab_url", default="https://gitlab.com"),
            _flag("gitlab_organization_id", type=NUMBER),
            _flag("gitlab_commits_since_days", type=NUMBER, default=90),
        ),
        fixed_argv=("--gitlab-token-env-var=GITLAB_TOKEN",),
        required_env=("GITLAB_TOKEN",),
    ),
    "digitalocean": _module(
        "digitalocean",
        fixed_argv=("--digitalocean-token-env-var=DIGITALOCEAN_TOKEN",),
        required_env=("DIGITALOCEAN_TOKEN",),
    ),
    "kandji": _module(
        "kandji",
        flags=(_flag("kandji_base_uri"), _flag("kandji_tenant_id")),
        fixed_argv=("--kandji-token-env-var=KANDJI_TOKEN",),
        required_env=("KANDJI_TOKEN",),
    ),
    "keycloak": _module(
        "keycloak",
        flags=(
            _flag("keycloak_client_id"),
            _flag("keycloak_url"),
            _flag("keycloak_realm", default="master"),
        ),
        fixed_argv=("--keycloak-client-secret-env-var=KEYCLOAK_CLIENT_SECRET",),
        required_env=("KEYCLOAK_CLIENT_SECRET",),
    ),
    "salesforce": _module(
        "salesforce",
        flags=(
            _flag("salesforce_login_url", default="https://login.salesforce.com"),
            _flag("salesforce_client_id"),
            _flag("salesforce_username"),
        ),
        fixed_argv=(
            "--salesforce-client-secret-env-var=SALESFORCE_CLIENT_SECRET",
            "--salesforce-private-key-env-var=SALESFORCE_PRIVATE_KEY",
        ),
        optional_env=("SALESFORCE_CLIENT_SECRET", "SALESFORCE_PRIVATE_KEY"),
    ),
    "kubernetes": _module(
        "kubernetes",
        flags=(_flag("managed_kubernetes"),),
        fixed_argv=("--k8s-kubeconfig=/etc/cartography/kube.config",),
        optional_env=_AWS_OPTIONAL_ENV,
    ),
    "jumpcloud": _module(
        "jumpcloud",
        flags=(_flag("jumpcloud_org_id"),),
        fixed_argv=("--jumpcloud-api-key-env-var=JUMPCLOUD_API_KEY",),
        required_env=("JUMPCLOUD_API_KEY",),
    ),
    "lastpass": _module(
        "lastpass",
        fixed_argv=(
            "--lastpass-cid-env-var=LASTPASS_CID",
            "--lastpass-provhash-env-var=LASTPASS_PROVHASH",
        ),
        required_env=("LASTPASS_CID", "LASTPASS_PROVHASH"),
    ),
    "bigfix": _module(
        "bigfix",
        flags=(_flag("bigfix_username"), _flag("bigfix_root_url")),
        fixed_argv=("--bigfix-password-env-var=BIGFIX_PASSWORD",),
        required_env=("BIGFIX_PASSWORD",),
    ),
    "duo": _module(
        "duo",
        flags=(_flag("duo_api_hostname"),),
        fixed_argv=(
            "--duo-api-key-env-var=DUO_API_KEY",
            "--duo-api-secret-env-var=DUO_API_SECRET",
        ),
        required_env=("DUO_API_KEY", "DUO_API_SECRET"),
    ),
    "workday": _module(
        "workday",
        flags=(_flag("workday_api_url"), _flag("workday_api_login")),
        fixed_argv=("--workday-api-password-env-var=WORKDAY_API_PASSWORD",),
        required_env=("WORKDAY_API_PASSWORD",),
    ),
    "scaleway": _module(
        "scaleway",
        flags=(_flag("scaleway_org"), _flag("scaleway_access_key")),
        fixed_argv=("--scaleway-secret-key-env-var=SCALEWAY_SECRET_KEY",),
        required_env=("SCALEWAY_SECRET_KEY",),
    ),
    "semgrep": _module(
        "semgrep",
        flags=(_flag("semgrep_dependency_ecosystems"),),
        fixed_argv=(
            "--semgrep-app-token-env-var=SEMGREP_APP_TOKEN",
            "--semgrep-oss-source=/etc/cartography/reports/semgrep",
        ),
        optional_env=("SEMGREP_APP_TOKEN",),
    ),
    "sentry": _module(
        "sentry",
        flags=(_flag("sentry_org"), _flag("sentry_host", default="https://sentry.io")),
        fixed_argv=("--sentry-token-env-var=SENTRY_TOKEN",),
        required_env=("SENTRY_TOKEN",),
    ),
    "snipeit": _module(
        "snipeit",
        flags=(_flag("snipeit_base_uri"), _flag("snipeit_tenant_id")),
        fixed_argv=("--snipeit-token-env-var=SNIPEIT_TOKEN",),
        required_env=("SNIPEIT_TOKEN",),
    ),
    "socketdev": _module(
        "socketdev",
        fixed_argv=("--socketdev-token-env-var=SOCKETDEV_TOKEN",),
        required_env=("SOCKETDEV_TOKEN",),
    ),
    "tailscale": _module(
        "tailscale",
        flags=(
            _flag("tailscale_org"),
            _flag("tailscale_base_url", default="https://api.tailscale.com/api/v2"),
        ),
        fixed_argv=(
            "--tailscale-token-env-var=TAILSCALE_TOKEN",
            "--tailscale-oauth-client-id-env-var=TAILSCALE_OAUTH_CLIENT_ID",
            "--tailscale-oauth-client-secret-env-var=TAILSCALE_OAUTH_CLIENT_SECRET",
        ),
        optional_env=("TAILSCALE_TOKEN", "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_CLIENT_SECRET"),
    ),
    "jamf": _module(
        "jamf",
        flags=(_flag("jamf_base_uri"), _flag("jamf_user")),
        fixed_argv=("--jamf-password-env-var=JAMF_PASSWORD",),
        required_env=("JAMF_PASSWORD",),
    ),
    "pagerduty": _module(
        "pagerduty",
        flags=(_flag("pagerduty_request_timeout", type=NUMBER),),
        fixed_argv=("--pagerduty-api-key-env-var=PAGERDUTY_API_KEY",),
        required_env=("PAGERDUTY_API_KEY",),
    ),
    "docker_scout": _module(
        "docker_scout",
        fixed_argv=("--docker-scout-source=/etc/cartography/reports/docker-scout",),
    ),
    "trivy": _module("trivy", fixed_argv=("--trivy-source=/etc/cartography/reports/trivy",)),
    "syft": _module("syft", fixed_argv=("--syft-source=/etc/cartography/reports/syft",)),
    "aibom": _module("aibom", fixed_argv=("--aibom-source=/etc/cartography/reports/aibom",)),
    "ubuntu": _module(
        "ubuntu",
        flags=(_flag("ubuntu_security_api_url"),),
        fixed_argv=("--ubuntu-security-enabled",),
    ),
    "sentinelone": _module(
        "sentinelone",
        flags=(
            _flag("sentinelone_account_ids"),
            _flag("sentinelone_site_ids"),
            _flag("sentinelone_api_url"),
        ),
        fixed_argv=("--sentinelone-api-token-env-var=SENTINELONE_API_TOKEN",),
        required_env=("SENTINELONE_API_TOKEN",),
    ),
    "tenable": _module(
        "tenable",
        flags=(
            _flag("tenable_url"),
            _flag("tenable_tenant_id"),
            _flag("tenable_findings_lookback_days", type=NUMBER, default=180, min_value=1),
        ),
        fixed_argv=(
            "--tenable-access-key-env-var=TENABLE_ACCESS_KEY",
            "--tenable-secret-key-env-var=TENABLE_SECRET_KEY",
        ),
        required_env=("TENABLE_ACCESS_KEY", "TENABLE_SECRET_KEY"),
    ),
    "cve_metadata": _module(
        "cve_metadata",
        flags=(_flag("cve_metadata_src", type=STRING_LIST, choices=("nvd", "epss")),),
        fixed_argv=("--cve-metadata-nist-api-key-env-var=NIST_NVD_TOKEN",),
        optional_env=("NIST_NVD_TOKEN",),
        description="CVE metadata enrichment from NVD and EPSS; run after CVE-producing modules.",
    ),
    "slack": _module(
        "slack",
        flags=(_flag("slack_teams"), _flag("slack_channels_memberships", type=BOOLEAN)),
        fixed_argv=("--slack-token-env-var=SLACK_TOKEN",),
        required_env=("SLACK_TOKEN",),
    ),
    "spacelift": _module(
        "spacelift",
        flags=(
            _flag("spacelift_api_endpoint"),
            _flag("spacelift_ec2_ownership_aws_profile"),
            _flag("spacelift_ec2_ownership_s3_bucket"),
            _flag("spacelift_ec2_ownership_s3_prefix"),
        ),
        fixed_argv=(
            "--spacelift-api-token-env-var=SPACELIFT_API_TOKEN",
            "--spacelift-api-key-id-env-var=SPACELIFT_API_KEY_ID",
            "--spacelift-api-key-secret-env-var=SPACELIFT_API_KEY_SECRET",
        ),
        optional_env=("SPACELIFT_API_TOKEN", "SPACELIFT_API_KEY_ID", "SPACELIFT_API_KEY_SECRET", *_AWS_OPTIONAL_ENV),
    ),
    "workos": _module(
        "workos",
        flags=(_flag("workos_client_id"),),
        fixed_argv=("--workos-apikey-env-var=WORKOS_API_KEY",),
        required_env=("WORKOS_API_KEY",),
    ),
    "subimage": _module(
        "subimage",
        flags=(
            _flag("subimage_tenant_url"),
            _flag("subimage_authkit_url", default="https://auth.subimage.io"),
        ),
        fixed_argv=(
            "--subimage-client-id-env-var=SUBIMAGE_CLIENT_ID",
            "--subimage-client-secret-env-var=SUBIMAGE_CLIENT_SECRET",
        ),
        required_env=("SUBIMAGE_CLIENT_ID", "SUBIMAGE_CLIENT_SECRET"),
    ),
    "vercel": _module(
        "vercel",
        flags=(_flag("vercel_team_id"), _flag("vercel_base_url", default="https://api.vercel.com")),
        fixed_argv=("--vercel-token-env-var=VERCEL_TOKEN",),
        required_env=("VERCEL_TOKEN",),
    ),
    "circleci": _module(
        "circleci",
        flags=(
            _flag("circleci_base_url", default="https://circleci.com/api/v2"),
            _flag("circleci_project_slugs"),
        ),
        fixed_argv=("--circleci-token-env-var=CIRCLECI_TOKEN",),
        required_env=("CIRCLECI_TOKEN",),
    ),
    "ontology": _module(
        "ontology",
        flags=(_flag("ontology_users_source"), _flag("ontology_devices_source")),
    ),
    "analysis": _module(
        "analysis",
        fixed_argv=("--analysis-job-directory=/etc/cartography/analysis",),
        description="Runs analysis jobs from the fixed mounted analysis directory.",
    ),
}


def get_module_spec(module: str) -> CartographyModuleSpec | None:
    return MODULE_REGISTRY.get(module)


def _validate_flag_value(spec: FlagSpec, value: object) -> str | None:
    """Return an error string when ``value`` is not acceptable for ``spec``."""
    if spec.type == BOOLEAN:
        if not isinstance(value, bool):
            return f"param '{spec.name}' must be a boolean"
        return None
    if spec.type == NUMBER:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"param '{spec.name}' must be an integer"
        if spec.min_value is not None and value < spec.min_value:
            return f"param '{spec.name}' must be >= {spec.min_value}"
        if spec.max_value is not None and value > spec.max_value:
            return f"param '{spec.name}' must be <= {spec.max_value}"
        return None
    if spec.type == STRING_LIST:
        if not isinstance(value, list) or not value:
            return f"param '{spec.name}' must be a non-empty list of strings"
        for item in value:
            if not isinstance(item, str) or not item:
                return f"param '{spec.name}' must contain only non-empty strings"
            if not _VALUE_RE.fullmatch(item):
                return f"param '{spec.name}' contains characters outside the allowed set"
            if spec.choices and item not in spec.choices:
                return f"param '{spec.name}' values must be one of {sorted(spec.choices)}"
        return None
    # STRING / ENUM
    if not isinstance(value, str) or not value:
        return f"param '{spec.name}' must be a non-empty string"
    if not _VALUE_RE.fullmatch(value):
        return f"param '{spec.name}' contains characters outside the allowed set"
    if spec.type == ENUM and value not in spec.choices:
        return f"param '{spec.name}' must be one of {sorted(spec.choices)}"
    if spec.type == STRING and spec.pattern is not None and not re.fullmatch(spec.pattern, value):
        return f"param '{spec.name}' does not match the allowed pattern"
    return None


def validate_module_params(module: str, params: dict[str, object]) -> list[str]:
    """Validate a module run's params against the registry; [] means valid."""
    spec = MODULE_REGISTRY.get(module)
    if spec is None:
        return [f"unknown cartography module '{module}' (known: {sorted(MODULE_REGISTRY)})"]
    errors: list[str] = []
    flags_by_name = {flag.name: flag for flag in spec.flags}
    for name, value in params.items():
        flag = flags_by_name.get(name)
        if flag is None:
            allowed = sorted(flags_by_name) if flags_by_name else "none"
            errors.append(f"module '{module}' does not allow param '{name}' (allowed: {allowed})")
            continue
        error = _validate_flag_value(flag, value)
        if error is not None:
            errors.append(f"module '{module}': {error}")
    return errors


def build_module_argv(module: str, params: dict[str, object]) -> list[str]:
    """Build the cartography argv for one validated module run.

    Raises ``ValueError`` when validation fails — callers must not pass the
    result of an unvalidated config to a subprocess.
    """
    errors = validate_module_params(module, params)
    if errors:
        raise ValueError("; ".join(errors))
    spec = MODULE_REGISTRY[module]
    # Exactly one sync stage per subprocess: create-indexes and analysis are
    # their own registry modules users place as stages, so parallel module
    # runs never repeat them or run analysis mid-ingestion.
    argv = [f"--selected-modules={module}", *spec.fixed_argv]
    for flag in spec.flags:
        value = params.get(flag.name, flag.default)
        if value is None:
            continue
        if flag.type == BOOLEAN:
            if value is True:
                argv.append(flag.flag)
        elif flag.type == STRING_LIST:
            assert isinstance(value, list)  # validated above or trusted registry default
            argv.extend(f"{flag.flag}={item}" for item in value)
        elif isinstance(value, (int, str)) and not isinstance(value, bool):
            # Validation (or the trusted registry default) guarantees the type
            # matches the flag; values always join the flag in one token.
            argv.append(f"{flag.flag}={value}")
    return argv
