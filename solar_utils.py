"""Compatibility entry point for sunrise and sunset schedule resolution.

The implementation moved to ``astronomy_utils`` when Home Suite adopted
Astral for local, network-free calculations. Keep this import stable for the
dispatcher and any external tools that already use ``resolve_solar_event``.
"""

from astronomy_utils import resolve_solar_event


__all__ = ["resolve_solar_event"]
