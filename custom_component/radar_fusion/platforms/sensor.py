"""Sensor platform for Radar Fusion integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from ..const import ATTR_FLOOR, DOMAIN
from ..hub import RadarFusionHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform from config entry."""
    del hass

    hub: RadarFusionHub = config_entry.runtime_data

    # Create sensor entity for each floor to show fused target count
    floors = hub.get_floors()
    entities = []

    for floor in floors:
        entities.append(
            RadarFusionTargetCountSensor(
                hub=hub,
                floor=floor,
                config_entry=config_entry,
            )
        )

    if entities:
        async_add_entities(entities)


class RadarFusionTargetCountSensor(SensorEntity):
    """Sensor entity showing count of fused targets on a floor."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        hub: RadarFusionHub,
        floor: str,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self.hub = hub
        self.floor = floor
        self.config_entry = config_entry
        self._unsub_update = None

        # Set up entity properties
        self._attr_unique_id = f"{DOMAIN}_{floor}_target_count"
        self._attr_name = f"Target Count"

        # Device info for registry
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
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        targets = self.hub.get_fused_targets(self.floor)
        return len(targets)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity state attributes."""
        targets = self.hub.get_fused_targets(self.floor)

        attributes: dict[str, Any] = {
            ATTR_FLOOR: self.floor,
            "target_positions": [
                {
                    "x": target.x,
                    "y": target.y,
                    "last_updated": target.last_updated.isoformat(),
                }
                for target in targets
            ],
        }

        return attributes
