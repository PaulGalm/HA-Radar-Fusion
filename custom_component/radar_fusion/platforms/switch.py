"""Switch platform for Radar Fusion integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import ATTR_BLOCKZONE_NAME, ATTR_FLOOR, DOMAIN
from ..hub import RadarFusionHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch platform from config entry."""
    del hass

    hub: RadarFusionHub = config_entry.runtime_data

    # Create switch entity for each blockzone on each floor
    entities = []
    blockzones = hub.get_blockzones()
    floors = hub.get_floors()

    for floor in floors:
        for blockzone_name in blockzones.keys():
            entities.append(
                BlockzoneToggleSwitch(
                    hub=hub,
                    floor=floor,
                    blockzone_name=blockzone_name,
                    config_entry=config_entry,
                )
            )

    if entities:
        async_add_entities(entities)


class BlockzoneToggleSwitch(SwitchEntity):
    """Switch entity for toggling blockzone active state."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hub: RadarFusionHub,
        floor: str,
        blockzone_name: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        self.hub = hub
        self.floor = floor
        self.blockzone_name = blockzone_name
        self.config_entry = config_entry
        self._unsub_update = None

        # Set up entity properties
        self._attr_unique_id = f"{DOMAIN}_{floor}_{blockzone_name}_toggle"
        self._attr_name = f"{blockzone_name}"

        # Device info for registry - link to floor device
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{floor}")},
            "name": f"Apartment - {floor}",
            "manufacturer": "Radar Fusion",
        }

    async def async_added_to_hass(self) -> None:
        """Register update callback when entity added to Home Assistant."""
        await super().async_added_to_hass()

        self._unsub_update = self.hub.register_update_callback(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister update callback when entity removed from Home Assistant."""
        await super().async_will_remove_from_hass()

        if self._unsub_update:
            self._unsub_update()

    @callback
    def _handle_update(self) -> None:
        """Handle update from hub."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return True if blockzone is active."""
        return self.hub.get_blockzone_state(self.blockzone_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on blockzone (activate blocking)."""
        self.hub.set_blockzone_active(self.blockzone_name, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off blockzone (deactivate blocking)."""
        self.hub.set_blockzone_active(self.blockzone_name, False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity state attributes."""
        attributes: dict[str, Any] = {
            ATTR_FLOOR: self.floor,
            ATTR_BLOCKZONE_NAME: self.blockzone_name,
        }

        return attributes
