# Radar Fusion for Home Assistant

Multi-sensor radar fusion for presence detection with ESPHome LD2450 sensors.

## Features

- 🎯 **Multi-Sensor Fusion**: Combine data from multiple LD2450 sensors with different orientations and positions
- 📍 **Custom Detection Zones**: Create polygon-based zones to define detection areas
- 🚫 **Block Zones**: Exclude areas from detection to filter false positives
- 🗺️ **Floor Organization**: Organize sensors and zones by floor
- 🔥 **Heatmap Tracking**: Visualize presence patterns with hourly, 24h, and all-time heatmaps
- 🎨 **Advanced Visualizations**: Includes two custom Lovelace cards for real-time and heatmap views
- 🎚️ **Fine-Grained Opacity Controls**: Independently control zone, detection cone, and heatmap transparency
- 🎮 **Visual Configuration**: Toggle headers, legends, grids, and other UI elements directly in card editor
- 🧪 **Test Mode**: Mock data generation for testing and development

## Requirements

- Home Assistant 2025.1.0+
- ESPHome LD2450 sensors with target entities:
  - `sensor.device_target{1-3}_{x,y}`

## Installation via HACS

1. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (menu) → Custom repositories
   - Repository: `https://github.com/PaulGalm/radar-fusion`
   - Category: Integration
2. Click "Explore & Download Repositories"
3. Search for "Radar Fusion"
4. Click "Download"
5. Restart Home Assistant
6. Add integration via Settings → Devices & Services → Add Integration → Radar Fusion

## Quick Start

### 1. Add the Integration

Settings → Devices & Services → Create Integration → Radar Fusion

Configure:
- **Name**: Display name for the integration
- **Staleness Timeout**: How long to keep targets without updates (default: 10s)
- **Test Mode**: Enable for mock data testing

### 2. Add Sensors

For each radar device:
- Select the device with target entities
- Set position (X, Y in millimeters)
- Set rotation (0-359°)
- Optional: Assign to a floor

### 3. Create Detection Zones

Define areas where you want to track presence:
- **Name**: Zone identifier (e.g., "living_room")
- **Floor**: Optional floor assignment
- **Vertices**: Polygon corners in format: `[[x1,y1], [x2,y2], [x3,y3], ...]`

### 4. Add to Dashboard

Add both visualization cards to see your radar data in action.

## Lovelace Cards

### Radar Fusion Card

Real-time visualization of sensor coverage, detection zones, and targets.

**Add resource:**
Settings → Dashboards → Resources → Add Resource
- URL: `/radar_fusion_static/radar-fusion-card.js`
- Type: JavaScript Module

**Configuration Options:**
```yaml
type: custom:radar-fusion-card
config_entry_id: YOUR_CONFIG_ENTRY_ID
floor_id: ground_floor                    # optional - filter to specific floor
floorplan_url: /local/floorplan.png       # optional - background image
floorplan_width_mm: 10000                 # optional - floorplan physical width
floorplan_height_mm: 8000                 # optional - floorplan physical height
offset_x: 0                               # optional - align radar to floorplan
offset_y: 0                               # optional - align radar to floorplan
width: 800                                # optional - card width (px)
height: 600                               # optional - card height (px)
grid_size: 5000                           # optional - grid cell size (mm)
zone_opacity: 0.5                         # optional - zone transparency (0.0-1.0)
detection_opacity: 0.3                    # optional - detection cone transparency (0.0-1.0)
show_header: true                         # optional - show title & buttons
show_legend: true                         # optional - show statistics
show_grid: false                          # optional - show grid overlay
```

**Interactive Controls:**
- **Zones**: Toggle zone visibility (green polygons)
- **Sensors**: Toggle sensor positions and targets (colored dots)
- **Detection Zones**: Toggle detection cone coverage (transparent cones)

**Visual Elements:**
- 🟢 **Green zones**: Detection zone areas
- 🔴 **Red zones**: Block zones (excluded areas)
- 🎯 **Colored circles**: Sensor positions and detected targets
- ⚪ **Transparent cones**: Sensor detection coverage (120° for LD2450)

### Radar Fusion Heatmap Card

Heatmap visualization showing where movement is detected over time.

**Add resource:**
Settings → Dashboards → Resources → Add Resource
- URL: `/radar_fusion_static/radar-fusion-heatmap-card.js`
- Type: JavaScript Module

**Configuration Options:**
```yaml
type: custom:radar-fusion-heatmap-card
config_entry_id: YOUR_CONFIG_ENTRY_ID
floor_id: ground_floor                    # optional - filter to specific floor
floorplan_url: /local/floorplan.png       # optional - background image
floorplan_width_mm: 10000                 # optional - floorplan physical width
floorplan_height_mm: 8000                 # optional - floorplan physical height
offset_x: 0                               # optional - align heatmap to floorplan
offset_y: 0                               # optional - align heatmap to floorplan
width: 800                                # optional - card width (px)
height: 600                               # optional - card height (px)
grid_size: 5000                           # optional - heatmap resolution (mm)
opacity: 50                               # optional - overlay transparency (0-100%)
show_header: true                         # optional - show title & controls
show_legend: true                         # optional - show statistics
show_grid: false                          # optional - show grid overlay
```

**Heatmap Scales:**
- **Hourly**: Last 3600 seconds of detection data
- **24h**: Last 86400 seconds of detection data
- **All-time**: Persistent heatmap data (survives restarts)

**Color Gradient:**
- 🟢 **Green**: Low detection density
- 🟡 **Yellow/Orange**: Medium detection density
- 🔴 **Red**: High detection density

**Controls:**
- Scale selector: Choose time period (hourly, 24h, all-time)
- Reset heatmap: Clear all heatmap data

## Services

### `radar_fusion.get_floor_data`

Get current sensor, zone, target, and heatmap data for visualization or automation.

**Parameters:**
- `config_entry_id` (required): Config entry ID
- `floor_id` (optional): Specific floor (null for all)

**Response:**
```json
{
  "floor_id": "ground_floor",
  "sensors": [...],
  "zones": [...],
  "block_zones": [...],
  "targets": [...],
  "heatmap": {
    "resolution_mm": 500,
    "hourly": {...},
    "24h": {...},
    "all_time": {...}
  }
}
```

### `radar_fusion.reset_heatmap`

Clear heatmap data for a floor or entire integration.

**Parameters:**
- `config_entry_id` (required): Config entry ID
- `floor_id` (optional): Specific floor (null for all floors)

### `radar_fusion.set_test_mode`

Enable or disable test mode for mock data testing.

**Parameters:**
- `config_entry_id` (required): Config entry ID
- `enabled` (required): Boolean to enable/disable

## Coordinate System

All coordinates use millimeters in a Cartesian system:

- **X-axis**: Left (-) to Right (+)
- **Y-axis**: Down (-) to Up (+)
- **Rotation**: 0° = pointing right, increases clockwise

### Sensor Calibration

Use `offset_x` and `offset_y` in card configuration to align radar coordinates with your floorplan image:

1. Note where a target appears on the radar visualization
2. Compare with actual position on floorplan
3. Adjust offset_x/offset_y until aligned
4. Save the card configuration

## Tips & Tricks

### Optimize Zone Detection
- Place zones only where you want to track presence
- Use block zones to exclude doorways, windows, etc.
- Keep zones reasonable size - avoid massive zones

### Fine-Tune Visualization
- Adjust `zone_opacity` to match your floorplan contrast
- Use `detection_opacity` for subtle coverage visualization
- Disable `show_legend` for compact minimalist view
- Disable `show_header` for full-screen visualization

### Performance
- Higher `grid_size` = better performance, lower resolution
- Lower `grid_size` = more detail, slightly higher CPU
- Default 5000mm (5m) cells are recommended for most homes
- The rolling heatmaps remove expired detections in arrival order. The all-time heatmap keeps its accumulated counts.

### Test Mode
Enable test mode to:
- Visualize cards before adding real sensors
- Test zone and automation logic
- Develop custom automations

## Troubleshooting

**Integration won't load:**
- Check Home Assistant logs for import errors
- Verify manifest.json is present and valid
- Restart Home Assistant

**No targets showing:**
- Ensure LD2450 sensors report target entities
- Check sensor positions/rotation are configured
- Verify targets are within defined zones
- Check block zones aren't filtering all targets

**Heatmap empty:**
- Enable test mode to verify functionality
- Targets must be detected first (check service response)
- Wait for targets to be detected over time
- Check heatmap resolution matches your space

**Visualization misaligned:**
- Verify sensor position and rotation in configuration
- Use card's `offset_x` and `offset_y` to calibrate
- Check floorplan dimensions match reality

## Development

This integration is developed in the [home-assistant/core](https://github.com/home-assistant/core) repository and synced to this HACS distribution repository.

**Supported devices:**
- ESPHome LD2450 radar sensors
- Any device providing `sensor.*target{1-3}_{x,y}` entities

## Support & Contributing

For issues, feature requests, or contributions:
- GitHub Issues: https://github.com/PaulGalm/radar-fusion/issues
- GitHub Discussions: https://github.com/PaulGalm/radar-fusion/discussions

## License

See [LICENSE](LICENSE) and [THIRD_PARTY_LICENSE](THIRD_PARTY_LICENSE) file for details.
