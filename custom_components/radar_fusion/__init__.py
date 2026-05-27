"""The Radar Fusion integration."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace import DOMAIN as LOVELACE_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_FLOOR_ID,
    CONF_SENSOR_NAME,
    CONF_SENSORS,
    CONF_TEST_MODE,
    DOMAIN,
    SERVICE_GET_FLOOR_DATA,
    SERVICE_RESET_HEATMAP,
)
from .coordinator import RadarFusionCoordinator

# Static path URL for serving frontend files
STATIC_PATH_URL = "/radar_fusion_static"

SERVICE_SET_TEST_MODE = "set_test_mode"

SERVICE_SET_TEST_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]

SERVICE_GET_FLOOR_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Optional(CONF_FLOOR_ID): vol.Any(cv.string, None),
    }
)

SERVICE_RESET_HEATMAP_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Optional(CONF_FLOOR_ID): vol.Any(cv.string, None),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Radar Fusion from a config entry."""
    # Create coordinator
    coordinator = RadarFusionCoordinator(hass, entry)

    # Store coordinator in runtime_data
    entry.runtime_data = coordinator

    # Store coordinator in hass.data for service access
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Create/update sensor devices
    await async_setup_sensor_devices(hass, entry)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener
    entry.async_on_unload(entry.add_update_listener(config_entry_update_listener))

    # Register services
    await async_register_services(hass)

    # Register static path for frontend files and Lovelace resources
    await _register_frontend(hass)

    return True


async def async_setup_sensor_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or update device entries for each sensor."""
    device_registry = dr.async_get(hass)
    sensors = entry.data.get(CONF_SENSORS, [])

    # Track existing sensor device IDs to clean up removed sensors
    existing_device_ids = set()

    for idx, sensor in enumerate(sensors):
        # Generate unique identifier for this sensor
        sensor_unique_id = f"{entry.entry_id}_sensor_{idx}"

        # Get sensor name
        sensor_name = sensor.get(CONF_SENSOR_NAME) or f"Sensor {idx + 1}"

        # Create or update device
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sensor_unique_id)},
            name=sensor_name,
            manufacturer="Radar Fusion",
            model="LD2450 Radar Sensor",
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=f"homeassistant://config/integrations/integration/{DOMAIN}",
        )

        existing_device_ids.add(device.id)

    # Clean up devices for sensors that were removed
    all_devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    for device in all_devices:
        # Check if this is a sensor device (has our domain identifier)
        is_sensor_device = any(
            identifier[0] == DOMAIN
            and identifier[1].startswith(f"{entry.entry_id}_sensor_")
            for identifier in device.identifiers
        )

        if is_sensor_device and device.id not in existing_device_ids:
            device_registry.async_remove_device(device.id)


async def config_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener, called when the config entry options are changed."""
    # Update sensor devices in case sensors were added/removed/renamed
    await async_setup_sensor_devices(hass, entry)
    # Reload to apply changes
    await hass.config_entries.async_reload(entry.entry_id)


async def _register_frontend(hass: HomeAssistant) -> None:
    """Register static path and Lovelace resources for Radar Fusion cards."""
    # Skip if http component is not loaded (e.g., in tests)
    if hass.http is None:
        return

    # Get the path to the frontend directory
    frontend_path = Path(__file__).parent / "frontend"

    # Register static path to serve frontend files
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_PATH_URL, str(frontend_path), cache_headers=False)]
    )

    # Access the Lovelace resources collection
    lovelace_data = hass.data.get(LOVELACE_DOMAIN)
    if lovelace_data is None:
        return

    resources = lovelace_data.resources
    if resources is None:
        return

    # Check if resources already registered
    existing_resources = resources.async_items()
    resource_urls = {item["url"] for item in existing_resources}

    # Define card resources to register
    card_resources = [
        {
            "url": f"{STATIC_PATH_URL}/radar-fusion-card.js",
            "res_type": "module",
        },
        {
            "url": f"{STATIC_PATH_URL}/radar-fusion-heatmap-card.js",
            "res_type": "module",
        },
    ]

    # Register each resource if not already present
    for resource in card_resources:
        if resource["url"] not in resource_urls:
            await resources.async_create_item(resource)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Radar Fusion integration (register services globally)."""
    await async_register_services(hass)
    return True


async def async_register_services(hass: HomeAssistant) -> None:
    """Register services for Radar Fusion."""

    async def handle_set_test_mode(call: ServiceCall) -> None:
        """Handle set_test_mode service call."""
        config_entry_id = call.data["config_entry_id"]
        enabled = call.data["enabled"]
        entry = None
        for entry_obj in hass.config_entries.async_entries(DOMAIN):
            if entry_obj.entry_id == config_entry_id:
                entry = entry_obj
                break
        if not entry:
            return
        new_options = dict(entry.options)
        new_options[CONF_TEST_MODE] = enabled
        hass.config_entries.async_update_entry(entry, options=new_options)

    async def handle_get_floor_data(call: ServiceCall) -> dict:
        """Handle get_floor_data service call."""
        config_entry_id = call.data["config_entry_id"]
        floor_id = call.data.get(CONF_FLOOR_ID)

        if config_entry_id not in hass.data[DOMAIN]:
            return {"error": "Config entry not found"}

        coordinator: RadarFusionCoordinator = hass.data[DOMAIN][config_entry_id]
        return coordinator.get_floor_data(floor_id)

    async def handle_reset_heatmap(call: ServiceCall) -> None:
        """Handle reset_heatmap service call."""
        config_entry_id = call.data["config_entry_id"]
        floor_id = call.data.get(CONF_FLOOR_ID)

        if config_entry_id not in hass.data[DOMAIN]:
            return

        coordinator: RadarFusionCoordinator = hass.data[DOMAIN][config_entry_id]
        coordinator.reset_heatmap(floor_id)

    # Only register once
    if not hass.services.has_service(DOMAIN, SERVICE_GET_FLOOR_DATA):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_FLOOR_DATA,
            handle_get_floor_data,
            schema=SERVICE_GET_FLOOR_DATA_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESET_HEATMAP):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_HEATMAP,
            handle_reset_heatmap,
            schema=SERVICE_RESET_HEATMAP_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_TEST_MODE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_TEST_MODE,
            handle_set_test_mode,
            schema=SERVICE_SET_TEST_MODE_SCHEMA,
        )
