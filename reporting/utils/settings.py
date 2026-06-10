from os import getenv


def bool_env(var_name: str, default: bool = False) -> bool:
    """
    Get an environment variable coerced to a boolean value.

    Example:

        Bash:
            $ export SOME_VAL=True
        settings.py:
            SOME_VAL = bool_env('SOME_VAL', False)

    Arguments:

        var_name: The name of the environment variable.
        default: The default to use if `var_name` is not specified in the
        environment.

    Returns: `var_name` or `default` coerced to a boolean using the following
        rules:

            "False", "false" or "" => False
            Any other non-empty string => True
    """
    test_val = getenv(var_name, default)
    # Explicitly check for 'False', 'false', and '0' since all non-empty
    # string are normally coerced to True.
    if test_val in ("False", "false", "0"):
        return False
    return bool(test_val)


def int_env(var_name: str, default: int = 0) -> int:
    """
    Get an environment variable coerced to an integer value.
    This has the same arguments as bool_env. If a value cannot be coerced to an
    integer, a ValueError will be raised.
    """
    return int(getenv(var_name, default))


def float_env(var_name: str, default: float = 0.0) -> float:
    """
    Get an environment variable coerced to a float.
    This has the same arguments as bool_env. If a value cannot be coerced to a
    float, a ValueError will be raised.
    """
    return float(getenv(var_name, default))


def str_env(var_name: str, default: str = "") -> str:
    """
    Get an environment variable as a string.
    This has the same arguments as bool_env.
    """
    return getenv(var_name, default)


def list_env(var_name: str, default: list[str] = None) -> list:
    """
    Get an environment variable as a string.
    This has the same arguments as bool_env.
    """
    val = str_env(var_name)
    if not val:
        if default is not None:
            return default
        else:
            return []
    return val.split(",")
