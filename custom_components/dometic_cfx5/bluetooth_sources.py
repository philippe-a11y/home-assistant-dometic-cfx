"""Enumerate available Bluetooth sources (adapters and proxies) for selection.

Used by both the config flow and the options flow so the user can pick which
source connects to the cooler: automatic (nearest reachable), a specific
ESPHome proxy, or a local adapter.
"""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import SOURCE_AUTO


def _readable_source_name(scanner) -> str:
    """Return a human-friendly label for a scanner, falling back to its MAC."""
    # habluetooth scanner objects expose a source (MAC) and usually a name.
    # Only read properties, never modify (per HA's scanner API guidance).
    name = getattr(scanner, "name", None)
    source = getattr(scanner, "source", None) or "?"
    if name and name != source:
        return f"{name} ({source})"
    return source


def async_source_options(hass: HomeAssistant) -> dict[str, str]:
    """Return a mapping of source value -> label for a selection dropdown.

    Always includes the automatic option first. Then every currently known
    scanner (local adapters and proxies), keyed by its source MAC.
    """
    options: dict[str, str] = {SOURCE_AUTO: "Automatic (nearest reachable)"}
    try:
        scanners = bluetooth.async_current_scanners(hass)
    except Exception:  # noqa: BLE001 - be defensive; selection is optional
        scanners = []
    for scanner in scanners:
        source = getattr(scanner, "source", None)
        if not source:
            continue
        options[source.upper()] = _readable_source_name(scanner)
    return options
