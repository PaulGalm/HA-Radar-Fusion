"""Coordinator for Radar Fusion integration."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BLOCK_ZONES,
    CONF_FLOOR_ID,
    CONF_HEATMAPS_ALLTIME,
    CONF_POSITION_X,
    CONF_POSITION_Y,
    CONF_ROTATION,
    CONF_SENSOR_NAME,
    CONF_SENSORS,
    CONF_STALENESS_TIMEOUT,
    CONF_TARGET_ENTITIES,
    CONF_VERTICES,
    CONF_ZONES,
    DEFAULT_STALENESS_TIMEOUT,
    DOMAIN,
    HEATMAP_24H_SECONDS,
    HEATMAP_HOURLY_SECONDS,
    HEATMAP_RES_MM,
    point_in_polygon,
    transform_coordinates,
)

_LOGGER = logging.getLogger(__name__)


class RadarFusionCoordinator(DataUpdateCoordinator):
    """Coordinator to manage radar fusion data."""

    @property
    def test_mode(self):
        """Return whether test mode is enabled."""
        if self.config_entry is None:
            return False
        return self.config_entry.options.get("test_mode", False)

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=1),
            config_entry=config_entry,
        )

        self.sensors = config_entry.data.get(CONF_SENSORS, [])
        self.zones = config_entry.options.get(CONF_ZONES, [])
        self.block_zones = config_entry.options.get(CONF_BLOCK_ZONES, [])
        self.staleness_timeout = config_entry.options.get(
            CONF_STALENESS_TIMEOUT, DEFAULT_STALENESS_TIMEOUT
        )

        # Track raw sensor states and timestamps
        self._sensor_states: dict[str, Any] = {}
        self._last_updates: dict[str, datetime] = {}

        # Heatmap event queues and counts per floor
        # Each event is (timestamp, x_idx, y_idx).
        self._events_hourly: dict[str | None, deque[tuple[datetime, int, int]]] = (
            defaultdict(deque)
        )
        self._events_24h: dict[str | None, deque[tuple[datetime, int, int]]] = (
            defaultdict(deque)
        )

        # Counts per bin: mapping floor_id -> {(x_idx,y_idx): count}
        self._counts_hourly: dict[str | None, dict[tuple[int, int], int]] = {}
        self._counts_24h: dict[str | None, dict[tuple[int, int], int]] = {}
        self._counts_alltime: dict[str | None, dict[tuple[int, int], int]] = {}

        # Load persisted all-time heatmaps from options
        persisted = config_entry.options.get(CONF_HEATMAPS_ALLTIME, {})
        for floor_id, mapping in persisted.items():
            # mapping is dict of "x_y" -> count
            d: dict[tuple[int, int], int] = {}
            for k, v in mapping.items():
                try:
                    x_str, y_str = k.split("_")
                    d[(int(x_str), int(y_str))] = int(v)
                except (ValueError, TypeError):
                    continue
            self._counts_alltime[floor_id] = d

        # Track block zone states (entity_id -> is_on)
        self._block_zone_states: dict[str, bool] = {}

        # Test mode target tracking
        self._test_targets: list[dict[str, Any]] | None = None
        self._test_target_state: dict[
            int, dict[str, Any]
        ] = {}  # Track velocity and state for each test target

        # Store all entity IDs to track
        self._tracked_entities = set()
        for sensor in self.sensors:
            self._tracked_entities.update(sensor.get(CONF_TARGET_ENTITIES, []))

        # Track last heatmap persistence time
        self._last_heatmap_persist = datetime.now()
        self._heatmap_persist_interval = timedelta(minutes=5)  # Persist every 5 minutes

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data."""
        now = datetime.now()

        # Get current sensor states
        for entity_id in self._tracked_entities:
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                self._sensor_states[entity_id] = state.state
                self._last_updates[entity_id] = now

        # Process targets from all sensors
        all_targets = self._process_all_targets(now)

        # Filter by block zones and staleness
        filtered_targets = self._filter_targets(all_targets)

        # Group targets by floor
        targets_by_floor: dict[str | None, list[dict[str, Any]]] = {}
        for target in filtered_targets:
            floor_id = target["floor_id"]
            if floor_id not in targets_by_floor:
                targets_by_floor[floor_id] = []
            targets_by_floor[floor_id].append(target)

        # Update heatmaps based on filtered targets
        self._update_heatmaps(filtered_targets, now)

        return {
            "targets_by_floor": targets_by_floor,
            "all_targets": all_targets,
            "filtered_targets": filtered_targets,
            "sensor_count": len(self.sensors),
            "zone_count": len(self.zones),
            "block_zone_count": len(self.block_zones),
        }

    def _persist_heatmaps(self) -> None:
        """Persist all-time heatmap counts to config entry options."""
        serializable: dict[str | None, dict[str, int]] = {}
        for floor, mapping in self._counts_alltime.items():
            serializable[floor if floor is not None else "None"] = {
                f"{x}_{y}": int(c) for (x, y), c in mapping.items()
            }

        if self.config_entry is None:
            _LOGGER.error("No config_entry available; cannot persist heatmap data")
            return

        new_options = {**self.config_entry.options, CONF_HEATMAPS_ALLTIME: serializable}
        try:
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
            _LOGGER.debug("Persisted heatmap data to config entry")
        except Exception:
            _LOGGER.exception("Failed to persist heatmap data")

    def reset_heatmap(self, floor_id: str | None = None) -> None:
        """Reset the all-time heatmap for a floor or all floors.

        If floor_id is None, reset all floors.
        """
        if floor_id is None:
            # Clear all
            self._counts_alltime = {f: {} for f in self._counts_alltime}
        else:
            self._counts_alltime[floor_id] = {}

        # Persist cleared maps immediately
        self._persist_heatmaps()

    def _bin_index(self, coord_mm: float) -> int:
        """Get heatmap bin index for coordinate in millimeters."""
        return int(coord_mm // HEATMAP_RES_MM)

    def _update_heatmaps(self, targets: list[dict[str, Any]], now: datetime) -> None:
        """Update hourly, 24h and all-time heatmaps with new detections."""
        # Ensure floor entries exist
        for t in targets:
            floor = t.get("floor_id")
            self._counts_hourly.setdefault(floor, {})
            self._counts_24h.setdefault(floor, {})
            self._counts_alltime.setdefault(floor, {})

            x_idx = (
                self._bin_index(t["x"])
                if isinstance(t.get("x"), (int, float))
                else None
            )
            y_idx = (
                self._bin_index(t["y"])
                if isinstance(t.get("y"), (int, float))
                else None
            )
            if x_idx is None or y_idx is None:
                continue

            # Append events
            self._events_hourly[floor].append((now, x_idx, y_idx))
            self._events_24h[floor].append((now, x_idx, y_idx))

            # Increment counts
            self._counts_hourly[floor][(x_idx, y_idx)] = (
                self._counts_hourly[floor].get((x_idx, y_idx), 0) + 1
            )
            self._counts_24h[floor][(x_idx, y_idx)] = (
                self._counts_24h[floor].get((x_idx, y_idx), 0) + 1
            )
            self._counts_alltime[floor][(x_idx, y_idx)] = (
                self._counts_alltime[floor].get((x_idx, y_idx), 0) + 1
            )

            # Debug log
            _LOGGER.debug(
                "Heatmap updated for floor %s: (%d, %d) all-time=%d",
                floor,
                x_idx,
                y_idx,
                self._counts_alltime[floor][(x_idx, y_idx)],
            )

        # Prune old events for hourly and 24h
        cutoff_hourly = now - timedelta(seconds=HEATMAP_HOURLY_SECONDS)
        cutoff_24h = now - timedelta(seconds=HEATMAP_24H_SECONDS)

        for floor, events in self._events_hourly.items():
            while events and events[0][0] < cutoff_hourly:
                _ts, xi, yi = events.popleft()
                key = (xi, yi)
                if key in self._counts_hourly.get(floor, {}):
                    self._counts_hourly[floor][key] -= 1
                    if self._counts_hourly[floor][key] <= 0:
                        del self._counts_hourly[floor][key]

        for floor, events in self._events_24h.items():
            while events and events[0][0] < cutoff_24h:
                _ts, xi, yi = events.popleft()
                key = (xi, yi)
                if key in self._counts_24h.get(floor, {}):
                    self._counts_24h[floor][key] -= 1
                    if self._counts_24h[floor][key] <= 0:
                        del self._counts_24h[floor][key]

        # Periodic persistence (every 5 minutes) to avoid too frequent config updates
        if now - self._last_heatmap_persist >= self._heatmap_persist_interval:
            self._persist_heatmaps()
            self._last_heatmap_persist = now

    def _process_all_targets(self, now: datetime) -> list[dict[str, Any]]:
        """Process all targets from all sensors."""
        all_targets = []

        for sensor_config in self.sensors:
            floor_id = sensor_config.get(CONF_FLOOR_ID)
            sensor_x = sensor_config.get(CONF_POSITION_X, 0)
            sensor_y = sensor_config.get(CONF_POSITION_Y, 0)
            rotation = sensor_config.get(CONF_ROTATION, 0)
            target_entities = sensor_config.get(CONF_TARGET_ENTITIES, [])

            # Group entities by target (target1_x, target1_y, target2_x, etc.)
            targets_data: dict[int, dict[str, Any]] = {}

            for entity_id in target_entities:
                # Parse entity to determine target number and coordinate
                # Expected formats:
                # - sensor.ld2450_target1_x
                # - sensor.test_radar_1_target_1_x (with underscores)
                parts = entity_id.split("_")
                if len(parts) < 2:
                    continue

                # Find target number and coordinate
                target_num = None
                coord_type = None
                for i, part in enumerate(parts):
                    # Look for "target" followed by a number (with or without underscore)
                    if part == "target" and i + 1 < len(parts):
                        # Next part should be the number
                        try:
                            target_num = int(parts[i + 1])
                        except ValueError:
                            continue
                    elif part.startswith("target"):
                        # target1, target2, etc. (no underscore)
                        try:
                            target_num = int(part[6:])
                        except ValueError:
                            continue
                    elif part in ("x", "y") and i == len(parts) - 1:
                        coord_type = part

                if target_num is None or coord_type is None:
                    continue

                # Get state value
                if entity_id not in self._sensor_states:
                    continue

                try:
                    value = float(self._sensor_states[entity_id])
                except (ValueError, TypeError):
                    continue

                # Store in targets_data
                if target_num not in targets_data:
                    targets_data[target_num] = {
                        "target_num": target_num,
                        "sensor_config": sensor_config,
                    }
                targets_data[target_num][coord_type] = value

                # Track last update
                if entity_id in self._last_updates:
                    if "last_update" not in targets_data[target_num]:
                        targets_data[target_num]["last_update"] = self._last_updates[
                            entity_id
                        ]
                    else:
                        # Use most recent update
                        targets_data[target_num]["last_update"] = max(
                            targets_data[target_num]["last_update"],
                            self._last_updates[entity_id],
                        )

            # Transform and add complete targets
            for target_data in targets_data.values():
                if "x" not in target_data or "y" not in target_data:
                    continue

                # Filter out targets at (0,0) - these indicate no detection
                # Allow small tolerance for floating point comparison
                if abs(target_data["x"]) < 0.1 and abs(target_data["y"]) < 0.1:
                    continue

                # Transform coordinates
                global_x, global_y = transform_coordinates(
                    target_data["x"],
                    target_data["y"],
                    sensor_x,
                    sensor_y,
                    rotation,
                )

                all_targets.append(
                    {
                        "sensor_id": id(sensor_config),
                        "target_num": target_data["target_num"],
                        "floor_id": floor_id,
                        "local_x": target_data["x"],
                        "local_y": target_data["y"],
                        "x": global_x,
                        "y": global_y,
                        "last_update": target_data.get("last_update", now),
                        "age_seconds": (
                            now - target_data.get("last_update", now)
                        ).total_seconds(),
                        "sensor_entities": target_entities,
                    }
                )

        return all_targets

    def _filter_targets(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter targets by staleness and block zones."""
        filtered = []

        for target in targets:
            # Filter stale targets
            if target["age_seconds"] > self.staleness_timeout:
                continue

            # Check if target is in any active block zone
            in_block_zone = False
            floor_id = target["floor_id"]

            for block_zone in self.block_zones:
                # Only check block zones on same floor
                if block_zone.get(CONF_FLOOR_ID) != floor_id:
                    continue

                # Check if block zone is enabled
                block_zone_entity_id = f"switch.{DOMAIN}_{block_zone.get('name', '').lower().replace(' ', '_')}_block"
                if not self._block_zone_states.get(block_zone_entity_id, False):
                    continue

                # Check if target is inside block zone
                vertices = block_zone.get(CONF_VERTICES, [])
                if point_in_polygon(target["x"], target["y"], vertices):
                    in_block_zone = True
                    break

            if not in_block_zone:
                filtered.append(target)

        return filtered

    @callback
    def update_block_zone_state(self, entity_id: str, is_on: bool) -> None:
        """Update block zone state."""
        self._block_zone_states[entity_id] = is_on
        # Trigger immediate update
        self.async_set_updated_data(self.data or {})

        # Subscribe to state changes
        @callback
        def _async_state_changed(event: Event[EventStateChangedData]) -> None:
            """Handle state changes."""
            new_state = event.data["new_state"]
            if new_state is None:
                return

            entity_id = event.data["entity_id"]

            # Update sensor state
            if entity_id in self._tracked_entities:
                if new_state.state not in ("unknown", "unavailable"):
                    self._sensor_states[entity_id] = new_state.state
                    self._last_updates[entity_id] = datetime.now()
                    # Trigger update
                    self.hass.async_create_task(self.async_request_refresh())

        # Subscribe to state changes
        async_track_state_change_event(
            self.hass,
            list(self._tracked_entities),
            _async_state_changed,
        )

    def get_targets_for_floor(self, floor_id: str | None) -> list[dict[str, Any]]:
        """Get filtered targets for a specific floor."""
        if not self.data:
            return []
        return self.data.get("targets_by_floor", {}).get(floor_id, [])

    def get_floor_data(self, floor_id: str | None) -> dict[str, Any]:
        """Get all data for a specific floor (for service call)."""
        if self.test_mode:
            # Serve mock/test data with realistic moving targets
            import math  # noqa: PLC0415
            import random  # noqa: PLC0415

            # Initialize test targets on first call
            if self._test_targets is None:
                self._test_targets = [
                    {
                        "id": 1,
                        "x": 0,
                        "y": 0,
                        "vx": random.uniform(-200, 200),  # mm/s
                        "vy": random.uniform(-200, 200),
                        "ax": random.uniform(-50, 50),  # acceleration
                        "ay": random.uniform(-50, 50),
                        "behavior": "wandering",  # wandering, circular, zigzag
                    },
                    {
                        "id": 2,
                        "x": 2000,
                        "y": 1000,
                        "vx": random.uniform(-300, 300),
                        "vy": random.uniform(-300, 300),
                        "ax": random.uniform(-80, 80),
                        "ay": random.uniform(-80, 80),
                        "behavior": "fast",
                    },
                    {
                        "id": 3,
                        "x": -500,
                        "y": 500,
                        "vx": random.uniform(-100, 100),
                        "vy": random.uniform(-100, 100),
                        "ax": 0,
                        "ay": 0,
                        "behavior": "slow",
                    },
                ]

            # Update test target positions with physics-like movement
            if self._test_targets is None:
                return {}
            for target in self._test_targets:
                # Update velocity based on acceleration
                target["vx"] += target["ax"]
                target["vy"] += target["ay"]

                # Add random jitter to acceleration
                if random.random() < 0.1:  # 10% chance per update
                    target["ax"] += random.uniform(-30, 30)
                    target["ay"] += random.uniform(-30, 30)
                    # Clamp acceleration
                    target["ax"] = max(-100, min(100, target["ax"]))
                    target["ay"] = max(-100, min(100, target["ay"]))

                # Clamp velocity
                max_velocity = (
                    400
                    if target["behavior"] == "fast"
                    else (100 if target["behavior"] == "slow" else 250)
                )
                speed = math.sqrt(target["vx"] ** 2 + target["vy"] ** 2)
                if speed > max_velocity:
                    scale = max_velocity / (speed + 0.001)
                    target["vx"] *= scale
                    target["vy"] *= scale

                # Update position
                target["x"] += target["vx"] * 0.1  # 100ms update
                target["y"] += target["vy"] * 0.1

                # Boundary conditions - bounce off zone edges
                if abs(target["x"]) > 2000:
                    target["x"] = max(-2000, min(2000, target["x"]))
                    target["vx"] *= -0.8  # Bounce with friction
                    target["ax"] *= -0.5

                if abs(target["y"]) > 1500:
                    target["y"] = max(-1500, min(1500, target["y"]))
                    target["vy"] *= -0.8  # Bounce with friction
                    target["ay"] *= -0.5

                # Occasionally add sudden direction changes (realistic)
                if random.random() < 0.05:  # 5% chance per update
                    if target["behavior"] == "wandering":
                        target["vx"] = random.uniform(-300, 300)
                        target["vy"] = random.uniform(-300, 300)
                    elif target["behavior"] == "zigzag":
                        target["vx"] = (
                            target["vx"] * -1 if random.random() > 0.5 else target["vx"]
                        )
                        target["vy"] = (
                            target["vy"] * -1 if random.random() > 0.5 else target["vy"]
                        )
                    elif target["behavior"] == "circular":
                        angle = math.atan2(target["y"], target["x"])
                        speed = math.sqrt(target["vx"] ** 2 + target["vy"] ** 2)
                        target["vx"] = math.cos(angle + 0.3) * speed
                        target["vy"] = math.sin(angle + 0.3) * speed

            sensors = [
                {
                    "name": "Test Sensor 1",
                    "position_x": 0,
                    "position_y": 0,
                    "rotation": 0,
                    "target_entities": ["sensor.test1_x", "sensor.test1_y"],
                    "target_count": 1,
                },
                {
                    "name": "Test Sensor 2",
                    "position_x": 2000,
                    "position_y": 1000,
                    "rotation": 45,
                    "target_entities": ["sensor.test2_x", "sensor.test2_y"],
                    "target_count": 1,
                },
            ]
            test_zone_vertices = [
                [-1000, -1000],
                [1000, -1000],
                [1000, 1000],
                [-1000, 1000],
            ]
            zones = [
                {
                    "name": "TestZone",
                    "vertices": test_zone_vertices,
                }
            ]
            block_zones = [
                {
                    "name": "BlockA",
                    "vertices": [[1500, 500], [2500, 500], [2500, 1500], [1500, 1500]],
                    "enabled": True,
                }
            ]

            # Convert test targets to output format (with floor_id for heatmap update)
            targets: list[dict[str, Any]] = [
                {
                    "x": t["x"],
                    "y": t["y"],
                    "age": random.uniform(0, 2),  # Random age for variety
                    "age_seconds": random.uniform(0, 2),
                    "floor_id": floor_id,  # Add floor_id for heatmap tracking
                    "sensor_entities": ["sensor.test1_x", "sensor.test1_y"]
                    if t["id"] == 1
                    else ["sensor.test2_x", "sensor.test2_y"],
                }
                for t in self._test_targets
            ]

            # Update heatmaps with target detections (same as real mode)
            self._update_heatmaps(targets, datetime.now())

            # Calculate occupancy for each zone
            zones_with_occupancy = [
                {
                    **z,
                    "occupancy": any(
                        point_in_polygon(
                            t["x"],
                            t["y"],
                            list(z.get("vertices", [])),  # type: ignore[arg-type]
                        )
                        for t in targets
                    ),
                }
                for z in zones
            ]

            # Return actual heatmap data from accumulated counts
            heatmap_data: dict[str, Any] = {
                "resolution_mm": HEATMAP_RES_MM,
                "hourly": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_hourly.get(floor_id, {}).items()
                },
                "24h": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_24h.get(floor_id, {}).items()
                },
                "all_time": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_alltime.get(floor_id, {}).items()
                },
            }

            _LOGGER.debug(
                "Test mode returning heatmap for floor %s",
                floor_id,
            )

            return {
                "floor_id": floor_id,
                "sensors": sensors,
                "zones": zones_with_occupancy,
                "block_zones": block_zones,
                "targets": targets,
                "heatmap": heatmap_data,
            }

        targets = self.get_targets_for_floor(floor_id)

        floor_sensors = [
            {
                "name": s.get(CONF_SENSOR_NAME, ""),
                "position_x": s.get(CONF_POSITION_X),
                "position_y": s.get(CONF_POSITION_Y),
                "rotation": s.get(CONF_ROTATION),
                "target_entities": s.get(CONF_TARGET_ENTITIES),
                "target_count": sum(
                    1
                    for t in targets
                    if any(
                        e in s.get(CONF_TARGET_ENTITIES, [])
                        for e in t.get("sensor_entities", [])
                    )
                ),
            }
            for s in self.sensors
            if s.get(CONF_FLOOR_ID) == floor_id
        ]

        floor_zones = [
            {
                "name": z.get("name"),
                "vertices": z.get(CONF_VERTICES),
                "occupancy": any(
                    point_in_polygon(t["x"], t["y"], z.get(CONF_VERTICES, []))
                    for t in targets
                ),
            }
            for z in self.zones
            if z.get(CONF_FLOOR_ID) == floor_id
        ]

        floor_block_zones = [
            {
                "name": z.get("name"),
                "vertices": z.get(CONF_VERTICES),
                "enabled": self._block_zone_states.get(
                    f"switch.{DOMAIN}_{z.get('name', '').lower().replace(' ', '_')}_block",
                    False,
                ),
            }
            for z in self.block_zones
            if z.get(CONF_FLOOR_ID) == floor_id
        ]

        return {
            "floor_id": floor_id,
            "sensors": floor_sensors,
            "zones": floor_zones,
            "block_zones": floor_block_zones,
            "targets": [
                {
                    "x": t["x"],
                    "y": t["y"],
                    "age": t["age_seconds"],
                    "sensor_entities": t.get("sensor_entities", []),
                }
                for t in targets
            ],
            "heatmap": {
                "resolution_mm": HEATMAP_RES_MM,
                "hourly": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_hourly.get(floor_id, {}).items()
                },
                "24h": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_24h.get(floor_id, {}).items()
                },
                "all_time": {
                    f"{x}_{y}": c
                    for (x, y), c in self._counts_alltime.get(floor_id, {}).items()
                },
            },
        }
