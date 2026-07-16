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
# per-flag pattern): no whitespace, quotes, or shell/control characters.
_VALUE_RE = re.compile(r"[A-Za-z0-9._/@:,+=-]+")

BOOLEAN = "boolean"
NUMBER = "number"
STRING = "string"
ENUM = "enum"

_FLAG_TYPES = (BOOLEAN, NUMBER, STRING, ENUM)


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
    type: str  # one of BOOLEAN | NUMBER | STRING | ENUM
    description: str = ""
    # Applied when the param is absent from the run's params.
    default: bool | int | str | None = None
    pattern: str | None = None  # re.fullmatch for STRING values
    choices: tuple[str, ...] = ()  # for ENUM
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
    # Internal stages (create-indexes, analysis) are added to every pipeline
    # by the workflow input builder and cannot be selected in user config.
    internal: bool = False


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
)


MODULE_REGISTRY: dict[str, CartographyModuleSpec] = {
    # Internal stages: cartography's own execution model runs create-indexes
    # before ingestion and analysis after it. The workflow input builder adds
    # these once per pipeline (first/last stage) so parallel module runs don't
    # each repeat them — subprocesses run exactly one --selected-modules stage.
    "create-indexes": CartographyModuleSpec(
        name="create-indexes",
        description="Creates cartography's Neo4j indexes (implicit first stage of every pipeline).",
        internal=True,
    ),
    "analysis": CartographyModuleSpec(
        name="analysis",
        description="Cartography's post-ingestion analysis jobs (implicit last stage of every pipeline).",
        internal=True,
    ),
    "aws": CartographyModuleSpec(
        name="aws",
        description="AWS asset inventory (uses the mounted AWS config/credentials).",
        flags=(
            FlagSpec(
                name="aws_sync_all_profiles",
                flag="--aws-sync-all-profiles",
                type=BOOLEAN,
                default=True,
                description="Sync every profile in the AWS config instead of only the default one.",
            ),
            FlagSpec(
                name="aws_requested_syncs",
                flag="--aws-requested-syncs",
                type=STRING,
                pattern=r"[a-z0-9_,:-]+",
                description="Comma-separated AWS resource types to sync (default: all).",
            ),
        ),
        fixed_argv=("--permission-relationships-file=/etc/cartography/permission_relationships.yaml",),
        optional_env=_AWS_OPTIONAL_ENV,
    ),
    "github": CartographyModuleSpec(
        name="github",
        description="GitHub organizations, repositories, and dependencies.",
        fixed_argv=("--github-config-env-var=GITHUB_TOKEN",),
        required_env=("GITHUB_TOKEN",),
    ),
    "cve": CartographyModuleSpec(
        name="cve",
        description="NIST NVD CVE feed.",
        fixed_argv=("--cve-enabled",),
    ),
    "cve_metadata": CartographyModuleSpec(
        name="cve_metadata",
        description=(
            "CVE metadata enrichment from the NIST NVD API. Must run after"
            " CVE-producing modules (e.g. github) — place it in a later stage."
        ),
        fixed_argv=("--cve-metadata-nist-api-key-env-var=NIST_NVD_TOKEN",),
        required_env=("NIST_NVD_TOKEN",),
    ),
    "crowdstrike": CartographyModuleSpec(
        name="crowdstrike",
        description="CrowdStrike Falcon hosts and vulnerability findings.",
        fixed_argv=(
            "--crowdstrike-client-id-env-var=CROWDSTRIKE_CLIENT_ID",
            "--crowdstrike-client-secret-env-var=CROWDSTRIKE_CLIENT_SECRET",
        ),
        required_env=("CROWDSTRIKE_CLIENT_ID", "CROWDSTRIKE_CLIENT_SECRET"),
    ),
    "kubernetes": CartographyModuleSpec(
        name="kubernetes",
        description="Kubernetes clusters (uses the mounted kubeconfig).",
        fixed_argv=("--k8s-kubeconfig=/etc/cartography/kube.config",),
    ),
    "okta": CartographyModuleSpec(
        name="okta",
        description="Okta organization users, groups, and applications.",
        flags=(
            FlagSpec(
                name="okta_org_id",
                flag="--okta-org-id",
                type=STRING,
                pattern=r"[A-Za-z0-9.-]+",
                description="Okta organization id to sync.",
            ),
        ),
        fixed_argv=("--okta-api-key-env-var=OKTA_API_KEY",),
        required_env=("OKTA_API_KEY",),
    ),
    "pagerduty": CartographyModuleSpec(
        name="pagerduty",
        description="PagerDuty users, teams, schedules, and services.",
        fixed_argv=("--pagerduty-api-key-env-var=PAGERDUTY_API_KEY",),
        required_env=("PAGERDUTY_API_KEY",),
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
    # separate pipeline stages (see the internal registry entries), so
    # parallel module runs never repeat them or run analysis mid-ingestion.
    argv = [f"--selected-modules={module}", *spec.fixed_argv]
    for flag in spec.flags:
        value = params.get(flag.name, flag.default)
        if value is None:
            continue
        if flag.type == BOOLEAN:
            if value is True:
                argv.append(flag.flag)
        elif isinstance(value, (int, str)) and not isinstance(value, bool):
            # Validation (or the trusted registry default) guarantees the type
            # matches the flag; values always join the flag in one token.
            argv.append(f"{flag.flag}={value}")
    return argv
