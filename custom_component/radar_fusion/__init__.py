"""The Radar Fusion integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BLOCKZONES,
    CONF_SENSORS,
    CONF_TARGET_TIMEOUT,
    CONF_ZONES,
    PLATFORMS,
)
from .data import Blockzone, RadarSensor, Zone
from .hub import RadarFusionHub

_LOGGER = logging.getLogger(__name__)

# Type alias for config entry with runtime data
type RadarFusionConfigEntry = ConfigEntry[RadarFusionHub]


async def async_setup_entry(hass: HomeAssistant, entry: RadarFusionConfigEntry) -> bool:
    """Set up Radar Fusion from a config entry."""
    _LOGGER.debug("Setting up Radar Fusion integration")

    # Parse configuration data
    data = entry.data

    # Load sensors configuration
    sensors_data = data.get(CONF_SENSORS, [])
    sensors = [RadarSensor.from_dict(sensor_data) for sensor_data in sensors_data]

    # Load zones configuration
    zones_data = data.get(CONF_ZONES, [])
    zones = [Zone.from_dict(zone_data) for zone_data in zones_data]

    # Load blockzones configuration
    blockzones_data = data.get(CONF_BLOCKZONES, [])
    blockzones = [
        Blockzone.from_dict(blockzone_data) for blockzone_data in blockzones_data
    ]

    # Get target timeout
    target_timeout = data.get(CONF_TARGET_TIMEOUT, 5)

    # Create hub coordinator
    hub = RadarFusionHub(
        hass=hass,
        sensors=sensors,
        zones=zones,
        blockzones=blockzones,
        target_timeout=target_timeout,
    )

    # Set up hub
    await hub.async_setup()

    # Store hub on the config entry for platform access
    entry.runtime_data = hub

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Radar Fusion integration setup complete")

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RadarFusionConfigEntry
) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Radar Fusion integration")

    # Unload platforms
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    # Shutdown hub
    if entry.runtime_data:
        await entry.runtime_data.async_shutdown()

    _LOGGER.debug("Radar Fusion integration unload complete")

    return True
