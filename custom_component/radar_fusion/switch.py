"""Switch platform entrypoint for Radar Fusion."""

from __future__ import annotations

from .platforms.switch import async_setup_entry as _async_setup_entry

async_setup_entry = _async_setup_entry
