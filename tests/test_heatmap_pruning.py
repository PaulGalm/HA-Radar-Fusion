"""Regression tests for pruning rolling heatmap queues."""

# ruff: noqa: PT009, SLF001

from collections import deque
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.radar_fusion.const import DOMAIN, HEATMAP_RES_MM
from custom_components.radar_fusion.coordinator import RadarFusionCoordinator


class HeatmapPruningTest(IsolatedAsyncioTestCase):
    """Check rolling counts with multiple time and floor buckets."""

    async def test_pruning_only_removes_expired_detections(self) -> None:
        """Preserve each bin's newer detections and the all-time counts."""
        hass = HomeAssistant(self.enterContext(TemporaryDirectory()))
        entry = ConfigEntry(
            domain=DOMAIN,
            title="Test Radar Fusion",
            version=1,
            minor_version=1,
            source="user",
            data={},
            options={},
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        coordinator = RadarFusionCoordinator(hass, entry)
        start = coordinator._last_heatmap_persist
        first = {"floor_id": "ground", "x": 1, "y": 1}
        second = {"floor_id": "ground", "x": HEATMAP_RES_MM + 1, "y": 1}
        upstairs = {"floor_id": "upper", "x": 1, "y": 1}
        old_detections = 2000
        with patch.object(coordinator, "_persist_heatmaps"):
            coordinator._update_heatmaps([first] * old_detections + [second], start)
            coordinator._update_heatmaps(
                [first, upstairs], start + timedelta(minutes=30)
            )
            coordinator._update_heatmaps([], start + timedelta(minutes=61))

            ground = coordinator.get_floor_data("ground")["heatmap"]
            upper = coordinator.get_floor_data("upper")["heatmap"]
            self.assertEqual(ground["hourly"], {"0_0": 1})
            self.assertEqual(ground["24h"], {"0_0": old_detections + 1, "1_0": 1})
            self.assertEqual(upper["hourly"], {"0_0": 1})

            coordinator._update_heatmaps([], start + timedelta(days=1, minutes=1))
            ground = coordinator.get_floor_data("ground")["heatmap"]
            upper = coordinator.get_floor_data("upper")["heatmap"]
            self.assertEqual(ground["hourly"], {})
            self.assertEqual(ground["24h"], {"0_0": 1})
            self.assertEqual(ground["all_time"], {"0_0": old_detections + 1, "1_0": 1})
            self.assertEqual(upper["24h"], {"0_0": 1})
            self.assertIsInstance(coordinator._events_hourly["ground"], deque)
            self.assertIsInstance(coordinator._events_24h["ground"], deque)
