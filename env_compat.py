"""Environment variable compatibility helpers for HomeSuite renames.

Public deployments should prefer HOMESUITE_* names. The original PiPhone-era
PIPHONE_* names remain accepted as fallbacks so older units, scripts, and
satellites keep working during the public-readiness rename.
"""

from __future__ import annotations

import os
from typing import MutableMapping, Optional

PRIMARY_PREFIX = "HOMESUITE_"
LEGACY_PREFIX = "PIPHONE_"


def primary_name_for(name: str) -> str:
    if name.startswith(LEGACY_PREFIX):
        return PRIMARY_PREFIX + name[len(LEGACY_PREFIX):]
    return name


def legacy_name_for(name: str) -> str:
    if name.startswith(PRIMARY_PREFIX):
        return LEGACY_PREFIX + name[len(PRIMARY_PREFIX):]
    return name


def install_homesuite_env_aliases(environ: Optional[MutableMapping[str, str]] = None) -> int:
    """Mirror HOMESUITE_* env vars into PIPHONE_* names when legacy is absent.

    This is intentionally one-way. If both names are set, the explicit legacy
    value wins for old code paths and old deployments keep their behavior.
    """
    env = environ if environ is not None else os.environ
    installed = 0
    for key, value in list(env.items()):
        if not key.startswith(PRIMARY_PREFIX):
            continue
        legacy = legacy_name_for(key)
        if legacy not in env:
            env[legacy] = value
            installed += 1
    return installed


def env_get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a prefixed env var, preferring HOMESUITE_* over PIPHONE_*."""
    primary = primary_name_for(name)
    if primary in os.environ:
        return os.environ.get(primary)
    return os.environ.get(name, default)


def env_truthy(name: str, default: str = "0") -> bool:
    value = env_get(name, default)
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
