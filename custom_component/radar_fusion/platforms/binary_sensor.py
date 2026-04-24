"""Binary sensor platform for Radar Fusion integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import ATTR_FLOOR, ATTR_ZONE_NAME, DOMAIN
from ..hub import RadarFusionHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor platform from config entry."""
    del hass

    hub: RadarFusionHub = config_entry.runtime_data

    # Create binary sensor entity for each zone on each floor
    entities = []
    zones = hub.get_zones()
    floors = hub.get_floors()

    for floor in floors:
        for zone_name in zones.keys():
            entities.append(
                ZonePresenceSensor(
                    hub=hub,
                    floor=floor,
                    zone_name=zone_name,
                    config_entry=config_entry,
                )
            )

    if entities:
        async_add_entities(entities)


class ZonePresenceSensor(BinarySensorEntity):
    """Binary sensor entity for zone presence detection."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = "presence"

    def __init__(
        self,
        hub: RadarFusionHub,
        floor: str,
        zone_name: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""
        self.hub = hub
        self.floor = floor
        self.zone_name = zone_name
        self.config_entry = config_entry
        self._unsub_update = None

        # Set up entity properties
        self._attr_unique_id = f"{DOMAIN}_{floor}_{zone_name}"
        self._attr_name = f"{zone_name}"

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
        """Return True if presence detected in zone."""
        return self.hub.get_zone_presence(self.floor, self.zone_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity state attributes."""
        attributes: dict[str, Any] = {
            ATTR_FLOOR: self.floor,
            ATTR_ZONE_NAME: self.zone_name,
        }

        # Add target count in this zone for debugging
        zone = self.hub.get_zones().get(self.zone_name)
        if zone:
            from ..data import point_in_polygon

            targets = self.hub.get_fused_targets(self.floor)
            targets_in_zone = [
                target
                for target in targets
                if point_in_polygon((target.x, target.y), zone.polygon)
            ]
            attributes["target_count"] = len(targets_in_zone)

        return attributes
