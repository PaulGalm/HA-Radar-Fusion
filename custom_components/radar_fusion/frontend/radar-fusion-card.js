class RadarFusionCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._showZones = true;
    this._showSensors = true;
    this._showDetectionZones = true;
    this._expandedView = false; // Track expanded view state
    this._sensorColors = [
      "#FF6B6B",
      "#4ECDC4",
      "#45B7D1",
      "#FFA07A",
      "#98D8C8",
      "#F7DC6F",
      "#BB8FCE",
      "#85C1E2",
      "#F8B739",
      "#52B788",
    ];
    this._heatmapScale = "hourly";
    this._updateInterval = null;
    this._updateFrequency = 1000; // Slower fetch
    this._lastTargetHash = null; // Only compare targets
    this._pendingData = null;
    this._animationFrameId = null;
  }

  connectedCallback() {
    // Start polling when card is added to DOM
    this.startPolling();
  }

  toggleExpandedView(expanded) {
    this._expandedView = expanded;
    if (expanded) {
      this.classList.add("expanded");
      // Prevent scrolling on body
      document.body.style.overflow = "hidden";
    } else {
      this.classList.remove("expanded");
      document.body.style.overflow = "";
    }
    this.render();
  }

  disconnectedCallback() {
    // Stop polling when card is removed from DOM
    this.stopPolling();
    // Clean up event listeners
    if (this._handleKeyPress) {
      document.removeEventListener("keydown", this._handleKeyPress);
    }
  }

  startPolling() {
    if (this._updateInterval) return;
    this._updateInterval = setInterval(async () => {
      if (this._hass && this._config.config_entry_id) {
        const newData = await this.getFloorData();
        if (!newData) return;

        // Hash only targets and block_zones (things that actually change frequently)
        const targetHash = JSON.stringify(
          newData.targets?.map((t) => [t.x, t.y, t.age]) || [],
        );

        if (targetHash !== this._lastTargetHash) {
          this._lastTargetHash = targetHash;
          this._pendingData = newData;
          // Use requestAnimationFrame for smooth rendering
          if (this._animationFrameId)
            cancelAnimationFrame(this._animationFrameId);
          this._animationFrameId = requestAnimationFrame(() =>
            this.drawRadar(),
          );
        }
      }
    }, this._updateFrequency);
  }

  stopPolling() {
    if (this._updateInterval) {
      clearInterval(this._updateInterval);
      this._updateInterval = null;
    }
  }

  setConfig(config) {
    this._config = {
      config_entry_id: config.config_entry_id || null, // Auto-discover if not provided
      width: config.width || 800,
      height: config.height || 600,
      grid_size: config.grid_size || 5000, // mm
      show_grid: config.show_grid === true,
      floor_id: config.floor_id || null,
      title: config.title || "Radar Fusion",
      floorplan_url: config.floorplan_url || null,
      floorplan_width_mm: config.floorplan_width_mm || null, // Physical width of floorplan in mm
      floorplan_height_mm: config.floorplan_height_mm || null, // Physical height of floorplan in mm
      offset_x: config.offset_x || 0, // Shift in mm
      offset_y: config.offset_y || 0, // Shift in mm
      zone_opacity: config.zone_opacity !== undefined ? config.zone_opacity : 0.5, // Zone opacity (0.0-1.0)
      detection_opacity: config.detection_opacity !== undefined ? config.detection_opacity : 0.3, // Detection cone opacity (0.0-1.0)
      show_header: config.show_header !== false, // Show header with title and buttons (default: true)
      show_legend: config.show_legend !== false, // Show legend and stats (default: true)
    };
    // Load floorplan image if provided
    if (this._config.floorplan_url) {
      const img = new Image();
      img.onload = () => {
        this._floorplanImage = img;
        // Re-render when image loads to apply correct dimensions
        this.render();
      };
      img.onerror = () => {
        console.warn("Failed to load floorplan image");
      };
      img.src = this._config.floorplan_url;
    }

    // If hass is already set, initialize the card now
    if (this._hass) {
      this.updateCard();
      this.startPolling();
    }
  }

  async discoverConfigEntry() {
    // Auto-discover radar_fusion config entry
    if (this._config.config_entry_id) return this._config.config_entry_id;

    try {
      console.log("Radar Fusion: Attempting auto-discovery...");

      // Look for any radar_fusion entity
      const states = this._hass.states;
      for (const entityId in states) {
        // Check if entity might belong to radar_fusion
        if (
          entityId.startsWith("binary_sensor.") ||
          entityId.startsWith("switch.")
        ) {
          try {
            const entityInfo = await this._hass.connection.sendMessagePromise({
              type: "config/entity_registry/get",
              entity_id: entityId,
            });

            if (
              entityInfo &&
              entityInfo.platform === "radar_fusion" &&
              entityInfo.config_entry_id
            ) {
              this._config.config_entry_id = entityInfo.config_entry_id;
              console.log(
                "Radar Fusion: Auto-discovered →",
                entityInfo.config_entry_id,
              );
              return entityInfo.config_entry_id;
            }
          } catch (err) {
            // Entity not in registry, skip
            continue;
          }
        }
      }

      console.warn(
        "Radar Fusion: No entities found. Please add sensors or zones first.",
      );
    } catch (error) {
      console.error("Radar Fusion: Discovery failed:", error);
    }

    return null;
  }

  set hass(hass) {
    const firstRun = !this._hass;
    this._hass = hass;

    // Don't start polling or update if we don't have a valid hass object
    if (!hass) return;

    // Only update/poll if card has been configured
    if (firstRun && this._config.config_entry_id !== undefined) {
      this.updateCard();
      this.startPolling();
    } else if (!firstRun) {
      // Subsequent hass updates - card is already running
      // Polling handles updates automatically
    }
  }

  async updateCard() {
    if (!this._hass) {
      console.error("Radar Fusion: No hass object");
      return;
    }

    // Show loading state initially
    this.renderLoading();

    // Auto-discover config entry if not set
    if (!this._config.config_entry_id) {
      console.log("Radar Fusion: No config_entry_id, starting discovery...");
      const discovered = await this.discoverConfigEntry();
      if (!discovered) {
        console.error("Radar Fusion: Discovery failed");
        this.renderError(
          "No Radar Fusion integration found. Please add it in Settings → Devices & Services.",
        );
        return;
      }
      console.log("Radar Fusion: Discovered config_entry_id:", discovered);
    }

    // Fetch initial data before rendering
    console.log("Radar Fusion: Fetching floor data...");
    const initialData = await this.getFloorData();
    if (initialData) {
      console.log("Radar Fusion: Got floor data:", initialData);
      this._pendingData = initialData;
      this._lastTargetHash = JSON.stringify(
        initialData.targets?.map((t) => [t.x, t.y, t.age]) || [],
      );
      this.render();
    } else {
      console.error("Radar Fusion: getFloorData returned null");
      this.renderError(
        "Failed to load radar data. Check that the integration is configured correctly.",
      );
    }
  }

  render() {
    // Calculate canvas dimensions based on floorplan if provided
    let canvasWidth = this._config.width;
    let canvasHeight = this._config.height;

    if (
      this._floorplanImage &&
      this._config.floorplan_width_mm &&
      this._config.floorplan_height_mm
    ) {
      // Use explicit dimensions
      const aspect =
        this._config.floorplan_width_mm / this._config.floorplan_height_mm;
      canvasHeight = Math.round(canvasWidth / aspect);
    } else if (this._floorplanImage && this._config.floorplan_width_mm) {
      // Calculate from image aspect ratio
      const imgAspect =
        this._floorplanImage.width / this._floorplanImage.height;
      canvasHeight = Math.round(canvasWidth / imgAspect);
    }

    const style = `
      <style>
        :host {
          display: block;
          padding: 16px;
        }
        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }
        .card-title {
          font-size: 20px;
          font-weight: 500;
        }
        .controls {
          display: flex;
          gap: 12px;
        }
        .toggle-btn {
          padding: 6px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          cursor: pointer;
          font-size: 12px;
          transition: all 0.2s;
        }
        .toggle-btn.active {
          background: var(--primary-color);
          color: var(--text-primary-color);
          border-color: var(--primary-color);
        }
        .canvas-container {
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          overflow: hidden;
          background: #1a1a1a;
          position: relative;
          width: 100%;
          aspect-ratio: ${canvasWidth} / ${canvasHeight};
        }
        canvas {
          display: block;
          width: 100%;
          height: 100%;
        }
        .legend {
          margin-top: 12px;
          display: flex;
          flex-wrap: wrap;
          gap: 16px;
          font-size: 12px;
        }
        .legend-item {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .legend-color {
          width: 16px;
          height: 16px;
          border-radius: 2px;
        }
        .stats {
          margin-top: 12px;
          font-size: 12px;
          color: var(--secondary-text-color);
        }
        :host(.expanded) {
          position: fixed;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          z-index: 999;
          padding: 0;
        }
        :host(.expanded) .card-container {
          width: 100%;
          height: 100%;
          display: flex;
          flex-direction: column;
        }
        :host(.expanded) .expanded-header {
          background: var(--primary-color);
          color: var(--text-primary-color);
          padding: 16px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 18px;
          font-weight: 500;
        }
        :host(.expanded) .expanded-close-btn {
          background: none;
          border: none;
          color: var(--text-primary-color);
          font-size: 28px;
          cursor: pointer;
          padding: 0;
          width: 40px;
          height: 40px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: opacity 0.2s;
        }
        :host(.expanded) .expanded-close-btn:hover {
          opacity: 0.7;
        }
        :host(.expanded) .canvas-container {
          flex: 1;
          overflow: auto;
          display: flex;
          align-items: center;
          justify-content: center;
          aspect-ratio: unset;
        }
        :host(.expanded) canvas {
          max-width: 100%;
          max-height: 100%;
          width: auto;
          height: auto;
          object-fit: contain;
        }
      </style>
    `;

    const html = `
      ${style}
      <div class="card-container">
        ${this._expandedView ? `
        <div class="expanded-header">
          <span>${this._config.title} - Expanded View</span>
          <button class="expanded-close-btn" id="close-expanded">✕</button>
        </div>
        ` : ''}
        ${this._config.show_header && !this._expandedView ? `
      <div class="card-header">
        <div class="card-title">${this._config.title}</div>
        <div class="controls">
          <button class="toggle-btn ${this._showZones ? "active" : ""}" id="toggle-zones">
            Zones
          </button>
          <button class="toggle-btn ${this._showSensors ? "active" : ""}" id="toggle-sensors">
            Sensors
          </button>
          <button class="toggle-btn ${this._showDetectionZones ? "active" : ""}" id="toggle-detection">
            Detection Zones
          </button>
          <button class="toggle-btn" id="expand-btn" title="Expand to fullscreen">⤢</button>
        </div>
      </div>
      ` : ''}
      <div class="canvas-container">
        <canvas id="radarCanvas" width="${canvasWidth}" height="${canvasHeight}"></canvas>
      </div>
      ${this._config.show_legend ? `
      <div class="legend" id="legend"></div>
      <div class="stats" id="stats"></div>
      ` : ''}
      </div>
    `;

    this.shadowRoot.innerHTML = html;

    // Add event listeners
    const expandBtn = this.shadowRoot.getElementById("expand-btn");
    if (expandBtn) {
      expandBtn.addEventListener("click", () => this.toggleExpandedView(true));
    }

    const closeExpandedBtn = this.shadowRoot.getElementById("close-expanded");
    if (closeExpandedBtn) {
      closeExpandedBtn.addEventListener("click", () => this.toggleExpandedView(false));
    }

    // Click on canvas to open expanded view
    const canvasEl = this.shadowRoot.getElementById("radarCanvas");
    if (canvasEl && !this._expandedView) {
      canvasEl.style.cursor = "pointer";
      canvasEl.addEventListener("click", () => this.toggleExpandedView(true));
    }

    // Escape key to close expanded view
    this._handleKeyPress = (e) => {
      if (e.key === "Escape" && this._expandedView) {
        this.toggleExpandedView(false);
      }
    };
    document.addEventListener("keydown", this._handleKeyPress);

    this.shadowRoot
      .getElementById("toggle-zones")
      .addEventListener("click", () => {
        this._showZones = !this._showZones;
        this.updateCard();
      });
    this.shadowRoot
      .getElementById("toggle-sensors")
      .addEventListener("click", () => {
        this._showSensors = !this._showSensors;
        this.updateCard();
      });
    this.shadowRoot
      .getElementById("toggle-detection")
      .addEventListener("click", () => {
        this._showDetectionZones = !this._showDetectionZones;
        this.updateCard();
      });

    this.drawRadar();
  }

  async drawRadar() {
    const canvas = this.shadowRoot.getElementById("radarCanvas");
    if (!canvas) return;

    const data = this._pendingData;
    if (!data) return;

    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;

    // Clear canvas only
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(0, 0, width, height);

    let gridSize = this._config.grid_size;
    let scale = Math.min(width / gridSize, height / gridSize) * 0.9;
    let actualGridHeight = gridSize; // For calculating origin positioning

    // If floorplan is provided and has dimensions metadata, scale according to floorplan
    if (this._floorplanImage && this._config.floorplan_width_mm) {
      const floorplanPhysicalWidth = this._config.floorplan_width_mm;
      const floorplanPhysicalHeight =
        this._config.floorplan_height_mm ||
        (this._floorplanImage.height / this._floorplanImage.width) *
        floorplanPhysicalWidth;

      gridSize = floorplanPhysicalWidth;
      actualGridHeight = floorplanPhysicalHeight;

      // Calculate scale based on canvas and floorplan dimensions
      const canvasAspect = width / height;
      const floorplanAspect = floorplanPhysicalWidth / floorplanPhysicalHeight;

      let scale_x = (width * 0.9) / floorplanPhysicalWidth;
      let scale_y = (height * 0.9) / floorplanPhysicalHeight;

      // Use the smaller scale to fit the entire floorplan
      scale = Math.min(scale_x, scale_y);
    }

    // Origin at lower-left corner (0,0 = lower-left, not center)
    const originX = width * 0.05; // 5% margin from left
    const originY = height * 0.95; // 5% margin from bottom

    // Helper function to convert mm coordinates to canvas (0,0 at lower-left)
    const toCanvas = (x, y) => ({
      x: originX + (x + this._config.offset_x) * scale,
      y: originY - (y + this._config.offset_y) * scale, // Invert Y for canvas
    });

    // Draw floorplan background if provided (maintain aspect ratio)
    if (this._floorplanImage) {
      const imgWidth = this._floorplanImage.width;
      const imgHeight = this._floorplanImage.height;
      const imgAspect = imgWidth / imgHeight;
      const canvasAspect = width / height;
      let drawWidth, drawHeight, drawX, drawY;

      if (imgAspect > canvasAspect) {
        // Image is wider, fit to height
        drawHeight = height;
        drawWidth = drawHeight * imgAspect;
      } else {
        // Image is taller, fit to width
        drawWidth = width;
        drawHeight = drawWidth / imgAspect;
      }

      // Center the image
      drawX = (width - drawWidth) / 2;
      drawY = (height - drawHeight) / 2;

      ctx.drawImage(this._floorplanImage, drawX, drawY, drawWidth, drawHeight);
    }

    // Draw grid
    if (this._config.show_grid) {
      ctx.strokeStyle = "#2a2a2a";
      ctx.lineWidth = 1;
      const gridStep = 1000; // 1 meter

      // Draw vertical grid lines (X axis)
      for (let x = 0; x <= gridSize; x += gridStep) {
        const pos = toCanvas(x, 0);
        ctx.beginPath();
        ctx.moveTo(pos.x, originY);
        ctx.lineTo(pos.x, originY - gridSize * scale);
        ctx.stroke();
      }

      // Draw horizontal grid lines (Y axis)
      for (let y = 0; y <= gridSize; y += gridStep) {
        const pos = toCanvas(0, y);
        ctx.beginPath();
        ctx.moveTo(originX, pos.y);
        ctx.lineTo(originX + gridSize * scale, pos.y);
        ctx.stroke();
      }

      // Draw center axes
      ctx.strokeStyle = "#3a3a3a";
      ctx.lineWidth = 2;
      // Draw X axis (horizontal at originY)
      ctx.beginPath();
      ctx.moveTo(originX, originY);
      ctx.lineTo(originX + gridSize * scale, originY);
      ctx.stroke();
      // Draw Y axis (vertical at originX)
      ctx.beginPath();
      ctx.moveTo(originX, originY);
      ctx.lineTo(originX, originY - gridSize * scale);
      ctx.stroke();
    }

    // Draw zones (polygons)
    if (this._showZones && data.zones) {
      console.log("Drawing zones:", data.zones.length, data.zones);
      data.zones.forEach((zone) => {
        if (zone.vertices && zone.vertices.length >= 3) {
          const fillAlpha = Math.round(this._config.zone_opacity * 255).toString(16).padStart(2, '0');
          const strokeAlpha = Math.round(Math.min(this._config.zone_opacity * 3, 1.0) * 255).toString(16).padStart(2, '0');
          ctx.fillStyle = `#4CAF50${fillAlpha}`;
          ctx.strokeStyle = `#4CAF50${strokeAlpha}`;
          ctx.lineWidth = 2;

          ctx.beginPath();
          const first = toCanvas(zone.vertices[0][0], zone.vertices[0][1]);
          ctx.moveTo(first.x, first.y);
          for (let i = 1; i < zone.vertices.length; i++) {
            const point = toCanvas(zone.vertices[i][0], zone.vertices[i][1]);
            ctx.lineTo(point.x, point.y);
          }
          ctx.closePath();
          ctx.fill();
          ctx.stroke();

          // Draw zone name
          const center = this.getPolygonCenter(zone.vertices);
          const centerCanvas = toCanvas(center.x, center.y);
          ctx.fillStyle = "#4CAF50";
          ctx.font = "14px sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(zone.name, centerCanvas.x, centerCanvas.y);
        }
      });
    } else {
      console.log(
        "Zones not shown - _showZones:",
        this._showZones,
        "data.zones:",
        data.zones,
      );
    }

    // Draw block zones
    if (this._showDetectionZones && data.block_zones) {
      data.block_zones.forEach((zone) => {
        if (zone.vertices && zone.vertices.length >= 3) {
          const fillAlpha = Math.round(this._config.zone_opacity * 255).toString(16).padStart(2, '0');
          const strokeAlpha = Math.round(Math.min(this._config.zone_opacity * 3, 1.0) * 255).toString(16).padStart(2, '0');
          ctx.fillStyle = `#F44336${fillAlpha}`;
          ctx.strokeStyle = `#F44336${strokeAlpha}`;
          ctx.lineWidth = 2;
          ctx.setLineDash([5, 5]);

          ctx.beginPath();
          const first = toCanvas(zone.vertices[0][0], zone.vertices[0][1]);
          ctx.moveTo(first.x, first.y);
          for (let i = 1; i < zone.vertices.length; i++) {
            const point = toCanvas(zone.vertices[i][0], zone.vertices[i][1]);
            ctx.lineTo(point.x, point.y);
          }
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
          ctx.setLineDash([]);

          // Draw zone name
          const center = this.getPolygonCenter(zone.vertices);
          const centerCanvas = toCanvas(center.x, center.y);
          ctx.fillStyle = "#F44336";
          ctx.font = "12px sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(
            zone.name + " (blocked)",
            centerCanvas.x,
            centerCanvas.y,
          );
        }
      });
    }

    // Draw sensors and detection zones
    if (data.sensors) {
      const legend = this.shadowRoot.getElementById("legend");
      if (legend) {
        legend.innerHTML = "";
      }

      data.sensors.forEach((sensor, idx) => {
        const color = this._sensorColors[idx % this._sensorColors.length];

        // Draw detection zone (6m range, 120° cone for LD2450)
        if (this._showDetectionZones) {
          const range = 6000; // 6 meters in mm
          const angle = 120; // degrees total (±60°)
          const rotation = sensor.rotation || 0;

          const fillAlpha = Math.round(this._config.detection_opacity * 255).toString(16).padStart(2, '0');
          const strokeAlpha = Math.round(Math.min(this._config.detection_opacity * 4, 1.0) * 255).toString(16).padStart(2, '0');
          ctx.fillStyle = color + fillAlpha;
          ctx.strokeStyle = color + strokeAlpha;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);

          const sensorPos = toCanvas(sensor.position_x, sensor.position_y);
          ctx.beginPath();
          ctx.moveTo(sensorPos.x, sensorPos.y);

          // Draw cone
          const startAngle = ((rotation - angle / 2) * Math.PI) / 180;
          const endAngle = ((rotation + angle / 2) * Math.PI) / 180;

          for (let a = startAngle; a <= endAngle; a += 0.1) {
            const x = sensor.position_x + range * Math.cos(a + Math.PI / 2);
            const y = sensor.position_y + range * Math.sin(a + Math.PI / 2);
            const pos = toCanvas(x, y);
            ctx.lineTo(pos.x, pos.y);
          }
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Draw sensor position
        if (this._showSensors) {
          const sensorPos = toCanvas(sensor.position_x, sensor.position_y);

          // Sensor marker
          ctx.fillStyle = color;
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(sensorPos.x, sensorPos.y, 10, 0, 2 * Math.PI);
          ctx.fill();
          ctx.stroke();

          // Direction indicator
          const rotation = sensor.rotation || 0;
          const dirLength = 20;
          const dirAngle = ((rotation + 90) * Math.PI) / 180;
          const dirX = sensorPos.x + dirLength * Math.cos(dirAngle);
          const dirY = sensorPos.y - dirLength * Math.sin(dirAngle);

          ctx.strokeStyle = color;
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.moveTo(sensorPos.x, sensorPos.y);
          ctx.lineTo(dirX, dirY);
          ctx.stroke();

          // Sensor label
          // Sensor label - show name if available, otherwise index
          const sensorLabel = sensor.name || `S${idx + 1}`;
          ctx.fillStyle = "#fff";
          ctx.font = "10px sans-serif";
          ctx.textAlign = "center";
          ctx.fillText(sensorLabel, sensorPos.x, sensorPos.y - 18);
        }

        // Add to legend
        const legendItem = document.createElement("div");
        legendItem.className = "legend-item";
        const sensorDisplayName = sensor.name || `Sensor ${idx + 1}`;
        legendItem.innerHTML = `
          <div class="legend-color" style="background: ${color}"></div>
          <span>${sensorDisplayName} (${sensor.target_count || 0} targets)</span>
        `;
        if (legend) {
          legend.appendChild(legendItem);
        }
      });
    }

    // Draw targets
    if (data.targets && data.targets.length > 0) {
      console.log("Drawing targets:", data.targets.length, data.targets);
      data.targets.forEach((target) => {
        // Skip invalid targets
        if (target.x === -1 || target.y === -1) {
          return;
        }

        const sensorIdx = data.sensors.findIndex(
          (s) =>
            target.sensor_entities &&
            s.target_entities &&
            s.target_entities.some((e) => target.sensor_entities.includes(e)),
        );
        const color = this._sensorColors[sensorIdx >= 0 ? sensorIdx : 0];

        const pos = toCanvas(target.x, target.y);

        console.log(
          `Target at (${target.x}, ${target.y}) -> canvas (${pos.x}, ${pos.y}), color: ${color}`,
        );

        // Draw target
        ctx.fillStyle = color;
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, 6, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();

        // Draw target trail/motion indicator
        if (target.age !== undefined && target.age < 5) {
          const alpha = 1 - target.age / 5;
          ctx.fillStyle =
            color +
            Math.floor(alpha * 255)
              .toString(16)
              .padStart(2, "0");
          ctx.beginPath();
          ctx.arc(pos.x, pos.y, 8 + target.age * 2, 0, 2 * Math.PI);
          ctx.fill();
        }
      });
    }

    // ...heatmap overlay removed: now handled by standalone card...

    // Update stats
    const totalTargets = data.targets?.length || 0;
    const activeSensors =
      data.sensors?.filter((s) => s.target_count > 0).length || 0;
    const totalZones = data.zones?.length || 0;
    if (statsEl) {
      statsEl.textContent = `${totalTargets} active targets • ${activeSensors}/${data.sensors?.length || 0} sensors • ${totalZones} zones`;
    }
  }

  getPolygonCenter(vertices) {
    let x = 0,
      y = 0;
    vertices.forEach((v) => {
      x += v[0];
      y += v[1];
    });
    return { x: x / vertices.length, y: y / vertices.length };
  }

  async getFloorData() {
    try {
      // Auto-discover config entry if needed
      const configEntryId = await this.discoverConfigEntry();
      if (!configEntryId) {
        console.error("No radar_fusion config entry found");
        return null;
      }

      console.log(
        "Calling get_floor_data service with config_entry_id:",
        configEntryId,
      );

      // Call the service using hass.callWS
      const result = await this._hass.callWS({
        type: "call_service",
        domain: "radar_fusion",
        service: "get_floor_data",
        service_data: {
          config_entry_id: configEntryId,
          floor_id: this._config.floor_id || null,
        },
        return_response: true,
      });

      console.log("Received floor data:", result);
      return result.response;
    } catch (error) {
      console.error("Failed to get floor data:", error);
      return null;
    }
  }

  async resetHeatmap() {
    try {
      const configEntryId = await this.discoverConfigEntry();
      if (!configEntryId) return null;

      await this._hass.callWS({
        type: "call_service",
        domain: "radar_fusion",
        service: "reset_heatmap",
        service_data: {
          config_entry_id: configEntryId,
          floor_id: this._config.floor_id || null,
        },
      });
      return true;
    } catch (err) {
      console.error("Failed to reset heatmap:", err);
      return false;
    }
  }

  renderError(message) {
    this.shadowRoot.innerHTML = `
      <style>
        .error-container {
          padding: 16px;
          text-align: center;
        }
        .error-title {
          color: var(--error-color);
          font-size: 18px;
          margin-bottom: 12px;
        }
        .error-message {
          color: var(--secondary-text-color);
          font-size: 14px;
          margin-bottom: 8px;
        }
        .error-help {
          color: var(--primary-color);
          font-size: 12px;
          margin-top: 16px;
        }
      </style>
      <div class="error-container">
        <div class="error-title">⚠️ Radar Fusion</div>
        <div class="error-message">${message}</div>
        <div class="error-help">
          💡 Make sure the integration is installed and configured<br>
          Settings → Devices & Services → Add Integration → Radar Fusion
        </div>
      </div>
    `;
  }

  renderLoading() {
    this.shadowRoot.innerHTML = `
      <style>
        .loading-container {
          padding: 32px;
          text-align: center;
        }
        .loading-title {
          font-size: 18px;
          margin-bottom: 16px;
          color: var(--primary-text-color);
        }
        .loading-spinner {
          width: 40px;
          height: 40px;
          margin: 0 auto;
          border: 4px solid var(--divider-color);
          border-top: 4px solid var(--primary-color);
          border-radius: 50%;
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      </style>
      <div class="loading-container">
        <div class="loading-title">🔄 Loading Radar Fusion...</div>
        <div class="loading-spinner"></div>
      </div>
    `;
  }

  getCardSize() {
    return 5;
  }

  static getConfigElement() {
    return document.createElement("radar-fusion-card-editor");
  }

  static getStubConfig() {
    return {
      config_entry_id: "",
      title: "Radar Fusion",
    };
  }
}

// Configuration editor for the visual card editor
class RadarFusionCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = { ...config };
    this.render();
    this.attachEventListeners();
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          padding: 16px;
        }
        .form-group {
          margin-bottom: 16px;
        }
        label {
          display: block;
          margin-bottom: 8px;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        input[type="text"],
        input[type="number"] {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          font-size: 14px;
          box-sizing: border-box;
          background: var(--card-background-color);
          color: var(--primary-text-color);
        }
        input:focus {
          outline: none;
          border-color: var(--primary-color);
        }
        .form-description {
          font-size: 12px;
          color: var(--secondary-text-color);
          margin-top: 4px;
        }
        input[type="checkbox"] {
          margin-right: 8px;
          cursor: pointer;
        }
        .checkbox-group {
          display: flex;
          align-items: center;
          margin-top: 8px;
        }
        .checkbox-group label {
          margin: 0;
          font-weight: normal;
        }
      </style>
      <div class="form-group">
        <label for="config_entry_id">Config Entry ID *</label>
        <input
          type="text"
          id="config_entry_id"
          placeholder="Config entry ID from Settings"
          value="${this._config.config_entry_id || ""}"
        />
        <div class="form-description">
          Find this in Settings → Devices & Services → Radar Fusion
        </div>
      </div>

      <div class="form-group">
        <label for="title">Title</label>
        <input
          type="text"
          id="title"
          placeholder="Radar Fusion"
          value="${this._config.title || "Radar Fusion"}"
        />
      </div>

      <div class="form-group">
        <label for="floor_id">Floor ID</label>
        <input
          type="text"
          id="floor_id"
          placeholder="Optional - leave empty for all floors"
          value="${this._config.floor_id || ""}"
        />
        <div class="form-description">
          Filter to show only a specific floor (e.g., "ground_floor", "first_floor")
        </div>
      </div>

      <div class="form-group">
        <label for="floorplan_url">Floorplan Image URL</label>
        <input
          type="text"
          id="floorplan_url"
          placeholder="/local/floorplan.png"
          value="${this._config.floorplan_url || ""}"
        />
        <div class="form-description">
          Path to your floorplan image (upload via Media browser)
        </div>
      </div>

      <div class="form-group">
        <label for="floorplan_width_mm">Floorplan Width (mm)</label>
        <input
          type="number"
          id="floorplan_width_mm"
          placeholder="10000"
          min="0"
          step="100"
          value="${this._config.floorplan_width_mm || ""}"
        />
        <div class="form-description">
          Physical width of your floorplan in millimeters
        </div>
      </div>

      <div class="form-group">
        <label for="floorplan_height_mm">Floorplan Height (mm)</label>
        <input
          type="number"
          id="floorplan_height_mm"
          placeholder="8000"
          min="0"
          step="100"
          value="${this._config.floorplan_height_mm || ""}"
        />
        <div class="form-description">
          Physical height of your floorplan in millimeters
        </div>
      </div>

      <div class="form-group">
        <label for="offset_x">X Offset (mm)</label>
        <input
          type="number"
          id="offset_x"
          placeholder="0"
          step="10"
          value="${this._config.offset_x || 0}"
        />
        <div class="form-description">
          Horizontal offset to align radar coordinates with floorplan
        </div>
      </div>

      <div class="form-group">
        <label for="offset_y">Y Offset (mm)</label>
        <input
          type="number"
          id="offset_y"
          placeholder="0"
          step="10"
          value="${this._config.offset_y || 0}"
        />
        <div class="form-description">
          Vertical offset to align radar coordinates with floorplan
        </div>
      </div>

      <div class="form-group">
        <label for="width">Width (pixels)</label>
        <input
          type="number"
          id="width"
          min="300"
          max="2000"
          step="100"
          value="${this._config.width || 800}"
        />
      </div>

      <div class="form-group">
        <label for="height">Height (pixels)</label>
        <input
          type="number"
          id="height"
          min="200"
          max="1500"
          step="100"
          value="${this._config.height || 600}"
        />
      </div>

      <div class="form-group">
        <label for="grid_size">Grid Size (mm)</label>
        <input
          type="number"
          id="grid_size"
          min="1000"
          max="20000"
          step="500"
          value="${this._config.grid_size || 5000}"
        />
      </div>

      <div class="form-group">
        <label for="zone_opacity">Zone Opacity</label>
        <input
          type="number"
          id="zone_opacity"
          min="0"
          max="1"
          step="0.1"
          value="${this._config.zone_opacity !== undefined ? this._config.zone_opacity : 0.5}"
        />
        <div class="form-description">
          Transparency of detection zones and block zones (0.0 = transparent, 1.0 = opaque)
        </div>
      </div>

      <div class="form-group">
        <label for="detection_opacity">Detection Cone Opacity</label>
        <input
          type="number"
          id="detection_opacity"
          min="0"
          max="1"
          step="0.1"
          value="${this._config.detection_opacity !== undefined ? this._config.detection_opacity : 0.3}"
        />
        <div class="form-description">
          Transparency of sensor detection cones (0.0 = transparent, 1.0 = opaque)
        </div>
      </div>

      <div class="form-group">
        <div class="checkbox-group">
          <input
            type="checkbox"
            id="show_header"
            ${this._config.show_header !== false ? "checked" : ""}
          />
          <label for="show_header">Show Header</label>
        </div>
        <div class="form-description">
          Display title and control buttons (Zones, Sensors, Detection Zones)
        </div>
      </div>

      <div class="form-group">
        <div class="checkbox-group">
          <input
            type="checkbox"
            id="show_legend"
            ${this._config.show_legend !== false ? "checked" : ""}
          />
          <label for="show_legend">Show Legend</label>
        </div>
        <div class="form-description">
          Display statistics at the bottom
        </div>
      </div>

      <div class="form-group">
        <div class="checkbox-group">
          <input
            type="checkbox"
            id="show_grid"
            ${this._config.show_grid ? "checked" : ""}
          />
          <label for="show_grid">Show Grid</label>
        </div>
      </div>
    `;
  }

  attachEventListeners() {
    const inputs = this.shadowRoot.querySelectorAll("input");
    inputs.forEach((input) => {
      input.addEventListener("change", (ev) => this.handleInputChange(ev));
    });
  }

  handleInputChange(ev) {
    const target = ev.target;
    const id = target.id;
    let value;

    if (target.type === "checkbox") {
      value = target.checked;
    } else if (target.type === "number") {
      // Parse as float for opacity fields, integer for others
      value = (id === "zone_opacity" || id === "detection_opacity") ? parseFloat(target.value) : parseInt(target.value, 10);
    } else {
      value = target.value;
    }

    this._config = { ...this._config, [id]: value };
    this.fireConfigChanged();
  }

  fireConfigChanged() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

customElements.define("radar-fusion-card-editor", RadarFusionCardEditor);
customElements.define("radar-fusion-card", RadarFusionCard);

// Register with custom cards registry
window.customCards = window.customCards || [];
window.customCards.push({
  type: "radar-fusion-card",
  name: "Radar Fusion Card",
  description: "Visualize radar sensors, zones, and detected targets",
  preview: true,
  configElement: "radar-fusion-card-editor",
});

console.info(
  "%c RADAR-FUSION-CARD %c v1.0.0 ",
  "color: white; background: #4CAF50; font-weight: bold;",
  "color: #4CAF50; background: white; font-weight: bold;",
);
