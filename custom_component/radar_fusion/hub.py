"""RadarFusionHub coordinator for managing target fusion and zone detection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .data import (
    Blockzone,
    FusedTarget,
    RadarSensor,
    Zone,
    point_in_polygon,
    transform_coordinates,
    validate_zones_blocktones,
)

_LOGGER = logging.getLogger(__name__)


class RadarFusionHub:
    """Coordinator for radar target fusion and zone detection."""

    def __init__(
        self,
        hass: HomeAssistant,
        sensors: list[RadarSensor],
        zones: list[Zone],
        blockzones: list[Blockzone],
        target_timeout: int,
    ) -> None:
        """Initialize the hub.

        Args:
            hass: Home Assistant instance
            sensors: List of RadarSensor configurations
            zones: List of Zone configurations
            blockzones: List of Blockzone configurations
            target_timeout: Seconds before removing stale targets from cache
        """
        self.hass = hass
        self.sensors = {sensor.entity_id: sensor for sensor in sensors}
        self.zones = {zone.name: zone for zone in zones}
        self.blockzones = {blockzone.name: blockzone for blockzone in blockzones}
        self.target_timeout = target_timeout

        # In-memory cache of fused targets per floor
        self.fused_targets: dict[str, list[FusedTarget]] = {}

        # State change listener removal callback
        self._unsub_state_changes: Callable[[], None] | None = None

        # Cleanup timer removal callback
        self._unsub_cleanup_timer: Callable[[], None] | None = None

        # Callbacks to notify platforms of updates
        self._update_callbacks: list[Callable[[], None]] = []

    async def async_setup(self) -> None:
        """Set up the hub - subscribe to ESPHome sensors and start cleanup timer."""
        _LOGGER.debug("Setting up RadarFusionHub")

        # Subscribe to target entity state changes
        target_entities = list(self.sensors.keys())
        self._unsub_state_changes = async_track_state_change(
            self.hass,
            target_entities,
            self._handle_state_change,
        )

        # Start cleanup timer for stale targets
        self._schedule_cleanup()

        _LOGGER.debug("RadarFusionHub setup complete")

    async def async_shutdown(self) -> None:
        """Shutdown hub - unsubscribe from all listeners."""
        _LOGGER.debug("Shutting down RadarFusionHub")

        if self._unsub_state_changes:
            self._unsub_state_changes()

        if self._unsub_cleanup_timer:
            self._unsub_cleanup_timer()

        _LOGGER.debug("RadarFusionHub shutdown complete")

    def register_update_callback(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register callback to be called when targets update.

        Args:
            callback: Function to call on update

        Returns:
            Callable to unregister the callback
        """
        self._update_callbacks.append(update_callback)

        def unregister() -> None:
            self._update_callbacks.remove(update_callback)

        return unregister

    @callback
    def _handle_state_change(
        self,
        entity_id: str,
        old_state: Any,
        new_state: Any,
    ) -> None:
        """Handle state change of ESPHome target entity."""
        if new_state is None or new_state.state == "unavailable":
            return

        # Extract target number and coordinate type from entity_id
        # Example: sensor.kitchen_presence_target_1_x -> target 1, x coordinate
        parts = entity_id.split("_")
        if len(parts) < 2:
            return

        try:
            # Get coordinate value
            coord_value = float(new_state.state)
        except ValueError, TypeError:
            return

        # Parse entity_id to extract target info
        # Format: ...target_<N>_<x|y>
        entity_name = entity_id.split(".")[-1]
        name_parts = entity_name.split("_")

        # Find "target" in name_parts
        target_idx = None
        coord_type = None
        try:
            for i, part in enumerate(name_parts):
                if part == "target" and i + 1 < len(name_parts):
                    target_idx = int(name_parts[i + 1])
                    if i + 2 < len(name_parts):
                        coord_type = name_parts[i + 2]
                    break
        except ValueError, IndexError:
            return

        if target_idx is None or coord_type is None:
            _LOGGER.warning(f"Could not parse target info from entity_id: {entity_id}")
            return

        if entity_id not in self.sensors:
            _LOGGER.warning(f"Received update from unknown sensor: {entity_id}")
            return

        # Dispatch update
        self._update_target(entity_id, target_idx, coord_type, coord_value)

    def _update_target(
        self,
        sensor_entity_id: str,
        target_idx: int,
        coord_type: str,
        value: float,
    ) -> None:
        """Update target coordinate for a sensor.

        This method handles partial updates as ESPHome sensors expose X and Y separately.
        Targets are added/updated when both X and Y have been received.
        """
        sensor = self.sensors[sensor_entity_id]
        floor = sensor.floor

        # Initialize floor cache if needed
        if floor not in self.fused_targets:
            self.fused_targets[floor] = []

        # Store coordinate in sensor's state - we'll batch them
        # For now, we'll retrieve both X and Y from HA state machine and fuse
        state = self.hass.states.get(
            sensor_entity_id.replace("_x", "_x")
            if coord_type == "x"
            else sensor_entity_id.replace("_x", "_y")
        )

        # Get both X and Y coordinates from ESPHome state machine
        base_entity_id = sensor_entity_id.rsplit("_", 1)[0]  # Remove _x or _y
        entity_x = f"{base_entity_id}_x"
        entity_y = f"{base_entity_id}_y"

        state_x = self.hass.states.get(entity_x)
        state_y = self.hass.states.get(entity_y)

        if not state_x or not state_y:
            return

        try:
            local_x = float(state_x.state)
            local_y = float(state_y.state)
        except ValueError, TypeError:
            return

        # Transform coordinates
        global_x, global_y = transform_coordinates(local_x, local_y, sensor)

        # Create or update fused target
        target_id = f"{floor}_{sensor_entity_id}_{target_idx}"
        now = dt_util.utcnow()

        # Find existing target or create new one
        fused_target = None
        for target in self.fused_targets[floor]:
            if target.floor == floor and target.source_sensors == [sensor_entity_id]:
                # Match target from this sensor
                if (
                    not hasattr(target, "_target_idx")
                    or target._target_idx == target_idx
                ):  # noqa: F821
                    fused_target = target
                    break

        if not fused_target:
            fused_target = FusedTarget(
                floor=floor,
                x=global_x,
                y=global_y,
                last_updated=now,
                source_sensors=[sensor_entity_id],
            )
            fused_target._target_idx = target_idx  # noqa: F841
            self.fused_targets[floor].append(fused_target)
        else:
            fused_target.x = global_x
            fused_target.y = global_y
            fused_target.last_updated = now

        # Notify platforms of update
        self._notify_update()

    def _notify_update(self) -> None:
        """Call all registered update callbacks."""
        for update_callback in self._update_callbacks:
            try:
                update_callback()
            except Exception as err:
                _LOGGER.error(f"Error in update callback: {err}")

    def _schedule_cleanup(self) -> None:
        """Schedule next cleanup of stale targets."""

        async def cleanup() -> None:
            now = dt_util.utcnow()
            timeout_delta = timedelta(seconds=self.target_timeout)

            for floor, targets in self.fused_targets.items():
                # Remove stale targets
                self.fused_targets[floor] = [
                    target
                    for target in targets
                    if now - target.last_updated < timeout_delta
                ]

            self._notify_update()

            # Schedule next cleanup
            self._schedule_cleanup()

        self._unsub_cleanup_timer = async_call_later(
            self.hass,
            1,  # Run cleanup every 1 second
            cleanup,
        )

    def get_fused_targets(self, floor: str) -> list[FusedTarget]:
        """Get all fused targets for a floor."""
        return self.fused_targets.get(floor, [])

    def get_zone_presence(self, floor: str, zone_name: str) -> bool:
        """Check if any target is present in the given zone.

        Args:
            floor: Floor identifier
            zone_name: Zone name

        Returns:
            True if target present in zone, False otherwise
        """
        if zone_name not in self.zones:
            _LOGGER.warning(f"Unknown zone: {zone_name}")
            return False

        zone = self.zones[zone_name]
        targets = self.get_fused_targets(floor)

        # Check if any target is in zone and not blocked by active blockzone
        for target in targets:
            if not self._is_target_blocked(floor, target):
                if point_in_polygon((target.x, target.y), zone.polygon):
                    return True

        return False

    def _is_target_blocked(self, floor: str, target: FusedTarget) -> bool:
        """Check if target is blocked by any active blockzone.

        Args:
            floor: Floor identifier
            target: FusedTarget to check

        Returns:
            True if target is blocked, False otherwise
        """
        for blockzone in self.blockzones.values():
            if blockzone.active:
                if point_in_polygon((target.x, target.y), blockzone.polygon):
                    return True

        return False

    def get_blockzone_state(self, blockzone_name: str) -> bool:
        """Get if blockzone is currently active.

        Args:
            blockzone_name: Blockzone name

        Returns:
            True if active (blocking), False if inactive
        """
        if blockzone_name not in self.blockzones:
            _LOGGER.warning(f"Unknown blockzone: {blockzone_name}")
            return False

        return self.blockzones[blockzone_name].active

    def set_blockzone_active(self, blockzone_name: str, active: bool) -> None:
        """Set blockzone active state.

        Args:
            blockzone_name: Blockzone name
            active: True to activate, False to deactivate
        """
        if blockzone_name not in self.blockzones:
            _LOGGER.warning(f"Unknown blockzone: {blockzone_name}")
            return

        self.blockzones[blockzone_name].active = active
        self._notify_update()

    def get_zones(self) -> dict[str, Zone]:
        """Get all zones."""
        return self.zones

    def get_blockzones(self) -> dict[str, Blockzone]:
        """Get all blockzones."""
        return self.blockzones

    def get_sensors(self) -> dict[str, RadarSensor]:
        """Get all sensors."""
        return self.sensors

    def get_floors(self) -> list[str]:
        """Get list of unique floors from configured sensors."""
        return list(set(sensor.floor for sensor in self.sensors.values()))
