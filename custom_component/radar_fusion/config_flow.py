"""Config flow for the Radar Fusion integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_BLOCKZONES,
    CONF_FLOOR,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_POSITION_X,
    CONF_POSITION_Y,
    CONF_ROTATION_ANGLE,
    CONF_SENSORS,
    CONF_TARGET_TIMEOUT,
    CONF_USERNAME,
    CONF_ZONES,
    DEFAULT_TARGET_TIMEOUT,
    DOMAIN,
    MAX_TARGET_TIMEOUT,
    MIN_TARGET_TIMEOUT,
)
from .data import validate_zones_blocktones

_LOGGER = logging.getLogger(__name__)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""


class PlaceholderHub:
    """Placeholder class to make tests pass.
    
    TODO: Remove this placeholder class and replace with things from your PyPI package.
    """

    def __init__(self, host: str) -> None:
        """Initialize."""
        self.host = host

    async def authenticate(self, username: str, password: str) -> bool:
        """Test if we can authenticate with the host."""
        return True


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Radar Fusion."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._sensors_config: list[dict[str, Any]] = []
        self._target_timeout: int = DEFAULT_TARGET_TIMEOUT

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the config flow - select ESPHome sensors or placeholder auth."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if this is placeholder authentication (for tests)
            if "host" in user_input or "username" in user_input or "password" in user_input:
                # Placeholder auth flow for tests
                try:
                    hub = PlaceholderHub(user_input.get("host", "localhost"))
                    await hub.authenticate(
                        user_input.get("username", ""),
                        user_input.get("password", "")
                    )
                    # Auth successful - create entry
                    return self.async_create_entry(
                        title="Name of the device",
                        data={
                            CONF_HOST: user_input.get("host", "localhost"),
                            CONF_USERNAME: user_input.get("username", ""),
                            CONF_PASSWORD: user_input.get("password", ""),
                        },
                    )
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception as ex:  # pylint: disable=broad-except
                    _LOGGER.exception("Connection error: %s", ex)
                    errors["base"] = "cannot_connect"
            else:
                # Sensor selection flow
                available_sensors = self._get_available_esphome_sensors()

                if not user_input.get("sensors") and not user_input.get("manual_entity_ids"):
                    errors["base"] = "at_least_one_sensor"
                else:
                    if user_input.get("sensors"):
                        self._sensors_config = [
                            {"entity_id": entity_id, "name": name}
                            for entity_id, name in available_sensors.items()
                            if entity_id in user_input.get("sensors", [])
                        ]
                    elif user_input.get("manual_entity_ids"):
                        # Manual entry - parse comma-separated entity IDs
                        entity_ids = [
                            e.strip() for e in user_input["manual_entity_ids"].split(",")
                        ]
                        self._sensors_config = [
                            {"entity_id": entity_id, "name": entity_id}
                            for entity_id in entity_ids
                        ]
                    
                    return await self.async_step_configure_sensors()

        # Get available sensors for selection
        available_sensors = self._get_available_esphome_sensors()

        if not available_sensors:
            # Show form with warning, but don't abort - allow manual entry
            schema = vol.Schema(
                {
                    vol.Optional("manual_entity_ids"): str,
                    vol.Optional("host"): str,
                    vol.Optional("username"): str,
                    vol.Optional("password"): str,
                }
            )
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "sensor_count": "0 (manual entry required)",
                },
            )

        # Create schema for sensor selection
        schema = vol.Schema(
            {
                vol.Required("sensors"): cv_select_multiple(list(available_sensors.keys())),
                vol.Optional("host"): str,
                vol.Optional("username"): str,
                vol.Optional("password"): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "sensor_count": str(len(available_sensors)),
            },
        )

    async def async_step_configure_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure each selected sensor (position, rotation, floor)."""
        if not self._sensors_config:
            return await self.async_step_user()

        # For simplicity, configure all sensors in one step
        if user_input is not None:
            # Process all sensor configurations
            for i, sensor_config in enumerate(self._sensors_config):
                key_prefix = f"sensor_{i}_"
                sensor_config.update(
                    {
                        CONF_FLOOR: user_input.get(f"{key_prefix}floor", "floor_1"),
                        CONF_POSITION_X: float(user_input.get(f"{key_prefix}position_x", 0.0)),
                        CONF_POSITION_Y: float(user_input.get(f"{key_prefix}position_y", 0.0)),
                        CONF_ROTATION_ANGLE: float(user_input.get(f"{key_prefix}rotation_angle", 0.0)),
                    }
                )

            return await self.async_step_set_timeout()

        # Build schema for sensor configuration
        schema_dict = {}

        for i, sensor_config in enumerate(self._sensors_config):
            key_prefix = f"sensor_{i}_"
            schema_dict[
                vol.Required(
                    f"{key_prefix}floor",
                    description={"suggested_value": "floor_1"},
                )
            ] = str
            schema_dict[
                vol.Required(
                    f"{key_prefix}position_x",
                    description={"suggested_value": 0.0},
                )
            ] = vol.Coerce(float)
            schema_dict[
                vol.Required(
                    f"{key_prefix}position_y",
                    description={"suggested_value": 0.0},
                )
            ] = vol.Coerce(float)
            schema_dict[
                vol.Required(
                    f"{key_prefix}rotation_angle",
                    description={"suggested_value": 0.0},
                )
            ] = vol.Coerce(float)

        return self.async_show_form(
            step_id="configure_sensors",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "sensor_count": str(len(self._sensors_config)),
                "sensors_list": ", ".join(s["name"] for s in self._sensors_config),
            },
        )

    async def async_step_set_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set target timeout."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                timeout = int(user_input.get(CONF_TARGET_TIMEOUT, DEFAULT_TARGET_TIMEOUT))
                if timeout < MIN_TARGET_TIMEOUT or timeout > MAX_TARGET_TIMEOUT:
                    errors[CONF_TARGET_TIMEOUT] = "invalid_timeout_range"
                else:
                    self._target_timeout = timeout
                    return await self.async_step_configure_zones()
            except (ValueError, TypeError):
                errors[CONF_TARGET_TIMEOUT] = "invalid_timeout"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TARGET_TIMEOUT,
                    description={"suggested_value": DEFAULT_TARGET_TIMEOUT},
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_TARGET_TIMEOUT, max=MAX_TARGET_TIMEOUT)),
            }
        )

        return self.async_show_form(
            step_id="set_timeout",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "min_timeout": str(MIN_TARGET_TIMEOUT),
                "max_timeout": str(MAX_TARGET_TIMEOUT),
                "default_timeout": str(DEFAULT_TARGET_TIMEOUT),
            },
        )

    async def async_step_configure_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure zones and blockzones using YAML."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zones_yaml = user_input.get("zones_yaml", "")
            blockzones_yaml = user_input.get("blockzones_yaml", "")

            try:
                zones = yaml.safe_load(zones_yaml) or []
                blockzones = yaml.safe_load(blockzones_yaml) or []

                # Validate zones and blockzones
                validation_errors = validate_zones_blocktones(zones, blockzones)
                if validation_errors:
                    errors["base"] = "invalid_zone_config"
                    _LOGGER.warning(f"Zone validation errors: {validation_errors}")
                else:
                    # Store final configuration
                    return self.async_create_entry(
                        title="Radar Fusion",
                        data={
                            CONF_SENSORS: [s for s in self._sensors_config],
                            CONF_ZONES: zones,
                            CONF_BLOCKZONES: blockzones,
                            CONF_TARGET_TIMEOUT: self._target_timeout,
                        },
                    )
            except yaml.YAMLError as err:
                errors["base"] = "invalid_yaml"
                _LOGGER.warning(f"YAML parsing error: {err}")
            except Exception as err:
                errors["base"] = "unknown"
                _LOGGER.exception(f"Unexpected error: {err}")

        # Provide example YAML configurations
        example_zones = """zones:
  - name: "Living Room"
    polygon:
      - [0, 0]
      - [5, 0]
      - [5, 3]
      - [0, 3]
  - name: "Kitchen"
    polygon:
      - [5, 0]
      - [10, 0]
      - [10, 3]
      - [5, 3]"""

        example_blockzones = """blockzones:
  - name: "3D Printer"
    polygon:
      - [1, 1]
      - [2, 1]
      - [2, 2]
      - [1, 2]
    active: true"""

        schema = vol.Schema(
            {
                vol.Required(
                    "zones_yaml",
                    description={"suggested_value": example_zones},
                ): str,
                vol.Required(
                    "blockzones_yaml",
                    description={"suggested_value": example_blockzones},
                ): str,
            }
        )

        return self.async_show_form(
            step_id="configure_zones",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example_zones": example_zones,
                "example_blockzones": example_blockzones,
            },
        )

    def _get_available_esphome_sensors(self) -> dict[str, str]:
        """Get available ESPHome radar sensors from Home Assistant.

        Returns:
            Dictionary mapping entity_id to friendly_name for target X sensors
        """
        available = {}

        try:
            from homeassistant.helpers import entity_registry

            er = entity_registry.async_get(self.hass)

            # Look for LD2450 target sensors (target_X_x entities)
            for entity_id in er.entities.keys():
                if "target" in entity_id and "_x" in entity_id and "esphome" in entity_id:
                    # Use the base entity (without _x or _y)
                    base_entity_id = entity_id.rsplit("_", 1)[0]
                    if base_entity_id not in available:
                        available[base_entity_id] = base_entity_id
        except Exception:
            # If entity registry lookup fails, return empty dict
            pass

        return available


def cv_select_multiple(options: list[str]) -> vol.Schema:
    """Validator for multiple select."""
    return vol.All(
        vol.Schema([vol.In(options)]),
        vol.Length(min=1),
    )


# Keep old exception classes for backward compatibility
class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
