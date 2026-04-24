"""Data models and algorithms for Radar Fusion integration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util


@dataclass
class RadarSensor:
    """Configuration for a single ESPHome radar sensor."""

    entity_id: str
    """Entity ID of ESPHome target X coordinate sensor."""
    name: str
    """User-friendly name for this sensor."""
    floor: str
    """Floor identifier (e.g., 'floor_1', 'floor_2')."""
    position_x: float
    """Global X position of sensor in meters."""
    position_y: float
    """Global Y position of sensor in meters."""
    rotation_angle: float
    """Rotation angle in degrees (0-360)."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "floor": self.floor,
            "position_x": self.position_x,
            "position_y": self.position_y,
            "rotation_angle": self.rotation_angle,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RadarSensor:
        """Create from dictionary."""
        return RadarSensor(
            entity_id=data["entity_id"],
            name=data["name"],
            floor=data["floor"],
            position_x=data["position_x"],
            position_y=data["position_y"],
            rotation_angle=data["rotation_angle"],
        )


@dataclass
class Zone:
    """Polygonal zone for presence detection."""

    name: str
    """Zone name."""
    polygon: list[list[float]]
    """List of [x, y] vertices forming closed polygon."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "name": self.name,
            "polygon": self.polygon,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Zone:
        """Create from dictionary."""
        return Zone(
            name=data["name"],
            polygon=data["polygon"],
        )

    def validate(self) -> list[str]:
        """Validate zone structure. Returns list of error messages."""
        errors = []
        if not self.name:
            errors.append("Zone name cannot be empty")
        if not self.polygon or len(self.polygon) < 3:
            errors.append(f"Zone '{self.name}' must have at least 3 vertices")
        if self.polygon and self.polygon[0] != self.polygon[-1]:
            errors.append(f"Zone '{self.name}' polygon must be closed (first vertex must equal last)")
        return errors


@dataclass
class Blockzone:
    """Polygonal blockzone where targets are ignored."""

    name: str
    """Blockzone name."""
    polygon: list[list[float]]
    """List of [x, y] vertices forming closed polygon."""
    active: bool = True
    """Whether this blockzone is currently active (blocking targets)."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "name": self.name,
            "polygon": self.polygon,
            "active": self.active,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Blockzone:
        """Create from dictionary."""
        return Blockzone(
            name=data["name"],
            polygon=data["polygon"],
            active=data.get("active", True),
        )

    def validate(self) -> list[str]:
        """Validate blockzone structure. Returns list of error messages."""
        errors = []
        if not self.name:
            errors.append("Blockzone name cannot be empty")
        if not self.polygon or len(self.polygon) < 3:
            errors.append(f"Blockzone '{self.name}' must have at least 3 vertices")
        if self.polygon and self.polygon[0] != self.polygon[-1]:
            errors.append(f"Blockzone '{self.name}' polygon must be closed (first vertex must equal last)")
        return errors


@dataclass
class FusedTarget:
    """In-memory fused target from coordinator."""

    floor: str
    """Floor where target is detected."""
    x: float
    """Global X coordinate."""
    y: float
    """Global Y coordinate."""
    last_updated: datetime
    """Timestamp of last update."""
    source_sensors: list[str] = field(default_factory=list)
    """List of source sensor entity IDs that contributed to this target."""


def point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """
    Determine if point is inside polygon using ray-casting algorithm.

    Args:
        point: (x, y) tuple
        polygon: List of [x, y] vertices forming closed polygon

    Returns:
        True if point is inside polygon, False otherwise
    """
    x, y = point
    n = len(polygon)
    inside = False

    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def rotate_2d(x: float, y: float, angle_degrees: float) -> tuple[float, float]:
    """
    Rotate 2D point around origin by angle.

    Args:
        x: X coordinate
        y: Y coordinate
        angle_degrees: Rotation angle in degrees

    Returns:
        (x_rotated, y_rotated) tuple
    """
    angle_rad = math.radians(angle_degrees)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    x_rotated = x * cos_a - y * sin_a
    y_rotated = x * sin_a + y * cos_a

    return x_rotated, y_rotated


def transform_coordinates(
    local_x: float,
    local_y: float,
    sensor: RadarSensor,
) -> tuple[float, float]:
    """
    Transform sensor-local coordinates to global apartment coordinates.

    Applies: translation (subtract sensor position) then rotation.

    Args:
        local_x: X coordinate in sensor's local frame
        local_y: Y coordinate in sensor's local frame
        sensor: RadarSensor configuration with position and rotation

    Returns:
        (global_x, global_y) tuple in global apartment frame
    """
    # Step 1: Translate to origin relative to sensor
    x_translated = local_x - sensor.position_x
    y_translated = local_y - sensor.position_y

    # Step 2: Rotate around origin
    global_x, global_y = rotate_2d(
        x_translated,
        y_translated,
        sensor.rotation_angle,
    )

    return global_x, global_y


def validate_zones_blocktones(zones: list[dict[str, Any]], blockzones: list[dict[str, Any]]) -> list[str]:
    """
    Validate zones and blockzones configuration.

    Args:
        zones: List of zone dictionaries
        blockzones: List of blockzone dictionaries

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    for zone_data in zones:
        zone = Zone.from_dict(zone_data)
        errors.extend(zone.validate())

    for blockzone_data in blockzones:
        blockzone = Blockzone.from_dict(blockzone_data)
        errors.extend(blockzone.validate())

    return errors
