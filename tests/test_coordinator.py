"""Tests for the coordinator's time handling."""

# ruff: noqa: PT009

from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.radar_fusion.const import (
    CONF_FLOOR_ID,
    CONF_SENSORS,
    CONF_STALENESS_TIMEOUT,
    CONF_TARGET_ENTITIES,
    DOMAIN,
)
from custom_components.radar_fusion.coordinator import RadarFusionCoordinator

_EASTERN = ZoneInfo("America/New_York")
_X_ENTITY = "sensor.radar_target1_x"
_Y_ENTITY = "sensor.radar_target1_y"
_FLOOR = "ground"


class CoordinatorTimestampsTest(IsolatedAsyncioTestCase):
    """Run the real coordinator with synthetic Home Assistant states."""

    async def asyncSetUp(self) -> None:
        """Create a Home Assistant instance with only a synthetic radar."""
        config_dir = self.enterContext(TemporaryDirectory())
        self.hass = HomeAssistant(config_dir)
        self.entry = ConfigEntry(
            domain=DOMAIN,
            title="Test Radar Fusion",
            version=1,
            minor_version=1,
            source="user",
            data={
                CONF_SENSORS: [
                    {
                        CONF_TARGET_ENTITIES: [_X_ENTITY, _Y_ENTITY],
                        CONF_FLOOR_ID: _FLOOR,
                    }
                ],
            },
            options={CONF_STALENESS_TIMEOUT: 60},
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        self.hass.states.async_set(_X_ENTITY, "1200")
        self.hass.states.async_set(_Y_ENTITY, "1000")

    async def _detect_at(self, instant: datetime) -> RadarFusionCoordinator:
        with patch.object(dt_util, "utcnow", return_value=instant):
            coordinator = RadarFusionCoordinator(self.hass, self.entry)
            await coordinator.async_refresh()
        self.hass.states.async_set(_X_ENTITY, STATE_UNAVAILABLE)
        self.hass.states.async_set(_Y_ENTITY, STATE_UNAVAILABLE)
        return coordinator

    async def test_staleness_when_the_local_clock_moves_back(self) -> None:
        """A detection made two elapsed minutes ago is stale after the clock folds."""
        before_fold = datetime(2025, 11, 2, 1, 59, tzinfo=_EASTERN).astimezone(UTC)
        after_fold = datetime(2025, 11, 2, 1, 1, fold=1, tzinfo=_EASTERN).astimezone(
            UTC
        )
        coordinator = await self._detect_at(before_fold)
        target = coordinator.get_targets_for_floor(_FLOOR)[0]
        self.assertEqual(target["last_update"], before_fold)

        with patch.object(dt_util, "utcnow", return_value=after_fold):
            await coordinator.async_refresh()

        self.assertEqual(coordinator.get_targets_for_floor(_FLOOR), [])
        self.assertEqual(coordinator.data["all_targets"][0]["age_seconds"], 120)

    async def test_heatmap_windows_use_elapsed_time(self) -> None:
        """Prune a detection after the elapsed hour and 24-hour cutoff."""
        detected_at = datetime(2025, 11, 2, 1, 30, tzinfo=_EASTERN).astimezone(UTC)
        coordinator = await self._detect_at(detected_at)
        one_hour = datetime(2025, 11, 2, 1, 30, fold=1, tzinfo=_EASTERN).astimezone(UTC)
        cases = (
            (one_hour, 1, 1),
            (one_hour + timedelta(seconds=1), 0, 1),
            (detected_at + timedelta(days=1), 0, 1),
            (detected_at + timedelta(days=1, seconds=1), 0, 0),
        )
        with patch.object(coordinator, "_persist_heatmaps"):
            for instant, hourly, daily in cases:
                with self.subTest(instant=instant):
                    with patch.object(dt_util, "utcnow", return_value=instant):
                        await coordinator.async_refresh()
                    self.assertTrue(coordinator.last_update_success)
                    heatmap = coordinator.get_floor_data(_FLOOR)["heatmap"]
                    self.assertEqual(sum(heatmap["hourly"].values()), hourly)
                    self.assertEqual(sum(heatmap["24h"].values()), daily)
                    self.assertEqual(sum(heatmap["all_time"].values()), 1)
