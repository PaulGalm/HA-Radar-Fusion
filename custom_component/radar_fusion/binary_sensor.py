"""Binary sensor platform entrypoint for Radar Fusion."""

from __future__ import annotations

from .platforms.binary_sensor import async_setup_entry as _async_setup_entry

async_setup_entry = _async_setup_entry
