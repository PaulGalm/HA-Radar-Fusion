"""Constants for the Radar Fusion integration."""

from homeassistant.const import Platform

DOMAIN = "radar_fusion"
VERSION = "0.1.0"

# Platforms
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Config defaults
DEFAULT_TARGET_TIMEOUT = 5  # seconds
MIN_TARGET_TIMEOUT = 1
MAX_TARGET_TIMEOUT = 60

# Configuration keys
CONF_SENSORS = "sensors"
CONF_ZONES = "zones"
CONF_BLOCKZONES = "blockzones"
CONF_TARGET_TIMEOUT = "target_timeout"
CONF_FLOOR = "floor"
CONF_POSITION_X = "position_x"
CONF_POSITION_Y = "position_y"
CONF_ROTATION_ANGLE = "rotation_angle"
CONF_POLYGON = "polygon"
CONF_ACTIVE = "active"
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Entity attributes
ATTR_FLOOR = "floor"
ATTR_SOURCE_SENSORS = "source_sensors"
ATTR_ZONE_NAME = "zone_name"
ATTR_BLOCKZONE_NAME = "blockzone_name"
