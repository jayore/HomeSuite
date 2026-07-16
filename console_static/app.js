(function () {
  "use strict";

  const SETUP_JOURNEY_STORAGE_KEY = "homesuite_setup_journey";
  const SETUP_JOURNEY_VIEWS = ["integrations", "rooms", "configuration", "audio", "console", "diagnostics"];

  function loadSetupJourney() {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(SETUP_JOURNEY_STORAGE_KEY) || "null");
      if (!saved || !saved.active || !SETUP_JOURNEY_VIEWS.includes(saved.view)) {
        window.sessionStorage.removeItem(SETUP_JOURNEY_STORAGE_KEY);
        return { active: false, label: "", view: "" };
      }
      return {
        active: true,
        label: String(saved.label || "").slice(0, 80),
        view: saved.view
      };
    } catch (_error) {
      return { active: false, label: "", view: "" };
    }
  }

  const initialSetupJourney = loadSetupJourney();

  const state = {
    snapshot: null,
    diagnostics: null,
    editableConfig: null,
    configEditing: false,
    configDraft: {},
    configPreview: null,
    configClientError: null,
    roomConfig: null,
    roomDraft: null,
    roomLoadError: null,
    roomCatalog: null,
    roomCatalogPromise: null,
    roomPreview: null,
    roomEditing: null,
    audioConfig: null,
    audioDraft: null,
    audioPreview: null,
    audioLoadError: null,
    audioSuggestion: null,
    integrations: null,
    integrationConfig: null,
    integrationPreview: null,
    integrationTests: {},
    integrationTestBusy: null,
    services: null,
    restartBusy: null,
    bootstrapRequired: false,
    setup: null,
    setupPreview: false,
    setupPreviewRole: "wakeword",
    setupActivationBusy: false,
    setupJourneyActive: initialSetupJourney.active,
    setupJourneyLabel: initialSetupJourney.label,
    setupJourneyView: initialSetupJourney.view,
    setupTested: false,
    audioCalibration: {
      token: null,
      stage: "idle",
      noise: null,
      speech: null,
      busy: false,
      error: null
    },
    mode: "test",
    chatBusy: false,
    sessionId: getSessionId(),
    messages: []
  };

  const INTEGRATION_TEST_SUCCESS_TTL_MS = 5000;
  const integrationTestDismissTimers = new Map();

  const BUTTON_MAP_KEYS = ["PHYSICAL_BUTTON_PINS", "PHYSICAL_BUTTON_ACTIONS"];
  const BUTTON_GESTURES = [
    { key: "press", label: "Single press", aliases: ["press", "single_press", "single"] },
    { key: "double_press", label: "Double press", aliases: ["double_press", "double"] },
    { key: "long_press", label: "Long press", aliases: ["long_press", "long", "hold"] }
  ];
  const INTEGRATION_VISUALS = {
    home_assistant: { icon: "house", color: "#18bcf2" },
    openai: { icon: "sparkles", color: "#10a37f" },
    plex: { icon: "play", color: "#ebaf00" },
    spotify: { icon: "music", color: "#1ed760" },
    telegram: { icon: "send-horizontal", color: "#26a5e4" },
    youtube: { icon: "video", color: "#ff0000" },
    alpaca: { icon: "trending-up", color: "#2f9e6f" },
    uptime_kuma: { icon: "activity", color: "#5cdd8b" },
    qbittorrent: { icon: "download", color: "#2f67ba" },
    seerr: { icon: "search", color: "#9b6bc0" },
    radarr: { icon: "film", color: "#ffcb3d" },
    sonarr: { icon: "tv", color: "#2596be" },
    lidarr: { icon: "headphones", color: "#00a65a" },
    porcupine: { icon: "audio-waveform", color: "#cf4f8b" },
    weather_astronomy: { icon: "cloud-sun", color: "#e5a21a" },
    calendar: { icon: "calendar-days", color: "#4285f4" }
  };

  const $ = function (selector, root) { return (root || document).querySelector(selector); };
  const $$ = function (selector, root) { return Array.from((root || document).querySelectorAll(selector)); };

  function getSessionId() {
    const stored = window.sessionStorage.getItem("homesuite_console_session_id");
    if (stored && /^[a-zA-Z0-9_-]{8,64}$/.test(stored)) return stored;
    const generated = (window.crypto && window.crypto.randomUUID)
      ? window.crypto.randomUUID().replace(/-/g, "").slice(0, 20)
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
    window.sessionStorage.setItem("homesuite_console_session_id", generated);
    return generated;
  }

  function getSetupTested() {
    try { return window.localStorage.getItem("homesuite_setup_tested") === "1"; }
    catch (_error) { return false; }
  }

  function rememberSetupTested() {
    state.setupTested = true;
    try { window.localStorage.setItem("homesuite_setup_tested", "1"); }
    catch (_error) { /* The setup hint can remain session-only. */ }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const node = element("span", "icon");
    node.setAttribute("data-icon", name);
    window.renderLucideIcons(node);
    return node;
  }

  function selectControl(control) {
    const wrapper = element("div", "select-control");
    const indicator = icon("chevron-down");
    indicator.classList.add("select-control-icon");
    indicator.setAttribute("aria-hidden", "true");
    wrapper.append(control, indicator);
    return wrapper;
  }

  function integrationProviderIcon(integration) {
    const visual = INTEGRATION_VISUALS[integration.id] || { icon: "plug", color: "#7c8a91" };
    const holder = element("span", "integration-provider-icon");
    holder.style.setProperty("--provider-color", visual.color);
    holder.setAttribute("aria-hidden", "true");
    holder.append(icon(visual.icon));
    return holder;
  }

  function statusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["ok", "configured", "guided"].includes(normalized)) return "ok";
    if (["warn", "partial", "deprecated"].includes(normalized)) return "warn";
    if (["fail", "not_configured", "unknown"].includes(normalized)) return "fail";
    return "skip";
  }

  function statusLabel(value) {
    const labels = {
      OK: "Ready",
      WARN: "Warning",
      FAIL: "Needs attention",
      SKIP: "Not checked",
      configured: "Configured",
      partial: "Partial",
      not_configured: "Not configured",
      guided: "Managed here",
      advanced: "File managed",
      deprecated: "Deprecated",
      unknown: "Unrecognized"
    };
    return labels[value] || String(value || "Unknown").replace(/_/g, " ");
  }

  function badge(value, customLabel) {
    return element("span", "status-badge " + statusClass(value), customLabel || statusLabel(value));
  }

  function roleLabel(role) {
    const labels = {
      text: "Text commands",
      api: "Companion API",
      ptt: "Push-to-talk",
      wakeword: "Wake-word listening"
    };
    return labels[role] || titleFromKey(role);
  }

  async function api(path, options) {
    const opts = Object.assign({ credentials: "same-origin" }, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, opts);
    let body;
    try { body = await response.json(); }
    catch (_error) { body = { ok: false, error: "invalid_response" }; }
    if (response.status === 401) {
      showLogin();
      throw new Error("Your console session ended. Sign in again.");
    }
    if (!response.ok || body.ok === false) {
      const error = new Error(String(body.error || "Request failed"));
      error.payload = body;
      throw error;
    }
    return body;
  }

  function showLogin(bootstrapRequired) {
    if (bootstrapRequired !== undefined) state.bootstrapRequired = Boolean(bootstrapRequired);
    redactEditableSecrets();
    $$(".secret-input input", $("#configuration-groups")).forEach(function (control) {
      control.value = "";
    });
    $("#app-shell").hidden = true;
    $("#login-screen").hidden = false;
    $("#login-form").hidden = state.bootstrapRequired;
    $("#bootstrap-form").hidden = !state.bootstrapRequired;
    $("#login-title").textContent = state.bootstrapRequired ? "Set up your console" : "Management Console";
    $("#login-intro").hidden = !state.bootstrapRequired;
    $("#login-intro").textContent = state.bootstrapRequired
      ? "This is a new Home Suite installation. Create the passphrase that protects configuration and testing on this node."
      : "";
    window.setTimeout(function () {
      $(state.bootstrapRequired ? "#bootstrap-passphrase" : "#console-key").focus();
    }, 0);
  }

  function showApp() {
    $("#login-screen").hidden = true;
    $("#app-shell").hidden = false;
    window.renderLucideIcons(document);
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(function () { toast.hidden = true; }, 4200);
  }

  function setHeaderStatus(status, label, compactLabel) {
    const holder = $("#header-status");
    const visualStatus = statusClass(status);
    const statusIcon = visualStatus === "ok"
      ? "circle-check"
      : (visualStatus === "warn" || visualStatus === "fail" ? "triangle-alert" : "refresh-cw");
    holder.className = "header-status " + visualStatus;
    holder.replaceChildren();
    const statusGlyph = icon(statusIcon);
    statusGlyph.classList.add("header-status-icon");
    holder.append(
      statusGlyph,
      element("span", "header-status-label", label),
      element("span", "header-status-compact", compactLabel || "")
    );
    holder.title = label + ". Open Diagnostics";
    holder.setAttribute("aria-label", holder.title);
  }

  function renderServiceRestartAction() {
    const button = $("#open-service-restart");
    const pending = ((state.services || {}).services || []).filter(function (service) {
      return service.restart_required;
    });
    button.hidden = pending.length === 0;
    button.disabled = Boolean(state.restartBusy);
    const label = $("span:last-child", button);
    if (label) label.textContent = state.restartBusy ? "Restarting" : "Restart required";
  }

  function renderServiceRestartDialog() {
    const holder = $("#service-restart-list");
    holder.replaceChildren();
    const services = ((state.services || {}).services || []);
    if (!services.length) {
      holder.append(element("div", "empty-state", "Service status is unavailable."));
      return;
    }
    services.forEach(function (service) {
      const isBusy = state.restartBusy === service.service;
      const healthy = Boolean(service.healthy) && service.active_state === "active";
      const row = element("div", "service-restart-row");
      if (!service.restart_supported) row.classList.add("unavailable");
      else if (!service.restart_required && healthy) row.classList.add("complete");
      const copy = element("div", "service-restart-copy");
      const heading = element("div", "service-restart-heading");
      heading.append(
        icon(!service.restart_supported ? "triangle-alert" : (service.restart_required ? "rotate-ccw" : "circle-check")),
        element("strong", "", service.label || titleFromKey(service.service)),
        badge(
          !service.restart_supported ? "FAIL" : (service.restart_required ? "WARN" : (healthy ? "OK" : "partial")),
          !service.restart_supported ? "Unavailable" : (service.restart_required ? "Restart required" : (healthy ? "Up to date" : "Starting"))
        )
      );
      copy.append(heading);
      if (service.restart_required) {
        const reasons = (service.restart_reasons || []).join(", ") || "Saved configuration";
        copy.append(element("p", "", "Activate: " + reasons + "."));
        if (service.service === "homesuite-console.service") {
          copy.append(element("small", "", "Restarting the console signs out this browser session."));
        } else {
          copy.append(element("small", "", "Wake-word and PTT listening pause briefly while the runtime starts."));
        }
      } else if (healthy) {
        copy.append(element("p", "", "Running and using the saved configuration."));
      } else {
        copy.append(element("p", "", service.unavailable_reason || "Waiting for the service to become healthy."));
      }
      row.append(copy);
      if (service.restart_required) {
        const restart = element("button", "button warning");
        restart.type = "button";
        restart.disabled = isBusy || Boolean(state.restartBusy) || !service.restart_supported;
        restart.append(
          icon("rotate-ccw"),
          element("span", "", isBusy ? "Restarting" : "Restart now")
        );
        restart.addEventListener("click", function () { restartManagedService(service.service); });
        row.append(restart);
      }
      holder.append(row);
    });
    window.renderLucideIcons(holder);
  }

  async function loadServices() {
    const data = await api("/api/services");
    state.services = data;
    renderServiceRestartAction();
    if ($("#service-restart-dialog").open) renderServiceRestartDialog();
    return data;
  }

  async function openServiceRestartDialog() {
    try {
      await loadServices();
      renderServiceRestartDialog();
      $("#service-restart-dialog").showModal();
    } catch (error) {
      showToast(error.message);
    }
  }

  function closeServiceRestartDialog() {
    if (state.restartBusy) return;
    const dialog = $("#service-restart-dialog");
    if (dialog.open) dialog.close();
  }

  async function waitForConsoleRestart() {
    let sawUnavailable = false;
    const deadline = Date.now() + 25000;
    while (Date.now() < deadline) {
      await new Promise(function (resolve) { window.setTimeout(resolve, 500); });
      try {
        const response = await fetch("/health?restart=" + Date.now(), { cache: "no-store" });
        if (response.ok && sawUnavailable) {
          window.location.reload();
          return;
        }
      } catch (_error) {
        sawUnavailable = true;
      }
    }
    window.location.reload();
  }

  async function restartManagedService(serviceName) {
    if (state.restartBusy) return;
    state.restartBusy = serviceName;
    renderServiceRestartAction();
    renderServiceRestartDialog();
    try {
      const requested = await api("/api/services/restart", {
        method: "POST",
        body: { service: serviceName }
      });
      if (serviceName === "homesuite-console.service") {
        showToast("Console restarting. Sign in again when it returns.");
        await waitForConsoleRestart();
        return;
      }
      const deadline = Date.now() + 30000;
      let ready = false;
      while (Date.now() < deadline) {
        await new Promise(function (resolve) { window.setTimeout(resolve, 650); });
        const status = await loadServices();
        const service = (status.services || []).find(function (row) { return row.service === serviceName; });
        if (service
            && service.healthy
            && !service.restart_required
            && service.invocation_id
            && service.invocation_id !== requested.previous_invocation_id) {
          ready = true;
          break;
        }
      }
      if (!ready) throw new Error("Home Suite did not become healthy within 30 seconds.");
      showToast("Home Suite restarted with the saved settings");
      await refreshAll(false);
    } catch (error) {
      showToast(error.message);
    } finally {
      state.restartBusy = null;
      renderServiceRestartAction();
      if ($("#service-restart-dialog").open) renderServiceRestartDialog();
    }
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
    if (Array.isArray(value)) return value.length ? value.join(", ") : null;
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  function renderOverview() {
    if (!state.snapshot) return;
    const overview = state.snapshot.overview;
    let readinessValue = "Checking";
    let readinessDetail = "Local checks";
    if (state.diagnostics) {
      const warningCount = state.diagnostics.checks.filter(function (check) { return check.status === "WARN"; }).length;
      const failureCount = state.diagnostics.checks.filter(function (check) { return check.status === "FAIL" && check.required; }).length;
      if (!state.diagnostics.ok) {
        readinessValue = "Needs attention";
        readinessDetail = failureCount + " required " + (failureCount === 1 ? "issue" : "issues");
      } else if (warningCount) {
        readinessValue = "Ready with " + warningCount + " warning" + (warningCount === 1 ? "" : "s");
        readinessDetail = "Required checks passed";
      } else {
        readinessValue = "Ready";
        readinessDetail = state.diagnostics.live ? "Live checks passed" : "Local checks passed";
      }
    }
    $("#overview-subtitle").textContent = overview.hostname + (overview.revision ? " · " + overview.revision : "");
    const metricData = [
      ["Node roles", overview.roles.length, overview.roles.map(roleLabel).join(", ")],
      ["Rooms", overview.room_count, "Default: " + (overview.default_room || "not set")],
      ["Integrations", overview.configured_integrations + "/" + overview.integration_count, "Fully configured"],
      ["Readiness", readinessValue, readinessDetail]
    ];
    const metrics = $("#overview-metrics");
    metrics.classList.remove("loading-block");
    metrics.replaceChildren();
    metricData.forEach(function (row) {
      const card = element("article", "metric-card");
      card.append(element("span", "", row[0]), element("strong", "", row[1]), element("small", "", row[2]));
      metrics.append(card);
    });

    const roles = $("#role-readiness");
    roles.classList.remove("loading-block");
    roles.replaceChildren();
    const roleRows = state.diagnostics ? state.diagnostics.roles : overview.roles.map(function (role) { return { role: role, status: "SKIP", required_failures: 0, warnings: 0 }; });
    roleRows.forEach(function (role) {
      const row = element("div", "role-row");
      row.append(element("strong", "", roleLabel(role.role)));
      row.append(element(
        "small",
        "",
        role.required_failures + " required failure" + (role.required_failures === 1 ? "" : "s") +
          " · " + role.warnings + " warning" + (role.warnings === 1 ? "" : "s")
      ));
      row.append(badge(role.status));
      roles.append(row);
    });

    const summary = $("#node-summary");
    summary.classList.remove("loading-block");
    summary.replaceChildren();
    [
      ["Hostname", overview.hostname],
      ["Python", overview.python],
      ["Platform", overview.platform],
      ["Revision", overview.revision || "Unavailable"]
    ].forEach(function (row) {
      const holder = element("div");
      holder.append(element("dt", "", row[0]), element("dd", "", row[1]));
      summary.append(holder);
    });
    $("#sidebar-node").replaceChildren(element("strong", "", overview.hostname), element("span", "", overview.revision || "Revision unavailable"));
  }

  function setupRoleSummary(roles) {
    const voice = roles.filter(function (role) { return role === "ptt" || role === "wakeword"; });
    if (voice.length) return voice.map(roleLabel).join(" + ");
    return roles.includes("api") ? "Text commands + Companion API" : "Text commands";
  }

  function currentView() {
    const active = $(".view.active");
    return active ? active.id.replace(/^view-/, "") : "overview";
  }

  function updateSetupNavigation() {
    const view = currentView();
    const statusKnown = Boolean(state.setup);
    const complete = Boolean(state.setup && state.setup.complete);
    const onJourneyView = state.setupJourneyActive && state.setupJourneyView === view;
    const setupNav = $("#setup-nav");
    setupNav.hidden = !statusKnown || (complete && view !== "setup" && !onJourneyView);
    $("#review-setup").hidden = !statusKnown || !complete;
    $("#setup-journey-bar").hidden = !statusKnown || !onJourneyView || view === "setup";
    $("#setup-journey-label").textContent = state.setupJourneyLabel
      ? "Setup: " + state.setupJourneyLabel
      : "Setup in progress";
  }

  function setSetupJourney(active, label, view) {
    const destination = SETUP_JOURNEY_VIEWS.includes(view) ? view : "";
    state.setupJourneyActive = Boolean(active && destination);
    state.setupJourneyLabel = state.setupJourneyActive ? String(label || "").slice(0, 80) : "";
    state.setupJourneyView = state.setupJourneyActive ? destination : "";
    try {
      if (state.setupJourneyActive) {
        window.sessionStorage.setItem(SETUP_JOURNEY_STORAGE_KEY, JSON.stringify({
          active: true,
          label: state.setupJourneyLabel,
          view: state.setupJourneyView
        }));
      } else {
        window.sessionStorage.removeItem(SETUP_JOURNEY_STORAGE_KEY);
      }
    } catch (_error) { /* private browsing may block storage */ }
    updateSetupNavigation();
  }

  function beginSetupDetour(label, view) {
    setSetupJourney(true, label, view);
  }

  function resumeSetupJourney() {
    if (!state.setupJourneyActive || !SETUP_JOURNEY_VIEWS.includes(state.setupJourneyView)) return false;
    navigate(state.setupJourneyView);
    return true;
  }

  function previewSetupRoles() {
    const roles = ["text", "api"];
    if (state.setupPreviewRole === "ptt" || state.setupPreviewRole === "both") roles.push("ptt");
    if (state.setupPreviewRole === "wakeword" || state.setupPreviewRole === "both") roles.push("wakeword");
    return roles;
  }

  function setupModel() {
    if (state.setupPreview) {
      const roles = previewSetupRoles();
      const voiceEnabled = roles.includes("ptt") || roles.includes("wakeword");
      const steps = [
        {
          id: "home_assistant",
          title: "Connect Home Assistant",
          description: "Add the Home Assistant URL and a long-lived access token, then test the connection.",
          detail: "Required for device state and control.",
          actionLabel: "Connect",
          action: "home_assistant"
        },
        {
          id: "rooms",
          title: "Review your first room",
          description: "Match a room to its Home Assistant area and choose its lighting and media targets.",
          detail: "The installer includes a starting room that can be edited or replaced.",
          actionLabel: "Review room",
          action: "rooms"
        },
        {
          id: "roles",
          title: "Choose how this node listens",
          description: "This example is configured for " + setupRoleSummary(roles).toLowerCase() + ".",
          detail: "PTT and wake-word listening can be enabled together on the same device.",
          actionLabel: "Choose roles",
          action: "roles"
        }
      ];
      if (voiceEnabled) {
        steps.push({
          id: "audio",
          title: "Set up voice and audio",
          description: "Choose the microphone and playback device, then calibrate speech in the real room.",
          detail: "OpenAI is currently used for hosted speech recognition and conversational replies.",
          actionLabel: "Set up audio",
          action: "audio"
        });
      }
      steps.push(
        {
          id: "test",
          title: "Try a command",
          description: "Use the Test Console to verify understanding without allowing device writes.",
          detail: "Live mode remains an explicit choice.",
          actionLabel: "Open test",
          action: "test"
        },
        {
          id: "activate",
          title: "Verify and activate",
          description: "Home Suite Doctor runs required live checks before the runtime is started.",
          detail: "Warnings do not block activation; required failures do.",
          actionLabel: "Activate Home Suite",
          action: "activate"
        }
      );
      return {
        preview: true,
        title: "Previewing a new " + setupRoleSummary(roles).toLowerCase() + " node",
        description: "This is the same guided path a fresh installation sees after creating its console passphrase.",
        steps: steps,
        completeCount: 0
      };
    }

    const setup = state.setup || {};
    const overview = state.snapshot ? state.snapshot.overview : {};
    const roles = overview.roles || ["text"];
    const voiceEnabled = roles.includes("ptt") || roles.includes("wakeword");
    const integrations = integrationRows();
    const homeAssistant = integrations.find(function (row) { return row.id === "home_assistant"; });
    const openAI = integrations.find(function (row) { return row.id === "openai"; });
    const homeAssistantReady = Boolean(homeAssistant && homeAssistant.status === "configured");
    const openAIReady = Boolean(openAI && openAI.status === "configured");
    const rooms = state.roomConfig ? (state.roomConfig.rooms || {}) : {};
    const roomCount = Object.keys(rooms).length || Number(overview.room_count || 0);
    const defaultRoom = state.roomConfig ? state.roomConfig.default_room : overview.default_room;
    const roomReady = Boolean(defaultRoom && roomCount);
    const profile = state.audioConfig ? (state.audioConfig.profile || {}) : {};
    const microphoneReady = Boolean(
      String(profile.device_match || "").trim()
      || (profile.device_index !== null && profile.device_index !== undefined)
    );
    const audioReady = !voiceEnabled || (microphoneReady && openAIReady);
    const diagnosticsReady = Boolean(state.diagnostics && state.diagnostics.ok);
    const requiredFailures = state.diagnostics
      ? state.diagnostics.checks.filter(function (check) { return check.status === "FAIL" && check.required; }).length
      : 0;
    const runtimeHealthy = Boolean(setup.runtime_healthy);
    const activationRequested = Boolean(setup.activation_requested);
    const activationSupported = setup.activation_supported !== false;
    const steps = [
      {
        id: "home_assistant",
        title: "Connect Home Assistant",
        complete: homeAssistantReady,
        description: homeAssistantReady
          ? "Home Assistant credentials are configured on this node."
          : "Add the Home Assistant URL and a long-lived access token, then test the connection.",
        detail: "Required for device state and control.",
        actionLabel: homeAssistantReady ? "Manage" : "Connect",
        action: "home_assistant"
      },
      {
        id: "rooms",
        title: "Review your first room",
        complete: roomReady,
        description: roomReady
          ? (roomCount + " room" + (roomCount === 1 ? " is" : "s are") + " configured; " + (defaultRoom || "none") + " is the default here.")
          : "Match a room to its Home Assistant area and choose its lighting and media targets.",
        detail: "Rooms are shared deployment settings; the default room remains specific to this node.",
        actionLabel: "Review rooms",
        action: "rooms"
      },
      {
        id: "roles",
        title: "Choose how this node listens",
        complete: Boolean(state.snapshot),
        description: "This node currently supports " + setupRoleSummary(roles).toLowerCase() + ".",
        detail: "PTT and wake-word listening can be enabled together on the same device.",
        actionLabel: "Manage roles",
        action: "roles"
      }
    ];
    if (voiceEnabled) {
      steps.push({
        id: "audio",
        title: "Set up voice and audio",
        complete: audioReady,
        description: audioReady
          ? "A microphone profile and the current hosted speech provider are configured."
          : "Choose the microphone and playback device, configure OpenAI, then calibrate speech in the real room.",
        detail: setupRoleSummary(roles) + " is enabled on this node.",
        actionLabel: audioReady ? "Review audio" : "Set up audio",
        action: "audio"
      });
    }
    steps.push({
      id: "test",
      title: "Try a command",
      complete: Boolean(state.setupTested || runtimeHealthy),
      description: state.setupTested || runtimeHealthy
        ? "A browser command has been tested, or this runtime is already active."
        : "Use the Test Console to verify understanding without allowing device writes.",
      detail: "Live mode remains an explicit choice.",
      actionLabel: "Open test",
      action: "test"
    });

    let activationDescription;
    let activationAction;
    let activationLabel;
    let activationReady = false;
    let activationDisabled = false;
    if (runtimeHealthy) {
      activationDescription = "The Home Suite runtime is healthy and ready for commands.";
      activationAction = "overview";
      activationLabel = "Open overview";
    } else if (activationRequested) {
      activationDescription = "Runtime startup has been requested. Open Diagnostics if it does not become healthy shortly.";
      activationAction = "refresh_setup";
      activationLabel = "Check status";
      activationReady = true;
    } else if (!activationSupported) {
      activationDescription = "The bounded runtime activation helper is not installed on this node.";
      activationAction = "diagnostics";
      activationLabel = "Open diagnostics";
      activationDisabled = false;
    } else if (diagnosticsReady) {
      activationDescription = "Required local checks pass. Activation will repeat them with live network checks.";
      activationAction = "activate";
      activationLabel = state.setupActivationBusy ? "Activating" : "Activate Home Suite";
      activationReady = true;
    } else {
      activationDescription = requiredFailures
        ? requiredFailures + " required " + (requiredFailures === 1 ? "issue needs" : "issues need") + " attention before activation."
        : "Run Home Suite Doctor and address any required setup checks before activation.";
      activationAction = "diagnostics";
      activationLabel = "Review checks";
    }
    steps.push({
      id: "activate",
      title: "Verify and activate",
      complete: runtimeHealthy,
      ready: activationReady,
      disabled: activationDisabled,
      description: activationDescription,
      detail: "Warnings do not block activation; required failures do.",
      actionLabel: activationLabel,
      action: activationAction
    });

    let title = "Finish setup, then activate Home Suite";
    let description = "Each step opens the existing management surface for that part of the system.";
    if (runtimeHealthy) {
      title = "Home Suite is running";
      description = "Setup is complete. Revisit any step whenever this node, its microphone, or an integration changes.";
    } else if (activationRequested) {
      title = "Home Suite is starting";
      description = "The activation request is saved; this page will reflect the runtime as soon as it becomes healthy.";
    }
    return {
      preview: false,
      title: title,
      description: description,
      steps: steps,
      completeCount: steps.filter(function (step) { return step.complete; }).length
    };
  }

  function renderSetupStep(step, index, preview) {
    const row = element("div", "setup-step" + (step.complete ? " complete" : ""));
    const number = element("span", "setup-step-number", step.complete ? "" : index + 1);
    if (step.complete) number.append(icon("circle-check"));
    const copy = element("div", "setup-step-copy");
    const title = element("div", "setup-step-title");
    title.append(element("h3", "", step.title));
    if (preview) title.append(badge("SKIP", "Preview"));
    else if (step.complete) title.append(badge("OK", "Complete"));
    else if (step.ready) title.append(badge("WARN", "Ready"));
    else title.append(badge("FAIL", "Needs setup"));
    copy.append(title, element("p", "", step.description));
    if (step.detail) copy.append(element("small", "", step.detail));
    const action = element("button", "button " + (step.action === "activate" && step.ready ? "primary" : "secondary") + " setup-step-action");
    action.type = "button";
    action.disabled = Boolean(preview || step.disabled || (step.action === "activate" && state.setupActivationBusy));
    action.title = preview ? "Actions are disabled in onboarding preview" : step.actionLabel;
    action.append(icon(step.action === "activate" ? "power" : "arrow-right"), element("span", "", step.actionLabel));
    action.addEventListener("click", function () { performSetupAction(step.action, step.title); });
    row.append(number, copy, action);
    return row;
  }

  function renderSetup() {
    updateSetupNavigation();
    if (!state.setup || !state.snapshot) return;
    const model = setupModel();
    const preview = model.preview;
    $("#setup-preview-banner").hidden = !preview;
    $("#preview-onboarding").hidden = preview || !state.setup.runtime_healthy;
    $("#exit-onboarding-preview").hidden = !preview;

    const total = model.steps.length;
    const complete = model.completeCount;
    const progress = total ? Math.round((complete / total) * 100) : 0;
    const summary = $("#setup-summary");
    summary.classList.remove("loading-block");
    summary.replaceChildren();
    const copy = element("div", "setup-summary-copy");
    copy.append(element("strong", "", model.title), element("p", "", model.description));
    const progressHolder = element("div", "setup-progress");
    const progressCopy = element("div");
    progressCopy.append(
      element("strong", "", preview ? "Preview" : complete + " of " + total),
      element("span", "", preview ? "No device changes" : "steps complete")
    );
    const track = element("div", "setup-progress-track");
    track.style.setProperty("--setup-progress", preview ? "0%" : progress + "%");
    track.append(element("span"));
    progressHolder.append(progressCopy, track);
    summary.append(copy, progressHolder);

    $("#setup-progress-label").textContent = preview ? "Preview only" : complete + " of " + total + " complete";
    const steps = $("#setup-steps");
    steps.classList.remove("loading-block");
    steps.replaceChildren();
    model.steps.forEach(function (step, index) { steps.append(renderSetupStep(step, index, preview)); });
    window.renderLucideIcons($("#view-setup"));
  }

  function showSetupResult(status, message) {
    const holder = $("#setup-result");
    holder.hidden = false;
    holder.className = "config-result setup-result " + status;
    holder.replaceChildren(
      icon(status === "fail" ? "triangle-alert" : (status === "warn" ? "info" : "circle-check")),
      element("span", "", message)
    );
    window.renderLucideIcons(holder);
  }

  async function performSetupAction(action, label) {
    if (state.setupPreview) return;
    if (action === "home_assistant") {
      beginSetupDetour(label, "integrations");
      navigate("integrations");
      await openIntegrationEditor("home_assistant");
      return;
    }
    if (action === "rooms") {
      beginSetupDetour(label, "rooms");
      navigate("rooms");
      const roomId = state.roomConfig && state.roomConfig.default_room;
      if (roomId && state.roomDraft && state.roomDraft[roomId]) openRoomEditor(roomId, "edit");
      return;
    }
    if (action === "roles") {
      beginSetupDetour(label, "configuration");
      navigate("configuration");
      await beginConfigurationEdit();
      return;
    }
    if (action === "audio") {
      beginSetupDetour(label, "audio");
      navigate("audio");
      return;
    }
    if (action === "test") {
      beginSetupDetour(label, "console");
      navigate("console");
      window.setTimeout(function () { $("#chat-input").focus(); }, 0);
      return;
    }
    if (action === "diagnostics") {
      beginSetupDetour(label, "diagnostics");
      navigate("diagnostics");
      await loadDiagnostics(true).catch(function (error) { showToast(error.message); });
      return;
    }
    if (action === "refresh_setup") {
      await refreshAll(false);
      return;
    }
    if (action === "overview") {
      navigate("overview");
      return;
    }
    if (action === "activate") await activateHomeSuite();
  }

  function wait(milliseconds) {
    return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); });
  }

  async function activateHomeSuite() {
    if (state.setupActivationBusy) return;
    state.setupActivationBusy = true;
    $("#setup-result").hidden = true;
    renderSetup();
    try {
      const result = await api("/api/setup/activate", { method: "POST", body: {} });
      state.diagnostics = result.report;
      state.setup = Object.assign({}, state.setup, {
        complete: true,
        activation_requested: true
      });
      renderDiagnostics();
      renderSetup();
      showSetupResult("warn", "Activation requested. Waiting for the Home Suite runtime to become healthy...");
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await wait(1000);
        await loadSetup();
        if (state.setup.runtime_healthy) break;
      }
      if (state.setup.runtime_healthy) {
        await Promise.all([
          loadDiagnostics(false).catch(function () {}),
          loadServices().catch(function () {})
        ]);
        showSetupResult("ok", "Home Suite is active and healthy.");
      } else {
        showSetupResult("warn", "Activation was requested, but the runtime is not healthy yet. Open Diagnostics for the current checks.");
      }
    } catch (error) {
      if (error.payload && error.payload.report) {
        state.diagnostics = error.payload.report;
        renderDiagnostics();
      }
      showSetupResult("fail", error.message);
    } finally {
      state.setupActivationBusy = false;
      renderSetup();
    }
  }

  function renderConfiguration() {
    if (!state.snapshot) return;
    if (state.editableConfig) {
      renderConfigSurface();
      return;
    }

    $("#config-navigation").hidden = true;
    $("#config-filter-empty").hidden = true;

    const groups = {};
    state.snapshot.node.forEach(function (row) { (groups[row.group] || (groups[row.group] = [])).push(row); });
    const holder = $("#configuration-groups");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    Object.keys(groups).forEach(function (name) {
      const section = element("section", "config-group");
      section.append(element("h2", "", name));
      const headings = element("div", "config-column-headings");
      headings.append(element("span", "", "Setting"), element("span", "", "Current value"));
      section.append(headings);
      groups[name].forEach(function (row) {
        const line = element("div", "config-row");
        const label = element("div", "config-label");
        label.append(element("strong", "", row.label), element("code", "", row.key));
        const value = element("div", "config-value");
        const formatted = formatValue(row.value);
        if (formatted === null) {
          value.append(element("span", "empty-value", "Not set"));
        } else if (typeof row.value === "object" && row.value !== null) {
          value.append(element("pre", "", formatted));
        } else {
          value.textContent = formatted;
        }
        line.append(label, value);
        section.append(line);
      });
      holder.append(section);
    });
  }

  function configControlId(key) {
    return "config-field-" + String(key || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  }

  function configSectionDomId(sectionId) {
    return "config-section-" + String(sectionId || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  }

  function applyConfigFilter() {
    const holder = $("#configuration-groups");
    const input = $("#config-search");
    if (!holder || !input) return;
    const query = input.value.trim().toLowerCase();
    let total = 0;
    let matches = 0;

    $$(".config-editor-section", holder).forEach(function (section) {
      const rows = $$(".config-field-row", section);
      total += rows.reduce(function (count, row) {
        return count + (row.dataset.configKeys ? row.dataset.configKeys.split(",").length : 1);
      }, 0);
      const sectionMatches = query && String(section.dataset.configSearchText || "").includes(query);
      let sectionMatchCount = 0;
      rows.forEach(function (row) {
        const rowMatches = !query || sectionMatches || String(row.dataset.configSearchText || "").includes(query);
        row.hidden = !rowMatches;
        if (rowMatches) {
          sectionMatchCount += row.dataset.configKeys ? row.dataset.configKeys.split(",").length : 1;
        }
      });
      if (query) {
        if (section.tagName === "DETAILS" && section.dataset.filterWasOpen === undefined) {
          section.dataset.filterWasOpen = section.open ? "true" : "false";
        }
        section.hidden = sectionMatchCount === 0;
        if (!section.hidden && section.tagName === "DETAILS") section.open = true;
        matches += sectionMatchCount;
      } else {
        section.hidden = false;
        if (section.tagName === "DETAILS" && section.dataset.filterWasOpen !== undefined) {
          section.open = section.dataset.filterWasOpen === "true";
          delete section.dataset.filterWasOpen;
        }
      }
    });

    $("#config-filter-count").textContent = query
      ? matches + " of " + total + " settings"
      : total + " settings";
    $("#config-filter-empty").hidden = !query || matches > 0;
  }

  function renderConfigNavigation() {
    const holder = $("#configuration-groups");
    const navigation = $("#config-navigation");
    const select = $("#config-section-jump");
    const previousValue = select.value;
    select.replaceChildren(element("option", "", "Jump to section"));
    select.firstElementChild.value = "";
    $$(".config-editor-section", holder).forEach(function (section) {
      const option = element("option", "", section.dataset.configSectionLabel);
      option.value = section.id;
      select.append(option);
    });
    if ($$("option", select).some(function (option) { return option.value === previousValue; })) {
      select.value = previousValue;
    }
    navigation.hidden = false;
    applyConfigFilter();
  }

  function jumpToConfigSection() {
    const select = $("#config-section-jump");
    if (!select.value) return;
    const target = document.getElementById(select.value);
    if (!target) return;
    const search = $("#config-search");
    if (search.value) {
      search.value = "";
      applyConfigFilter();
    }
    if (target.tagName === "DETAILS") target.open = true;
    window.requestAnimationFrame(function () {
      target.scrollIntoView({ block: "start", behavior: "smooth" });
      select.value = "";
    });
  }

  function editorValue(field) {
    if (field.type === "list_integer" || field.type === "list_string") {
      return Array.isArray(field.value) ? field.value.join("\n") : "";
    }
    if (field.type === "json_object") {
      return JSON.stringify(field.value && typeof field.value === "object" ? field.value : {}, null, 2);
    }
    if (field.type === "boolean") return Boolean(field.value);
    return field.value === null || field.value === undefined ? "" : field.value;
  }

  function normalizedEditorValue(field, value) {
    if (field.type === "boolean") return Boolean(value);
    if (field.type === "number" || field.type === "integer") {
      return String(value).trim() === "" ? "" : Number(value);
    }
    if (field.type === "list_integer") {
      return String(value || "").split(/[\n,]+/).map(function (item) { return item.trim(); }).filter(Boolean).map(Number);
    }
    if (field.type === "list_string") {
      return String(value || "").split(/[\n,]+/).map(function (item) { return item.trim(); }).filter(Boolean);
    }
    if (field.type === "json_object") {
      if (value && typeof value === "object" && !Array.isArray(value)) return value;
      const text = String(value || "").trim();
      if (!text) return {};
      try { return JSON.parse(text); }
      catch (_error) { return text; }
    }
    return String(value === null || value === undefined ? "" : value).trim();
  }

  function configValuesEqual(field, value) {
    if (field.secret && field.value === null) return !String(value || "").trim();
    const current = normalizedEditorValue(field, editorValue(field));
    const candidate = normalizedEditorValue(field, value);
    return stableStringify(current) === stableStringify(candidate);
  }

  function readConfigControl(field, control) {
    return field.type === "boolean" ? control.checked : control.value;
  }

  function configDraftChanges() {
    return Object.keys(state.configDraft).map(function (key) { return state.configDraft[key]; });
  }

  function updateConfigChangeCount() {
    const count = Object.keys(state.configDraft).length;
    $("#config-change-count").textContent = count
      ? count + " pending change" + (count === 1 ? "" : "s")
      : "No pending changes";
    $("#review-config-changes").disabled = count === 0 || Boolean(state.configClientError);
    $$(".config-field-row", $("#configuration-groups")).forEach(function (row) {
      const keys = row.dataset.configKeys
        ? row.dataset.configKeys.split(",")
        : [row.dataset.configKey];
      const draft = keys.map(function (key) { return state.configDraft[key]; }).find(Boolean);
      row.classList.toggle("pending-change", Boolean(draft));
      if (row.dataset.configKeys) return;
      const control = $("[data-config-control]", row);
      const reset = $("[data-config-action='reset']", row);
      const clear = $("[data-config-action='clear']", row);
      const special = draft && draft.action !== "set" ? draft.action : null;
      if (control) control.disabled = Boolean(special);
      if (reset) reset.classList.toggle("active", special === "reset");
      if (clear) clear.classList.toggle("active", special === "clear");
    });
  }

  function setConfigValueDraft(field, control) {
    const rawValue = readConfigControl(field, control);
    if (configValuesEqual(field, rawValue)) {
      delete state.configDraft[field.key];
    } else {
      state.configDraft[field.key] = {
        key: field.key,
        action: "set",
        value: normalizedEditorValue(field, rawValue)
      };
    }
    state.configPreview = null;
    if (["PTT_ENABLED", "PTT_GPIO_PIN", "PHYSICAL_BUTTONS_ENABLED"].includes(field.key) && $("#button-map-editor")) {
      const fieldsByKey = {};
      state.editableConfig.fields.forEach(function (candidate) { fieldsByKey[candidate.key] = candidate; });
      stageButtonMapper(fieldsByKey);
      return;
    }
    updateConfigChangeCount();
  }

  function toggleConfigAction(field, action) {
    const current = state.configDraft[field.key];
    if (current && current.action === action) {
      delete state.configDraft[field.key];
      const row = $$(".config-field-row", $("#configuration-groups")).find(function (candidate) {
        return candidate.dataset.configKey === field.key;
      });
      const control = row ? $("[data-config-control]", row) : null;
      if (control) {
        const baseline = editorValue(field);
        if (field.type === "boolean") {
          control.checked = Boolean(baseline);
          const label = $(".switch-label", row);
          if (label) label.textContent = control.checked ? "Enabled" : "Disabled";
        } else {
          control.value = baseline;
        }
      }
    } else {
      state.configDraft[field.key] = { key: field.key, action: action };
    }
    state.configPreview = null;
    updateConfigChangeCount();
  }

  function buildConfigControl(field) {
    const id = configControlId(field.key);
    let control;
    if (field.type === "boolean") {
      const wrapper = element("label", "switch-control");
      wrapper.htmlFor = id;
      control = element("input");
      control.type = "checkbox";
      control.checked = Boolean(field.value);
      const track = element("span", "switch-track");
      const label = element("span", "switch-label", control.checked ? "Enabled" : "Disabled");
      control.addEventListener("change", function () {
        label.textContent = control.checked ? "Enabled" : "Disabled";
        setConfigValueDraft(field, control);
      });
      wrapper.append(control, track, label);
      control.id = id;
      control.dataset.configControl = "true";
      return { holder: wrapper, control: control };
    }

    if (field.type === "choice") {
      control = element("select");
      field.choices.forEach(function (choice) {
        const option = element("option", "", choice.label);
        option.value = choice.value;
        control.append(option);
      });
      const current = String(editorValue(field));
      if (!$$("option", control).some(function (option) { return option.value === current; })) {
        const option = element("option", "", current || "Not configured");
        option.value = current;
        control.prepend(option);
      }
      control.value = current;
    } else if (field.type === "list_integer" || field.type === "list_string" || field.type === "json_object") {
      control = element("textarea");
      control.rows = field.type === "json_object" ? 8 : 3;
      control.value = editorValue(field);
    } else {
      control = element("input");
      control.type = field.secret ? "password" : (field.type === "url" ? "url" : (field.type === "number" || field.type === "integer" ? "number" : "text"));
      control.value = editorValue(field);
      if (field.type === "number") control.step = "any";
      if (field.type === "integer") control.step = "1";
      if (field.minimum !== null && field.minimum !== undefined) control.min = field.minimum;
      if (field.maximum !== null && field.maximum !== undefined) control.max = field.maximum;
      if (field.secret) control.autocomplete = "new-password";
    }
    control.id = id;
    control.dataset.configControl = "true";
    control.placeholder = field.secret
      ? (field.configured ? "Configured credential" : (field.placeholder || "Paste credential here"))
      : (field.placeholder || "");
    const eventName = field.type === "choice" ? "change" : "input";
    control.addEventListener(eventName, function () { setConfigValueDraft(field, control); });
    if (control.tagName === "SELECT") return { holder: selectControl(control), control: control };
    if (!field.secret) return { holder: control, control: control };

    const wrapper = element("div", "secret-input");
    const reveal = element("button", "icon-button secret-reveal");
    reveal.type = "button";
    reveal.title = "Show credential";
    reveal.setAttribute("aria-label", "Show credential");
    reveal.setAttribute("aria-pressed", "false");
    reveal.append(icon("eye"));
    reveal.addEventListener("click", function () {
      const showing = control.type === "text";
      control.type = showing ? "password" : "text";
      const label = showing ? "Show credential" : "Hide credential";
      reveal.title = label;
      reveal.setAttribute("aria-label", label);
      reveal.setAttribute("aria-pressed", showing ? "false" : "true");
      reveal.replaceChildren(icon(showing ? "eye" : "eye-off"));
      window.renderLucideIcons(reveal);
    });
    wrapper.append(control, reveal);
    return { holder: wrapper, control: control };
  }

  function stableStringify(value) {
    function stable(child) {
      if (Array.isArray(child)) return child.map(stable);
      if (!child || typeof child !== "object") return child;
      return Object.keys(child).sort(function (left, right) {
        return left.localeCompare(right, undefined, { numeric: true });
      }).reduce(function (result, key) {
        result[key] = stable(child[key]);
        return result;
      }, {});
    }
    return JSON.stringify(stable(value));
  }

  function plainObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function buttonActionCommands(action) {
    if (typeof action === "string") return action.trim() ? [action.trim()] : [];
    if (Array.isArray(action)) {
      return action.reduce(function (commands, item) {
        return commands.concat(buttonActionCommands(item));
      }, []);
    }
    if (plainObject(action) === action) {
      if (Object.prototype.hasOwnProperty.call(action, "command")) return buttonActionCommands(action.command);
      if (Object.prototype.hasOwnProperty.call(action, "commands")) return buttonActionCommands(action.commands);
    }
    return [];
  }

  function buttonActionRepeats(action) {
    return Boolean(plainObject(action) === action && (
      action.repeat_while_held || action.repeat || action.repeat_until_release
    ));
  }

  function buttonGestureEntry(actionMap, gesture) {
    for (const alias of gesture.aliases) {
      if (Object.prototype.hasOwnProperty.call(actionMap, alias)) {
        return { key: alias, action: actionMap[alias] };
      }
    }
    return null;
  }

  function buttonActionFromEditor(entry, textarea, repeatControl) {
    const commands = String(textarea.value || "").split("\n").map(function (command) {
      return command.trim();
    }).filter(Boolean);
    const repeat = Boolean(repeatControl && repeatControl.checked);
    if (!commands.length) return null;

    if (entry) {
      const original = {
        commands: buttonActionCommands(entry.action),
        repeat: buttonActionRepeats(entry.action)
      };
      if (stableStringify(original) === stableStringify({ commands: commands, repeat: repeat })) {
        return deepClone(entry.action);
      }
    }

    const advanced = entry && plainObject(entry.action) === entry.action
      ? deepClone(entry.action)
      : {};
    delete advanced.command;
    delete advanced.commands;
    delete advanced.repeat_while_held;
    delete advanced.repeat;
    delete advanced.repeat_until_release;
    if (!repeat) {
      delete advanced.repeat_interval_ms;
      delete advanced.max_repeats;
    }

    const hasAdvancedMetadata = Object.keys(advanced).length > 0;
    if (!repeat && !hasAdvancedMetadata) return commands.length === 1 ? commands[0] : commands;
    advanced[commands.length === 1 ? "command" : "commands"] = commands.length === 1 ? commands[0] : commands;
    if (repeat) advanced.repeat_while_held = true;
    return advanced;
  }

  function workingConfigValue(field) {
    const draft = state.configDraft[field.key];
    return draft && draft.action === "set" ? draft.value : field.value;
  }

  function buttonModels(fieldsByKey) {
    const pinsField = fieldsByKey.PHYSICAL_BUTTON_PINS;
    const actionsField = fieldsByKey.PHYSICAL_BUTTON_ACTIONS;
    const pins = plainObject(pinsField ? workingConfigValue(pinsField) : {});
    const actions = plainObject(actionsField ? workingConfigValue(actionsField) : {});
    const ids = Array.from(new Set(Object.keys(pins).concat(Object.keys(actions))));
    ids.sort(function (left, right) {
      const leftNumber = Number(left);
      const rightNumber = Number(right);
      if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
      return left.localeCompare(right);
    });
    return ids.map(function (id) {
      return {
        id: id,
        pin: Object.prototype.hasOwnProperty.call(pins, id) ? pins[id] : "",
        actionMap: deepClone(plainObject(actions[id])),
        hadActionEntry: Object.prototype.hasOwnProperty.call(actions, id)
      };
    });
  }

  function setStructuredConfigDraft(field, value) {
    if (configValuesEqual(field, value)) {
      delete state.configDraft[field.key];
    } else {
      state.configDraft[field.key] = { key: field.key, action: "set", value: value };
    }
    state.configPreview = null;
  }

  function setButtonMapperError(message) {
    state.configClientError = message || null;
    const error = $("#button-map-error");
    if (error) {
      error.textContent = message || "";
      error.hidden = !message;
    }
    updateConfigChangeCount();
  }

  function stageButtonMapper(fieldsByKey) {
    const editor = $("#button-map-editor");
    if (!editor) return;
    const rows = $$(".button-map-edit-row", editor);
    const pins = {};
    const actions = {};
    const usedIds = new Set();
    const usedPins = new Set();
    let errorMessage = "";

    rows.forEach(function (row) {
      const idControl = $("[data-button-id]", row);
      const pinControl = $("[data-button-pin]", row);
      idControl.setCustomValidity("");
      pinControl.setCustomValidity("");
      const id = Number(idControl.value);
      const pin = Number(pinControl.value);

      if (!Number.isInteger(id) || id < 1) {
        const message = "Each button needs a whole-number ID of 1 or greater.";
        idControl.setCustomValidity(message);
        errorMessage = errorMessage || message;
        return;
      }
      if (usedIds.has(id)) {
        const message = "Button " + id + " appears more than once.";
        idControl.setCustomValidity(message);
        errorMessage = errorMessage || message;
        return;
      }
      usedIds.add(id);

      if (String(pinControl.value).trim() === "" || !Number.isInteger(pin) || pin < 0 || pin > 27) {
        const message = "Button " + id + " needs a BCM GPIO pin from 0 to 27.";
        pinControl.setCustomValidity(message);
        errorMessage = errorMessage || message;
        return;
      }
      if (usedPins.has(pin)) {
        const message = "BCM GPIO " + pin + " is assigned more than once.";
        pinControl.setCustomValidity(message);
        errorMessage = errorMessage || message;
        return;
      }
      usedPins.add(pin);

      pins[String(id)] = pin;
      const model = row.buttonModel || { actionMap: {}, hadActionEntry: false };
      const actionMap = deepClone(model.actionMap);
      BUTTON_GESTURES.forEach(function (gesture) {
        gesture.aliases.forEach(function (alias) { delete actionMap[alias]; });
        const textarea = $("[data-button-gesture='" + gesture.key + "']", row);
        const repeat = gesture.key === "long_press" ? $("[data-button-repeat]", row) : null;
        const action = buttonActionFromEditor(buttonGestureEntry(model.actionMap, gesture), textarea, repeat);
        if (action !== null) actionMap[gesture.key] = action;
      });
      if (Object.keys(actionMap).length || model.hadActionEntry) actions[String(id)] = actionMap;
    });

    const buttonsEnabledField = fieldsByKey.PHYSICAL_BUTTONS_ENABLED;
    const pttEnabledField = fieldsByKey.PTT_ENABLED;
    const pttPinField = fieldsByKey.PTT_GPIO_PIN;
    const buttonsEnabled = buttonsEnabledField && Boolean(workingConfigValue(buttonsEnabledField));
    if (!errorMessage && buttonsEnabled && !rows.length) {
      errorMessage = "Add at least one button, or disable command buttons.";
    }
    if (!errorMessage && buttonsEnabled && pttEnabledField && Boolean(workingConfigValue(pttEnabledField))) {
      const pttPin = Number(pttPinField ? workingConfigValue(pttPinField) : "");
      if (Number.isInteger(pttPin) && usedPins.has(pttPin)) {
        const conflicting = rows.find(function (row) {
          return Number($("[data-button-pin]", row).value) === pttPin;
        });
        const message = "BCM GPIO " + pttPin + " is already assigned to PTT.";
        if (conflicting) $("[data-button-pin]", conflicting).setCustomValidity(message);
        errorMessage = message;
      }
    }

    if (errorMessage) {
      setButtonMapperError(errorMessage);
      return;
    }
    setStructuredConfigDraft(fieldsByKey.PHYSICAL_BUTTON_PINS, pins);
    setStructuredConfigDraft(fieldsByKey.PHYSICAL_BUTTON_ACTIONS, actions);
    setButtonMapperError("");
  }

  function buildButtonGestureControl(model, gesture, fieldsByKey) {
    const entry = buttonGestureEntry(model.actionMap, gesture);
    const cell = element("div", "button-map-cell gesture-cell");
    cell.append(element("span", "button-map-mobile-label", gesture.label));
    const textarea = element("textarea");
    textarea.rows = 2;
    textarea.dataset.buttonGesture = gesture.key;
    textarea.value = entry ? buttonActionCommands(entry.action).join("\n") : "";
    textarea.placeholder = gesture.key === "press" ? "e.g. toggle play pause" : "Optional command";
    textarea.setAttribute("aria-label", gesture.label + " commands");
    cell.append(textarea);
    let repeat = null;
    if (gesture.key === "long_press") {
      const repeatLabel = element("label", "button-repeat-control");
      repeat = element("input");
      repeat.type = "checkbox";
      repeat.dataset.buttonRepeat = "true";
      repeat.checked = entry ? buttonActionRepeats(entry.action) : false;
      repeat.disabled = !textarea.value.trim();
      repeat.addEventListener("change", function () { stageButtonMapper(fieldsByKey); });
      repeatLabel.append(repeat, element("span", "", "Repeat while held"));
      cell.append(repeatLabel);
    }
    textarea.addEventListener("input", function () {
      if (repeat) {
        repeat.disabled = !textarea.value.trim();
        if (repeat.disabled) repeat.checked = false;
      }
      stageButtonMapper(fieldsByKey);
    });
    return cell;
  }

  function buildButtonEditRow(model, fieldsByKey) {
    const row = element("div", "button-map-edit-row");
    row.buttonModel = model;

    const idCell = element("label", "button-map-cell number-cell");
    idCell.append(element("span", "button-map-mobile-label", "Button ID"));
    const id = element("input");
    id.type = "number";
    id.min = "1";
    id.step = "1";
    id.value = model.id;
    id.dataset.buttonId = "true";
    id.setAttribute("aria-label", "Button ID");
    id.addEventListener("input", function () { stageButtonMapper(fieldsByKey); });
    idCell.append(id);

    const pinCell = element("label", "button-map-cell number-cell");
    pinCell.append(element("span", "button-map-mobile-label", "BCM pin"));
    const pin = element("input");
    pin.type = "number";
    pin.min = "0";
    pin.max = "27";
    pin.step = "1";
    pin.value = model.pin;
    pin.placeholder = "e.g. 17";
    pin.dataset.buttonPin = "true";
    pin.setAttribute("aria-label", "BCM GPIO pin");
    pin.addEventListener("input", function () { stageButtonMapper(fieldsByKey); });
    pinCell.append(pin);

    row.append(idCell, pinCell);
    BUTTON_GESTURES.forEach(function (gesture) {
      row.append(buildButtonGestureControl(model, gesture, fieldsByKey));
    });
    const remove = element("button", "icon-button button-map-remove");
    remove.type = "button";
    remove.title = "Remove button " + model.id;
    remove.setAttribute("aria-label", remove.title);
    remove.append(icon("trash-2"));
    remove.addEventListener("click", function () {
      row.remove();
      stageButtonMapper(fieldsByKey);
    });
    row.append(remove);
    return row;
  }

  function nextButtonId(editor) {
    const used = new Set($$("[data-button-id]", editor).map(function (control) {
      return Number(control.value);
    }).filter(Number.isInteger));
    let candidate = 1;
    while (used.has(candidate)) candidate += 1;
    return candidate;
  }

  function renderButtonMapper(fieldsByKey) {
    const row = element("div", "config-field-row button-mapper-row");
    row.dataset.configKey = "PHYSICAL_BUTTON_PINS";
    row.dataset.configKeys = BUTTON_MAP_KEYS.join(",");
    row.dataset.configSearchText = "button mappings physical button pins actions gpio auxiliary commands gestures";
    const copy = element("div", "config-field-copy");
    const title = element("div", "config-field-title");
    title.append(element("span", "config-custom-label", "Button mappings"));
    const metadata = element("div", "config-field-metadata");
    metadata.append(element("code", "", "PHYSICAL_BUTTON_PINS"), element("code", "", "PHYSICAL_BUTTON_ACTIONS"));
    const description = element(
      "p",
      "config-field-description",
      "Assign each auxiliary button a BCM GPIO pin and ordinary Home Suite commands for its supported gestures."
    );
    copy.append(title, metadata, description);
    const help = element("details", "config-field-help");
    help.append(element("summary", "", "How to configure"));
    help.append(element("p", "", "Button IDs link wiring, normal actions, and any applet button-mode layouts, so keep existing IDs stable once another mode uses them. Commands use the same language accepted by voice and the Test Console; enter one command per line to run several in sequence."));
    const docs = documentationLink("docs/GPIO_BUTTONS.md");
    if (docs) help.append(docs);
    copy.append(help);

    const valueHolder = element("div", "config-field-value button-map-value");
    const display = element("div", "config-field-display button-map-display");
    display.dataset.configDisplay = "true";
    const models = buttonModels(fieldsByKey);
    if (!models.length) {
      display.append(element("span", "empty-value", "No command buttons configured"));
    } else {
      const table = element("div", "button-map-summary");
      table.append(
        element("span", "button-map-summary-heading", "Button"),
        element("span", "button-map-summary-heading", "BCM pin"),
        element("span", "button-map-summary-heading", "Commands")
      );
      models.forEach(function (model) {
        const commandParts = [];
        BUTTON_GESTURES.forEach(function (gesture) {
          const entry = buttonGestureEntry(model.actionMap, gesture);
          if (!entry) return;
          const commands = buttonActionCommands(entry.action);
          if (!commands.length) return;
          const repeat = gesture.key === "long_press" && buttonActionRepeats(entry.action) ? " (repeats)" : "";
          commandParts.push(gesture.label + ": " + commands.join("; ") + repeat);
        });
        table.append(
          element("strong", "", model.id),
          element("code", "", model.pin === "" ? "Not set" : model.pin),
          element("span", commandParts.length ? "" : "empty-value", commandParts.join(" · ") || "No actions assigned")
        );
      });
      display.append(table);
    }
    valueHolder.append(display);

    const editor = element("div", "config-field-control button-map-editor");
    editor.id = "button-map-editor";
    editor.dataset.configEditor = "true";
    editor.hidden = !state.configEditing;
    const headings = element("div", "button-map-headings");
    ["Button ID", "BCM pin", "Single press", "Double press", "Long press", ""].forEach(function (heading) {
      headings.append(element("span", "", heading));
    });
    const editRows = element("div", "button-map-edit-rows");
    models.forEach(function (model) { editRows.append(buildButtonEditRow(model, fieldsByKey)); });
    const error = element("p", "button-map-error");
    error.id = "button-map-error";
    error.hidden = true;
    const add = element("button", "field-action button-map-add");
    add.type = "button";
    add.append(icon("plus"), element("span", "", "Add button"));
    add.addEventListener("click", function () {
      const model = { id: String(nextButtonId(editor)), pin: "", actionMap: {}, hadActionEntry: false };
      editRows.append(buildButtonEditRow(model, fieldsByKey));
      const control = $("[data-button-pin]", editRows.lastElementChild);
      setButtonMapperError("Choose a BCM GPIO pin for the new button.");
      control.setCustomValidity("Choose a BCM GPIO pin for the new button.");
      control.focus();
      window.renderLucideIcons(editRows.lastElementChild);
    });
    editor.append(headings, editRows, error, add);
    valueHolder.append(editor);
    row.append(copy, valueHolder);
    return row;
  }

  function captureConfigDisclosures() {
    const holder = $("#configuration-groups");
    return {
      sections: new Set($$("details.config-editor-section", holder).filter(function (section) {
        return section.dataset.filterWasOpen !== undefined
          ? section.dataset.filterWasOpen === "true"
          : section.open;
      }).map(function (section) {
        return section.dataset.configSection;
      })),
      help: new Set($$("details.config-field-help[open]", holder).map(function (details) {
        const row = details.closest(".config-field-row");
        return row ? row.dataset.configKey : "";
      })),
      inventory: new Set($$("details.config-inventory-section[open]", $("#config-inventory")).map(function (section) {
        return section.dataset.inventorySection;
      }))
    };
  }

  function documentationLink(path, label) {
    const filename = String(path || "").split("/").pop();
    if (!filename) return null;
    const link = element("a", "documentation-link", label || "Open documentation");
    link.href = "/docs/" + encodeURIComponent(filename);
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }

  function renderConfigInventory(disclosures) {
    if (!state.editableConfig || !state.editableConfig.inventory) return;
    const inventory = state.editableConfig.inventory;
    const summary = inventory.summary;
    const coverage = $("#config-coverage");
    coverage.classList.remove("loading-block");
    coverage.replaceChildren();

    const coverageCopy = element("div", "config-coverage-copy");
    coverageCopy.append(
      element("strong", "", "Configuration status"),
      element(
        "p",
        "",
        (summary.attention_count
          ? summary.attention_count + " setting" + (summary.attention_count === 1 ? " needs" : "s need") + " attention. "
          : "No configuration issues found. ") +
          summary.guided_available + " settings can be managed here. " +
          summary.file_managed_available + " advanced settings are available in configuration files."
      )
    );
    const coverageStats = element("div", "config-coverage-stats");
    [
      ["Settings in use", summary.active_assignments],
      ["Advanced in use", summary.advanced_active],
      ["Needs attention", summary.attention_count]
    ].forEach(function (item) {
      const stat = element("div");
      stat.append(element("span", "", item[0]), element("strong", "", item[1]));
      coverageStats.append(stat);
    });
    coverage.append(coverageCopy, coverageStats);

    const holder = $("#config-inventory");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    const visibleRows = inventory.rows.filter(function (row) {
      return row.effective &&
        row.classification !== "guided" &&
        (row.scope !== "credentials" || row.configured);
    });
    if (!visibleRows.length) return;

    const preserved = disclosures && disclosures.inventory
      ? disclosures.inventory
      : new Set();
    const groups = [
      {
        id: "attention",
        label: "Settings needing attention",
        description: "Compatibility aliases and assignments that Home Suite does not recognize.",
        rows: visibleRows.filter(function (row) {
          return row.classification === "deprecated" || row.classification === "unknown";
        }),
        open: true
      },
      {
        id: "advanced",
        label: "Advanced file-managed settings",
        description: "Supported settings that are visible here but do not yet have a guided editor.",
        rows: visibleRows.filter(function (row) { return row.classification === "advanced"; }),
        open: false
      }
    ];

    groups.forEach(function (group) {
      if (!group.rows.length) return;
      const section = element("details", "config-inventory-section");
      section.dataset.inventorySection = group.id;
      section.open = group.open || preserved.has(group.id);
      const summaryRow = element("summary");
      const summaryCopy = element("div");
      summaryCopy.append(
        element("strong", "", group.label),
        element("span", "", group.description)
      );
      summaryRow.append(summaryCopy, badge("SKIP", String(group.rows.length)));
      section.append(summaryRow);

      const table = element("div", "config-inventory-table");
      const headings = element("div", "config-inventory-headings");
      headings.append(
        element("span", "", "Setting"),
        element("span", "", "Scope"),
        element("span", "", "Current"),
        element("span", "", "Status")
      );
      table.append(headings);
      group.rows.forEach(function (row) {
        const line = element("div", "config-inventory-row");
        const setting = element("div", "config-inventory-setting");
        setting.append(
          element("strong", "", row.label),
          element("code", "", row.key),
          element("small", "", row.category)
        );
        const scope = element("div", "", row.scope_label);
        const current = element("div", "", row.value_summary);
        const stateHolder = element("div", "config-inventory-state");
        stateHolder.append(badge(row.classification));
        if (row.replacement) {
          stateHolder.append(element("small", "", "Use " + row.replacement));
        }
        if (row.classification === "deprecated" || row.classification === "unknown") {
          stateHolder.append(element("small", "config-inventory-guidance", row.guidance));
        }
        const docs = documentationLink(row.docs_path, "Documentation");
        if (docs) stateHolder.append(docs);
        line.append(setting, scope, current, stateHolder);
        table.append(line);
      });
      section.append(table);
      holder.append(section);
    });
    window.renderLucideIcons(holder);
  }

  function renderConfigDisplayValue(field) {
    const display = element("div", "config-field-display");
    display.dataset.configDisplay = "true";
    if (field.secret) {
      display.append(badge(field.configured ? "configured" : "not_configured"));
      return display;
    }
    let displayValue = field.value;
    if (field.type === "choice") {
      const selected = field.choices.find(function (choice) {
        return String(choice.value) === String(field.value === null || field.value === undefined ? "" : field.value);
      });
      if (selected) displayValue = selected.label;
    }
    const formatted = formatValue(displayValue);
    if (formatted === null) {
      display.append(element("span", "empty-value", "Not set"));
    } else if (typeof displayValue === "object" && displayValue !== null) {
      display.append(element("pre", "", formatted));
    } else {
      display.textContent = formatted;
    }
    return display;
  }

  function renderConfigSurface(disclosures) {
    if (!state.editableConfig) return;
    const holder = $("#configuration-groups");
    const preserved = disclosures || captureConfigDisclosures();
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    const fieldsByKey = {};
    state.editableConfig.fields.forEach(function (field) { fieldsByKey[field.key] = field; });

    state.editableConfig.sections.forEach(function (sectionData) {
      const section = element(sectionData.optional ? "details" : "section", "config-editor-section");
      section.dataset.configSection = sectionData.id;
      section.dataset.configSectionLabel = sectionData.label;
      section.dataset.configSearchText = String(sectionData.label || "").toLowerCase();
      section.id = configSectionDomId(sectionData.id);
      if (sectionData.optional) {
        section.open = preserved.sections.has(sectionData.id);
        section.append(element("summary", "", sectionData.label));
      } else {
        section.append(element("h2", "", sectionData.label));
      }
      const rows = element("div", "config-editor-fields");
      const headings = element("div", "config-column-headings");
      headings.append(
        element("span", "", "Setting"),
        element("span", "", state.configEditing ? "Edit value" : "Current value")
      );
      rows.append(headings);
      sectionData.field_keys.forEach(function (key) {
        if (key === "PHYSICAL_BUTTON_PINS") {
          rows.append(renderButtonMapper(fieldsByKey));
          return;
        }
        if (key === "PHYSICAL_BUTTON_ACTIONS") return;
        const field = fieldsByKey[key];
        if (!field) return;
        const row = element("div", "config-field-row");
        row.dataset.configKey = field.key;
        row.dataset.configSearchText = [
          sectionData.label,
          field.label,
          field.key,
          field.description,
          field.help_text
        ].filter(Boolean).join(" ").toLowerCase();
        row.classList.add("config-field-type-" + field.type.replace(/[^a-z0-9]+/g, "-"));

        const copy = element("div", "config-field-copy");
        const title = element("div", "config-field-title");
        const label = element("label", "", field.label);
        label.htmlFor = configControlId(field.key);
        title.append(label);
        const metadata = element("div", "config-field-metadata");
        metadata.append(element("code", "", field.key));
        if (field.required) metadata.append(element("span", "required-mark", "Required"));
        const description = element("p", "config-field-description", field.description);
        description.id = configControlId(field.key) + "-description";
        copy.append(title, metadata, description);
        if (field.help_text || field.docs_path) {
          const help = element("details", "config-field-help");
          help.open = preserved.help.has(field.key);
          help.append(element("summary", "", "How to configure"));
          if (field.help_text) help.append(element("p", "", field.help_text));
          if (field.docs_path) {
            const docs = documentationLink(field.docs_path);
            if (docs) help.append(docs);
          }
          copy.append(help);
        }

        const valueHolder = element("div", "config-field-value");
        const display = renderConfigDisplayValue(field);
        display.hidden = state.configEditing;
        valueHolder.append(display);

        const controlHolder = element("div", "config-field-control");
        controlHolder.dataset.configEditor = "true";
        controlHolder.hidden = !state.configEditing;
        const built = buildConfigControl(field);
        built.control.setAttribute("aria-describedby", description.id);
        controlHolder.append(built.holder);

        const actions = element("div", "field-actions");
        if (field.can_reset) {
          const reset = element("button", "field-action");
          reset.type = "button";
          reset.dataset.configAction = "reset";
          reset.append(icon("rotate-ccw"), element("span", "", "Use inherited value"));
          reset.addEventListener("click", function () { toggleConfigAction(field, "reset"); });
          actions.append(reset);
        }
        if (field.can_clear) {
          const clear = element("button", "field-action");
          clear.type = "button";
          clear.dataset.configAction = "clear";
          clear.append(icon("trash-2"), element("span", "", "Clear configured value"));
          clear.addEventListener("click", function () { toggleConfigAction(field, "clear"); });
          actions.append(clear);
        }
        if (actions.childElementCount) {
          row.classList.add("has-field-actions");
          controlHolder.append(actions);
        }
        valueHolder.append(controlHolder);

        row.append(copy, valueHolder);
        rows.append(row);
      });
      section.append(rows);
      holder.append(section);
    });
    renderConfigNavigation();
    renderConfigInventory(preserved);
    window.renderLucideIcons(holder);
    updateConfigChangeCount();
  }

  async function loadEditableConfig(includeSecrets) {
    state.editableConfig = await api(
      includeSecrets ? "/api/config/edit-state" : "/api/config",
      includeSecrets ? { method: "POST", body: {} } : undefined
    );
    return state.editableConfig;
  }

  function redactEditableSecrets() {
    if (!state.editableConfig) return;
    state.editableConfig.fields.forEach(function (field) {
      if (field.secret) field.value = null;
    });
  }

  function showConfigurationEditMode(editing) {
    state.configEditing = editing;
    $("#edit-configuration").hidden = editing;
    $("#cancel-configuration-edit").hidden = !editing;
    $("#config-editor-actions").hidden = !editing;
    $("#configuration-editor").classList.toggle("editing", editing);
    $$('[data-config-display="true"]', $("#configuration-groups")).forEach(function (display) {
      display.hidden = editing;
    });
    $$('[data-config-editor="true"]', $("#configuration-groups")).forEach(function (editor) {
      editor.hidden = !editing;
    });
    updateConfigChangeCount();
  }

  async function beginConfigurationEdit() {
    const button = $("#edit-configuration");
    button.disabled = true;
    try {
      const disclosures = captureConfigDisclosures();
      await loadEditableConfig(true);
      state.configDraft = {};
      state.configPreview = null;
      state.configClientError = null;
      state.configEditing = true;
      renderConfigSurface(disclosures);
      showConfigurationEditMode(true);
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function cancelConfigurationEdit() {
    if (Object.keys(state.configDraft).length && !window.confirm("Discard the pending configuration changes?")) return;
    const disclosures = captureConfigDisclosures();
    state.configDraft = {};
    state.configPreview = null;
    state.configClientError = null;
    state.configEditing = false;
    redactEditableSecrets();
    renderConfigSurface(disclosures);
    showConfigurationEditMode(false);
  }

  function renderConfigReview(preview) {
    const holder = $("#config-review-list");
    holder.replaceChildren();
    preview.changes.forEach(function (change) {
      const row = element("div", "config-review-row");
      const label = element("div");
      label.append(element("strong", "", change.label), element("code", "", change.key));
      const values = element("div", "config-review-values");
      values.append(
        element("span", "", change.before),
        element("span", "review-arrow", "to"),
        element("span", "", change.after)
      );
      row.append(label, values);
      holder.append(row);
    });
    const restart = $("#config-restart-notice");
    restart.textContent = preview.restart_services.length
      ? "The settings will be saved without interrupting Home Suite. Use Restart required when you are ready to activate them."
      : "These changes do not require a service restart.";
  }

  async function reviewConfigurationChanges() {
    if (state.configClientError) {
      showToast(state.configClientError);
      return;
    }
    const changes = configDraftChanges();
    if (!changes.length) return;
    const button = $("#review-config-changes");
    button.disabled = true;
    try {
      const preview = await api("/api/config/preview", {
        method: "POST",
        body: { changes: changes }
      });
      if (!preview.change_count) {
        state.configDraft = {};
        updateConfigChangeCount();
        showToast("Those values already match the saved configuration");
        return;
      }
      state.configPreview = preview;
      renderConfigReview(preview);
      $("#config-review-dialog").showModal();
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = Object.keys(state.configDraft).length === 0 || Boolean(state.configClientError);
    }
  }

  function showConfigApplyResult(result) {
    const holder = $("#config-result");
    holder.replaceChildren(icon("circle-check"));
    const services = result.restart_services.length ? " Use Restart required above when you are ready to activate them." : "";
    const backup = result.backup_dir ? " Backup: " + result.backup_dir + "." : "";
    holder.append(element("span", "", "Saved " + result.change_count + " setting" + (result.change_count === 1 ? "" : "s") + "." + services + backup));
    holder.hidden = false;
  }

  async function applyConfigurationChanges() {
    if (!state.configPreview) return;
    const button = $("#apply-config-changes");
    button.disabled = true;
    const disclosures = captureConfigDisclosures();
    try {
      const result = await api("/api/config/apply", {
        method: "POST",
        body: {
          changes: configDraftChanges(),
          revisions: state.configPreview.revisions
        }
      });
      $("#config-review-dialog").close();
      state.configDraft = {};
      state.configPreview = null;
      state.configClientError = null;
      await loadEditableConfig(true);
      await loadServices().catch(function () {});
      renderConfigSurface(disclosures);
      showConfigurationEditMode(true);
      showConfigApplyResult(result);
      showToast("Configuration saved");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function titleFromKey(key) {
    return String(key || "").replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
  }

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function roomDraftChanged() {
    if (!state.roomConfig || !state.roomDraft) return false;
    return JSON.stringify(state.roomConfig.rooms) !== JSON.stringify(state.roomDraft);
  }

  function roomPendingCount() {
    if (!state.roomConfig || !state.roomDraft) return 0;
    const baseline = state.roomConfig.rooms || {};
    const draft = state.roomDraft || {};
    const ids = new Set(Object.keys(baseline).concat(Object.keys(draft)));
    let count = 0;
    ids.forEach(function (id) {
      if (JSON.stringify(baseline[id]) !== JSON.stringify(draft[id])) count += 1;
    });
    return count;
  }

  function roomTargetValue(value, room) {
    if (value === null || value === undefined || value === "") return "Not configured";
    if (typeof value !== "object") return String(value);
    if (value.type === "area") return "HA area: " + (value.area_id || room.ha_area_id || "not set");
    if (value.type === "entity") return value.entity_id || "Not configured";
    if (value.type === "entities") return (value.entity_ids || []).join(", ") || "Not configured";
    return JSON.stringify(value);
  }

  function roomActionButton(iconName, label, handler, disabled) {
    const button = element("button", "icon-button room-action");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.disabled = Boolean(disabled);
    button.append(icon(iconName));
    button.addEventListener("click", handler);
    return button;
  }

  function updateRoomActionBar() {
    const count = roomPendingCount();
    const holder = $("#room-editor-actions");
    holder.hidden = count === 0;
    $("#room-change-count").textContent = count
      ? count + " room" + (count === 1 ? "" : "s") + " changed"
      : "No pending room changes";
  }

  function renderRooms() {
    if (!state.roomConfig || !state.roomDraft) {
      if (!state.snapshot) return;
      $("#add-room").disabled = true;
      $("#rooms-subtitle").textContent = state.snapshot.rooms.length + " configured room" + (state.snapshot.rooms.length === 1 ? "" : "s") + (state.roomLoadError ? " · read only" : "");
      const fallback = $("#rooms-list");
      fallback.classList.remove("loading-block");
      fallback.replaceChildren();
      state.snapshot.rooms.forEach(function (room) {
        const card = element("article", "room-card");
        const header = element("div", "room-card-header");
        const title = element("div");
        const details = [room.id];
        if (room.ha_area_id) details.push("HA area: " + room.ha_area_id);
        if (room.aliases.length) details.push("Aliases: " + room.aliases.join(", "));
        title.append(element("h2", "", room.label), element("p", "", details.join(" · ")));
        header.append(title);
        if (room.is_default) header.append(badge("configured", "Default on this device"));
        const body = element("div", "room-body");
        const counts = element("div", "room-counts");
        const countList = element("dl", "count-list");
        Object.keys(room.counts).forEach(function (key) {
          const line = element("div");
          line.append(element("dt", "", titleFromKey(key)), element("dd", "", room.counts[key]));
          countList.append(line);
        });
        counts.append(countList);
        const capabilities = element("div", "capability-list");
        room.capabilities.forEach(function (capability) {
          const line = element("div", "capability-row");
          line.append(element("strong", "", titleFromKey(capability.key)), element("span", capability.configured ? "" : "empty-value", capability.value));
          capabilities.append(line);
        });
        body.append(counts, capabilities);
        card.append(header, body);
        fallback.append(card);
      });
      $("#room-editor-actions").hidden = true;
      return;
    }
    $("#add-room").disabled = false;
    const rooms = state.roomDraft;
    const roomIds = Object.keys(rooms);
    $("#rooms-subtitle").textContent = roomIds.length + " configured room" + (roomIds.length === 1 ? "" : "s") + " · shared topology";
    const holder = $("#rooms-list");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    roomIds.forEach(function (roomId) {
      const room = rooms[roomId] || {};
      const defaults = room.defaults || {};
      const baseline = state.roomConfig.rooms[roomId];
      const pending = JSON.stringify(baseline) !== JSON.stringify(room);
      const card = element("article", "room-card");
      if (pending) card.classList.add("pending-change");
      const header = element("div", "room-card-header");
      const title = element("div");
      const details = [roomId];
      if (room.ha_area_id) details.push("HA area: " + room.ha_area_id);
      if ((room.aliases || []).length) details.push("Aliases: " + room.aliases.join(", "));
      title.append(element("h2", "", room.label || titleFromKey(roomId)), element("p", "", details.join(" · ")));
      header.append(title);
      const headerMeta = element("div", "room-card-meta");
      if (roomId === state.roomConfig.default_room) headerMeta.append(badge("configured", "Default on this device"));
      if (pending) headerMeta.append(badge("warn", baseline ? "Pending changes" : "New room"));
      const actions = element("div", "room-card-actions");
      actions.append(
        roomActionButton("pencil", "Edit " + (room.label || roomId), function () { openRoomEditor(roomId, "edit"); }),
        roomActionButton("copy", "Duplicate " + (room.label || roomId), function () { openRoomEditor(roomId, "duplicate"); }),
        roomActionButton(
          "trash-2",
          roomId === state.roomConfig.default_room ? "The default room cannot be removed" : "Remove " + (room.label || roomId),
          function () { removeRoomDraft(roomId); },
          roomId === state.roomConfig.default_room
        )
      );
      headerMeta.append(actions);
      header.append(headerMeta);

      const body = element("div", "room-body");
      const counts = element("div", "room-counts");
      const countList = element("dl", "count-list");
      const roomCounts = {
        media_players: (room.media_players || room.audio_outputs || []).length,
        devices: (room.devices || []).length,
        shortcuts: (room.scenes || []).length
      };
      Object.keys(roomCounts).forEach(function (key) {
        const line = element("div");
        line.append(element("dt", "", titleFromKey(key)), element("dd", "", roomCounts[key]));
        countList.append(line);
      });
      counts.append(countList);
      const capabilities = element("div", "capability-list");
      [
        ["Brightness", defaults.brightness_target],
        ["Color light", defaults.color_light],
        ["Volume", defaults.volume_target],
        ["Primary audio", defaults.audio_output],
        ["Announcements", defaults.announcements],
        ["Television", defaults.tv]
      ].forEach(function (capability) {
        const line = element("div", "capability-row");
        const display = roomTargetValue(capability[1], room);
        line.append(element("strong", "", capability[0]), element("span", display === "Not configured" ? "empty-value" : "", display));
        capabilities.append(line);
      });
      body.append(counts, capabilities);
      card.append(header, body);
      holder.append(card);
    });
    if (!roomIds.length) holder.append(element("div", "panel", "No rooms configured."));
    updateRoomActionBar();
    window.renderLucideIcons(holder);
  }

  function roomFormControl(id, labelText, value, options) {
    const opts = options || {};
    const field = element("div", "room-form-field" + (opts.full ? " full" : ""));
    const label = element("label", "", labelText);
    label.htmlFor = id;
    let control;
    if (opts.type === "select") {
      control = element("select");
      (opts.choices || []).forEach(function (choice) {
        const option = element("option", "", choice.label);
        option.value = choice.value;
        control.append(option);
      });
      control.value = value === null || value === undefined ? "" : String(value);
    } else if (opts.type === "textarea") {
      control = element("textarea");
      control.rows = opts.rows || 3;
      control.value = value || "";
    } else {
      control = element("input");
      control.type = opts.type === "number" ? "number" : "text";
      control.value = value === null || value === undefined ? "" : String(value);
      if (opts.min !== undefined) control.min = String(opts.min);
      if (opts.max !== undefined) control.max = String(opts.max);
      if (opts.step !== undefined) control.step = String(opts.step);
    }
    control.id = id;
    if (opts.placeholder) control.placeholder = opts.placeholder;
    if (opts.list) control.setAttribute("list", opts.list);
    if (opts.required) control.required = true;
    if (opts.disabled) control.disabled = true;
    field.append(label, control.tagName === "SELECT" ? selectControl(control) : control);
    if (opts.description) field.append(element("small", "", opts.description));
    return { field: field, control: control };
  }

  function roomFormSection(title, options) {
    const opts = options || {};
    const section = element(opts.collapsible ? "details" : "section", "room-form-section");
    if (opts.collapsible) {
      section.open = Boolean(opts.open);
      section.append(element("summary", "", title));
    } else {
      section.append(element("h3", "", title));
    }
    const body = element("div", "room-form-grid");
    section.append(body);
    return { section: section, body: body };
  }

  function addRoomRepeaterRow(container, columns, values) {
    const row = element("div", "room-repeater-row repeater-columns-" + columns.length);
    columns.forEach(function (column) {
      const field = element("label");
      field.append(element("span", "", column.label));
      let control;
      if (column.type === "select") {
        control = element("select");
        column.choices.forEach(function (choice) {
          const option = element("option", "", choice.label);
          option.value = choice.value;
          control.append(option);
        });
      } else {
        control = element("input");
        control.type = "text";
        if (column.list) control.setAttribute("list", column.list);
        if (column.placeholder) control.placeholder = column.placeholder;
      }
      control.dataset.roomColumn = column.key;
      control.value = values && values[column.key] ? values[column.key] : (column.defaultValue || "");
      field.append(control.tagName === "SELECT" ? selectControl(control) : control);
      row.append(field);
    });
    const remove = roomActionButton("trash-2", "Remove row", function () { row.remove(); });
    remove.classList.add("room-repeater-remove");
    row.append(remove);
    container.append(row);
  }

  function roomRepeater(id, labelText, columns, values, addLabel) {
    const wrapper = element("div", "room-repeater full");
    wrapper.id = id;
    wrapper.append(element("strong", "", labelText));
    const rows = element("div", "room-repeater-rows");
    (values || []).forEach(function (value) { addRoomRepeaterRow(rows, columns, value); });
    const add = element("button", "field-action");
    add.type = "button";
    add.append(icon("plus"), element("span", "", addLabel));
    add.addEventListener("click", function () { addRoomRepeaterRow(rows, columns, {}); });
    wrapper.append(rows, add);
    return wrapper;
  }

  function readRoomRepeater(id, requiredKeys) {
    return $$(".room-repeater-row", $("#" + id)).map(function (row) {
      const value = {};
      $$('[data-room-column]', row).forEach(function (control) {
        value[control.dataset.roomColumn] = control.value.trim();
      });
      const present = Object.keys(value).some(function (key) { return value[key]; });
      if (!present) return null;
      requiredKeys.forEach(function (key) {
        if (!value[key]) throw new Error("Complete every " + titleFromKey(key).toLowerCase() + " in " + titleFromKey(id) + ".");
      });
      return value;
    }).filter(Boolean);
  }

  function renderRoomDatalists() {
    const holder = $("#room-datalists");
    holder.replaceChildren();
    const catalog = state.roomCatalog || { areas: [], entities: [] };
    const lists = {
      "ha-area-options": catalog.areas || [],
      "ha-all-entities": catalog.entities || [],
      "ha-light-entities": (catalog.entities || []).filter(function (row) { return row.domain === "light"; }),
      "ha-brightness-entities": (catalog.entities || []).filter(function (row) { return ["light", "number", "input_number"].includes(row.domain); }),
      "ha-volume-entities": (catalog.entities || []).filter(function (row) { return ["media_player", "number", "input_number"].includes(row.domain); }),
      "ha-media-entities": (catalog.entities || []).filter(function (row) { return row.domain === "media_player"; }),
      "ha-remote-entities": (catalog.entities || []).filter(function (row) { return row.domain === "remote"; }),
      "ha-scene-entities": (catalog.entities || []).filter(function (row) { return row.domain === "scene"; }),
      "ha-script-entities": (catalog.entities || []).filter(function (row) { return row.domain === "script"; })
    };
    Object.keys(lists).forEach(function (id) {
      const datalist = element("datalist");
      datalist.id = id;
      lists[id].forEach(function (row) {
        const option = element("option");
        option.value = row.id;
        option.label = row.label || row.id;
        datalist.append(option);
      });
      holder.append(datalist);
    });
  }

  async function ensureRoomCatalog(force) {
    if (state.roomCatalog && !force) return state.roomCatalog;
    if (state.roomCatalogPromise && !force) return state.roomCatalogPromise;
    const request = api("/api/rooms/catalog", {
      method: "POST",
      body: { force: Boolean(force) }
    }).then(function (catalog) {
      state.roomCatalog = catalog;
      renderRoomDatalists();
      return catalog;
    }).finally(function () {
      state.roomCatalogPromise = null;
    });
    state.roomCatalogPromise = request;
    return request;
  }

  function brightnessMode(target) {
    return target && ["area", "entity", "entities"].includes(target.type) ? target.type : "disabled";
  }

  function updateBrightnessFields() {
    const mode = $("#room-brightness-mode").value;
    $("#room-brightness-area-field").hidden = mode !== "area";
    $("#room-brightness-entity-field").hidden = mode !== "entity";
    $("#room-brightness-entities-field").hidden = mode !== "entities";
  }

  function renderRoomEditForm(room, mode) {
    const holder = $("#room-edit-fields");
    holder.replaceChildren();
    const defaults = room.defaults || {};
    const brightness = defaults.brightness_target || null;

    const identity = roomFormSection("Room identity");
    identity.body.append(
      roomFormControl("room-id", "Room ID", room.id || "", {
        required: true,
        disabled: mode === "edit",
        placeholder: "living_room",
        description: "Stable lowercase ID used by routing and saved room focus."
      }).field,
      roomFormControl("room-label", "Display name", room.label || "", {
        required: true,
        placeholder: "Living Room"
      }).field,
      roomFormControl("room-ha-area", "Home Assistant area", room.ha_area_id || "", {
        list: "ha-area-options",
        placeholder: "living_room"
      }).field,
      roomFormControl("room-aliases", "Spoken aliases", (room.aliases || []).join("\n"), {
        type: "textarea",
        full: true,
        placeholder: "living room\nlounge"
      }).field
    );
    holder.append(identity.section);

    const lighting = roomFormSection("Lighting");
    const modeField = roomFormControl("room-brightness-mode", "Room brightness", brightnessMode(brightness), {
      type: "select",
      choices: [
        { value: "disabled", label: "Disabled" },
        { value: "area", label: "All lights in the HA area" },
        { value: "entity", label: "Proxy or helper entity" },
        { value: "entities", label: "Selected light entities" }
      ]
    });
    modeField.control.addEventListener("change", updateBrightnessFields);
    const areaField = roomFormControl("room-brightness-area", "Brightness area override", brightness && brightness.area_id || "", {
      list: "ha-area-options",
      placeholder: "Uses the room area when blank"
    });
    areaField.field.id = "room-brightness-area-field";
    const entityField = roomFormControl("room-brightness-entity", "Brightness proxy or helper", brightness && brightness.entity_id || "", {
      list: "ha-brightness-entities",
      placeholder: "light.living_room_brightness"
    });
    entityField.field.id = "room-brightness-entity-field";
    const entitiesField = roomFormControl("room-brightness-entities", "Selected lights", brightness && (brightness.entity_ids || []).join("\n") || "", {
      type: "textarea",
      full: true,
      placeholder: "light.ceiling\nlight.floor_lamp"
    });
    entitiesField.field.id = "room-brightness-entities-field";
    lighting.body.append(
      modeField.field,
      areaField.field,
      entityField.field,
      entitiesField.field,
      roomFormControl("room-color-light", "Room color light", defaults.color_light || "", {
        list: "ha-light-entities",
        placeholder: "light.living_room_color"
      }).field
    );
    holder.append(lighting.section);

    const audio = roomFormSection("Audio and media", { collapsible: true, open: true });
    audio.body.append(
      roomFormControl("room-volume-target", "Room volume target", defaults.volume_target && defaults.volume_target.entity_id || "", {
        list: "ha-volume-entities",
        placeholder: "media_player.living_room"
      }).field,
      roomFormControl("room-audio-output", "Primary audio output", defaults.audio_output || "", {
        list: "ha-media-entities",
        placeholder: "media_player.living_room"
      }).field,
      roomFormControl("room-announcements", "Announcement output", defaults.announcements || "", {
        list: "ha-media-entities",
        placeholder: "media_player.living_room"
      }).field,
      roomFormControl("room-spotcast-name", "Spotify Connect device", defaults.spotcast_device_name || "", {
        placeholder: "Livingroom"
      }).field,
      roomFormControl("room-spotcast-aliases", "Spotify device aliases", (defaults.spotcast_device_aliases || []).join("\n"), {
        type: "textarea",
        placeholder: "sonos"
      }).field,
      roomFormControl("room-audio-outputs", "Room audio outputs", (room.audio_outputs || []).join("\n"), {
        type: "textarea",
        list: "ha-media-entities",
        placeholder: "media_player.living_room"
      }).field,
      roomFormControl("room-focus-participants", "Media focus participants", (room.focus_participants || []).join("\n"), {
        type: "textarea",
        placeholder: "media_player.living_room"
      }).field
    );
    audio.body.append(roomRepeater(
      "room-media-players",
      "Client media players",
      [
        { key: "entity", label: "Entity", list: "ha-media-entities", placeholder: "media_player.living_room" },
        { key: "label", label: "Label", placeholder: "Living Room" }
      ],
      room.media_players || [],
      "Add media player"
    ));
    const audioAliasRows = Object.keys(room.audio_aliases || {}).map(function (alias) {
      return { alias: alias, entity: room.audio_aliases[alias] };
    });
    audio.body.append(roomRepeater(
      "room-audio-aliases",
      "Named audio outputs",
      [
        { key: "alias", label: "Spoken name", placeholder: "bookshelf" },
        { key: "entity", label: "Entity", list: "ha-media-entities", placeholder: "media_player.bookshelf" }
      ],
      audioAliasRows,
      "Add named output"
    ));
    holder.append(audio.section);

    const television = roomFormSection("Television and Plex", { collapsible: true });
    television.body.append(
      roomFormControl("room-tv", "Television media player", defaults.tv || "", {
        list: "ha-media-entities",
        placeholder: "media_player.living_room_tv"
      }).field,
      roomFormControl("room-tv-remote", "Television remote", defaults.tv_remote || "", {
        list: "ha-remote-entities",
        placeholder: "remote.living_room_tv"
      }).field,
      roomFormControl("room-tv-scene", "Power-on scene", defaults.tv_on_scene || "", {
        list: "ha-scene-entities",
        placeholder: "scene.tv_on"
      }).field,
      roomFormControl("room-plex-client", "Plex client name", defaults.plex_client_name || "", {
        placeholder: "Apple TV"
      }).field,
      roomFormControl("room-plex-script", "Plex launch script", defaults.plex_launch_script || "", {
        list: "ha-script-entities",
        placeholder: "script.launch_plex"
      }).field
    );
    holder.append(television.section);

    const clients = roomFormSection("Client controls", { collapsible: true });
    clients.body.append(roomRepeater(
      "room-devices",
      "Visible devices",
      [
        { key: "entity", label: "Entity", list: "ha-all-entities", placeholder: "light.floor_lamp" },
        { key: "label", label: "Label", placeholder: "Floor Lamp" }
      ],
      room.devices || [],
      "Add device"
    ));
    const shortcutRows = (room.scenes || []).map(function (entry) {
      const action = ["command", "scene", "script"].find(function (key) { return entry[key]; }) || "command";
      return { label: entry.label || "", action: action, target: entry[action] || "" };
    });
    clients.body.append(roomRepeater(
      "room-shortcuts",
      "Room shortcuts",
      [
        { key: "label", label: "Label", placeholder: "Movie" },
        {
          key: "action",
          label: "Action",
          type: "select",
          defaultValue: "command",
          choices: [
            { value: "command", label: "Home Suite command" },
            { value: "scene", label: "HA scene" },
            { value: "script", label: "HA script" }
          ]
        },
        { key: "target", label: "Command or entity", placeholder: "living room bright" }
      ],
      shortcutRows,
      "Add shortcut"
    ));
    holder.append(clients.section);
    updateBrightnessFields();
    window.renderLucideIcons(holder);
  }

  function emptyRoomDraft() {
    return {
      id: "",
      label: "",
      ha_area_id: null,
      aliases: [],
      defaults: {
        brightness_target: null,
        color_light: null,
        volume_target: null,
        audio_output: null,
        announcements: null,
        spotcast_device_name: null,
        spotcast_device_aliases: [],
        tv: null,
        tv_remote: null,
        tv_on_scene: null,
        plex_client_name: null,
        plex_launch_script: null
      },
      media_players: [],
      audio_outputs: [],
      audio_aliases: {},
      focus_participants: [],
      scenes: [],
      devices: []
    };
  }

  function uniqueRoomCopyId(roomId) {
    let candidate = roomId + "_copy";
    let suffix = 2;
    while (state.roomDraft[candidate]) {
      candidate = roomId + "_copy_" + suffix;
      suffix += 1;
    }
    return candidate;
  }

  function openRoomEditor(roomId, mode) {
    let room;
    if (mode === "add") {
      room = emptyRoomDraft();
    } else {
      room = deepClone(state.roomDraft[roomId]);
      room.id = roomId;
      if (mode === "duplicate") {
        room.id = uniqueRoomCopyId(roomId);
        room.label = (room.label || titleFromKey(roomId)) + " Copy";
        room.aliases = [room.label.toLowerCase()];
      }
    }
    state.roomEditing = { originalId: mode === "edit" ? roomId : null, mode: mode, room: room };
    $("#room-edit-context").textContent = mode === "duplicate" ? "Duplicate room" : "Shared room";
    $("#room-edit-title").textContent = mode === "add" ? "Add room" : (mode === "duplicate" ? "Duplicate room" : "Edit " + (room.label || roomId));
    $("#room-edit-error").hidden = true;
    renderRoomEditForm(room, mode);
    $("#room-edit-dialog").showModal();
    window.setTimeout(function () { $("#room-label").focus(); }, 0);
    ensureRoomCatalog(false).catch(function (error) { showToast(error.message); });
  }

  function roomLines(id) {
    return $("#" + id).value.split(/[\n,]+/).map(function (value) { return value.trim(); }).filter(Boolean);
  }

  function roomOptional(id) {
    const value = $("#" + id).value.trim();
    return value || null;
  }

  function collectRoomEditForm() {
    const editing = state.roomEditing;
    const room = deepClone(editing.room);
    const roomId = $("#room-id").value.trim();
    const label = $("#room-label").value.trim();
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(roomId)) throw new Error("Room ID must use lowercase letters, numbers, and underscores.");
    if (!label) throw new Error("Enter a display name for this room.");
    if ((!editing.originalId || editing.originalId !== roomId) && state.roomDraft[roomId]) throw new Error("That room ID already exists.");

    room.id = roomId;
    room.label = label;
    room.ha_area_id = roomOptional("room-ha-area");
    room.aliases = roomLines("room-aliases");
    const defaults = Object.assign({}, room.defaults || {});
    const mode = $("#room-brightness-mode").value;
    if (mode === "disabled") defaults.brightness_target = null;
    if (mode === "area") {
      defaults.brightness_target = { type: "area" };
      const areaOverride = roomOptional("room-brightness-area");
      if (areaOverride) defaults.brightness_target.area_id = areaOverride;
    }
    if (mode === "entity") defaults.brightness_target = { type: "entity", entity_id: roomOptional("room-brightness-entity") };
    if (mode === "entities") defaults.brightness_target = { type: "entities", entity_ids: roomLines("room-brightness-entities") };
    defaults.color_light = roomOptional("room-color-light");
    const volume = roomOptional("room-volume-target");
    defaults.volume_target = volume ? { type: "entity", entity_id: volume } : null;
    defaults.audio_output = roomOptional("room-audio-output");
    defaults.announcements = roomOptional("room-announcements");
    defaults.spotcast_device_name = roomOptional("room-spotcast-name");
    defaults.spotcast_device_aliases = roomLines("room-spotcast-aliases");
    defaults.tv = roomOptional("room-tv");
    defaults.tv_remote = roomOptional("room-tv-remote");
    defaults.tv_on_scene = roomOptional("room-tv-scene");
    defaults.plex_client_name = roomOptional("room-plex-client");
    defaults.plex_launch_script = roomOptional("room-plex-script");
    room.defaults = defaults;
    room.audio_outputs = roomLines("room-audio-outputs");
    room.focus_participants = roomLines("room-focus-participants");
    room.media_players = readRoomRepeater("room-media-players", ["entity", "label"]);
    room.devices = readRoomRepeater("room-devices", ["entity", "label"]);
    room.audio_aliases = {};
    readRoomRepeater("room-audio-aliases", ["alias", "entity"]).forEach(function (entry) {
      room.audio_aliases[entry.alias] = entry.entity;
    });
    room.scenes = readRoomRepeater("room-shortcuts", ["label", "action", "target"]).map(function (entry) {
      const result = { label: entry.label };
      result[entry.action] = entry.target;
      return result;
    });
    return room;
  }

  function saveRoomDraft(event) {
    event.preventDefault();
    const error = $("#room-edit-error");
    try {
      const room = collectRoomEditForm();
      if (state.roomEditing.originalId && state.roomEditing.originalId !== room.id) {
        delete state.roomDraft[state.roomEditing.originalId];
      }
      state.roomDraft[room.id] = room;
      state.roomPreview = null;
      $("#room-edit-dialog").close();
      state.roomEditing = null;
      renderRooms();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
    }
  }

  function closeRoomEditor() {
    $("#room-edit-dialog").close();
    state.roomEditing = null;
  }

  function removeRoomDraft(roomId) {
    const room = state.roomDraft[roomId];
    if (!room || !window.confirm("Remove " + (room.label || roomId) + " from the shared room configuration?")) return;
    delete state.roomDraft[roomId];
    state.roomPreview = null;
    renderRooms();
  }

  function discardRoomChanges() {
    if (!roomDraftChanged()) return;
    if (!window.confirm("Discard all pending room changes?")) return;
    state.roomDraft = deepClone(state.roomConfig.rooms);
    state.roomPreview = null;
    renderRooms();
  }

  function renderRoomReview(preview) {
    const holder = $("#room-review-list");
    holder.replaceChildren();
    preview.changes.forEach(function (change) {
      const row = element("div", "config-review-row room-review-row");
      const label = element("div");
      label.append(element("strong", "", change.label), element("code", "", change.room_id));
      const detail = element("div", "room-review-detail");
      detail.append(badge(change.action === "remove" ? "FAIL" : (change.action === "add" ? "configured" : "WARN"), titleFromKey(change.action)));
      detail.append(element("span", "", change.details));
      row.append(label, detail);
      holder.append(row);
    });
    $("#room-restart-notice").textContent = "The room setup will be saved without interrupting Home Suite. Use Restart required when you are ready to activate it.";
  }

  async function reviewRoomChanges() {
    if (!roomDraftChanged()) return;
    const button = $("#review-room-changes");
    button.disabled = true;
    try {
      const preview = await api("/api/rooms/preview", {
        method: "POST",
        body: { rooms: state.roomDraft }
      });
      if (!preview.change_count) {
        state.roomDraft = deepClone(state.roomConfig.rooms);
        renderRooms();
        return;
      }
      state.roomPreview = preview;
      renderRoomReview(preview);
      $("#room-review-dialog").showModal();
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function applyRoomChanges() {
    if (!state.roomPreview) return;
    const button = $("#apply-room-changes");
    button.disabled = true;
    try {
      const result = await api("/api/rooms/apply", {
        method: "POST",
        body: {
          rooms: state.roomDraft,
          revision: state.roomPreview.revision
        }
      });
      $("#room-review-dialog").close();
      state.roomPreview = null;
      await loadRoomConfig();
      await loadServices().catch(function () {});
      const output = $("#room-result");
      output.replaceChildren(icon("circle-check"), element("span", "", "Saved " + result.change_count + " room change" + (result.change_count === 1 ? "" : "s") + ". No service was restarted. Backup: " + result.backup_dir + "."));
      output.hidden = false;
      showToast("Room configuration saved");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function audioSummaryRow(label, value, detail) {
    const row = element("div");
    row.append(element("dt", "", label));
    const display = element("dd", "", value || "Not configured");
    if (detail) {
      display.append(element("small", "", detail));
    }
    row.append(display);
    return row;
  }

  function audioSourceLabel(source) {
    const labels = {
      local_prefs: "Saved on this device",
      app_config: "Application configuration",
      service_environment: "Service setting",
      system_default: "ALSA system default",
      effective_default: "Inherited default"
    };
    return labels[source] || "Current setting";
  }

  function formatAudioRate(value) {
    const rate = Number(value || 0);
    if (!rate) return "Not set";
    return rate % 1000 === 0 ? (rate / 1000) + " kHz" : rate.toLocaleString() + " Hz";
  }

  const audioSettingFieldIds = {
    stream_latency: "audio-stream-latency",
    mixer_value: "audio-mixer-value",
    alsa_card: "audio-alsa-card"
  };
  const audioDirectSuggestionSettings = new Set(["stream_latency", "mixer_value"]);

  function renderAudioHardware() {
    const holder = $("#audio-hardware");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    if (!state.audioConfig) return;
    const hardware = state.audioConfig.hardware || {};
    const groups = [
      ["Inputs", hardware.inputs || []],
      ["Outputs", hardware.outputs || []]
    ];
    groups.forEach(function (entry) {
      const group = element("section", "audio-hardware-group");
      group.append(element("h3", "", entry[0]));
      if (!entry[1].length) {
        group.append(element("div", "audio-hardware-row", "No devices detected."));
      }
      entry[1].forEach(function (device) {
        const row = element("div", "audio-hardware-row");
        const copy = element("div");
        copy.append(
          element("strong", "", device.card_name || device.device_name || "Audio device"),
          element("span", "", device.device_name || device.pcm_name || "")
        );
        const identifier = element("code", "audio-hardware-device", device.plughw || device.hw || "");
        let deviceStatus;
        if (device.direction === "input" && device.busy) {
          deviceStatus = badge("configured", state.audioConfig.wakeword_enabled ? "In use by Home Suite" : "In use");
        } else {
          deviceStatus = badge("OK", "Available");
        }
        row.append(copy, identifier, deviceStatus);
        group.append(row);
      });
      holder.append(group);
    });
  }

  function audioRuntimeNotice() {
    const notice = $("#audio-runtime-notice");
    if (!state.audioConfig) {
      notice.hidden = true;
      return;
    }
    const runtime = state.audioConfig.runtime || {};
    notice.replaceChildren();
    notice.className = "mode-notice";
    if (runtime.available === false) {
      notice.hidden = false;
      notice.classList.add("fail");
      notice.append(icon("triangle-alert"), element("span", "", runtime.error || "The running Home Suite service does not support guided calibration yet."));
    } else if (runtime.active && !state.audioCalibration.token) {
      notice.hidden = false;
      notice.classList.add("warn");
      notice.append(icon("activity"), element("span", "", "Another audio setup session is using the microphone."));
    } else if (runtime.busy_reason && !state.audioCalibration.token) {
      notice.hidden = false;
      notice.classList.add("warn");
      notice.append(icon("info"), element("span", "", runtime.busy_reason));
    } else if (state.audioCalibration.token) {
      notice.hidden = false;
      notice.classList.add("ok");
      notice.append(icon("mic"), element("span", "", "Voice capture is paused for calibration and will resume automatically."));
    } else {
      notice.hidden = true;
    }
    window.renderLucideIcons(notice);
  }

  function renderAudioCalibration() {
    const holder = $("#audio-calibration");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    if (!state.audioConfig) return;
    const calibration = state.audioCalibration;
    const runtime = state.audioConfig.runtime || {};

    if (calibration.stage === "results" && calibration.speech) {
      const speech = calibration.speech;
      const noise = calibration.noise;
      const heading = element("div", "audio-results-heading");
      const resultStatus = speech.status || "review";
      heading.append(
        badge(resultStatus === "healthy" ? "OK" : (resultStatus === "poor" ? "FAIL" : "WARN"), resultStatus === "healthy" ? "Healthy" : (resultStatus === "poor" ? "Needs adjustment" : "Review")),
        element("h3", "", "Calibration results")
      );
      const metrics = element("div", "audio-metric-grid");
      [
        ["Noise floor", noise ? Number(noise.metrics.p20_dbfs).toFixed(1) + " dBFS" : "Not measured"],
        ["Speech peak", Number(speech.metrics.peak_dbfs).toFixed(1) + " dBFS"],
        ["Speech level", Number(speech.metrics.p90_dbfs).toFixed(1) + " dBFS"],
        ["Dropped blocks", String(Number(speech.overflows || 0) + Number(noise && noise.overflows || 0))]
      ].forEach(function (entry) {
        const metric = element("div", "audio-metric");
        metric.append(element("span", "", entry[0]), element("strong", "", entry[1]));
        metrics.append(metric);
      });
      const guidance = element("section", "audio-guidance");
      const guidanceHeading = element("div", "audio-guidance-heading");
      const appliedSuggestion = (speech.adjustments || []).some(function (adjustment) { return adjustment.applied; });
      guidanceHeading.append(
        element("h4", "", "What to do next"),
        element("span", "", appliedSuggestion
          ? "Saved suggestions are marked below. Use Restart required before retesting."
          : "Suggestions only. Nothing was changed.")
      );
      const adjustmentList = element("div", "audio-adjustment-list");
      let adjustments = speech.adjustments || [];
      if (!adjustments.length) {
        adjustments = (speech.recommendations || []).map(function (line) {
          return { title: line, detail: "", tone: "attention" };
        });
      }
      adjustments.forEach(function (adjustment) {
        const tone = ["good", "change", "attention"].includes(adjustment.tone) ? adjustment.tone : "attention";
        const row = element("div", "audio-adjustment-row " + tone);
        row.append(icon(tone === "good" ? "circle-check" : (tone === "change" ? "sliders-horizontal" : "triangle-alert")));
        const copy = element("div", "audio-adjustment-copy");
        copy.append(element("strong", "", adjustment.title || "Review this result"));
        if (adjustment.detail) copy.append(element("p", "", adjustment.detail));
        const meta = [];
        if (adjustment.setting_label) meta.push(adjustment.setting_label);
        if (adjustment.current_value !== null && adjustment.current_value !== undefined) {
          meta.push((adjustment.applied ? "Measured with: " : "Current: ") + titleFromKey(String(adjustment.current_value)));
        }
        if (adjustment.suggested_value !== null && adjustment.suggested_value !== undefined) {
          meta.push((adjustment.applied ? "Saved: " : "Suggested: ") + titleFromKey(String(adjustment.suggested_value)));
        }
        if (adjustment.applied) meta.push("Restart required");
        if (meta.length) copy.append(element("span", "audio-adjustment-meta", meta.join(" · ")));
        row.append(copy);
        const fieldId = audioSettingFieldIds[adjustment.setting];
        const hasSuggestedValue = adjustment.suggested_value !== null && adjustment.suggested_value !== undefined;
        const canApply = hasSuggestedValue && audioDirectSuggestionSettings.has(adjustment.setting);
        if (adjustment.applied) {
          row.append(badge("configured", "Saved"));
        } else if (canApply) {
          const apply = element("button", "button primary compact-button");
          apply.type = "button";
          apply.append(icon("circle-check"), element("span", "", "Apply " + titleFromKey(String(adjustment.suggested_value))));
          apply.addEventListener("click", function () { reviewAudioSuggestion(adjustment, apply); });
          row.append(apply);
        } else if (fieldId) {
          const edit = element("button", "button secondary compact-button");
          edit.type = "button";
          edit.append(icon("pencil"), element("span", "", "Edit"));
          edit.addEventListener("click", function () { openAudioEditor(fieldId); });
          row.append(edit);
        }
        adjustmentList.append(row);
      });
      guidance.append(guidanceHeading, adjustmentList);
      const actions = element("div", "audio-calibration-actions");
      const again = element("button", "button secondary");
      again.type = "button";
      again.append(icon("rotate-ccw"), element("span", "", "Run again"));
      again.addEventListener("click", startAudioCalibration);
      actions.append(again);
      const stateHolder = element("div", "audio-calibration-state");
      stateHolder.append(heading, metrics, guidance, actions);
      holder.append(stateHolder);
      window.renderLucideIcons(holder);
      return;
    }

    if (calibration.stage === "recording_noise" || calibration.stage === "recording_speech") {
      const isNoise = calibration.stage === "recording_noise";
      const stage = element("div", "audio-calibration-state");
      const copy = element("div", "audio-calibration-stage");
      copy.append(element("span", "audio-stage-number", isNoise ? "1" : "2"));
      const text = element("div");
      text.append(
        element("h3", "", isNoise ? "Measuring room noise" : "Listening to normal speech"),
        element("p", "", isNoise ? "Stay quiet until the measurement finishes." : "Say several commands at the distance you normally use.")
      );
      copy.append(text);
      const progress = element("div", "audio-calibration-progress");
      progress.style.setProperty("--calibration-duration", isNoise ? "3s" : "5s");
      progress.append(element("span"));
      stage.append(copy, progress);
      holder.append(stage);
      return;
    }

    if (calibration.token) {
      const isSpeech = calibration.stage === "speech_ready";
      const stage = element("div", "audio-calibration-state");
      const copy = element("div", "audio-calibration-stage");
      copy.append(element("span", "audio-stage-number", isSpeech ? "2" : "1"));
      const text = element("div");
      text.append(
        element("h3", "", isSpeech ? "Measure normal speech" : "Measure the room noise"),
        element("p", "", isSpeech
          ? "Speak naturally for five seconds. Include a few commands you really use."
          : "Keep the room as it normally sounds, but stay quiet for three seconds.")
      );
      copy.append(text);
      const actions = element("div", "audio-calibration-actions");
      const measure = element("button", "button primary");
      measure.type = "button";
      measure.append(icon(isSpeech ? "audio-waveform" : "mic"), element("span", "", isSpeech ? "Measure speech" : "Measure room noise"));
      measure.addEventListener("click", function () { captureAudioCalibration(isSpeech ? "speech" : "noise"); });
      const cancel = element("button", "button secondary", "Cancel");
      cancel.type = "button";
      cancel.addEventListener("click", function () { releaseAudioCalibration("cancelled"); });
      actions.append(measure, cancel);
      stage.append(copy, actions);
      holder.append(stage);
      window.renderLucideIcons(holder);
      return;
    }

    const intro = element("div", "audio-calibration-intro");
    const copy = element("div", "audio-calibration-copy");
    copy.append(
      element("h3", "", "Check this microphone in its real room"),
      element("p", "", "Home Suite measures the normal noise floor, speech level, clipping, and dropped audio blocks. Wake-word or PTT capture pauses only during the short test and resumes automatically.")
    );
    if (calibration.error) copy.append(element("p", "form-error", calibration.error));
    const start = element("button", "button primary");
    start.type = "button";
    start.disabled = runtime.available === false || Boolean(runtime.active) || Boolean(runtime.busy_reason) || calibration.busy;
    start.append(icon("mic"), element("span", "", calibration.busy ? "Preparing" : "Start calibration"));
    start.addEventListener("click", startAudioCalibration);
    intro.append(copy, start);
    holder.append(intro);
    window.renderLucideIcons(holder);
  }

  function renderAudio() {
    if (!state.audioConfig) return;
    const profile = state.audioConfig.profile || {};
    const profileLatency = titleFromKey(profile.stream_latency || "low");
    let streamSummary = profileLatency + " latency";
    if (state.audioConfig.ptt_enabled && state.audioConfig.wakeword_enabled) {
      streamSummary = "Wake word " + profileLatency.toLowerCase() + " · PTT high";
    } else if (state.audioConfig.ptt_enabled) {
      streamSummary = "High latency · PTT";
    }
    const input = $("#audio-input-summary");
    input.classList.remove("loading-block");
    input.replaceChildren(
      audioSummaryRow("Profile", profile.name, audioSourceLabel(state.audioConfig.profile_source)),
      audioSummaryRow("Microphone", profile.device_match || (profile.device_index !== null ? "PortAudio " + profile.device_index : "Not set")),
      audioSummaryRow("Format", formatAudioRate(profile.sample_rate) + " · " + profile.channels + " channel" + (Number(profile.channels) === 1 ? "" : "s")),
      audioSummaryRow("Stream", streamSummary),
      audioSummaryRow("Hardware gain", profile.mixer_value === null || profile.mixer_value === undefined ? "Not managed" : String(profile.mixer_value), profile.alsa_card && profile.mixer_control ? profile.alsa_card + " · " + profile.mixer_control : null)
    );
    const output = $("#audio-output-summary");
    output.classList.remove("loading-block");
    output.replaceChildren(
      audioSummaryRow("Playback device", state.audioConfig.output_effective, audioSourceLabel(state.audioConfig.output_source)),
      audioSummaryRow("Assistant replies", state.audioConfig.assistant_output_mode === "sonos" ? "Room audio" : "This device"),
      audioSummaryRow("Voice roles", [state.audioConfig.wakeword_enabled ? "Wake word" : null, state.audioConfig.ptt_enabled ? "PTT" : null].filter(Boolean).join(" + ") || "None")
    );
    const test = $("#test-audio-output");
    test.disabled = !state.audioConfig.output_effective || (state.audioConfig.runtime || {}).available === false || Boolean((state.audioConfig.runtime || {}).active);
    audioRuntimeNotice();
    renderAudioCalibration();
    renderAudioHardware();
  }

  async function startAudioCalibration() {
    if (!state.audioConfig || state.audioCalibration.busy) return;
    state.audioCalibration.busy = true;
    state.audioCalibration.error = null;
    state.audioCalibration.stage = "idle";
    renderAudioCalibration();
    try {
      const result = await api("/api/audio/calibration/acquire", { method: "POST", body: {} });
      state.audioCalibration.token = result.token;
      state.audioCalibration.stage = "noise_ready";
      state.audioCalibration.noise = null;
      state.audioCalibration.speech = null;
      state.audioConfig.runtime = result;
    } catch (error) {
      state.audioCalibration.error = error.message;
    } finally {
      state.audioCalibration.busy = false;
      audioRuntimeNotice();
      renderAudioCalibration();
    }
  }

  async function captureAudioCalibration(phase) {
    const token = state.audioCalibration.token;
    if (!token || !state.audioConfig) return;
    state.audioCalibration.stage = phase === "noise" ? "recording_noise" : "recording_speech";
    state.audioCalibration.error = null;
    renderAudioCalibration();
    try {
      const result = await api("/api/audio/calibration/capture", {
        method: "POST",
        body: {
          token: token,
          phase: phase,
          profile: state.audioConfig.profile,
          noise_metrics: state.audioCalibration.noise
            ? Object.assign({}, state.audioCalibration.noise.metrics, {
                overflow_count: Number(state.audioCalibration.noise.overflows || 0)
              })
            : null
        }
      });
      if (phase === "noise") {
        state.audioCalibration.noise = result;
        state.audioCalibration.stage = "speech_ready";
      } else {
        state.audioCalibration.speech = result;
        await releaseAudioCalibration("complete", true);
        state.audioCalibration.stage = "results";
      }
    } catch (error) {
      state.audioCalibration.error = error.message;
      await releaseAudioCalibration("capture_error", true);
      state.audioCalibration.stage = "idle";
    }
    audioRuntimeNotice();
    renderAudioCalibration();
  }

  async function releaseAudioCalibration(reason, quiet) {
    const token = state.audioCalibration.token;
    if (!token) return;
    try {
      const result = await api("/api/audio/calibration/release", {
        method: "POST",
        body: { token: token, reason: reason || "complete" }
      });
      if (state.audioConfig) state.audioConfig.runtime = result;
    } catch (error) {
      if (!quiet) showToast(error.message);
    } finally {
      state.audioCalibration.token = null;
      if (reason !== "complete") state.audioCalibration.stage = "idle";
      audioRuntimeNotice();
      renderAudioCalibration();
    }
  }

  function audioSwitchField(id, labelText, checked, description) {
    const field = element("div", "room-form-field");
    const label = element("label", "", labelText);
    label.htmlFor = id;
    const wrapper = element("label", "switch-control");
    const input = element("input");
    input.type = "checkbox";
    input.id = id;
    input.checked = Boolean(checked);
    wrapper.append(input, element("span", "switch-track"), element("span", "switch-label", input.checked ? "Enabled" : "Disabled"));
    input.addEventListener("change", function () { $(".switch-label", wrapper).textContent = input.checked ? "Enabled" : "Disabled"; });
    field.append(label, wrapper);
    if (description) field.append(element("small", "", description));
    return { field: field, control: input };
  }

  function audioDetectedInputChoices(profile) {
    const choices = [];
    const seen = new Set();
    function add(value, label) {
      if (!value || seen.has(value)) return;
      seen.add(value);
      choices.push({ value: value, label: label || value });
    }
    add(profile.device_match, profile.device_match + " (current)");
    (((state.audioConfig || {}).hardware || {}).inputs || []).forEach(function (device) {
      add(device.card_name, device.card_name + (device.busy ? " · in use" : ""));
    });
    return choices;
  }

  function audioOutputChoices() {
    const config = state.audioConfig || {};
    const choices = [{ value: "", label: "Use current service or system setting" }];
    const seen = new Set([""]);
    function add(value, label) {
      if (!value || seen.has(value)) return;
      seen.add(value);
      choices.push({ value: value, label: label });
    }
    add(config.output_override, config.output_override + " (current override)");
    ((config.hardware || {}).outputs || []).forEach(function (device) {
      add(device.plughw, device.card_name + " · " + device.device_name);
    });
    return choices;
  }

  function updateAudioMixerControls(preferred) {
    const card = $("#audio-alsa-card");
    const control = $("#audio-mixer-control");
    if (!card || !control) return;
    const devices = (((state.audioConfig || {}).hardware || {}).inputs || []);
    const selected = devices.find(function (device) { return device.card_id === card.value; });
    const choices = [{ value: "", label: "No hardware gain control" }];
    const controls = selected ? (selected.mixer_controls || []).slice() : [];
    const wanted = preferred !== undefined ? preferred : control.value;
    if (wanted && !controls.includes(wanted)) controls.unshift(wanted);
    controls.forEach(function (name) { choices.push({ value: name, label: name }); });
    control.replaceChildren();
    choices.forEach(function (choice) {
      const option = element("option", "", choice.label);
      option.value = choice.value;
      control.append(option);
    });
    control.value = wanted || "";
  }

  function renderAudioEditForm() {
    const config = state.audioConfig;
    const draft = state.audioDraft;
    if (!config || !draft) return;
    const profile = draft.profile;
    const holder = $("#audio-edit-fields");
    holder.replaceChildren();

    const input = roomFormSection("Microphone input");
    const detected = roomFormControl("audio-input-device", "Microphone", profile.device_match, {
      type: "select",
      choices: audioDetectedInputChoices(profile),
      required: true,
      description: "Stored by device name so USB card-number changes do not break capture."
    });
    const profileName = roomFormControl("audio-profile-name", "Profile name", profile.name, {
      required: true,
      placeholder: "living_room_mic",
      description: "A short label for this node's calibrated microphone setup."
    });
    const sampleRate = roomFormControl("audio-sample-rate", "Sample rate", profile.sample_rate, {
      type: "number", min: 8000, max: 192000, step: 1000,
      description: "Use the microphone's native hardware rate; 48 kHz is common for USB devices."
    });
    const channels = roomFormControl("audio-channels", "Input channels", profile.channels, {
      type: "number", min: 1, max: 8, step: 1,
      description: "Home Suite uses the first channel for mono command audio."
    });
    const latency = roomFormControl("audio-stream-latency", config.ptt_enabled ? "Wake-word stream latency" : "Stream latency", profile.stream_latency, {
      type: "select",
      choices: [
        { value: "low", label: "Low · faster response" },
        { value: "high", label: "High · more overflow protection" }
      ],
      description: config.ptt_enabled
        ? "This controls continuous wake-word capture. PTT uses High latency for scheduling headroom."
        : "Choose High if calibration or runtime logs report dropped input blocks."
    });
    if (config.ptt_enabled && !config.wakeword_enabled) latency.field.hidden = true;
    const strict = audioSwitchField("audio-strict-match", "Require this microphone", profile.strict_device_match, "Prevents Home Suite from silently capturing a different default input if this mic is disconnected.");
    detected.control.addEventListener("change", function () {
      const device = ((config.hardware || {}).inputs || []).find(function (row) { return row.card_name === detected.control.value; });
      if (!device) return;
      $("#audio-device-index").value = "";
      $("#audio-alsa-card").value = device.card_id || "";
      if (device.default_sample_rate) sampleRate.control.value = String(device.default_sample_rate);
      strict.control.checked = true;
      strict.control.dispatchEvent(new Event("change"));
      updateAudioMixerControls("");
    });
    input.body.append(profileName.field, detected.field, sampleRate.field, channels.field, latency.field, strict.field);
    holder.append(input.section);

    const gain = roomFormSection("Capture gain", { collapsible: true, open: true });
    const cardChoices = [{ value: "", label: "No ALSA mixer card" }];
    ((config.hardware || {}).inputs || []).forEach(function (device) {
      cardChoices.push({ value: device.card_id, label: device.card_name + " (" + device.card_id + ")" });
    });
    if (profile.alsa_card && !cardChoices.some(function (choice) { return choice.value === profile.alsa_card; })) {
      cardChoices.push({ value: profile.alsa_card, label: profile.alsa_card + " (current)" });
    }
    const alsaCard = roomFormControl("audio-alsa-card", "ALSA capture card", profile.alsa_card || "", {
      type: "select", choices: cardChoices,
      description: "Stable ALSA ID used to read and restore hardware gain."
    });
    const mixerControl = roomFormControl("audio-mixer-control", "Mixer control", profile.mixer_control || "", {
      type: "select", choices: [],
      description: "Leave disabled when the microphone exposes no hardware capture control."
    });
    const mixerValue = roomFormControl("audio-mixer-value", "Hardware capture gain", profile.mixer_value, {
      type: "number", min: 0, max: 65535, step: 1,
      description: "The exact ALSA control value confirmed by calibration."
    });
    const verify = roomFormControl("audio-verify-interval", "Restore gain every", profile.verify_interval_sec, {
      type: "number", min: 0, max: 3600, step: 1,
      description: "Seconds between checks. Use 0 to disable; 10-15 seconds is useful when another mixer may reset gain."
    });
    const deviceIndex = roomFormControl("audio-device-index", "PortAudio index override", profile.device_index, {
      type: "number", min: 0, max: 4096, step: 1,
      description: "Usually blank. Numeric indices can change after reboot or USB changes."
    });
    alsaCard.control.addEventListener("change", function () {
      updateAudioMixerControls("");
      if (!alsaCard.control.value) mixerValue.control.value = "";
    });
    mixerControl.control.addEventListener("change", function () {
      if (!mixerControl.control.value) mixerValue.control.value = "";
    });
    gain.body.append(alsaCard.field, mixerControl.field, mixerValue.field, verify.field, deviceIndex.field);
    holder.append(gain.section);
    updateAudioMixerControls(profile.mixer_control || "");

    const output = roomFormSection("Local playback");
    const outputDevice = roomFormControl("audio-output-device", "Playback device", draft.output_override || "", {
      type: "select", choices: audioOutputChoices(), full: true,
      description: "Used for local speech and cue sounds. Leaving this unchanged preserves the current service setting: " + config.output_effective
    });
    output.body.append(outputDevice.field);
    holder.append(output.section);

    const processing = roomFormSection("Signal processing", { collapsible: true, open: false });
    const nsChoices = [0, 1, 2, 3, 4].map(function (value) { return { value: String(value), label: value === 0 ? "Off" : "Level " + value }; });
    processing.body.append(
      roomFormControl("audio-ww-ns", "Wake-word noise suppression", profile.noise_suppression_level, { type: "select", choices: nsChoices }).field,
      roomFormControl("audio-command-ns", "Command noise suppression", profile.command_noise_suppression_level, { type: "select", choices: nsChoices }).field,
      roomFormControl("audio-ww-agc", "Wake-word automatic gain target", profile.auto_gain_dbfs, { type: "number", min: 0, max: 31, step: 1, description: "0 disables automatic gain." }).field,
      roomFormControl("audio-command-agc", "Command automatic gain target", profile.command_auto_gain_dbfs, { type: "number", min: 0, max: 31, step: 1, description: "0 disables automatic gain." }).field,
      roomFormControl("audio-ww-gain", "Wake-word software gain", profile.volume_multiplier, { type: "number", min: 0.05, max: 8, step: 0.05 }).field,
      roomFormControl("audio-command-gain", "Command software gain", profile.command_volume_multiplier, { type: "number", min: 0.05, max: 8, step: 0.05 }).field,
      roomFormControl("audio-ptt-gain", "PTT software gain", profile.ptt_volume_multiplier, { type: "number", min: 0.05, max: 4, step: 0.05 }).field,
      roomFormControl("audio-aec-mode", "Echo cancellation", profile.aec_mode, { type: "select", choices: [{ value: "none", label: "None" }, { value: "hardware", label: "Provided by microphone hardware" }] }).field
    );
    processing.body.append(element("p", "audio-form-note", "Prefer microphone placement and hardware gain before software gain. The echo-cancellation setting records hardware capability; Home Suite does not synthesize a speaker reference path."));
    holder.append(processing.section);
  }

  function numericAudioValue(id, optional) {
    const raw = $("#" + id).value.trim();
    if (!raw && optional) return null;
    const value = Number(raw);
    if (!Number.isFinite(value)) throw new Error("Enter a valid number for " + titleFromKey(id.replace(/^audio-/, "")) + ".");
    return value;
  }

  function readAudioDraft() {
    const profile = Object.assign({}, state.audioDraft.profile, {
      name: $("#audio-profile-name").value.trim(),
      device_match: $("#audio-input-device").value.trim(),
      device_index: numericAudioValue("audio-device-index", true),
      sample_rate: numericAudioValue("audio-sample-rate"),
      channels: numericAudioValue("audio-channels"),
      stream_latency: $("#audio-stream-latency").value,
      strict_device_match: $("#audio-strict-match").checked,
      alsa_card: $("#audio-alsa-card").value || null,
      mixer_control: $("#audio-mixer-control").value || null,
      mixer_value: numericAudioValue("audio-mixer-value", true),
      verify_interval_sec: numericAudioValue("audio-verify-interval"),
      noise_suppression_level: numericAudioValue("audio-ww-ns"),
      command_noise_suppression_level: numericAudioValue("audio-command-ns"),
      auto_gain_dbfs: numericAudioValue("audio-ww-agc"),
      command_auto_gain_dbfs: numericAudioValue("audio-command-agc"),
      volume_multiplier: numericAudioValue("audio-ww-gain"),
      command_volume_multiplier: numericAudioValue("audio-command-gain"),
      ptt_volume_multiplier: numericAudioValue("audio-ptt-gain"),
      aec_mode: $("#audio-aec-mode").value
    });
    return { profile: profile, output_override: $("#audio-output-device").value || null };
  }

  function renderAudioSuggestionReview(pending) {
    const adjustment = pending.adjustment;
    const summary = $("#audio-suggestion-summary");
    summary.replaceChildren();
    summary.append(
      icon(adjustment.tone === "change" ? "sliders-horizontal" : "triangle-alert"),
      element("div", "", "")
    );
    const copy = summary.lastElementChild;
    copy.append(
      element("strong", "", adjustment.title || "Calibration found a setting to change"),
      element("p", "", adjustment.detail || "Review the suggested value before saving it.")
    );

    const holder = $("#audio-suggestion-change");
    holder.replaceChildren();
    const row = element("div", "config-review-row");
    const label = element("div");
    label.append(
      element("strong", "", adjustment.setting_label || titleFromKey(adjustment.setting)),
      element("code", "", adjustment.setting)
    );
    const values = element("div", "config-review-values");
    values.append(
      element("span", "", titleFromKey(String(pending.currentValue))),
      element("span", "review-arrow", "to"),
      element("span", "", titleFromKey(String(adjustment.suggested_value)))
    );
    row.append(label, values);
    holder.append(row);

    const apply = $("#apply-audio-suggestion");
    apply.disabled = false;
    $("span:last-child", apply).textContent = "Apply " + titleFromKey(String(adjustment.suggested_value));
    window.renderLucideIcons($("#audio-suggestion-dialog"));
  }

  async function reviewAudioSuggestion(adjustment, sourceButton) {
    if (!state.audioConfig || state.audioSuggestion) return;
    if (!audioDirectSuggestionSettings.has(adjustment.setting)
        || adjustment.suggested_value === null
        || adjustment.suggested_value === undefined) {
      openAudioEditor(audioSettingFieldIds[adjustment.setting]);
      return;
    }
    const currentValue = state.audioConfig.profile[adjustment.setting];
    if (String(currentValue) !== String(adjustment.current_value)) {
      showToast("This setting changed after calibration. Run calibration again before applying its suggestion.");
      return;
    }
    sourceButton.disabled = true;
    try {
      const draft = {
        profile: deepClone(state.audioConfig.profile),
        output_override: state.audioConfig.output_override
      };
      draft.profile[adjustment.setting] = adjustment.suggested_value;
      const preview = await api("/api/audio/preview", { method: "POST", body: draft });
      if (!preview.change_count) {
        showToast("That suggestion is already saved");
        return;
      }
      state.audioSuggestion = {
        adjustment: adjustment,
        currentValue: currentValue,
        draft: draft,
        preview: preview
      };
      renderAudioSuggestionReview(state.audioSuggestion);
      $("#audio-suggestion-dialog").showModal();
    } catch (error) {
      showToast(error.message);
    } finally {
      sourceButton.disabled = false;
    }
  }

  function closeAudioSuggestion() {
    const dialog = $("#audio-suggestion-dialog");
    if (dialog.open) dialog.close();
    state.audioSuggestion = null;
  }

  async function applyAudioSuggestion() {
    const pending = state.audioSuggestion;
    if (!pending) return;
    const button = $("#apply-audio-suggestion");
    button.disabled = true;
    try {
      const result = await api("/api/audio/apply", {
        method: "POST",
        body: {
          profile: pending.draft.profile,
          output_override: pending.draft.output_override,
          revision: pending.preview.revision
        }
      });
      if (result.applied) {
        pending.adjustment.applied = true;
        pending.adjustment.applied_value = pending.adjustment.suggested_value;
      }
      $("#audio-suggestion-dialog").close();
      state.audioSuggestion = null;
      await loadAudio();
      await loadServices().catch(function () {});
      const notice = $("#audio-result");
      notice.hidden = false;
      notice.replaceChildren(
        icon("circle-check"),
        element("span", "", result.applied
          ? (pending.adjustment.setting_label || "Audio setting") + " saved at "
            + titleFromKey(String(pending.adjustment.suggested_value))
            + ". Use Restart required, then rerun calibration."
          : "That suggestion was already saved.")
      );
      window.renderLucideIcons(notice);
      showToast(result.applied ? "Calibration suggestion saved" : "No audio settings changed");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function openAudioEditor(focusFieldId) {
    if (!state.audioConfig) return;
    state.audioDraft = {
      profile: deepClone(state.audioConfig.profile),
      output_override: state.audioConfig.output_override
    };
    state.audioPreview = null;
    $("#audio-edit-error").hidden = true;
    renderAudioEditForm();
    $("#audio-edit-dialog").showModal();
    if (typeof focusFieldId === "string" && focusFieldId) {
      window.setTimeout(function () {
        const control = $("#" + focusFieldId);
        if (!control) return;
        const section = control.closest("details");
        if (section) section.open = true;
        control.scrollIntoView({ block: "center", behavior: "smooth" });
        control.focus({ preventScroll: true });
      }, 0);
    }
  }

  function closeAudioEditor() {
    $("#audio-edit-dialog").close();
    state.audioDraft = null;
  }

  function renderAudioReview(preview) {
    const holder = $("#audio-review-list");
    holder.replaceChildren();
    (preview.changes || []).forEach(function (change) {
      const row = element("div", "config-review-row");
      const copy = element("div");
      copy.append(element("strong", "", change.label || titleFromKey(change.key)), element("code", "", change.key));
      const detail = element("div", "room-review-detail");
      detail.append(icon("chevron-right"), element("span", "", change.details || ((change.before || "Default") + " to " + (change.after || "Default"))));
      row.append(copy, detail);
      holder.append(row);
    });
    $("#audio-restart-notice").textContent = preview.change_count
      ? "Use Restart required after applying so voice capture and playback use the new setup. The console remains available."
      : "No audio settings changed.";
    $("#apply-audio-changes").disabled = !preview.change_count;
    window.renderLucideIcons(holder);
  }

  async function reviewAudioChanges(event) {
    event.preventDefault();
    const error = $("#audio-edit-error");
    error.hidden = true;
    try {
      state.audioDraft = readAudioDraft();
      const preview = await api("/api/audio/preview", { method: "POST", body: state.audioDraft });
      state.audioPreview = preview;
      renderAudioReview(preview);
      $("#audio-edit-dialog").close();
      $("#audio-review-dialog").showModal();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
    }
  }

  async function applyAudioChanges() {
    if (!state.audioDraft || !state.audioPreview) return;
    const button = $("#apply-audio-changes");
    button.disabled = true;
    try {
      const result = await api("/api/audio/apply", {
        method: "POST",
        body: {
          profile: state.audioDraft.profile,
          output_override: state.audioDraft.output_override,
          revision: state.audioPreview.revision
        }
      });
      $("#audio-review-dialog").close();
      state.audioDraft = null;
      state.audioPreview = null;
      await loadAudio();
      await loadServices().catch(function () {});
      const notice = $("#audio-result");
      notice.hidden = false;
      notice.replaceChildren(icon("circle-check"), element("span", "", result.applied ? "Audio settings saved. Use Restart required to activate the new profile." : "No audio settings changed."));
      window.renderLucideIcons(notice);
      showToast("Audio settings saved");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function testAudioOutput() {
    if (!state.audioConfig) return;
    const button = $("#test-audio-output");
    button.disabled = true;
    try {
      await api("/api/audio/test-output", {
        method: "POST",
        body: { device: state.audioConfig.output_effective }
      });
      showToast("Test cue played");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
      loadAudio().catch(function () {});
    }
  }

  function integrationRows() {
    const managed = state.integrations ? (state.integrations.integrations || []) : [];
    const managedIds = new Set(managed.map(function (row) { return row.id; }));
    const deployment = state.snapshot
      ? (state.snapshot.integrations || []).filter(function (row) { return !managedIds.has(row.id); })
      : [];
    return managed.concat(deployment);
  }

  function integrationStatusLabel(integration) {
    if (integration.status === "configured") return "Ready";
    if (integration.status === "partial") return "Incomplete";
    return "Not configured";
  }

  function integrationMeta(integration) {
    if (integration.scope !== "device") {
      if (integration.status === "configured") return "Configured in shared deployment settings";
      if (integration.status === "partial") return "Some shared deployment settings are still missing";
      return "Not configured in shared deployment settings";
    }
    const total = (integration.credential_keys || []).length;
    const configured = (integration.configured_fields || []).length;
    if (integration.status === "configured") {
      return total + " required setting" + (total === 1 ? "" : "s") + " configured";
    }
    if (integration.status === "partial") {
      return configured + " of " + total + " required settings configured";
    }
    return "Set up this integration when you want to use it";
  }

  function integrationDocumentation(integration) {
    let path = integration.docs_path;
    if (!path && integration.id === "calendar") path = "docs/INTEGRATIONS.md";
    if (!path && integration.id === "weather_astronomy") path = "docs/WEATHER.md";
    return documentationLink(path, "Guide");
  }

  function clearIntegrationTestDismissTimer(integrationId) {
    const timer = integrationTestDismissTimers.get(integrationId);
    if (timer !== undefined) window.clearTimeout(timer);
    integrationTestDismissTimers.delete(integrationId);
  }

  function clearIntegrationTestDismissTimers() {
    integrationTestDismissTimers.forEach(function (timer) { window.clearTimeout(timer); });
    integrationTestDismissTimers.clear();
  }

  function scheduleIntegrationTestDismiss(integrationId) {
    clearIntegrationTestDismissTimer(integrationId);
    const result = state.integrationTests[integrationId];
    if (!result || result.status !== "success") return;
    const timer = window.setTimeout(function () {
      integrationTestDismissTimers.delete(integrationId);
      if (state.integrationTests[integrationId] !== result) return;
      delete state.integrationTests[integrationId];
      renderIntegrations();
    }, INTEGRATION_TEST_SUCCESS_TTL_MS);
    integrationTestDismissTimers.set(integrationId, timer);
  }

  function renderIntegrations() {
    const holder = $("#integration-list");
    if (!state.snapshot && !state.integrations) return;
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    integrationRows().forEach(function (integration) {
      const card = element("article", "integration-card");
      card.dataset.integrationId = integration.id;
      const header = element("div", "integration-card-header");
      header.append(
        integrationProviderIcon(integration),
        element("h2", "", integration.label),
        badge(integration.status, integrationStatusLabel(integration))
      );
      card.append(
        header,
        element("p", "", integration.description),
        element("div", "integration-meta", integrationMeta(integration))
      );

      const actions = element("div", "integration-card-actions");
      if (integration.editable) {
        const manage = element("button", "button " + (integration.status === "not_configured" ? "primary" : "secondary"));
        manage.type = "button";
        manage.append(
          icon(integration.status === "not_configured" ? "plus" : "pencil"),
          element("span", "", integration.status === "not_configured" ? "Set up" : "Manage")
        );
        manage.addEventListener("click", function () { openIntegrationEditor(integration.id, manage); });
        actions.append(manage);
      }
      if (integration.test_supported) {
        const test = element("button", "button secondary");
        test.type = "button";
        test.disabled = integration.status !== "configured" || Boolean(state.integrationTestBusy);
        test.title = integration.status === "configured"
          ? "Test this connection"
          : "Configure all required settings before testing";
        test.append(icon("activity"), element("span", "", "Test"));
        test.addEventListener("click", function () { testIntegrationConnection(integration.id); });
        actions.append(test);
      }
      const docs = integrationDocumentation(integration);
      if (docs) actions.append(docs);
      if (actions.childElementCount) card.append(actions);

      const testState = state.integrationTests[integration.id];
      if (state.integrationTestBusy === integration.id) {
        const result = element("div", "integration-test-result busy");
        result.append(element("strong", "", "Testing connection"), element("span", "", "Waiting for a bounded read-only response..."));
        card.append(result);
      } else if (testState) {
        const result = element("div", "integration-test-result " + testState.status);
        result.append(element("strong", "", testState.summary), element("span", "", testState.detail));
        card.append(result);
      }
      holder.append(card);
    });
    window.renderLucideIcons(holder);
  }

  function integrationControlId(key) {
    return "integration-field-" + String(key || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  }

  function buildIntegrationControl(field) {
    const id = integrationControlId(field.key);
    let control;
    if (field.type === "list_integer" || field.type === "list_string" || field.type === "json_object") {
      control = element("textarea");
      control.rows = field.type === "json_object" ? 7 : 3;
      control.value = editorValue(field);
    } else if (field.type === "choice") {
      control = element("select");
      (field.choices || []).forEach(function (choice) {
        const option = element("option", "", choice.label);
        option.value = choice.value;
        control.append(option);
      });
      control.value = String(editorValue(field));
    } else {
      control = element("input");
      control.type = field.secret ? "password" : (field.type === "url" ? "url" : "text");
      control.value = editorValue(field);
      if (field.secret) control.autocomplete = "new-password";
    }
    control.id = id;
    control.dataset.integrationControl = field.key;
    control.placeholder = field.placeholder || (field.secret ? "Paste credential here" : "");
    if (field.required) control.required = true;
    if (control.tagName === "SELECT") return { holder: selectControl(control), control: control };
    if (!field.secret) return { holder: control, control: control };

    const wrapper = element("div", "secret-input");
    const reveal = element("button", "icon-button secret-reveal");
    reveal.type = "button";
    reveal.title = "Show credential";
    reveal.setAttribute("aria-label", "Show credential");
    reveal.setAttribute("aria-pressed", "false");
    reveal.append(icon("eye"));
    reveal.addEventListener("click", function () {
      const showing = control.type === "text";
      control.type = showing ? "password" : "text";
      const label = showing ? "Show credential" : "Hide credential";
      reveal.title = label;
      reveal.setAttribute("aria-label", label);
      reveal.setAttribute("aria-pressed", showing ? "false" : "true");
      reveal.replaceChildren(icon(showing ? "eye" : "eye-off"));
      window.renderLucideIcons(reveal);
    });
    wrapper.append(control, reveal);
    return { holder: wrapper, control: control };
  }

  function renderIntegrationEditForm() {
    if (!state.integrationConfig) return;
    const integration = state.integrationConfig.integration;
    const credentialKeys = new Set(integration.credential_keys || []);
    $("#integration-edit-dialog").classList.toggle("compact", (state.integrationConfig.fields || []).length <= 2);
    $("#integration-edit-title").textContent = integration.label;
    const holder = $("#integration-edit-fields");
    holder.replaceChildren();
    holder.append(element(
      "p",
      "integration-form-intro",
      "These values are stored in this device's private configuration. Existing credentials are shown only inside this authenticated session so they can be reviewed and maintained in one place."
    ));
    const grid = element("div", "integration-form-grid");
    (state.integrationConfig.fields || []).forEach(function (field) {
      const row = element("div", "integration-form-row");
      const copy = element("div", "integration-form-copy");
      const title = element("div", "integration-form-title");
      const label = element("label", "", field.label);
      label.htmlFor = integrationControlId(field.key);
      title.append(label, element("code", "", field.key));
      if (credentialKeys.has(field.key)) title.append(element("span", "required-mark", "Required for connection"));
      copy.append(title, element("p", "", field.description));
      if (field.help_text || field.docs_path) {
        const help = element("details", "config-field-help");
        help.append(element("summary", "", "How to configure"));
        if (field.help_text) help.append(element("p", "", field.help_text));
        const docs = documentationLink(field.docs_path);
        if (docs) help.append(docs);
        copy.append(help);
      }
      const controlHolder = element("div", "integration-form-control");
      const built = buildIntegrationControl(field);
      controlHolder.append(built.holder);
      if (field.can_clear) {
        controlHolder.append(element("span", "integration-field-note", "Clear this field to remove the saved value."));
      } else if (credentialKeys.has(field.key) && !field.required) {
        controlHolder.append(element("span", "integration-field-note", "Required only when this integration is enabled."));
      }
      row.append(copy, controlHolder);
      grid.append(row);
    });
    holder.append(grid);
    $("#integration-edit-error").hidden = true;
    window.renderLucideIcons(holder);
  }

  async function openIntegrationEditor(integrationId, sourceButton) {
    if (sourceButton) sourceButton.disabled = true;
    try {
      state.integrationConfig = await api("/api/integrations/edit-state", {
        method: "POST",
        body: { integration_id: integrationId }
      });
      state.integrationPreview = null;
      renderIntegrationEditForm();
      $("#integration-edit-dialog").showModal();
    } catch (error) {
      showToast(error.message);
    } finally {
      if (sourceButton) sourceButton.disabled = false;
    }
  }

  function redactIntegrationConfig() {
    if (!state.integrationConfig) return;
    (state.integrationConfig.fields || []).forEach(function (field) {
      if (field.secret) field.value = null;
    });
  }

  function closeIntegrationEditor() {
    const dialog = $("#integration-edit-dialog");
    if (dialog.open) dialog.close();
    redactIntegrationConfig();
    state.integrationConfig = null;
    state.integrationPreview = null;
  }

  function integrationValuePresent(value) {
    if (value === null || value === undefined) return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return String(value).trim() !== "";
  }

  function integrationChanges() {
    if (!state.integrationConfig) return [];
    const changes = [];
    (state.integrationConfig.fields || []).forEach(function (field) {
      const control = $("[data-integration-control='" + field.key + "']", $("#integration-edit-fields"));
      if (!control) return;
      const rawValue = field.type === "boolean" ? control.checked : control.value;
      if (configValuesEqual(field, rawValue)) return;
      const value = normalizedEditorValue(field, rawValue);
      if (!integrationValuePresent(value)) {
        if (field.required) throw new Error(field.label + " cannot be empty.");
        if (field.configured) changes.push({ key: field.key, action: "clear" });
        return;
      }
      changes.push({ key: field.key, action: "set", value: value });
    });
    return changes;
  }

  function renderIntegrationReview(preview) {
    const holder = $("#integration-review-list");
    holder.replaceChildren();
    (preview.changes || []).forEach(function (change) {
      const row = element("div", "config-review-row");
      const label = element("div");
      label.append(element("strong", "", change.label), element("code", "", change.key));
      const values = element("div", "config-review-values");
      values.append(
        element("span", "", change.before),
        element("span", "review-arrow", "to"),
        element("span", "", change.after)
      );
      row.append(label, values);
      holder.append(row);
    });
    $("#integration-review-context").textContent = state.integrationConfig.integration.label;
    $("#integration-restart-notice").textContent = preview.restart_services.length
      ? "The settings will be saved without interrupting Home Suite. Use Restart required when you are ready to activate them."
      : "These changes do not require a service restart.";
  }

  async function reviewIntegrationChanges(event) {
    event.preventDefault();
    const error = $("#integration-edit-error");
    error.hidden = true;
    let changes;
    try {
      changes = integrationChanges();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
      return;
    }
    if (!changes.length) {
      showToast("Those values already match the saved configuration");
      return;
    }
    const button = $("#review-integration-changes");
    button.disabled = true;
    try {
      const preview = await api("/api/config/preview", { method: "POST", body: { changes: changes } });
      if (!preview.change_count) {
        showToast("Those values already match the saved configuration");
        return;
      }
      state.integrationPreview = { preview: preview, changes: changes };
      renderIntegrationReview(preview);
      $("#integration-edit-dialog").close();
      $("#integration-review-dialog").showModal();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  function backToIntegrationEditor() {
    $("#integration-review-dialog").close();
    $("#integration-edit-dialog").showModal();
  }

  async function applyIntegrationChanges() {
    if (!state.integrationConfig || !state.integrationPreview) return;
    const pending = state.integrationPreview;
    const integration = state.integrationConfig.integration;
    const button = $("#apply-integration-changes");
    button.disabled = true;
    try {
      const result = await api("/api/config/apply", {
        method: "POST",
        body: { changes: pending.changes, revisions: pending.preview.revisions }
      });
      $("#integration-review-dialog").close();
      redactIntegrationConfig();
      state.integrationConfig = null;
      state.integrationPreview = null;
      clearIntegrationTestDismissTimer(integration.id);
      delete state.integrationTests[integration.id];
      await Promise.all([
        loadIntegrations(),
        loadServices().catch(function () {}),
        loadSnapshot().catch(function () {})
      ]);
      const notice = $("#integration-result");
      notice.hidden = false;
      notice.replaceChildren(
        icon("circle-check"),
        element("span", "", "Saved " + result.change_count + " setting" + (result.change_count === 1 ? "" : "s") + " for " + integration.label + ". Test the connection, then activate the saved settings when ready.")
      );
      window.renderLucideIcons(notice);
      showToast(integration.label + " settings saved");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function testIntegrationConnection(integrationId) {
    if (state.integrationTestBusy) return;
    clearIntegrationTestDismissTimer(integrationId);
    delete state.integrationTests[integrationId];
    state.integrationTestBusy = integrationId;
    renderIntegrations();
    try {
      const result = await api("/api/integrations/test", {
        method: "POST",
        body: { integration_id: integrationId }
      });
      state.integrationTests[integrationId] = result;
      showToast(result.summary);
    } catch (error) {
      state.integrationTests[integrationId] = {
        status: "failed",
        summary: "Connection test could not run",
        detail: error.message
      };
      showToast(error.message);
    } finally {
      state.integrationTestBusy = null;
      renderIntegrations();
      scheduleIntegrationTestDismiss(integrationId);
    }
  }

  function renderChatRooms() {
    if (!state.snapshot && !state.roomConfig) return;
    const select = $("#chat-room");
    const previous = select.value;
    select.replaceChildren();
    if (state.roomConfig) {
      Object.keys(state.roomConfig.rooms || {}).forEach(function (roomId) {
        const room = state.roomConfig.rooms[roomId] || {};
        const option = element("option", "", room.label || titleFromKey(roomId));
        option.value = roomId;
        option.selected = roomId === (previous || state.roomConfig.default_room);
        select.append(option);
      });
    } else {
      state.snapshot.rooms.forEach(function (room) {
        const option = element("option", "", room.label);
        option.value = room.id;
        option.selected = room.id === (previous || state.snapshot.overview.default_room);
        select.append(option);
      });
    }
  }

  function renderDiagnostics() {
    if (!state.diagnostics) return;
    const report = state.diagnostics;
    const summary = $("#diagnostic-summary");
    const warnings = report.checks.filter(function (check) { return check.status === "WARN"; }).length;
    const failures = report.checks.filter(function (check) { return check.status === "FAIL" && check.required; }).length;
    const visualStatus = report.ok ? (warnings ? "warn" : "ok") : "fail";
    summary.className = "diagnostic-summary " + visualStatus;
    summary.replaceChildren();
    summary.append(icon(visualStatus === "ok" ? "circle-check" : "triangle-alert"));
    const copy = element("div", "diagnostic-summary-copy");
    copy.append(
      element("strong", "", report.ok
        ? (warnings ? "Required checks passed" : "All checks passed")
        : failures + " required check" + (failures === 1 ? "" : "s") + " failed"),
      element("span", "", (warnings
        ? warnings + " warning" + (warnings === 1 ? " needs" : "s need") + " review"
        : "No warnings") + " · " + (report.live ? "Live and local checks" : "Local checks"))
    );
    summary.append(copy);
    if (warnings || failures) {
      const review = element("button", "button secondary diagnostic-summary-action");
      review.type = "button";
      review.append(icon("chevron-down"), element("span", "", failures ? "Review issues" : "Review warning" + (warnings === 1 ? "" : "s")));
      review.addEventListener("click", function () {
        const target = $(".diagnostic-row.fail, .diagnostic-row.warn", $("#diagnostic-groups"));
        if (target) target.scrollIntoView({ block: "center", behavior: "smooth" });
      });
      summary.append(review);
    }

    const groups = {};
    report.checks.forEach(function (check) { (groups[check.group] || (groups[check.group] = [])).push(check); });
    const holder = $("#diagnostic-groups");
    holder.classList.remove("loading-block");
    holder.replaceChildren();
    const priority = { FAIL: 0, WARN: 1, OK: 2, SKIP: 3 };
    const checkPriority = function (status) {
      return Object.prototype.hasOwnProperty.call(priority, status) ? priority[status] : 4;
    };
    Object.keys(groups).sort(function (left, right) {
      const leftPriority = Math.min.apply(null, groups[left].map(function (check) { return checkPriority(check.status); }));
      const rightPriority = Math.min.apply(null, groups[right].map(function (check) { return checkPriority(check.status); }));
      return leftPriority - rightPriority;
    }).forEach(function (name) {
      const section = element("section", "diagnostic-group");
      section.append(element("h2", "", name));
      groups[name].slice().sort(function (left, right) {
        return checkPriority(left.status) - checkPriority(right.status);
      }).forEach(function (check) {
        const row = element("div", "diagnostic-row");
        row.classList.add(statusClass(check.status));
        const copy = element("div", "diagnostic-copy");
        copy.append(element("p", "", check.detail || (check.required ? "Required" : "Optional")));
        if (check.action && check.action.guidance) {
          copy.append(element("span", "diagnostic-guidance", check.action.guidance));
        }
        row.append(badge(check.status), element("strong", "", check.label), copy);
        if (check.action && check.action.view) {
          const action = element("button", "button secondary diagnostic-action");
          action.type = "button";
          action.append(icon("arrow-right"), element("span", "", check.action.label || "Open"));
          action.addEventListener("click", function () { openDiagnosticAction(check.action); });
          row.append(action);
        }
        section.append(row);
      });
      holder.append(section);
    });
    window.renderLucideIcons($("#view-diagnostics"));
    setHeaderStatus(
      report.ok ? (warnings ? "WARN" : "OK") : "FAIL",
      report.ok
        ? (warnings ? warnings + " warning" + (warnings === 1 ? "" : "s") : "System ready")
        : failures + " issue" + (failures === 1 ? "" : "s") + " need attention",
      warnings || failures ? String(warnings || failures) : ""
    );
    renderOverview();
  }

  function openDiagnosticAction(action) {
    navigate(action.view);
    if (action.view !== "integrations" || !action.target) return;
    window.requestAnimationFrame(function () {
      const card = $("[data-integration-id='" + action.target + "']", $("#integration-list"));
      if (!card) return;
      card.scrollIntoView({ block: "center", behavior: "smooth" });
      card.classList.add("diagnostic-target");
      window.setTimeout(function () { card.classList.remove("diagnostic-target"); }, 1800);
    });
  }

  async function downloadSupportBundle() {
    const button = $("#download-support-bundle");
    button.disabled = true;
    const includeLive = Boolean(state.diagnostics && state.diagnostics.live);
    try {
      const response = await fetch("/api/support-bundle?live=" + (includeLive ? "1" : "0"), {
        credentials: "same-origin",
        cache: "no-store"
      });
      if (response.status === 401) {
        showLogin();
        throw new Error("Your console session ended. Sign in again.");
      }
      if (!response.ok) {
        let message = "The support bundle could not be generated.";
        try {
          const payload = await response.json();
          if (payload && payload.error) message = String(payload.error);
        } catch (_error) { /* use the safe fallback */ }
        throw new Error(message);
      }
      const disposition = String(response.headers.get("Content-Disposition") || "");
      const match = disposition.match(/filename="([A-Za-z0-9._-]+)"/);
      const filename = match ? match[1] : "homesuite-support.tar.gz";
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = element("a");
      link.href = url;
      link.download = filename;
      link.hidden = true;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(function () { window.URL.revokeObjectURL(url); }, 1000);
      showToast("Redacted support bundle downloaded");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function renderMessage(message) {
    const holder = element("article", "message " + message.role + (message.pending ? " pending" : "") + (message.error ? " error" : ""));
    holder.append(element("div", "message-bubble", message.text));
    if (message.meta) holder.append(element("div", "message-meta", message.meta));
    return holder;
  }

  function renderMessages() {
    const holder = $("#chat-messages");
    holder.replaceChildren();
    if (!state.messages.length) {
      holder.append(element("div", "chat-empty", "No messages in this session."));
      return;
    }
    state.messages.forEach(function (message) { holder.append(renderMessage(message)); });
    holder.scrollTop = holder.scrollHeight;
  }

  function setMode(mode) {
    state.mode = mode;
    $$(".segmented-control button").forEach(function (button) { button.classList.toggle("active", button.dataset.mode === mode); });
    const notice = $("#mode-notice");
    notice.className = "mode-notice " + mode;
    notice.replaceChildren(icon(mode === "live" ? "triangle-alert" : "shield-check"));
    notice.append(element("span", "", mode === "live"
      ? "Live. Commands can control devices and create persistent actions."
      : "Preview only. Device writes and persistent actions are blocked."));
  }

  async function sendMessage(text) {
    if (state.chatBusy) return;
    state.chatBusy = true;
    $("#send-message").disabled = true;
    state.messages.push({ role: "user", text: text, meta: state.mode === "live" ? "Live" : "Test preview" });
    const pending = { role: "assistant", text: "Working...", pending: true, meta: state.mode === "live" ? "Live" : "Test preview" };
    state.messages.push(pending);
    renderMessages();
    try {
      const result = await api("/api/command", {
        method: "POST",
        body: {
          text: text,
          mode: state.mode,
          room: $("#chat-room").value || null,
          session_id: state.sessionId
        }
      });
      pending.pending = false;
      pending.text = result.response || (result.source === "cancelled" ? "Dismissed." : "No spoken response.");
      const parts = [result.mode === "live" ? "Live" : "Test preview"];
      if (result.source) parts.push(result.source.replace(/_/g, " "));
      if (result.elapsed_ms !== undefined) parts.push(result.elapsed_ms + " ms");
      pending.meta = parts.join(" · ");
      rememberSetupTested();
      renderSetup();
    } catch (error) {
      pending.pending = false;
      pending.error = true;
      pending.text = error.message;
      pending.meta = state.mode === "live" ? "Live request failed" : "Test request failed";
    } finally {
      state.chatBusy = false;
      $("#send-message").disabled = false;
      renderMessages();
      $("#chat-input").focus();
    }
  }

  async function loadSnapshot() {
    const data = await api("/api/snapshot");
    state.snapshot = data;
    renderOverview();
    renderRooms();
    renderIntegrations();
    renderChatRooms();
    renderSetup();
  }

  async function loadSetup() {
    const data = await api("/api/setup");
    state.setup = data;
    renderSetup();
    return data;
  }

  async function loadIntegrations() {
    const data = await api("/api/integrations");
    state.integrations = data;
    renderIntegrations();
    renderSetup();
    return data;
  }

  async function loadRoomConfig() {
    const data = await api("/api/rooms");
    state.roomConfig = data;
    state.roomDraft = deepClone(data.rooms || {});
    state.roomLoadError = null;
    state.roomPreview = null;
    renderRooms();
    renderChatRooms();
    renderSetup();
  }

  async function loadAudio() {
    const data = await api("/api/audio");
    state.audioConfig = data;
    state.audioLoadError = null;
    renderAudio();
    renderSetup();
  }

  async function loadDiagnostics(live) {
    const buttons = [$("#run-local-diagnostics"), $("#run-live-diagnostics")];
    buttons.forEach(function (button) { button.disabled = true; });
    setHeaderStatus("SKIP", live ? "Running live checks" : "Running checks", "");
    try {
      const data = await api("/api/diagnostics?live=" + (live ? "1" : "0"));
      state.diagnostics = data.report;
      renderDiagnostics();
      renderSetup();
    } finally {
      buttons.forEach(function (button) { button.disabled = false; });
    }
  }

  async function refreshAll(notify) {
    const button = $("#refresh-overview");
    button.disabled = true;
    try {
      await Promise.all([
        loadSetup(),
        loadSnapshot(),
        loadIntegrations().catch(function () {
          state.integrations = null;
          renderIntegrations();
        }),
        loadDiagnostics(false),
        loadEditableConfig().catch(function () { state.editableConfig = null; }),
        loadAudio().catch(function (error) {
          state.audioConfig = null;
          state.audioLoadError = error.message;
        }),
        loadServices().catch(function () {
          state.services = null;
          renderServiceRestartAction();
        }),
        loadRoomConfig().catch(function (error) {
          state.roomConfig = null;
          state.roomDraft = null;
          state.roomLoadError = error.message;
        })
      ]);
      renderConfiguration();
      renderRooms();
      renderSetup();
      if (notify) showToast("Overview refreshed");
    } catch (error) {
      showToast(error.message);
      setHeaderStatus("FAIL", "Unable to refresh", "Error");
    } finally {
      button.disabled = false;
    }
  }

  function navigate(view) {
    if (view !== "audio" && state.audioCalibration.token) {
      releaseAudioCalibration("navigation", true);
    }
    if (state.setupJourneyActive && view !== "setup" && view !== state.setupJourneyView) {
      setSetupJourney(false);
    }
    $$(".view").forEach(function (section) { section.classList.toggle("active", section.id === "view-" + view); });
    $$(".nav-item[data-view]").forEach(function (button) { button.classList.toggle("active", button.dataset.view === view); });
    $$("[data-actions-view]").forEach(function (group) {
      group.hidden = group.dataset.actionsView !== view;
    });
    const section = $("#view-" + view);
    $("#topbar-title").textContent = section ? section.dataset.title : "Home Suite";
    if (view === "setup") {
      setSetupJourney(false);
      renderSetup();
    } else {
      updateSetupNavigation();
    }
    closeSidebar();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function openSidebar() {
    $("#sidebar").classList.add("open");
    $("#sidebar-scrim").hidden = false;
  }

  function closeSidebar() {
    $("#sidebar").classList.remove("open");
    $("#sidebar-scrim").hidden = true;
  }

  function bindEvents() {
    $("#bootstrap-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      const passphrase = $("#bootstrap-passphrase").value;
      const confirmation = $("#bootstrap-confirmation").value;
      const error = $("#bootstrap-error");
      error.hidden = true;
      try {
        await api("/api/bootstrap", {
          method: "POST",
          body: { passphrase: passphrase, confirmation: confirmation }
        });
        $("#bootstrap-passphrase").value = "";
        $("#bootstrap-confirmation").value = "";
        state.bootstrapRequired = false;
        showApp();
        await refreshAll();
        navigate("setup");
      } catch (exception) {
        error.textContent = exception.message;
        error.hidden = false;
      }
    });

    $("#login-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      const key = $("#console-key").value;
      const error = $("#login-error");
      error.hidden = true;
      try {
        await api("/api/login", { method: "POST", body: { key: key } });
        $("#console-key").value = "";
        showApp();
        await refreshAll();
        if (!resumeSetupJourney() && (!state.setup || !state.setup.complete)) navigate("setup");
      } catch (exception) {
        error.textContent = exception.message === "invalid_key" ? "That passphrase was not accepted." : exception.message;
        error.hidden = false;
      }
    });

    $("#logout-button").addEventListener("click", async function () {
      if (state.audioCalibration.token) await releaseAudioCalibration("logout", true);
      try { await api("/api/logout", { method: "POST" }); } catch (_error) { /* session may already be gone */ }
      state.mode = "test";
      state.messages = [];
      state.editableConfig = null;
      state.configEditing = false;
      state.configDraft = {};
      state.configPreview = null;
      state.configClientError = null;
      state.roomConfig = null;
      state.roomDraft = null;
      state.roomLoadError = null;
      state.roomCatalog = null;
      state.roomCatalogPromise = null;
      state.roomPreview = null;
      state.roomEditing = null;
      state.audioConfig = null;
      state.audioDraft = null;
      state.audioPreview = null;
      state.audioLoadError = null;
      state.audioSuggestion = null;
      redactIntegrationConfig();
      state.integrations = null;
      state.integrationConfig = null;
      state.integrationPreview = null;
      clearIntegrationTestDismissTimers();
      state.integrationTests = {};
      state.integrationTestBusy = null;
      state.services = null;
      state.restartBusy = null;
      state.setup = null;
      state.setupPreview = false;
      state.setupPreviewRole = "wakeword";
      state.setupActivationBusy = false;
      setSetupJourney(false);
      state.audioCalibration = { token: null, stage: "idle", noise: null, speech: null, busy: false, error: null };
      renderServiceRestartAction();
      showConfigurationEditMode(false);
      showLogin(false);
    });
    $$(".nav-item[data-view]").forEach(function (button) { button.addEventListener("click", function () { navigate(button.dataset.view); }); });
    $("#review-setup").addEventListener("click", function () {
      state.setupPreview = false;
      navigate("setup");
    });
    $("#return-to-setup").addEventListener("click", function () {
      navigate("setup");
      refreshAll(false).catch(function (error) { showToast(error.message); });
    });
    $("#exit-setup-journey").addEventListener("click", function () {
      setSetupJourney(false);
    });
    $("#preview-onboarding").addEventListener("click", function () {
      state.setupPreview = true;
      state.setupPreviewRole = $("#setup-preview-role").value;
      $("#setup-result").hidden = true;
      renderSetup();
    });
    $("#exit-onboarding-preview").addEventListener("click", function () {
      state.setupPreview = false;
      renderSetup();
    });
    $("#setup-preview-role").addEventListener("change", function (event) {
      state.setupPreviewRole = event.currentTarget.value;
      renderSetup();
    });
    $("#header-status").addEventListener("click", function () { navigate("diagnostics"); });
    $("#open-menu").addEventListener("click", openSidebar);
    $("#close-menu").addEventListener("click", closeSidebar);
    $("#sidebar-scrim").addEventListener("click", closeSidebar);
    $("#refresh-overview").addEventListener("click", function () { refreshAll(true); });
    $("#refresh-audio").addEventListener("click", function () {
      const button = $("#refresh-audio");
      button.disabled = true;
      loadAudio().then(function () { showToast("Audio hardware refreshed"); }).catch(function (error) { showToast(error.message); }).finally(function () { button.disabled = false; });
    });
    $("#edit-audio").addEventListener("click", function () { openAudioEditor(); });
    $("#test-audio-output").addEventListener("click", testAudioOutput);
    $("#audio-edit-form").addEventListener("submit", reviewAudioChanges);
    $("#close-audio-editor").addEventListener("click", closeAudioEditor);
    $("#cancel-audio-editor").addEventListener("click", closeAudioEditor);
    $("#close-audio-review").addEventListener("click", function () { $("#audio-review-dialog").close(); });
    $("#back-to-audio").addEventListener("click", function () {
      $("#audio-review-dialog").close();
      renderAudioEditForm();
      $("#audio-edit-dialog").showModal();
    });
    $("#apply-audio-changes").addEventListener("click", applyAudioChanges);
    $("#close-audio-suggestion").addEventListener("click", closeAudioSuggestion);
    $("#cancel-audio-suggestion").addEventListener("click", closeAudioSuggestion);
    $("#apply-audio-suggestion").addEventListener("click", applyAudioSuggestion);
    $("#audio-suggestion-dialog").addEventListener("close", function () {
      state.audioSuggestion = null;
    });
    $("#open-service-restart").addEventListener("click", openServiceRestartDialog);
    $("#close-service-restart").addEventListener("click", closeServiceRestartDialog);
    $("#done-service-restart").addEventListener("click", closeServiceRestartDialog);
    $("#edit-configuration").addEventListener("click", beginConfigurationEdit);
    $("#config-search").addEventListener("input", applyConfigFilter);
    $("#config-search").addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !event.currentTarget.value) return;
      event.currentTarget.value = "";
      applyConfigFilter();
    });
    $("#config-section-jump").addEventListener("change", jumpToConfigSection);
    $("#refresh-integrations").addEventListener("click", function () {
      const button = $("#refresh-integrations");
      button.disabled = true;
      loadIntegrations().then(function () {
        showToast("Integrations refreshed");
      }).catch(function (error) {
        showToast(error.message);
      }).finally(function () {
        button.disabled = false;
      });
    });
    $("#integration-edit-form").addEventListener("submit", reviewIntegrationChanges);
    $("#close-integration-editor").addEventListener("click", closeIntegrationEditor);
    $("#cancel-integration-editor").addEventListener("click", closeIntegrationEditor);
    $("#integration-edit-dialog").addEventListener("cancel", function (event) {
      event.preventDefault();
      closeIntegrationEditor();
    });
    $("#close-integration-review").addEventListener("click", backToIntegrationEditor);
    $("#back-to-integration").addEventListener("click", backToIntegrationEditor);
    $("#integration-review-dialog").addEventListener("cancel", function (event) {
      event.preventDefault();
      backToIntegrationEditor();
    });
    $("#apply-integration-changes").addEventListener("click", applyIntegrationChanges);
    $("#cancel-configuration-edit").addEventListener("click", cancelConfigurationEdit);
    $("#configuration-editor").addEventListener("submit", function (event) {
      event.preventDefault();
      reviewConfigurationChanges();
    });
    $("#close-config-review").addEventListener("click", function () { $("#config-review-dialog").close(); });
    $("#back-to-config").addEventListener("click", function () { $("#config-review-dialog").close(); });
    $("#apply-config-changes").addEventListener("click", applyConfigurationChanges);
    $("#add-room").addEventListener("click", function () { openRoomEditor(null, "add"); });
    $("#discard-room-changes").addEventListener("click", discardRoomChanges);
    $("#review-room-changes").addEventListener("click", reviewRoomChanges);
    $("#room-edit-form").addEventListener("submit", saveRoomDraft);
    $("#close-room-editor").addEventListener("click", closeRoomEditor);
    $("#cancel-room-editor").addEventListener("click", closeRoomEditor);
    $("#close-room-review").addEventListener("click", function () { $("#room-review-dialog").close(); });
    $("#back-to-rooms").addEventListener("click", function () { $("#room-review-dialog").close(); });
    $("#apply-room-changes").addEventListener("click", applyRoomChanges);
    $("#run-local-diagnostics").addEventListener("click", function () { loadDiagnostics(false).catch(function (error) { showToast(error.message); }); });
    $("#run-live-diagnostics").addEventListener("click", function () { loadDiagnostics(true).catch(function (error) { showToast(error.message); }); });
    $("#download-support-bundle").addEventListener("click", downloadSupportBundle);
    $$(".segmented-control button").forEach(function (button) {
      button.addEventListener("click", function () {
        const target = button.dataset.mode;
        if (target === state.mode) return;
        if (target === "live" && !window.confirm("Switch to Live? Messages can control real devices and create persistent actions.")) return;
        setMode(target);
      });
    });
    $("#clear-chat").addEventListener("click", function () { state.messages = []; renderMessages(); });
    $("#chat-form").addEventListener("submit", function (event) {
      event.preventDefault();
      const input = $("#chat-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendMessage(text);
    });
    $("#chat-input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $("#chat-form").requestSubmit();
      }
    });
  }

  async function boot() {
    window.renderLucideIcons(document);
    state.setupTested = getSetupTested();
    bindEvents();
    setMode("test");
    try {
      const session = await api("/api/session");
      if (!session.authenticated) {
        showLogin(session.bootstrap_required);
        return;
      }
      showApp();
      await refreshAll();
      if (!resumeSetupJourney() && (!state.setup || !state.setup.complete)) navigate("setup");
    } catch (error) {
      if (!$("#login-screen").hidden) return;
      showToast(error.message);
    }
  }

  window.addEventListener("pagehide", function () {
    const token = state.audioCalibration.token;
    if (!token || !navigator.sendBeacon) return;
    const body = new Blob([JSON.stringify({ token: token, reason: "page_closed" })], { type: "application/json" });
    navigator.sendBeacon("/api/audio/calibration/release", body);
  });

  boot();
})();
