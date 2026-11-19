"""Exercise Radar Fusion's sensor subscriptions with Home Assistant events."""

# ruff: noqa: PT009, SLF001

from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.radar_fusion.const import (
    CONF_SENSORS,
    CONF_TARGET_ENTITIES,
    DOMAIN,
)
from custom_components.radar_fusion.coordinator import RadarFusionCoordinator

SENSOR_ID = "sensor.radar_target_x"
BLOCK_SWITCH_ID = "switch.radar_block_zone"


class SensorListenerTest(IsolatedAsyncioTestCase):
    """Verify setup, event dispatch, and unload on a real Home Assistant bus."""

    async def asyncSetUp(self) -> None:
        """Set up an entry without installing the platform entities."""
        self.hass = HomeAssistant(self.enterContext(TemporaryDirectory()))
        self.entry = ConfigEntry(
            domain=DOMAIN,
            title="Test Radar Fusion",
            version=1,
            minor_version=1,
            source="user",
            state=ConfigEntryState.SETUP_IN_PROGRESS,
            data={CONF_SENSORS: [{CONF_TARGET_ENTITIES: [SENSOR_ID]}]},
            options={},
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        self.coordinator = RadarFusionCoordinator(self.hass, self.entry)
        self.addAsyncCleanup(self.entry._async_process_on_unload, self.hass)
        await self.coordinator.async_config_entry_first_refresh()

    async def test_sensor_changes_refresh_without_block_switches(self) -> None:
        """Track configured sensors even when the switch platform has no entities."""
        with patch.object(self.coordinator, "async_request_refresh") as refresh:
            self.hass.states.async_set("sensor.untracked", "6")
            self.hass.states.async_set(SENSOR_ID, "42")
            await self.hass.async_block_till_done()

        refresh.assert_awaited_once()
        self.assertEqual(self.coordinator._sensor_states, {SENSOR_ID: "42"})

    async def test_repeated_block_switch_updates_keep_one_sensor_listener(self) -> None:
        """Adding and toggling switches does not multiply refresh requests."""
        for state in (False, True, False, True):
            self.coordinator.update_block_zone_state(BLOCK_SWITCH_ID, state)

        with patch.object(self.coordinator, "async_request_refresh") as refresh:
            self.hass.states.async_set(SENSOR_ID, "42")
            await self.hass.async_block_till_done()

        refresh.assert_awaited_once()

    async def test_entry_unload_unsubscribes_from_sensor_events(self) -> None:
        """An unloaded coordinator no longer handles state changes."""
        self.coordinator.update_block_zone_state(BLOCK_SWITCH_ID, is_on=True)
        await self.entry._async_process_on_unload(self.hass)

        with patch.object(self.coordinator, "async_request_refresh") as refresh:
            self.hass.states.async_set(SENSOR_ID, "42")
            await self.hass.async_block_till_done()

        refresh.assert_not_called()
        self.assertNotIn(SENSOR_ID, self.coordinator._sensor_states)
