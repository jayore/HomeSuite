"""
Shared runtime-mode helper for direct-effect modules.

Purpose:
-   provide one canonical answer for the narrow question:
  "should this module perform real side effects right now?"

Current rule:
-   explicit PIPHONE_TEST_MODE=1 disables real side effects
-   command_runtime non-live modes (such as capture) disable real side effects
-   PIPHONE_NO_RUNTIME_INIT=1 does NOT by itself imply dry-run/test behavior

Important scope note:
-   this helper is intentionally narrow
-   use it for modules that directly execute real effects outside the normal
  injected/stubbed helper seams
-   do NOT blindly replace all env/mode logic with this helper

Why:
Most PiPhone modules do not need runtime-mode awareness because they call
injected helpers such as call_ha_service(...), and the runtime already decides
whether those helpers are live or stubbed.

This helper exists for modules that bypass those shared seams and therefore
need a consistent test-vs-live decision for direct execution.

Current intended use:
-   plex_controls.py
-   announcement_controls.py

Intentionally deferred / not automatically covered:
-   scheduler persistence/validation policy in schedule_controls.py

That scheduler logic answers a different question:
-   not just "are real side effects allowed?"
-   but also "should this environment persist scheduled jobs?"

For example, command_runtime capture mode should validate/preview routing
without persisting a schedule, even though production homesuite.service may not
set PIPHONE_LIVE globally. That logic should stay separate unless/until we
design a broader shared runtime-policy helper on purpose.
"""

import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass


def is_test_mode() -> bool:
    """Explicit dry-run/test mode."""
    return os.environ.get("PIPHONE_TEST_MODE") == "1"


def is_command_runtime_nonlive() -> bool:
    """
    True when running under command_runtime in a non-live mode such as capture.

    command_runtime live mode sets:
        PIPHONE_COMMAND_RUNTIME=1
        PIPHONE_LIVE=1

    command_runtime capture mode sets:
        PIPHONE_COMMAND_RUNTIME=1
        PIPHONE_LIVE is not 1
    """
    return (
        os.environ.get("PIPHONE_COMMAND_RUNTIME") == "1"
        and os.environ.get("PIPHONE_LIVE") != "1"
    )


def allow_real_effects() -> bool:
    """
    Real side effects are allowed only when we are not in explicit test mode
    and not in a non-live command_runtime mode such as capture.
    """
    return (not is_test_mode()) and (not is_command_runtime_nonlive())
