"""Configuration validation: fail fast on invalid environment variables.

Validates numeric and boolean environment variables at startup, before the
server binds. A bad value stops with one plain line and no traceback, like
the other startup checks.
"""

import os

NUMERIC = {
    "DIKDIK_PORT": (1, 65535),
    "DIKDIK_EVENT_RETENTION_SECONDS": (0, 86400 * 30),
}

BOOLEANS = {"DIKDIK_DORMANT", "DIKDIK_NO_BROWSER", "DIKDIK_ROTATE_TOKEN"}


def validate_config(env: dict[str, str] | None = None) -> list[str]:
    """Return a list of validation error messages (empty if valid)."""
    env = env or dict(os.environ)
    errors = []
    for key, (lo, hi) in NUMERIC.items():
        value = env.get(key)
        if value is None or value == "":
            continue
        try:
            num = int(value)
            if not lo <= num <= hi:
                errors.append(f"{key} must be between {lo} and {hi}")
        except ValueError:
            errors.append(f"{key} must be a number, got {value!r}")
    for key in BOOLEANS:
        value = env.get(key)
        if value is None or value == "":
            continue
        if value.lower() not in ("1", "true", "0", "false", ""):
            errors.append(f"{key} must be 1/true/0/false, got {value!r}")
    return errors


def check_config_or_exit(env: dict[str, str] | None = None) -> None:
    """Validate config and exit with a clear message if invalid."""
    errors = validate_config(env)
    if errors:
        import sys
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(2)