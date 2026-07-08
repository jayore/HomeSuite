#!/usr/bin/env python3
"""
Shared PiPhone command runtime.

Purpose:
- centralize command execution modes
- preserve existing REPL behavior patterns
- future home for capture mode (scheduler / tools)

Current modes:
    test  -> safe stubbed mode
    live  -> real execution
"""

from __future__ import annotations

import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
from dataclasses import dataclass
from typing import Optional, Any

from request_context import (
    RequestContext,
    build_request_context,
    set_current_request_context,
    clear_current_request_context,
)
import interaction_flow

_SUPPORTED = {"test", "capture", "live"}

_runtime_module = None
_mode_initialized: Optional[str] = None


# --------------------------------------------------
# Result object
# --------------------------------------------------

@dataclass
class CommandResult:
    text: str
    mode: str
    return_value: Any = None
    context: Optional[RequestContext] = None
    action_occurred: bool = False


# --------------------------------------------------
# Env / mode setup
# --------------------------------------------------

def _configure_env(mode: str) -> None:
    """
    Runtime mode environment.

    command_runtime.py is a machine-facing command executor. It should never
    start handset/audio/GPIO runtime initialization. Even in live mode, we only
    want real Home Assistant writes through the existing command stack.

    This keeps scheduler/CLI execution from disturbing the production
    homesuite.service handset loop.
    """
    os.environ["PIPHONE_SKIP_PID_LOCK"] = "1"

    # Always avoid handset runtime side effects from command_runtime:
    # - no GPIO setup/cleanup
    # - no signal handler registration
    # - no audio warmups
    # - no PID lock contention with production homesuite.service
    os.environ["PIPHONE_NO_RUNTIME_INIT"] = "1"
    os.environ["PIPHONE_LIGHT_IMPORT"] = "1"
    os.environ["PIPHONE_COMMAND_RUNTIME"] = "1"

    if mode == "live":
        # Real HA writes, but no handset runtime init.
        os.environ["PIPHONE_LIVE"] = "1"
        os.environ.pop("PIPHONE_TEST_MODE", None)

    elif mode == "capture":
        # Full routing / HA state resolution, but no real writes.
        os.environ.pop("PIPHONE_LIVE", None)
        os.environ.pop("PIPHONE_TEST_MODE", None)

    elif mode == "test":
        # Lightweight / isolated dry-run.
        os.environ["PIPHONE_TEST_MODE"] = "1"
        os.environ.pop("PIPHONE_LIVE", None)

    else:
        raise ValueError(f"Unsupported mode: {mode}")

# --------------------------------------------------
# Safe write-blocking stubs
# --------------------------------------------------

def _stub_call_ha_service(*args, **kwargs):
    service = args[0] if len(args) >= 1 else kwargs.get("service")
    data = args[1] if len(args) >= 2 else kwargs.get("data", {})
    print(f"HA_STUB call: {service} {data}")
    try:
        import command_dispatch
        command_dispatch._ACTION_OCCURRED = True
    except Exception:
        pass
    return True


def _stub_ha_get_states(*args, **kwargs):
    return []


def _stub_refresh_runnable_cache(*args, **kwargs):
    return None


def _apply_test_stubs(runtime_module) -> None:
    """
    Matches spirit of current pptest behavior.
    """
    # Stubs go into command_dispatch and the imported main runtime module.
    try:
        import command_dispatch
        command_dispatch.call_ha_service = _stub_call_ha_service
        command_dispatch.ha_get_states = _stub_ha_get_states
    except Exception:
        pass

    try:
        main.call_ha_service = _stub_call_ha_service
    except Exception:
        pass

    try:
        runtime_module.ha_get_states = _stub_ha_get_states
    except Exception:
        pass

    try:
        runtime_module.refresh_runnable_cache = _stub_refresh_runnable_cache
    except Exception:
        pass


# --------------------------------------------------
# Runtime init
# --------------------------------------------------

def initialize_runtime(mode: str = "test"):
    global _runtime_module, _mode_initialized

    mode = (mode or "").strip().lower()

    if mode not in _SUPPORTED:
        raise ValueError(f"Unsupported mode: {mode}")

    if _runtime_module is not None:
        if _mode_initialized != mode:
            raise RuntimeError(
                f"Runtime already initialized as {_mode_initialized}; "
                f"cannot switch to {mode} in same process."
            )
        return _runtime_module

    _configure_env(mode)

    import main

    _runtime_module = main
    _mode_initialized = mode

    if mode == "test":
        _apply_test_stubs(main)

    elif mode == "capture":
        try:
            import command_dispatch
            command_dispatch.call_ha_service = _stub_call_ha_service
        except Exception:
            pass
        try:
            main.call_ha_service = _stub_call_ha_service
        except Exception:
            pass

    return main


# --------------------------------------------------
# Public API
# --------------------------------------------------

def run_command(
    text: str,
    mode: str = "test",
    *,
    source_id: Optional[str] = None,
    source_type: Optional[str] = None,
    origin: Optional[str] = None,
    source_room: Optional[str] = None,
    effective_target_room: Optional[str] = None,
) -> CommandResult:
    """
    Run a natural-language command through the normal stack.

    Phase 1 request-context support is intentionally metadata-only.
    It does not alter command routing or runtime behavior yet.
    """
    runtime_module = initialize_runtime(mode)

    context = build_request_context(
        source_id=source_id,
        source_type=source_type,
        origin=origin,
        source_room=source_room,
        effective_target_room=effective_target_room,
    )

    try:
        print(f"REQUEST_CONTEXT {context.to_log_dict()}")
    except Exception:
        pass

    set_current_request_context(context)
    try:
        try:
            import command_dispatch as _cd
            _cd._ACTION_OCCURRED = False
        except Exception:
            pass

        rv = runtime_module.process_device_commands(text)

        if rv:
            interaction_flow.inject_into_history(text, rv)

        try:
            import command_dispatch as _cd
            action_occurred = bool(_cd._ACTION_OCCURRED)
        except Exception:
            action_occurred = False

        return CommandResult(
            text=text,
            mode=mode,
            return_value=rv,
            context=context,
            action_occurred=action_occurred,
        )
    finally:
        clear_current_request_context()


def mode_label(mode: str) -> str:
    mode = (mode or "").lower()
    return {
        "test": "TEST MODE",
        "capture": "CAPTURE MODE",
        "live": "LIVE MODE",
    }.get(mode, mode.upper())


# --------------------------------------------------
# CLI helper (optional)
# --------------------------------------------------

if __name__ == "__main__":
    import sys

    live = "--live" in sys.argv
    capture = "--capture" in sys.argv

    if live:
        mode = "live"
    elif capture:
        mode = "capture"
    else:
        mode = "test"

    text = " ".join(
        x for x in sys.argv[1:]
        if x not in ("--live", "--capture")
    ).strip()

    if not text:
        print("Usage:")
        print("  python3 command_runtime.py turn off stair light")
        print("  python3 command_runtime.py --live turn off stair light")
        raise SystemExit(1)

    print(f"PiPhone command_runtime ({mode_label(mode)})")
    run_command(text, mode=mode)
