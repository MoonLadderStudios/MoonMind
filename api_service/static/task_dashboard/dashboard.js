(() => {
  function describeWorkerPauseState(system = {}, metrics = {}) {
    const workersPaused = Boolean(system.workersPaused);
    const mode = (system.mode || "").toString().toLowerCase();
    const state = workersPaused
      ? mode === "quiesce"
        ? "quiesce"
        : "paused"
      : "running";
    const label = workersPaused
      ? `Workers: Paused (${mode === "quiesce" ? "Quiesce" : "Drain"})`
      : "Workers: Running";
    let reasonText = "Workers are accepting new jobs.";
    if (workersPaused) {
      reasonText = system.reason || "Paused without operator reason.";
    } else if (system.reason) {
      reasonText = `Last action: ${system.reason}`;
    }
    return {
      label,
      reason: reasonText,
      state,
      drained: metrics.isDrained ? "Yes" : "No",
    };
  }

  function requiresResumeConfirmation(snapshot) {
    return Boolean(
      snapshot &&
      snapshot.metrics &&
      Object.prototype.hasOwnProperty.call(snapshot.metrics, "isDrained") &&
      !snapshot.metrics.isDrained,
    );
  }

  function createWorkerPauseTransport(workerPauseSettings) {
    if (
      !workerPauseSettings ||
      typeof workerPauseSettings.get !== "string" ||
      typeof workerPauseSettings.post !== "string"
    ) {
      return null;
    }

    const pollInterval = Math.max(
      1000,
      Number(workerPauseSettings.pollIntervalMs) || 5000,
    );
    const getEndpoint = workerPauseSettings.get;
    const postEndpoint = workerPauseSettings.post;

    async function parseJsonBody(response, contextLabel) {
      try {
        return await response.json();
      } catch (error) {
        console.warn(`${contextLabel} response was not valid JSON`, {
          status: response.status,
          name: errorNameForLog(error),
        });
        return null;
      }
    }

    function messageFromDetail(detail) {
      if (!detail) {
        return "";
      }
      if (typeof detail === "string") {
        return detail;
      }
      if (typeof detail === "object" && typeof detail.message === "string") {
        return detail.message;
      }
      return "";
    }

    async function fetchState() {
      const response = await fetch(getEndpoint, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      const responseBody = await parseJsonBody(response, "worker pause status");
      if (!response.ok) {
        const detailMessage = messageFromDetail(responseBody && responseBody.detail).trim();
        if (detailMessage && (response.status === 401 || response.status === 403)) {
          throw new Error(detailMessage);
        }
        throw new Error("Unable to load worker pause status.");
      }
      return responseBody || {};
    }

    async function submitAction(payload) {
      const response = await fetch(postEndpoint, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      const responseBody = await parseJsonBody(response, "worker pause update");
      if (!response.ok) {
        throw new Error(response.statusText || "Failed to update worker pause state.");
      }
      return responseBody || {};
    }

    return {
      pollInterval,
      fetchState,
      submitAction,
    };
  }

  if (typeof window !== "undefined") {
    window.__workerPauseTest = {
      describeWorkerPauseState,
      requiresResumeConfirmation,
    };
  }

  const configNode = document.getElementById("task-dashboard-config");
  const root = document.getElementById("dashboard-content");
  if (!configNode || !root) {
    return;
  }

  const config = JSON.parse(configNode.textContent || "{}");
  const pollIntervals = config.pollIntervalsMs || {
    list: 5000,
    detail: 2000,
    events: 1000,
  };
  const sourceConfig = config.sources || {};
  const queueSourceConfig =
    sourceConfig.queue && typeof sourceConfig.queue === "object" ? sourceConfig.queue : {};
  const proposalsSourceConfig =
    sourceConfig.proposals && typeof sourceConfig.proposals === "object"
      ? sourceConfig.proposals
      : {};
  const runtimeCapabilitiesEndpoint =
    String(queueSourceConfig.runtimeCapabilities || "");
  const runtimeCapabilitiesCacheTtlMs = 5 * 60 * 1000;
  const runtimeCapabilitiesCache = {
    payload: null,
    expiresAtMs: 0,
    inFlight: null,
  };
  const DASHBOARD_DETAIL_SEGMENT_PATTERN = /^(?:mm:)?[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  const manifestsSourceConfig =
    sourceConfig.manifests && typeof sourceConfig.manifests === "object"
      ? sourceConfig.manifests
      : {};
  const schedulesSourceConfig =
    sourceConfig.schedules && typeof sourceConfig.schedules === "object"
      ? sourceConfig.schedules
      : {};
  const temporalSourceConfig =
    sourceConfig.temporal && typeof sourceConfig.temporal === "object"
      ? sourceConfig.temporal
      : {};
  const featuresConfig =
    config.features && typeof config.features === "object" && !Array.isArray(config.features)
      ? config.features
      : {};
  const temporalDashboardFeature =
    featuresConfig.temporalDashboard &&
      typeof featuresConfig.temporalDashboard === "object" &&
      !Array.isArray(featuresConfig.temporalDashboard)
      ? featuresConfig.temporalDashboard
      : {};
  const temporalDashboardEnabled = Boolean(temporalDashboardFeature.enabled);
  const temporalListEnabled =
    temporalDashboardEnabled && Boolean(temporalDashboardFeature.listEnabled);
  const temporalDetailEnabled =
    temporalDashboardEnabled && Boolean(temporalDashboardFeature.detailEnabled);
  const temporalActionsEnabled =
    temporalDashboardEnabled && Boolean(temporalDashboardFeature.actionsEnabled);
  const temporalSubmitEnabled =
    temporalDashboardEnabled && Boolean(temporalDashboardFeature.submitEnabled);
  const temporalDebugFieldsEnabled =
    temporalDashboardEnabled && Boolean(temporalDashboardFeature.debugFieldsEnabled);

  const TEMPORAL_INLINE_INPUT_MAX_CHARS = 4000;
  const systemConfig = config.system || {};
  const supportedWorkerRuntimes =
    Array.isArray(systemConfig.supportedWorkerRuntimes) &&
      systemConfig.supportedWorkerRuntimes.length > 0
      ? systemConfig.supportedWorkerRuntimes
      : ["codex", "gemini_cli", "claude", "jules", "universal"];

  function normalizeRuntimeIdentifier(value) {
    return String(value || "").trim().toLowerCase();
  }

  function normalizeTaskRuntimeList(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    const seen = new Set();
    const normalized = [];
    values.forEach((value) => {
      const candidate = normalizeRuntimeIdentifier(value);
      if (!candidate || seen.has(candidate)) {
        return;
      }
      seen.add(candidate);
      normalized.push(candidate);
    });
    return normalized;
  }

  const configuredTaskRuntimes =
    Array.isArray(systemConfig.supportedTaskRuntimes) &&
      systemConfig.supportedTaskRuntimes.length > 0
      ? normalizeTaskRuntimeList(systemConfig.supportedTaskRuntimes)
      : [];
  const inferredTaskRuntimes = normalizeTaskRuntimeList(
    supportedWorkerRuntimes.filter(
      (runtime) => normalizeRuntimeIdentifier(runtime) !== "universal",
    ),
  );
  const supportedTaskRuntimes =
    configuredTaskRuntimes.length > 0
      ? configuredTaskRuntimes
      : inferredTaskRuntimes.length > 0
        ? inferredTaskRuntimes
        : ["codex", "gemini_cli", "claude", "jules"];
  const normalizedDefaultTaskRuntime = normalizeRuntimeIdentifier(
    systemConfig.defaultTaskRuntime,
  );
  const defaultTaskRuntime =
    (supportedTaskRuntimes.includes(normalizedDefaultTaskRuntime)
      ? normalizedDefaultTaskRuntime
      : null) ||
    (supportedTaskRuntimes.includes("codex")
      ? "codex"
      : supportedTaskRuntimes[0] || "codex");
  const configuredModelDefaults =
    systemConfig.defaultTaskModelByRuntime &&
      typeof systemConfig.defaultTaskModelByRuntime === "object" &&
      !Array.isArray(systemConfig.defaultTaskModelByRuntime)
      ? systemConfig.defaultTaskModelByRuntime
      : {};
  const configuredEffortDefaults =
    systemConfig.defaultTaskEffortByRuntime &&
      typeof systemConfig.defaultTaskEffortByRuntime === "object" &&
      !Array.isArray(systemConfig.defaultTaskEffortByRuntime)
      ? systemConfig.defaultTaskEffortByRuntime
      : {};
  function resolveRuntimeDefault(defaultsByRuntime, runtime) {
    const runtimeKey = String(runtime || "").trim().toLowerCase();
    if (!runtimeKey) {
      return "";
    }
    const value = defaultsByRuntime[runtimeKey];
    return value ? String(value).trim() : "";
  }
  function normalizeRuntimeOptions(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    const seen = new Set();
    const normalized = [];
    values.forEach((value) => {
      const candidate = String(value || "").trim();
      if (!candidate || seen.has(candidate)) {
        return;
      }
      seen.add(candidate);
      normalized.push(candidate);
    });
    return normalized;
  }

  const JULES_RUNTIME = "jules";
  const CLICK_GLOW_CLASS = "is-clicked";
  const CLICK_GLOW_DURATION_MS = 180;
  const CLICK_GLOW_SELECTOR = [
    "button:not(.secondary):not(.queue-action):not(.queue-submit-primary):not(.queue-step-icon-button)",
    ".button:not(.secondary):not(.queue-action):not(.queue-submit-primary)",
    ".queue-action",
    ".queue-submit-primary",
    ".queue-step-icon-button",
  ].join(", ");
  const clickGlowTimers = new WeakMap();

  const listSubmitRuntimes = () => Array.from(new Set([...supportedTaskRuntimes]));

  /** Maps task submit runtime (dashboard) to managed_agent_provider_profiles.runtime_id. */
  const mapTaskRuntimeToAuthProfileRuntimeId = (mode) => {
    const runtimeMap = {
      gemini: "gemini_cli",
      gemini_cli: "gemini_cli",
      claude: "claude_code",
      codex: "codex_cli",
    };
    const m = String(mode || "").trim().toLowerCase();
    return runtimeMap[m] || "";
  };

  const TASK_RUNTIME_LABELS = {
    codex: "Codex CLI",
    codex_cli: "Codex CLI",
    gemini: "Gemini CLI",
    gemini_cli: "Gemini CLI",
    claude: "Claude Code",
    claude_code: "Claude Code",
    cursor_cli: "Cursor CLI",
    [JULES_RUNTIME]: "Jules",
  };

  const buildSubmitRuntimeOptions = (workerRuntimes = []) =>
    normalizeRuntimeOptions(workerRuntimes);

  const submitRuntimeOptions = buildSubmitRuntimeOptions(supportedTaskRuntimes);

  function extractTemporalActionExecution(payload) {
    if (!payload || typeof payload !== "object") {
      return null;
    }
    const executionField = "execution";
    const candidate = payload[executionField];
    return candidate && typeof candidate === "object" ? candidate : null;
  }

  function describeTemporalCompatibilityFreshness(payload) {
    const refreshField = "refresh";
    const staleField = "staleState";
    const refreshedAtField = "refreshedAt";
    const degradedCountField = "degradedCount";
    const refresh =
      payload &&
        typeof payload === "object" &&
        payload[refreshField] &&
        typeof payload[refreshField] === "object"
        ? payload[refreshField]
        : null;
    return {
      stale: Boolean(
        (payload && typeof payload === "object" && payload[staleField]) ||
        (refresh && refresh.listStale),
      ),
      refetchSuggested: Boolean(refresh && refresh.refetchSuggested),
      refreshedAt:
        (payload && typeof payload === "object" && payload[refreshedAtField]) ||
        (refresh && refresh.refreshedAt) ||
        null,
      degradedCount: Boolean(
        payload && typeof payload === "object" && payload[degradedCountField],
      ),
    };
  }

  if (typeof window !== "undefined") {
    window.__taskDashboardTemporalCompatibilityTest = {
      describeTemporalCompatibilityFreshness,
      extractTemporalActionExecution,
    };
  }

  const formatRuntimeLabel = (runtimeValue) => {
    const normalized = String(runtimeValue || "").trim().toLowerCase();
    if (!normalized) {
      return "";
    }

    if (TASK_RUNTIME_LABELS[normalized]) {
      return TASK_RUNTIME_LABELS[normalized];
    }

    const titleCased = normalized
      .replace(/[^a-z0-9]+/g, " ")
      .trim()
      .split(" ")
      .map((part) =>
        part
          ? part.charAt(0).toUpperCase() + part.slice(1)
          : "",
      )
      .join(" ");

    return titleCased || normalized;
  };

  const renderRuntimeOptions = (options, selectedRuntime) => {
    const selected = String(selectedRuntime || "").trim();
    return options
      .map((runtime) => {
        const runtimeValue = String(runtime || "").trim();
        if (!runtimeValue) {
          return "";
        }
        const label = formatRuntimeLabel(runtimeValue);
        if (!label) {
          return "";
        }
        return `<option value="${escapeHtml(runtimeValue)}" ${runtimeValue === selected ? "selected" : ""
          }>${escapeHtml(label)}</option>`;
      })
      .join("");
  };
  const isRuntimeCapabilitiesCacheFresh = () =>
    runtimeCapabilitiesCache.payload !== null &&
    runtimeCapabilitiesCache.expiresAtMs > Date.now();
  const parseRuntimeCapabilitiesResponse = (payload) => {
    const rawItems = payload && typeof payload === "object" ? payload.items : null;
    if (!rawItems || typeof rawItems !== "object") {
      return {};
    }
    const normalizedItems = {};
    Object.entries(rawItems).forEach(([runtime, entry]) => {
      const runtimeKey = normalizeTaskRuntimeInput(runtime);
      if (!runtimeKey) {
        return;
      }
      const rawModels = entry && typeof entry === "object" ? entry.models : null;
      const rawEfforts = entry && typeof entry === "object" ? entry.efforts : null;
      normalizedItems[runtimeKey] = {
        models: normalizeRuntimeOptions(rawModels),
        efforts: normalizeRuntimeOptions(rawEfforts),
      };
    });
    return normalizedItems;
  };
  const loadRuntimeCapabilitiesFromEndpoint = async () => {
    if (!runtimeCapabilitiesEndpoint) {
      return {};
    }
    const priorPayload = runtimeCapabilitiesCache.payload;
    const stale = !isRuntimeCapabilitiesCacheFresh();
    if (!stale && priorPayload !== null) {
      return priorPayload;
    }
    if (runtimeCapabilitiesCache.inFlight) {
      return runtimeCapabilitiesCache.inFlight;
    }
    runtimeCapabilitiesCache.inFlight = (async () => {
      try {
        const payload = await fetchJson(runtimeCapabilitiesEndpoint);
        const normalizedPayload = parseRuntimeCapabilitiesResponse(payload);
        runtimeCapabilitiesCache.payload = normalizedPayload;
        runtimeCapabilitiesCache.expiresAtMs = Date.now() + runtimeCapabilitiesCacheTtlMs;
        return normalizedPayload;
      } catch (_error) {
        if (priorPayload !== null) {
          return priorPayload;
        }
        return {};
      } finally {
        runtimeCapabilitiesCache.inFlight = null;
      }
    })();
    return runtimeCapabilitiesCache.inFlight;
  };
  const codexDefaultTaskModel =
    resolveRuntimeDefault(configuredModelDefaults, "codex") ||
    String(systemConfig.defaultTaskModel || "").trim();
  const codexDefaultTaskEffort =
    resolveRuntimeDefault(configuredEffortDefaults, "codex") ||
    String(systemConfig.defaultTaskEffort || "").trim();
  const defaultTaskModel = resolveRuntimeDefault(
    { ...configuredModelDefaults, codex: codexDefaultTaskModel },
    defaultTaskRuntime,
  );
  const defaultTaskEffort = resolveRuntimeDefault(
    { ...configuredEffortDefaults, codex: codexDefaultTaskEffort },
    defaultTaskRuntime,
  );
  const defaultRepository = String(systemConfig.defaultRepository || "").trim();
  const defaultPublishMode =
    normalizePublishModeInput(systemConfig.defaultPublishMode) || "pr";
  const defaultProposeTasks = Object.prototype.hasOwnProperty.call(
    systemConfig,
    "defaultProposeTasks",
  )
    ? Boolean(systemConfig.defaultProposeTasks)
    : false;
  const attachmentPolicyConfig =
    systemConfig.attachmentPolicy &&
      typeof systemConfig.attachmentPolicy === "object" &&
      !Array.isArray(systemConfig.attachmentPolicy)
      ? systemConfig.attachmentPolicy
      : {};
  const parseAttachmentPolicyInt = (rawValue, fallback) => {
    const parsed = Number(rawValue);
    if (Number.isFinite(parsed)) {
      return Math.max(1, Math.trunc(parsed));
    }
    return fallback;
  };
  const parseAllowedContentTypes = () => {
    if (!Array.isArray(attachmentPolicyConfig.allowedContentTypes)) {
      return ["image/png", "image/jpeg", "image/webp"];
    }
    const normalized = attachmentPolicyConfig.allowedContentTypes
      .map((value) => String(value || "").trim().toLowerCase())
      .filter((value) => Boolean(value));
    return normalized.length > 0
      ? normalized
      : ["image/png", "image/jpeg", "image/webp"];
  };
  const attachmentPolicy = {
    enabled: Object.prototype.hasOwnProperty.call(attachmentPolicyConfig, "enabled")
      ? Boolean(attachmentPolicyConfig.enabled)
      : false,
    maxCount: parseAttachmentPolicyInt(
      attachmentPolicyConfig.maxCount,
      10,
    ),
    maxBytes: parseAttachmentPolicyInt(
      attachmentPolicyConfig.maxBytes,
      10 * 1024 * 1024,
    ),
    totalBytes: Math.max(
      1,
      parseAttachmentPolicyInt(
        attachmentPolicyConfig.totalBytes,
        25 * 1024 * 1024,
      ),
    ),
    allowedContentTypes: parseAllowedContentTypes(),
  };
  const ownerRepoPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const taskTemplateCatalogConfig =
    systemConfig.taskTemplateCatalog &&
      typeof systemConfig.taskTemplateCatalog === "object" &&
      !Array.isArray(systemConfig.taskTemplateCatalog)
      ? systemConfig.taskTemplateCatalog
      : {};
  const taskTemplateCatalogEnabled = Boolean(taskTemplateCatalogConfig.enabled);
  const taskTemplateSaveEnabled = Boolean(taskTemplateCatalogConfig.templateSaveEnabled);
  const workerPauseConfig =
    systemConfig.workerPause &&
      typeof systemConfig.workerPause === "object" &&
      !Array.isArray(systemConfig.workerPause)
      ? systemConfig.workerPause
      : null;
  const workerPauseTransport = createWorkerPauseTransport(workerPauseConfig);
  const providerProfileEndpoints =
    systemConfig.providerProfiles &&
      typeof systemConfig.providerProfiles === "object" &&
      !Array.isArray(systemConfig.providerProfiles)
      ? {
        list: String(systemConfig.providerProfiles.list || "/api/v1/provider-profiles"),
        create: String(systemConfig.providerProfiles.create || "/api/v1/provider-profiles"),
        detail: String(systemConfig.providerProfiles.detail || "/api/v1/provider-profiles/{profileId}"),
        update: String(systemConfig.providerProfiles.update || "/api/v1/provider-profiles/{profileId}"),
        delete: String(systemConfig.providerProfiles.delete || "/api/v1/provider-profiles/{profileId}"),
      }
      : null;
  const taskTemplateEndpoints = {
    list: String(queueSourceConfig.taskStepTemplates || "/api/task-step-templates"),
    detail: String(
      queueSourceConfig.taskStepTemplateDetail || "/api/task-step-templates/{slug}",
    ),
    expand: String(
      queueSourceConfig.taskStepTemplateExpand || "/api/task-step-templates/{slug}:expand",
    ),
    save: String(
      queueSourceConfig.taskStepTemplateSave || "/api/task-step-templates/save-from-task",
    ),
    favorite: String(
      queueSourceConfig.taskStepTemplateFavorite || "/api/task-step-templates/{slug}:favorite",
    ),
  };
  const THEME_STORAGE_KEY = "moonmind.theme";
  const THEME_DARK_CLASS = "dark";
  const THEME_MEDIA_QUERY = "(prefers-color-scheme: dark)";

  const TASK_LIST_TITLE_MAX_CHARS = 400;
  const ACTIVE_QUEUE_FETCH_LIMIT = 50;
  const ACTIVE_TEMPORAL_FETCH_LIMIT = 50;
  const QUEUE_PAGE_SIZE_OPTIONS = [20, 25, 50, 100];
  const DEFAULT_QUEUE_PAGE_SIZE = 50;
  const pollers = [];
  const disposers = [];
  const persistentPollers = [];
  const persistentDisposers = [];
  let cachedAvailableSkillIds = null;
  const AUTO_REFRESH_STORAGE_KEY = "moonmind.tasks.autoRefresh";
  const autoRefreshChangeListeners = new Set();

  function errorNameForLog(error) {
    if (!error || typeof error !== "object") {
      return "unknown";
    }
    const name = error.name;
    return name ? String(name) : "unknown";
  }

  function readStoredAutoRefreshPreference() {
    try {
      const raw = window.localStorage
        ? window.localStorage.getItem(AUTO_REFRESH_STORAGE_KEY)
        : null;
      if (raw === "paused") {
        return true;
      }
      if (raw && raw !== "active" && window.localStorage) {
        window.localStorage.removeItem(AUTO_REFRESH_STORAGE_KEY);
      }
    } catch (error) {
      console.warn("auto refresh preference read failed", {
        name: errorNameForLog(error),
      });
    }
    return false;
  }

  function persistAutoRefreshPreference(paused) {
    try {
      if (!window.localStorage) {
        return;
      }
      if (paused) {
        window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, "paused");
      } else {
        window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, "active");
      }
    } catch (error) {
      console.warn("auto refresh preference persist failed", {
        name: errorNameForLog(error),
      });
    }
  }

  let autoRefreshPaused = readStoredAutoRefreshPreference();

  function isAutoRefreshActive() {
    return !autoRefreshPaused;
  }

  function notifyAutoRefreshListeners() {
    const enabled = isAutoRefreshActive();
    autoRefreshChangeListeners.forEach((listener) => {
      try {
        listener(enabled);
      } catch (error) {
        console.error("auto refresh listener failed", {
          name: errorNameForLog(error),
        });
      }
    });
  }

  function onAutoRefreshChange(listener) {
    if (typeof listener !== "function") {
      console.warn("onAutoRefreshChange requires a function listener");
      return () => { };
    }
    autoRefreshChangeListeners.add(listener);
    return () => {
      autoRefreshChangeListeners.delete(listener);
    };
  }

  function setAutoRefreshPaused(nextPaused) {
    const normalized = Boolean(nextPaused);
    if (normalized === autoRefreshPaused) {
      return;
    }
    autoRefreshPaused = normalized;
    persistAutoRefreshPreference(normalized);
    syncAutoRefreshControls();
    notifyAutoRefreshListeners();
  }

  function renderAutoRefreshControls() {
    return `
      <div class="toolbar-controls">
        <label class="queue-inline-toggle toolbar-live-toggle">
          <input type="checkbox" data-auto-refresh-toggle ${isAutoRefreshActive() ? "checked" : ""
      } aria-pressed="${isAutoRefreshActive() ? "true" : "false"}" />
          Live updates
        </label>
        <span class="small" data-auto-refresh-status>${isAutoRefreshActive() ? "" : "Updates paused to keep selections stable."
      }</span>
      </div>
    `;
  }

  function syncAutoRefreshControls() {
    const toggleNodes = root.querySelectorAll("[data-auto-refresh-toggle]");
    toggleNodes.forEach((node) => {
      if (node instanceof HTMLInputElement) {
        node.checked = isAutoRefreshActive();
        node.setAttribute(
          "aria-label",
          isAutoRefreshActive() ? "Disable live updates" : "Enable live updates",
        );
        node.setAttribute("aria-pressed", isAutoRefreshActive() ? "true" : "false");
      }
    });
    const statusNodes = root.querySelectorAll("[data-auto-refresh-status]");
    statusNodes.forEach((node) => {
      node.textContent = isAutoRefreshActive()
        ? ""
        : "Updates paused to keep selections stable.";
    });
  }

  function bindAutoRefreshControls() {
    const toggleNodes = root.querySelectorAll("[data-auto-refresh-toggle]");
    toggleNodes.forEach((node) => {
      if (node instanceof HTMLInputElement) {
        node.addEventListener("change", () => {
          setAutoRefreshPaused(!node.checked);
        });
      }
    });
  }

  function stopPolling() {
    while (pollers.length > 0) {
      clearInterval(pollers.pop());
    }
    while (disposers.length > 0) {
      const dispose = disposers.pop();
      if (typeof dispose === "function") {
        try {
          dispose();
        } catch (error) {
          console.error("polling disposer failed", error);
        }
      }
    }
  }

  function stopPersistentPolling() {
    while (persistentPollers.length > 0) {
      clearInterval(persistentPollers.pop());
    }
    while (persistentDisposers.length > 0) {
      const dispose = persistentDisposers.pop();
      if (typeof dispose === "function") {
        try {
          dispose();
        } catch (error) {
          console.error("persistent polling disposer failed", error);
        }
      }
    }
  }

  function startPolling(task, intervalMs, options = {}) {
    const runImmediately = options.runImmediately !== false;
    const skipAutoRefresh = options.skipAutoRefresh === true;
    const persistent = options.persistent === true;
    let inFlight = null;
    let rerunRequested = false;
    let disposed = false;

    registerDisposer(() => {
      disposed = true;
      rerunRequested = false;
    }, { persistent });

    const run = (forced = false) => {
      if (disposed) {
        return;
      }
      if (!forced) {
        if (!skipAutoRefresh) {
          if (!isAutoRefreshActive()) {
            return;
          }
          if (window.getSelection && window.getSelection().toString().trim().length > 0) {
            return;
          }
        }
        if (document.visibilityState === "hidden") {
          return;
        }
      }
      if (inFlight) {
        rerunRequested = true;
        return;
      }
      inFlight = Promise.resolve()
        .then(() => task())
        .catch((error) => {
          console.error("polling task failed", error);
        })
        .finally(() => {
          inFlight = null;
          if (!rerunRequested || disposed) {
            rerunRequested = false;
            return;
          }
          rerunRequested = false;
          run(false);
        });
    };

    if (runImmediately) {
      run(true);
    }
    const timer = window.setInterval(() => run(false), intervalMs);
    (persistent ? persistentPollers : pollers).push(timer);
    if (!skipAutoRefresh) {
      const disposeAutoRefreshListener = onAutoRefreshChange((enabled) => {
        if (enabled) {
          run(true);
        }
      });
      registerDisposer(() => disposeAutoRefreshListener(), { persistent });
    }
  }

  function registerDisposer(disposer, options = {}) {
    if (typeof disposer !== "function") {
      return;
    }
    if (options.persistent === true) {
      persistentDisposers.push(disposer);
      return;
    }
    disposers.push(disposer);
  }

  function initWorkerPauseBanner(workerPauseTransport) {
    if (!workerPauseTransport) {
      return null;
    }
    const section = document.querySelector("[data-worker-pause]");
    if (!section) {
      return null;
    }

    const statusNode = section.querySelector("[data-worker-pause-status]");
    const reasonNode = section.querySelector("[data-worker-pause-reason]");
    let latestSnapshot = null;

    function render(snapshot) {
      const system = snapshot.system || {};
      const metrics = snapshot.metrics || {};
      const description = describeWorkerPauseState(system, metrics);
      const isPaused = Boolean(system.workersPaused);
      section.dataset.state = description.state;
      if (!isPaused) {
        section.hidden = true;
        if (statusNode) {
          statusNode.textContent = "Workers: Running";
        }
        if (reasonNode) {
          reasonNode.textContent = "";
        }
        return;
      }
      section.hidden = false;
      if (statusNode) {
        if (description.label.startsWith("Workers:")) {
          const normalized = description.label.replace("Workers:", "Workers").trim();
          statusNode.textContent = `⚠️ ${normalized}`;
        } else {
          statusNode.textContent = `⚠️ ${description.label}`;
        }
      }
      if (reasonNode) {
        const rawReason = system.reason || description.reason || "";
        reasonNode.textContent = rawReason ? `- "${rawReason}"` : "";
      }
      latestSnapshot = snapshot;
    }

    async function refresh() {
      try {
        const snapshot = await workerPauseTransport.fetchState();
        render(snapshot);
      } catch (error) {
        console.error("worker pause banner refresh failed", error);
      }
    }

    return {
      pollInterval: workerPauseTransport.pollInterval,
      refresh,
      getLatestSnapshot() {
        return latestSnapshot;
      },
    };
  }

  function readStoredThemePreference() {
    try {
      const raw = window.localStorage ? window.localStorage.getItem(THEME_STORAGE_KEY) : null;
      if (raw === "dark" || raw === "light") {
        return raw;
      }
      if (raw && window.localStorage) {
        window.localStorage.removeItem(THEME_STORAGE_KEY);
      }
    } catch (_error) {
      // Ignore storage access failures and fall back to system preference.
    }
    return null;
  }

  function persistThemePreference(mode) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(THEME_STORAGE_KEY, mode);
      }
    } catch (_error) {
      // Ignore storage failures and keep in-memory mode.
    }
  }

  function getSystemThemeMode(mediaQueryList = null) {
    if (mediaQueryList && typeof mediaQueryList.matches === "boolean") {
      return mediaQueryList.matches ? "dark" : "light";
    }
    if (window.matchMedia) {
      return window.matchMedia(THEME_MEDIA_QUERY).matches ? "dark" : "light";
    }
    return "light";
  }

  function syncThemeToggle(mode, source) {
    const toggle = document.querySelector(".theme-toggle");
    if (!toggle) {
      return;
    }
    const titleCaseMode = mode === "dark" ? "Dark" : "Light";
    toggle.textContent = `Theme: ${titleCaseMode}`;
    toggle.setAttribute("aria-label", mode === "dark" ? "Switch to light mode" : "Switch to dark mode");
    toggle.setAttribute("aria-pressed", mode === "dark" ? "true" : "false");
    toggle.dataset.themeSource = source;
  }

  function applyResolvedTheme(mode, source) {
    const resolvedMode = mode === "dark" ? "dark" : "light";
    const documentRoot = document.documentElement;
    documentRoot.classList.toggle(THEME_DARK_CLASS, resolvedMode === "dark");
    documentRoot.dataset.theme = resolvedMode;
    documentRoot.dataset.themeSource = source;
    syncThemeToggle(resolvedMode, source);
    return resolvedMode;
  }

  function initTheme() {
    const mediaQueryList = window.matchMedia ? window.matchMedia(THEME_MEDIA_QUERY) : null;
    let storedPreference = readStoredThemePreference();
    let currentMode = applyResolvedTheme(
      storedPreference || getSystemThemeMode(mediaQueryList),
      storedPreference ? "user" : "system",
    );

    const toggle = document.querySelector(".theme-toggle");
    const handleToggleClick = () => {
      const nextMode = currentMode === "dark" ? "light" : "dark";
      storedPreference = nextMode;
      persistThemePreference(nextMode);
      currentMode = applyResolvedTheme(nextMode, "user");
    };

    if (toggle) {
      toggle.addEventListener("click", handleToggleClick);
    }

    const handleSystemPreferenceChange = (event) => {
      if (storedPreference) {
        return;
      }
      currentMode = applyResolvedTheme(event.matches ? "dark" : "light", "system");
    };

    if (mediaQueryList && typeof mediaQueryList.addEventListener === "function") {
      mediaQueryList.addEventListener("change", handleSystemPreferenceChange);
    }

    return () => {
      if (toggle) {
        toggle.removeEventListener("click", handleToggleClick);
      }
      if (mediaQueryList && typeof mediaQueryList.removeEventListener === "function") {
        mediaQueryList.removeEventListener("change", handleSystemPreferenceChange);
      }
    };
  }

  function triggerClickGlow(node) {
    if (!(node instanceof HTMLElement)) {
      return;
    }

    const existingTimer = clickGlowTimers.get(node);
    if (existingTimer) {
      window.clearTimeout(existingTimer);
    }

    node.classList.remove(CLICK_GLOW_CLASS);
    // Force style recalculation so repeated clicks retrigger the effect.
    void node.offsetWidth;
    node.classList.add(CLICK_GLOW_CLASS);

    const timer = window.setTimeout(() => {
      node.classList.remove(CLICK_GLOW_CLASS);
      clickGlowTimers.delete(node);
    }, CLICK_GLOW_DURATION_MS);
    clickGlowTimers.set(node, timer);
  }

  function initButtonClickGlow() {
    if (typeof document.addEventListener !== "function") {
      return;
    }

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const glowTarget = target.closest(CLICK_GLOW_SELECTOR);
      if (!(glowTarget instanceof HTMLElement)) {
        return;
      }
      if (glowTarget.hasAttribute("disabled") || glowTarget.getAttribute("aria-disabled") === "true") {
        return;
      }
      triggerClickGlow(glowTarget);
    });
  }

  function activateNav(pathname) {
    const activePath =
      pathname === "/tasks/queue/new" ||
        pathname === "/tasks/create"
        ? "/tasks/create"
        : pathname === "/tasks/queue" || pathname === "/tasks/list"
          ? "/tasks/list"
          : pathname;
    const links = document.querySelectorAll("a[data-nav]");
    links.forEach((link) => {
      const href = link.getAttribute("href") || "";
      if (href === activePath) {
        link.classList.add("active");
      } else {
        link.classList.remove("active");
      }
    });
  }

  function syncTemporalNavVisibility() {
    const temporalLinks = document.querySelectorAll("[data-temporal-nav]");
    temporalLinks.forEach((link) => {
      if (temporalListEnabled) {
        link.removeAttribute("hidden");
      } else {
        link.setAttribute("hidden", "hidden");
      }
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function pick(obj, ...keys) {
    for (const key of keys) {
      if (obj && Object.prototype.hasOwnProperty.call(obj, key)) {
        return obj[key];
      }
    }
    return undefined;
  }

  function normalizeDashboardDetailSegment(value) {
    const text = String(value ?? "").trim();
    if (!text || text === "." || text === ".." || text.toLowerCase() === "new") {
      return "";
    }
    if (!DASHBOARD_DETAIL_SEGMENT_PATTERN.test(text)) {
      return "";
    }
    return text;
  }

  function resolvePromotedQueueRoute(response) {
    const job = pick(response, "job");
    const rawJobId =
      job && typeof job === "object" && !Array.isArray(job)
        ? pick(job, "id", "jobId")
        : pick(response, "jobId");
    const safeJobId = normalizeDashboardDetailSegment(rawJobId);
    if (!safeJobId) {
      return "/tasks/list?source=temporal";
    }
    return `/tasks/${safeJobId}`;
  }

  function buildUnifiedTaskDetailRoute(rawId, source) {
    const safeId = normalizeDashboardDetailSegment(rawId);
    const normalizedSource = String(source || "").trim().toLowerCase();
    const sourceParam =
      normalizedSource === "temporal"
        ? `?source=${encodeURIComponent(normalizedSource)}`
        : "";
    if (!safeId) {
      return "/tasks/list";
    }
    return `/tasks/${safeId}${sourceParam}`;
  }


  function temporalWaitingReason(execution) {
    const waitingReason = String(pick(execution, "waitingReason") || "").trim();
    if (waitingReason) {
      return waitingReason;
    }

    const state = String(pick(execution, "state") || "").trim().toLowerCase();
    if (state !== "awaiting_external") {
      return "";
    }

    const memo = pick(execution, "memo") || {};
    const memoWaitingReason = String(pick(memo, "waitingReason") || "").trim();
    if (memoWaitingReason) {
      return memoWaitingReason;
    }

    return String(pick(memo, "summary") || "").trim();
  }

  function formatTimestamp(value) {
    if (!value) {
      return "-";
    }

    try {
      return new Date(value).toLocaleString();
    } catch (_error) {
      return String(value);
    }
  }

  function sanitizeCssToken(value, fallback = "") {
    const token = String(value ?? "")
      .toLowerCase()
      .replaceAll(/[^a-z0-9_-]/g, "");
    return token || fallback;
  }

  function extractRuntimeFromPayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }

    const directRuntime = pick(payload, "targetRuntime", "target_runtime", "runtime");
    if (directRuntime) {
      return String(directRuntime);
    }

    const task = pick(payload, "task");
    if (task && typeof task === "object" && !Array.isArray(task)) {
      const runtimeNode = pick(task, "runtime");
      if (runtimeNode && typeof runtimeNode === "object" && !Array.isArray(runtimeNode)) {
        const runtimeMode = pick(runtimeNode, "mode");
        if (runtimeMode) {
          return String(runtimeMode);
        }
      }
      const taskRuntime = pick(task, "targetRuntime", "target_runtime", "runtime");
      if (taskRuntime) {
        return String(taskRuntime);
      }
    }

    return null;
  }

  function extractTaskNode(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    const task = pick(payload, "task");
    if (!task || typeof task !== "object" || Array.isArray(task)) {
      return null;
    }
    return task;
  }

  function extractObject(payload, key) {
    const node = payload && typeof payload === "object" && !Array.isArray(payload) ? pick(payload, key) : null;
    return node && typeof node === "object" && !Array.isArray(node) ? node : null;
  }

  function extractRuntimeValueFromPayload(payload, fieldName) {
    const task = extractTaskNode(payload);
    const taskRuntimeNode = extractObject(task, "runtime");
    const taskCodexNode = extractObject(task, "codex");
    const payloadCodexNode = extractObject(payload, "codex");
    const payloadInputsCodexNode = extractObject(extractObject(payload, "inputs"), "codex");

    const candidateNodes = [
      taskRuntimeNode,
      taskCodexNode,
      payloadCodexNode,
      payloadInputsCodexNode,
      payload,
    ];

    for (const node of candidateNodes) {
      const normalized = String(pick(node, fieldName) ?? "").trim();
      if (normalized) {
        return normalized;
      }
    }
    return null;
  }

  function extractRuntimeModelFromPayload(payload) {
    return extractRuntimeValueFromPayload(payload, "model");
  }

  function extractRuntimeEffortFromPayload(payload) {
    return extractRuntimeValueFromPayload(payload, "effort");
  }

  function extractSkillFromPayload(payload) {
    const task = extractTaskNode(payload);
    if (!task) {
      return null;
    }
    const toolNode = pick(task, "tool");
    if (toolNode && typeof toolNode === "object" && !Array.isArray(toolNode)) {
      const toolName = String(pick(toolNode, "name") || pick(toolNode, "id") || "").trim();
      if (toolName) {
        return toolName;
      }
    }
    const skillNode = pick(task, "skill");
    if (skillNode && typeof skillNode === "object" && !Array.isArray(skillNode)) {
      const skillId = String(
        pick(skillNode, "name") || pick(skillNode, "id") || "",
      ).trim();
      return skillId || null;
    }
    return null;
  }

  function extractPublishModeFromPayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    const task = extractTaskNode(payload);
    if (task) {
      const publishNode = pick(task, "publish");
      if (publishNode && typeof publishNode === "object" && !Array.isArray(publishNode)) {
        const mode = pick(publishNode, "mode");
        if (mode) {
          return String(mode).toLowerCase();
        }
      }
    }
    const publishNode = pick(payload, "publish");
    if (publishNode && typeof publishNode === "object" && !Array.isArray(publishNode)) {
      const mode = pick(publishNode, "mode");
      if (mode) {
        return String(mode).toLowerCase();
      }
    }
    const legacyMode = pick(payload, "publishMode");
    return legacyMode ? String(legacyMode).toLowerCase() : null;
  }

  function renderRuntime(runtime) {
    const raw = String(runtime || "").trim();
    if (!raw) {
      return "-";
    }
    const label = formatRuntimeLabel(raw);
    return escapeHtml(label || raw);
  }

  function deriveStageFromEvent(event) {
    const payload = pick(event, "payload");
    const payloadStage =
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? String(pick(payload, "stage") || "").trim()
        : "";
    const message = String(pick(event, "message") || "").trim();
    const candidate = payloadStage || message;
    if (candidate.startsWith("moonmind.task.prepare")) {
      return "prepare";
    }
    if (candidate.startsWith("moonmind.task.execute")) {
      return "execute";
    }
    if (candidate.startsWith("moonmind.task.publish")) {
      return "publish";
    }
    if (candidate.startsWith("task.git.")) {
      return "git";
    }
    return "-";
  }

  function deriveStageFromArtifactName(name) {
    const candidate = String(name || "");
    if (candidate.includes("prepare.log")) {
      return "prepare";
    }
    if (candidate.includes("execute.log") || candidate.includes("codex_exec.log")) {
      return "execute";
    }
    if (candidate.includes("publish.log") || candidate.includes("publish_result")) {
      return "publish";
    }
    if (candidate.includes("task_context")) {
      return "prepare";
    }
    if (candidate.includes("changes.patch")) {
      return "execute";
    }
    return "-";
  }

  function getEventPayload(event) {
    const payload = pick(event, "payload");
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    return payload;
  }

  function isLogEvent(event) {
    const payload = getEventPayload(event);
    return Boolean(payload && pick(payload, "kind") === "log");
  }

  function eventLevel(event) {
    return String(pick(event, "level") || "info").trim().toLowerCase();
  }

  function eventMatchesOutputFilter(event, filter) {
    const normalizedFilter = String(filter || "all").trim().toLowerCase();
    if (normalizedFilter === "all") {
      return true;
    }
    if (normalizedFilter === "stages") {
      return !isLogEvent(event);
    }
    if (normalizedFilter === "logs") {
      return isLogEvent(event);
    }
    if (normalizedFilter === "warnings") {
      const level = eventLevel(event);
      if (level === "warn" || level === "warning" || level === "error") {
        return true;
      }
      const payload = getEventPayload(event);
      return Boolean(payload && String(pick(payload, "stream") || "") === "stderr");
    }
    return true;
  }

  function formatLiveOutputLine(event) {
    const timestamp = formatTimestamp(pick(event, "createdAt"));
    const level = eventLevel(event);
    const message = String(pick(event, "message") || "").replaceAll("\r", "");
    const payload = getEventPayload(event);
    if (payload && pick(payload, "kind") === "log") {
      const stream = String(pick(payload, "stream") || "stdout").trim();
      const stage = String(pick(payload, "stage") || deriveStageFromEvent(event)).trim();
      return `[${timestamp}] [${stream}] [${stage}] ${message}`;
    }
    const stage = deriveStageFromEvent(event);
    return `[${timestamp}] [${level}] [${stage}] ${message}`;
  }

  function buildLiveOutput(events, filter) {
    return events
      .filter((event) => eventMatchesOutputFilter(event, filter))
      .map((event) => formatLiveOutputLine(event))
      .join("\n");
  }

  function normalizeTaskRuntimeInput(value) {
    const normalized = normalizeRuntimeIdentifier(value);
    if (!normalized) {
      return "";
    }
    return supportedTaskRuntimes.includes(normalized) ? normalized : "";
  }

  function normalizePublishModeInput(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) {
      return "";
    }
    return ["none", "branch", "pr"].includes(normalized) ? normalized : "";
  }

  function isValidRepositoryInput(value) {
    const candidate = String(value || "").trim();
    if (!candidate) {
      return false;
    }
    if (ownerRepoPattern.test(candidate)) {
      return true;
    }
    if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
      try {
        const parsed = new URL(candidate);
        if (!parsed.hostname || parsed.pathname === "/" || !parsed.pathname) {
          return false;
        }
        return !parsed.username && !parsed.password;
      } catch (_error) {
        return false;
      }
    }
    return candidate.startsWith("git@");
  }

  function parseCapabilitiesCsv(value) {
    const parts = String(value || "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    return Array.from(new Set(parts));
  }

  function stringifySkillArgs(args) {
    if (!args || typeof args !== "object" || Array.isArray(args)) {
      return "";
    }
    const keys = Object.keys(args);
    if (keys.length === 0) {
      return "";
    }

    // Custom replacer to handle circular references
    const cache = new Set();
    const replacer = (key, value) => {
      if (typeof value === 'object' && value !== null) {
        if (cache.has(value)) {
          return '[Circular]';
        }
        cache.add(value);
      }
      return value;
    };

    try {
      return JSON.stringify(args, replacer, 2);
    } catch (error) {
      console.warn("Failed to format skill args for dashboard draft", error);
      return "[unserializable skill args]";
    }
  }

  function extractCapabilityCsv(value) {
    if (!Array.isArray(value)) {
      return "";
    }
    return normalizeRuntimeOptions(value).join(", ");
  }

  function buildQueueSubmissionDraftFromJob(job) {
    const jobPayload = pick(job, "payload");
    const payload =
      jobPayload && typeof jobPayload === "object" && !Array.isArray(jobPayload)
        ? jobPayload
        : {};
    const task =
      payload && typeof payload.task === "object" && !Array.isArray(payload.task)
        ? payload.task
        : {};
    const publishNode =
      task && typeof task.publish === "object" && !Array.isArray(task.publish)
        ? task.publish
        : {};
    const hasProposeTasks = Object.prototype.hasOwnProperty.call(
      task,
      "proposeTasks",
    );
    const runtime = extractRuntimeFromPayload(payload);
    const runtimeNode =
      task && typeof task.runtime === "object" && !Array.isArray(task.runtime)
        ? task.runtime
        : {};
    const gitNode =
      task && typeof task.git === "object" && !Array.isArray(task.git) ? task.git : {};
    const toolNode =
      task && typeof task.tool === "object" && !Array.isArray(task.tool)
        ? task.tool
        : {};
    const skillNode =
      Object.keys(toolNode).length > 0
        ? toolNode
        : (
          task && typeof task.skill === "object" && !Array.isArray(task.skill)
            ? task.skill
            : {}
        );

    const taskSteps = Array.isArray(task.steps) ? task.steps : [];
    let objectiveInstructions = String(task.instructions || "").trim();
    if (!objectiveInstructions && taskSteps.length > 0) {
      objectiveInstructions = String(pick(taskSteps[0], "instructions") || "").trim();
    }
    const firstStep = taskSteps[0] || null;
    const firstStepInstructions =
      firstStep && typeof firstStep === "object" && !Array.isArray(firstStep)
        ? String(pick(firstStep, "instructions") || "").trim()
        : "";
    const firstStepToolNode =
      firstStep &&
        typeof firstStep === "object" &&
        !Array.isArray(firstStep) &&
        firstStep.tool &&
        typeof firstStep.tool === "object" &&
        !Array.isArray(firstStep.tool)
        ? firstStep.tool
        : {};
    const firstStepSkillNode =
      Object.keys(firstStepToolNode).length > 0
        ? firstStepToolNode
        : (
          firstStep &&
            typeof firstStep === "object" &&
            !Array.isArray(firstStep) &&
            firstStep.skill &&
            typeof firstStep.skill === "object" &&
            !Array.isArray(firstStep.skill)
            ? firstStep.skill
            : {}
        );
    const firstStepSkillName = String(
      firstStepSkillNode.name || firstStepSkillNode.id || "",
    ).trim();
    const firstStepInlineInputs =
      firstStepSkillNode.inputs &&
      typeof firstStepSkillNode.inputs === "object" &&
      !Array.isArray(firstStepSkillNode.inputs)
        ? firstStepSkillNode.inputs
        : (
          firstStepSkillNode.args &&
          typeof firstStepSkillNode.args === "object" &&
          !Array.isArray(firstStepSkillNode.args)
            ? firstStepSkillNode.args
            : {}
        );
    const firstStepSkillId = firstStepSkillName;
    const firstStepSkillArgs = stringifySkillArgs(firstStepInlineInputs);
    const firstStepSkillCaps = extractCapabilityCsv(
      firstStepSkillNode.requiredCapabilities,
    );
    const firstStepHasTemplateBinding =
      Boolean(firstStep) &&
      String(pick(firstStep, "id") || "").trim() &&
      firstStepInstructions === objectiveInstructions &&
      !firstStepSkillId &&
      !firstStepSkillArgs &&
      !firstStepSkillCaps;

    const primaryToolName = String(skillNode.name || skillNode.id || "auto").trim() || "auto";
    const primaryInlineInputs =
      skillNode.inputs && typeof skillNode.inputs === "object" && !Array.isArray(skillNode.inputs)
        ? skillNode.inputs
        : {};
    const primarySkillArgsNode =
      Object.keys(primaryInlineInputs).length > 0
        ? primaryInlineInputs
        : (
          skillNode.args && typeof skillNode.args === "object" && !Array.isArray(skillNode.args)
            ? skillNode.args
            : {}
        );

    const primaryStep = {
      id: "",
      instructions: objectiveInstructions,
      skillId: primaryToolName,
      skillArgs: stringifySkillArgs(primarySkillArgsNode),
      skillRequiredCapabilities: extractCapabilityCsv(skillNode.requiredCapabilities),
      templateStepId: "",
      templateInstructions: "",
    };
    const steps = firstStepHasTemplateBinding ? [] : [primaryStep];

    taskSteps.forEach((rawStep, index) => {
      if (!rawStep || typeof rawStep !== "object" || Array.isArray(rawStep)) {
        return;
      }
      const stepInstructions = String(rawStep.instructions || "").trim();
      const stepToolNode =
        rawStep.tool && typeof rawStep.tool === "object" && !Array.isArray(rawStep.tool)
          ? rawStep.tool
          : {};
      const stepSkillNode =
        Object.keys(stepToolNode).length > 0
          ? stepToolNode
          : (
            rawStep.skill && typeof rawStep.skill === "object" && !Array.isArray(rawStep.skill)
              ? rawStep.skill
              : {}
          );
      const stepSkillId = String(stepSkillNode.name || stepSkillNode.id || "").trim();
      const stepInlineInputs =
        stepSkillNode.inputs &&
        typeof stepSkillNode.inputs === "object" &&
        !Array.isArray(stepSkillNode.inputs)
          ? stepSkillNode.inputs
          : (
            stepSkillNode.args &&
            typeof stepSkillNode.args === "object" &&
            !Array.isArray(stepSkillNode.args)
              ? stepSkillNode.args
              : {}
          );
      const stepSkillArgs = stringifySkillArgs(stepInlineInputs);
      const stepSkillCaps = extractCapabilityCsv(stepSkillNode.requiredCapabilities);
      const isPrimaryMirror =
        index === 0 &&
        stepInstructions === objectiveInstructions &&
        !stepSkillId &&
        !stepSkillArgs &&
        !stepSkillCaps;
      if (isPrimaryMirror) {
        if (!firstStepHasTemplateBinding) {
          return;
        }
      }
      const preservePrimarySkillDefaults = isPrimaryMirror && firstStepHasTemplateBinding;
      steps.push({
        id: String(rawStep.id || "").trim(),
        instructions: stepInstructions,
        skillId: preservePrimarySkillDefaults ? primaryStep.skillId : stepSkillId,
        skillArgs: preservePrimarySkillDefaults ? primaryStep.skillArgs : stepSkillArgs,
        skillRequiredCapabilities: preservePrimarySkillDefaults
          ? primaryStep.skillRequiredCapabilities
          : stepSkillCaps,
        templateStepId: "",
        templateInstructions: "",
      });
    });

    const appliedTemplateState = Array.isArray(task.appliedStepTemplates)
      ? task.appliedStepTemplates
        .filter((entry) => entry && typeof entry === "object" && !Array.isArray(entry))
        .map((entry) => ({
          slug: String(entry.slug || "").trim(),
          version: String(entry.version || "").trim(),
          inputs:
            entry.inputs && typeof entry.inputs === "object" && !Array.isArray(entry.inputs)
              ? entry.inputs
              : {},
          stepIds: Array.isArray(entry.stepIds)
            ? entry.stepIds
              .map((stepId) => String(stepId || "").trim())
              .filter(Boolean)
            : [],
          appliedAt: String(entry.appliedAt || "").trim() || new Date().toISOString(),
          capabilities: Array.isArray(entry.capabilities)
            ? normalizeRuntimeOptions(entry.capabilities)
            : [],
        }))
        .filter((entry) => entry.slug && entry.version)
      : [];

    const draftPublishMode = Object.prototype.hasOwnProperty.call(
      publishNode,
      "mode",
    )
      ? publishNode.mode
      : payload.publishMode || extractPublishModeFromPayload(payload);

    const providerProfileId = (() => {
      const ref =
        runtimeNode.executionProfileRef ?? runtimeNode.execution_profile_ref;
      return typeof ref === "string" ? ref.trim() : "";
    })();

    return {
      editJobId: String(pick(job, "id") || "").trim(),
      expectedUpdatedAt: String(pick(job, "updatedAt") || "").trim(),
      runtime: runtime == null ? "" : String(runtime),
      model:
        Object.prototype.hasOwnProperty.call(runtimeNode, "model")
          ? String(runtimeNode.model ?? "")
          : "",
      effort:
        Object.prototype.hasOwnProperty.call(runtimeNode, "effort")
          ? String(runtimeNode.effort ?? "")
          : "",
      repository: String(payload.repository || "").trim(),
      startingBranch: String(gitNode.startingBranch || "").trim(),
      newBranch: String(gitNode.newBranch || "").trim(),
      affinityKey: String(pick(job, "affinityKey") || "").trim(),
      publishMode: (() => {
        const draftMode = String(draftPublishMode ?? "");
        if (draftMode) {
          return draftMode;
        }
        return "";
      })(),
      priority: Number(pick(job, "priority") || 0),
      maxAttempts: Number(pick(job, "maxAttempts") || 3),
      proposeTasks: hasProposeTasks
        ? Boolean(task.proposeTasks)
        : defaultProposeTasks,
      instruction: objectiveInstructions,
      steps,
      appliedTemplateState,
      templateFeatureRequest: "",
      providerProfileId,
    };
  }

  async function loadAvailableSkillIds(runtime = "worker") {
    const runtimeKey = String(runtime || "worker").trim().toLowerCase();
    if (cachedAvailableSkillIds && typeof cachedAvailableSkillIds === "object") {
      const cached = Array.isArray(cachedAvailableSkillIds[runtimeKey])
        ? cachedAvailableSkillIds[runtimeKey]
        : [];
      if (cached.length > 0) {
        return cached;
      }
    }

    const skillsEndpoint = queueSourceConfig.skills || "/api/tasks/skills";
    try {
      const payload = await fetchJson(skillsEndpoint);
      const grouped =
        payload?.items && typeof payload.items === "object" && !Array.isArray(payload.items)
          ? payload.items
          : {};
      const legacyItems = Array.isArray(payload?.legacyItems)
        ? payload.legacyItems
        : Array.isArray(payload?.items)
          ? payload.items
          : [];
      const workerDiscovered = Array.isArray(grouped.worker)
        ? grouped.worker
        : legacyItems;
      const normalizeIds = (items, withAuto = false) => {
        const discovered = items
          .map((item) => {
            if (typeof item === "string") {
              return item.trim();
            }
            if (item && typeof item.id === "string") {
              return item.id.trim();
            }
            return "";
          })
          .filter(Boolean);
        return Array.from(new Set(withAuto ? ["auto", ...discovered] : discovered));
      };
      cachedAvailableSkillIds = {
        worker: normalizeIds(workerDiscovered, true),
      };
    } catch (error) {
      console.error("skills list load failed", error);
      return ["auto"];
    }

    const resolved = cachedAvailableSkillIds?.[runtimeKey];
    if (Array.isArray(resolved) && resolved.length > 0) {
      return resolved;
    }
    return runtimeKey === "worker" ? ["auto"] : [];
  }

  function populateSkillDatalist(datalistId, skillIds) {
    const node = document.getElementById(datalistId);
    if (!node) {
      return;
    }
    const options = (Array.isArray(skillIds) && skillIds.length > 0 ? skillIds : ["auto"])
      .map((skillId) => `<option value="${escapeHtml(skillId)}"></option>`)
      .join("");
    node.innerHTML = options;
  }

  function deriveRequiredCapabilities({
    runtimeMode,
    publishMode,
    taskSkillRequiredCapabilities = [],
    stepSkillRequiredCapabilities = [],
  }) {
    const capabilities = [runtimeMode, "git"];
    if (publishMode === "pr") {
      capabilities.push("gh");
    }
    capabilities.push(...taskSkillRequiredCapabilities);
    capabilities.push(...stepSkillRequiredCapabilities);
    return Array.from(new Set(capabilities));
  }

  function normalizeStatus(source, rawStatus) {
    const sourceMap = (config.statusMaps || {})[source] || {};
    const statusKey = String(rawStatus || "").toLowerCase();
    if (statusKey in sourceMap) {
      return sourceMap[statusKey];
    }

    if (statusKey.includes("running")) {
      return "running";
    }
    if (["failed", "error", "failure"].includes(statusKey)) {
      return "failed";
    }
    if (["success", "succeeded", "completed"].includes(statusKey)) {
      return "succeeded";
    }
    return "queued";
  }

  function statusBadge(source, rawStatus) {
    const normalized = normalizeStatus(source, rawStatus);
    const statusClassToken = sanitizeCssToken(normalized, "queued");
    return `<span class="status status-${statusClassToken}">${escapeHtml(normalized)}</span>`;
  }

  const FINISH_OUTCOME_LABELS = {
    PUBLISHED_PR: "Published PR",
    PUBLISHED_BRANCH: "Published Branch",
    NO_CHANGES: "No Changes",
    PUBLISH_DISABLED: "Publish Disabled",
    FAILED: "Failed",
    CANCELLED: "Cancelled",
  };

  function normalizeFinishOutcomeCode(rawCode) {
    const code = String(rawCode || "")
      .trim()
      .toUpperCase();
    return code || "";
  }

  function finishOutcomeLabel(rawCode) {
    const code = normalizeFinishOutcomeCode(rawCode);
    if (!code) {
      return "-";
    }
    return FINISH_OUTCOME_LABELS[code] || code;
  }

  function finishOutcomeBadge(rawCode) {
    const code = normalizeFinishOutcomeCode(rawCode);
    if (!code) {
      return "-";
    }
    const token = sanitizeCssToken(code.toLowerCase(), "queued");
    return `<span class="status status-${token}">${escapeHtml(
      finishOutcomeLabel(code),
    )}</span>`;
  }

  function endpoint(template, replacements) {
    let resolved = template;
    Object.entries(replacements).forEach(([key, value]) => {
      resolved = resolved.split(`{${key}}`).join(encodeURIComponent(String(value)));
    });
    return resolved;
  }

  function withQueueSummaryFlag(url) {
    if (!url || typeof url !== "string") {
      return url;
    }
    if (/[?&]summary=/.test(url)) {
      return url;
    }
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}summary=true`;
  }

  function withTemporalSourceFlag(url) {
    if (!url || typeof url !== "string") {
      return url;
    }
    if (/[?&]source=/.test(url)) {
      return url;
    }
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}source=temporal`;
  }

  function sanitizeExternalHttpUrl(candidate) {
    const raw = String(candidate || "").trim();
    if (!raw) {
      return "";
    }
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return "";
      }
      return parsed.href;
    } catch (_error) {
      return "";
    }
  }

  async function fetchJson(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    const body = options.body;
    const isFormData =
      typeof FormData === "function" && body instanceof FormData;
    if (!isFormData && !Object.prototype.hasOwnProperty.call(headers, "Content-Type")) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(url, {
      credentials: "include",
      headers,
      ...options,
    });

    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { message: text };
      }
    }

    if (!response.ok) {
      const detail = payload?.detail;
      const message =
        (typeof detail === "string" && detail) ||
        detail?.message ||
        payload?.message ||
        `${response.status} ${response.statusText}`;
      const error = new Error(message);
      error.status = response.status;
      error.statusText = response.statusText;
      error.payload = payload;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        error.code = detail.code ? String(detail.code) : "";
      } else {
        error.code = "";
      }
      throw error;
    }

    return payload;
  }

  function classifyLiveSessionError(error) {
    const code = String(error?.code || "")
      .trim()
      .toLowerCase();
    const status = Number(error?.status || 0);

    if (code === "live_session_not_found") {
      return "disabled";
    }

    if (status === 404 && !code) {
      return "route_missing";
    }

    return "other";
  }

  function setView(title, subtitle, body, options = {}) {
    const { showAutoRefreshControls = false } = options;
    const normalizedSubtitle = String(subtitle || "").trim();
    root.innerHTML = `
      <div class="toolbar">
        <div>
          <h2 class="page-title">${escapeHtml(title)}</h2>
          ${normalizedSubtitle
        ? `<p class="page-meta">${escapeHtml(normalizedSubtitle)}</p>`
        : ""
      }
        </div>
        ${showAutoRefreshControls ? renderAutoRefreshControls() : ""}
      </div>
      ${body}
    `;
    bindAutoRefreshControls();
    syncAutoRefreshControls();
  }

  function renderTaskTable(rows, sortState) {
    if (rows.length === 0) {
      return "<p class='small'>No rows available.</p>";
    }

    const activeSortField = sortState && sortState.field ? sortState.field : null;
    const activeSortDir = sortState && sortState.direction === "asc" ? "asc" : "desc";

    function sortIndicator(field) {
      if (field !== activeSortField) {
        return "";
      }
      return `<span class="sort-indicator" aria-hidden="true">${activeSortDir === "asc" ? "\u25b2" : "\u25bc"}</span>`;
    }

    function thClass(field) {
      if (field !== activeSortField) {
        return "sortable-header";
      }
      return `sortable-header sort-${activeSortDir}`;
    }

    function ariaSort(field) {
      if (field !== activeSortField) {
        return "none";
      }
      return activeSortDir === "asc" ? "ascending" : "descending";
    }

    function sortableTh(field, label) {
      return `<th class="${thClass(field)}" data-sort-field="${escapeHtml(field)}" aria-sort="${ariaSort(field)}">${escapeHtml(label)}${sortIndicator(field)}</th>`;
    }

    const primaryFields = taskFieldDefinitions.filter(
      (definition) => definition.tableSection !== "timeline",
    );
    const timelineFields = taskFieldDefinitions.filter(
      (definition) => definition.tableSection === "timeline",
    );

    const renderDefinitionHeader = (definition) =>
      sortableTh(definition.key, definition.label);
    const primaryHeaders = primaryFields.map(renderDefinitionHeader).join("");
    const timelineHeaders = timelineFields.map(renderDefinitionHeader).join("");

    const renderDefinitionCell = (row, definition) =>
      `<td data-field="${escapeHtml(definition.key)}">${renderTaskFieldValue(row, definition)}</td>`;

    const body = rows
      .map((row) => {
        const primaryCells = primaryFields.map((definition) => renderDefinitionCell(row, definition)).join("");
        const timelineCells = timelineFields.map((definition) => renderDefinitionCell(row, definition)).join("");
        const linkTarget = row.link ? escapeHtml(row.link) : "#";
        const idLabel = row.id ? escapeHtml(row.id) : "-";
        const titleLabel = row.title ? escapeHtml(row.title) : "-";
        const sourceLabel = row.sourceLabel ? escapeHtml(row.sourceLabel) : "-";
        const rawStatus = String(row.rawStatus || "").trim() || "-";
        return `
        <tr>
          <td>${sourceLabel}</td>
          <td><a href="${linkTarget}">${idLabel}</a></td>
          ${primaryCells}
          <td>${statusBadge(row.source, row.rawStatus)} <span class="small">${escapeHtml(
          rawStatus,
        )}</span></td>
          <td>${titleLabel}</td>
          ${timelineCells}
        </tr>
      `;
      })
      .join("");

    return `
      <table>
        <thead>
          <tr>
            ${sortableTh("type", "Type")}
            ${sortableTh("id", "ID")}
            ${primaryHeaders}
            ${sortableTh("status", "Status")}
            ${sortableTh("title", "Title")}
            ${timelineHeaders}
          </tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
    `;
  }

  function renderRowsTable(rows) {
    return renderTaskTable(rows);
  }

  function renderTaskCards(rows) {
    if (!rows || rows.length === 0) {
      return "";
    }
    return rows
      .map((row) => {
        const fieldItems = taskFieldDefinitions
          .map(
            (definition) => `
              <div>
                <dt>${escapeHtml(definition.label)}</dt>
                <dd>${renderTaskFieldValue(row, definition)}</dd>
              </div>
            `,
          )
          .join("");
        const skillLabel = row.skillId || "";
        const runtimeLabel = row.runtimeMode || "";
        const metaParts = [runtimeLabel, skillLabel].filter(Boolean);
        const metaText = metaParts.join(" · ") || "Task";
        const linkTarget = row.link ? escapeHtml(row.link) : "#";
        const titleBase = row.title ? row.title : "Queue Job";
        const titleWithId = row.id ? `${titleBase} · ${row.id}` : titleBase;
        const rawStatus = String(row.rawStatus || "").trim() || "-";
        const statusField = `
          <div>
            <dt>Status</dt>
            <dd>
              <span class="queue-card-status-field">
                ${statusBadge(row.source, row.rawStatus)}
                <span class="queue-card-status-raw small">${escapeHtml(rawStatus)}</span>
              </span>
            </dd>
          </div>
        `;
        return `
          <li class="queue-card">
            <div class="queue-card-header">
              <div>
                <a href="${linkTarget}" class="queue-card-title">${escapeHtml(titleWithId)}</a>
                <p class="queue-card-meta">${escapeHtml(metaText)}</p>
              </div>
            </div>
            <dl class="queue-card-fields">
              ${statusField}
              ${fieldItems}
            </dl>
            <div class="queue-card-actions">
              <a href="${linkTarget}" class="button secondary" role="button">View details</a>
            </div>
          </li>
        `;
      })
      .filter(Boolean)
      .join("");
  }

  function renderTaskLayouts(rows, sortState) {
    if (!rows || rows.length === 0) {
      return "<p class='small'>No rows available.</p>";
    }

    const tableAttributes = [
      'class="queue-table-wrapper"',
      'data-layout="table"',
    ].join(" ");
    const cardsHtml = `<ul class="queue-card-list" data-layout="card" role="list">${renderTaskCards(rows)}</ul>`;

    return `
      <div class="queue-layouts">
        <div ${tableAttributes}>${renderTaskTable(rows, sortState)}</div>
        ${cardsHtml}
      </div>
    `;
  }

  function renderActivePageContent(rows, errors = [], sortState) {
    const notices = (errors || [])
      .map(
        (source) =>
          `<div class="notice error">${escapeHtml(
            `Unable to load ${source} data source.`,
          )}</div>`,
      )
      .join("");
    const normalizedRows = Array.isArray(rows) ? rows.slice() : [];
    const sortedRows = sortState && sortState.field
      ? sortRowsByColumn(normalizedRows, sortState.field, sortState.direction)
      : sortRows(normalizedRows);
    const layouts = renderTaskLayouts(sortedRows, sortState);
    return `${notices}${layouts}`;
  }

  function summarizeInstructionPreview(value) {
    if (typeof value !== "string") {
      return "";
    }
    const raw = String(value ?? "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .trim();
    if (!raw) {
      return "";
    }
    const [firstParagraph] = raw.split(/\n\s*\n/, 1);
    const collapsed = firstParagraph
      .replace(/\s*\n\s*/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!collapsed) {
      return "";
    }
    if (collapsed.length <= TASK_LIST_TITLE_MAX_CHARS) {
      return collapsed;
    }
    const truncated = collapsed.slice(0, TASK_LIST_TITLE_MAX_CHARS);
    const lastSpace = truncated.lastIndexOf(" ");
    const safeCut = lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated;
    return `${safeCut.trimEnd()}...`;
  }

  function filterProposalsByTag(rows, tag) {
    const normalizedTag = String(tag || "").trim().toLowerCase();
    const candidates = Array.isArray(rows) ? rows : [];
    if (!normalizedTag) {
      return candidates;
    }
    return candidates.filter((row) => {
      const tagList = (pick(row, "tags") || []).map((candidateTag) =>
        String(candidateTag || "").trim().toLowerCase(),
      );
      return tagList.includes(normalizedTag);
    });
  }

  function renderProposalTable(rows) {
    const candidates = (rows || []).filter(Boolean);
    return candidates
      .map((row) => {
        const id = pick(row, "id");
        const preview = pick(row, "taskPreview") || {};
        const origin = pick(row, "origin") || {};
        const originSource = pick(origin, "source") || "-";
        const originLink =
          originSource === "queue" && pick(origin, "id")
            ? `<a href="${escapeHtml(
              buildUnifiedTaskDetailRoute(pick(origin, "id"), "queue"),
            )}">queue/${escapeHtml(String(pick(origin, "id") || ""))}</a>`
            : escapeHtml(originSource);
        const repo = pick(row, "repository") || pick(preview, "repository") || "-";
        const runtimeMode = pick(preview, "runtimeMode") || "-";
        const instructions = pick(preview, "instructions") || "";
        const tags = (pick(row, "tags") || []).join(", ");
        const priority = (pick(row, "reviewPriority") || "normal").toUpperCase();
        const overrideReason = pick(row, "priorityOverrideReason");
        const priorityBadge = `<span class="badge priority-${escapeHtml(
          priority.toLowerCase(),
        )}" ${overrideReason ? `title="Override: ${escapeHtml(String(overrideReason))}"` : ""}>${escapeHtml(priority)}</span>`;
        const dedupHash = (pick(row, "dedupHash") || "").toString();
        const dedupShort = dedupHash ? dedupHash.slice(0, 8) : "-";
        return `
          <tr data-proposal-id="${escapeHtml(String(id || ""))}">
            <td><a href="/tasks/proposals/${encodeURIComponent(
          String(id || ""),
        )}">${escapeHtml(String(id || "").slice(0, 8) || "-")}</a></td>
            <td>${escapeHtml(pick(row, "title") || "(untitled)")}</td>
            <td>${escapeHtml(repo)}</td>
            <td>${escapeHtml(pick(row, "category") || "-")}</td>
            <td>${escapeHtml(runtimeMode)}</td>
            <td>${priorityBadge}</td>
            <td>${statusBadge("proposals", pick(row, "status"))}</td>
            <td>${escapeHtml(formatTimestamp(pick(row, "createdAt")))}</td>
            <td>${originLink}</td>
            <td>${escapeHtml(tags || "-")}</td>
                <td><code>${escapeHtml(dedupShort)}</code></td>
            <td>
              <div class="stack compact">
                <button
                  type="button"
                  class="proposal-action queue-action"
                  data-action="promote"
                  data-proposal-action="promote"
                  data-proposal-id="${escapeHtml(
          String(id || ""),
        )}">Promote</button>
                <button
                  type="button"
                  class="danger proposal-action queue-action queue-action-danger"
                  data-action="dismiss"
                  data-proposal-action="dismiss"
                  data-proposal-id="${escapeHtml(
          String(id || ""),
        )}">Dismiss</button>
              </div>
            </td>
          </tr>
          ${instructions
            ? `<tr><td colspan="13"><span class="small">${escapeHtml(
              instructions,
            )}</span><br/><span class="tiny">Dedup Hash: <code>${escapeHtml(
              dedupHash || "-",
            )}</code></span></td></tr>`
            : ""
          }
        `;
      })
      .join("");
  }

  function renderProposalCards(rows) {
    const candidates = (rows || []).filter(Boolean);
    return candidates
      .map((row) => {
        const id = pick(row, "id");
        const preview = pick(row, "taskPreview") || {};
        const origin = pick(row, "origin") || {};
        const originSource = pick(origin, "source") || "-";
        const originLink =
          originSource === "queue" && pick(origin, "id")
            ? `<a href="/tasks/queue/${encodeURIComponent(
              String(pick(origin, "id") || ""),
            )}">queue/${escapeHtml(String(pick(origin, "id") || ""))}</a>`
            : escapeHtml(originSource);
        const repo = pick(row, "repository") || pick(preview, "repository") || "-";
        const runtimeMode = pick(preview, "runtimeMode") || "-";
        const instructions = pick(preview, "instructions") || "";
        const instructionText = String(instructions || "").trim();
        const instructionPreview = instructionText
          ? `${escapeHtml(instructionText.slice(0, 140))}${instructionText.length > 140 ? "..." : ""
          }`
          : "-";
        const tags = (pick(row, "tags") || []).join(", ");
        const priority = (pick(row, "reviewPriority") || "normal").toUpperCase();
        const overrideReason = pick(row, "priorityOverrideReason");
        const priorityBadge = `<span class="badge priority-${escapeHtml(
          priority.toLowerCase(),
        )}" ${overrideReason ? `title="Override: ${escapeHtml(String(overrideReason))}"` : ""}>${escapeHtml(priority)}</span>`;
        const dedupHash = (pick(row, "dedupHash") || "").toString();
        const dedupShort = dedupHash ? dedupHash.slice(0, 8) : "-";
        const rowId = String(id || "");
        const title = pick(row, "title") || "(untitled)";
        const titleWithId = rowId ? `${title} · ${rowId}` : title;
        const encodedRowId = encodeURIComponent(String(id || ""));
        return `
          <li class="queue-card" data-proposal-id="${escapeHtml(rowId)}">
            <div data-proposal-id="${escapeHtml(rowId)}"
              data-proposal-title="${escapeHtml(title)}"
              data-proposal-repo="${escapeHtml(repo)}"></div>
            <div class="queue-card-header">
              <div>
                <a href="/tasks/proposals/${encodedRowId}" class="queue-card-title">${escapeHtml(
          titleWithId,
        )}</a>
                <p class="queue-card-meta">${escapeHtml(repo)}</p>
              </div>
              <div class="queue-card-status">
                ${statusBadge("proposals", pick(row, "status"))}
                <span class="queue-card-status-raw small">${escapeHtml(
          String(pick(row, "status") || "-").trim(),
        )}</span>
              </div>
            </div>
            <dl class="queue-card-fields">
              <div data-field="id">
                <dt>ID</dt>
                <dd><code>${escapeHtml(rowId.slice(0, 8) || "-")}</code></dd>
              </div>
              <div data-field="category">
                <dt>Category</dt>
                <dd>${escapeHtml(pick(row, "category") || "-")}</dd>
              </div>
              <div data-field="priority">
                <dt>Priority</dt>
                <dd>${priorityBadge}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>${statusBadge("proposals", pick(row, "status"))}</dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>${escapeHtml(formatTimestamp(pick(row, "createdAt")))}</dd>
              </div>
              <div>
                <dt>Origin</dt>
                <dd>${originLink}</dd>
              </div>
              <div data-field="runtime">
                <dt>Runtime</dt>
                <dd><code>${escapeHtml(runtimeMode)}</code></dd>
              </div>
              <div>
                <dt>Tags</dt>
                <dd>${escapeHtml(tags || "-")}</dd>
              </div>
              <div>
                <dt>Dedup</dt>
                <dd><code>${escapeHtml(dedupShort)}</code></dd>
              </div>
              <div>
                <dt>Instructions</dt>
                <dd><span class="small">${instructionPreview}</span></dd>
              </div>
            </dl>
            <div class="queue-card-actions">
              <a href="/tasks/proposals/${encodedRowId}" class="button secondary" role="button">View details</a>
              <button
                type="button"
                class="proposal-action queue-action"
                data-action="promote"
                data-proposal-action="promote"
                data-proposal-id="${escapeHtml(String(id || ""))}">Promote</button>
              <button
                type="button"
                class="danger proposal-action queue-action queue-action-danger"
                data-action="dismiss"
                data-proposal-action="dismiss"
                data-proposal-id="${escapeHtml(String(id || ""))}">Dismiss</button>
            </div>
          </li>
        `;
      })
      .join("");
  }

  function renderProposalLayouts(rows = [], tag = "") {
    const normalizedRows = (rows || []).filter(Boolean);
    if (!normalizedRows.length) {
      return "<p class='small'>No proposals found for the current filters.</p>";
    }
    const filteredRows = filterProposalsByTag(normalizedRows, tag);
    if (!filteredRows.length) {
      return "<p class='small'>No proposals found for the current filters.</p>";
    }
    return `
      <div class="queue-layouts">
        <div class="queue-table-wrapper" data-layout="table" data-sticky-table="false">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Title</th>
                <th>Repository</th>
                <th>Category</th>
                <th>Runtime</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Created</th>
                <th>Origin</th>
                <th>Tags</th>
                <th>Dedup</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>${renderProposalTable(filteredRows)}</tbody>
          </table>
        </div>
        <ul class="queue-card-list" data-layout="card" role="list">${renderProposalCards(
      filteredRows,
    )}</ul>
      </div>
    `;
  }

  function renderProposalActionFeedback(feedback = null) {
    const node =
      feedback && typeof feedback === "object" && !Array.isArray(feedback) ? feedback : null;
    const message = node ? String(node.message || "").trim() : "";
    const statusFilter = node ? String(node.statusFilter || "").trim().toLowerCase() : "";
    const shouldLinkToStatus =
      statusFilter === "dismissed" || statusFilter === "promoted" || statusFilter === "open";
    const jumpLink = shouldLinkToStatus
      ? `<a href="/tasks/proposals?status=${encodeURIComponent(
        statusFilter,
      )}" class="small">View ${escapeHtml(statusFilter)} proposals</a>`
      : "";
    const content = message
      ? `<div class="notice ok">${escapeHtml(message)}${jumpLink ? `<br/>${jumpLink}` : ""}</div>`
      : "";
    return `<div class="proposal-action-feedback">${content}</div>`;
  }

  // Queue metadata for table columns and card field rows is centralized here.
  // Card status remains a fixed leading row in renderTaskCards so mobile keeps
  // status first regardless of future taskFieldDefinitions ordering. When
  // expanding queue metadata, update docs/TaskUiQueue.md ("Extending queue
  // fields") and add tests that exercise the new label/value pairs.
  const taskFieldDefinitions = [
    {
      key: "runtimeMode",
      label: "Runtime",
      render: (row) => renderRuntime(row.runtimeMode),
      tableSection: "primary",
    },
    {
      key: "skillId",
      label: "Skill",
      render: (row) => escapeHtml(row.skillId || "-"),
      tableSection: "primary",
    },
    {
      key: "scheduledFor",
      label: "Scheduled",
      render: (row) => escapeHtml(formatTimestamp(row.scheduledFor || row.createdAt)),
      tableSection: "timeline",
    },
    {
      key: "startedAt",
      label: "Started",
      render: (row) => escapeHtml(formatTimestamp(row.startedAt)),
      tableSection: "timeline",
    },
    {
      key: "finishedAt",
      label: "Finished",
      render: (row) => escapeHtml(formatTimestamp(row.finishedAt)),
      tableSection: "timeline",
    },
  ];

  function renderTaskFieldValue(row, definition) {
    if (!definition || typeof definition.render !== "function") {
      return "-";
    }
    const rendered = definition.render(row);
    if (rendered === null || rendered === undefined || rendered === "") {
      return "-";
    }
    return rendered;
  }

  const cloneForTestHarness = (value) => {
    if (!value || typeof value !== "object") {
      return value;
    }
    if (Array.isArray(value)) {
      return value.map((item) => cloneForTestHarness(item));
    }
    const cloned = {};
    Object.entries(value).forEach(([key, nested]) => {
      if (key === "__proto__" || key === "prototype" || key === "constructor") {
        return;
      }
      cloned[key] = cloneForTestHarness(nested);
    });
    return cloned;
  };

  const cloneStepStateEntries = (steps = []) => {
    if (!Array.isArray(steps)) {
      return [];
    }
    return steps.map((step) => {
      if (!step || typeof step !== "object") {
        return {};
      }
      return cloneForTestHarness(step);
    });
  };

  const normalizeSubmissionDraftForTest = (draft = {}) =>
    cloneForTestHarness(
      draft && typeof draft === "object" && !Array.isArray(draft) ? draft : {},
    );

  const resetWorkerSubmissionFields = (draft = {}) => {
    const normalized = normalizeSubmissionDraftForTest(draft);
    normalized.instruction = "";
    normalized.templateFeatureRequest = "";
    normalized.steps = [];
    return normalized;
  };

  const createSubmitDraftController = (queueDraft = {}) => {
    let workerDraft = normalizeSubmissionDraftForTest(queueDraft);
    if (!Array.isArray(workerDraft.steps)) {
      workerDraft.steps = [];
    }
    workerDraft.steps = cloneStepStateEntries(workerDraft.steps);

    return {
      saveWorker: (draft) => {
        workerDraft = normalizeSubmissionDraftForTest(draft);
        if (!Array.isArray(workerDraft.steps)) {
          workerDraft.steps = [];
        }
        workerDraft.steps = cloneStepStateEntries(workerDraft.steps);
      },
      loadWorker: () => normalizeSubmissionDraftForTest(workerDraft),
    };
  };

  const shouldUseTemporalSubmit = (runtimeMode, options = {}) => {
    const normalizedMode = String(runtimeMode || "").trim().toLowerCase();
    const temporalSubmitEnabled = Boolean(options.temporalSubmitEnabled);
    const isEditMode = Boolean(options.isEditMode);
    if (!temporalSubmitEnabled) {
      return false;
    }
    if (isEditMode) {
      return false;
    }
    return true;
  };

  const determineSubmitDestination = (runtimeMode, endpoints = {}, options = {}) => {
    const normalizedMode = String(runtimeMode || "").trim().toLowerCase();
    const queueEndpoint = String(endpoints.queue || "/api/queue/jobs").trim();
    if (shouldUseTemporalSubmit(normalizedMode, options)) {
      return {
        mode: "temporal",
        endpoint: String(endpoints.temporal || "/api/executions").trim(),
      };
    }
    return { mode: "worker", endpoint: queueEndpoint };
  };


  const hasExplicitSkillSelection = (skillId) => {
    const normalized = String(skillId || "").trim().toLowerCase();
    return Boolean(normalized) && normalized !== "auto";
  };

  const validatePrimaryStepSubmission = (primaryStep = {}, options = {}) => {
    if (!primaryStep || typeof primaryStep !== "object" || Array.isArray(primaryStep)) {
      return {
        ok: false,
        error: "Add at least one step before submitting.",
      };
    }
    const instructions = String(primaryStep.instructions || "").trim();
    const skillId = String(primaryStep.skillId || "").trim();
    const additionalStepsCount = Number(options.additionalStepsCount) || 0;
    if (!instructions && additionalStepsCount > 0) {
      return {
        ok: false,
        error: "Primary step instructions are required when additional steps are provided.",
      };
    }
    if (instructions || hasExplicitSkillSelection(skillId)) {
      return {
        ok: true,
        value: {
          instructions,
          skillId,
        },
      };
    }
    return {
      ok: false,
      error: "Primary step requires instructions or an explicit skill selection.",
    };
  };

  const cloneTemporalSubmitRequest = (requestBody = {}) =>
    cloneForTestHarness(
      requestBody && typeof requestBody === "object" && !Array.isArray(requestBody)
        ? requestBody
        : {},
    );

  const resolveArtifactUploadTarget = (uploadUrl) => {
    const raw = String(uploadUrl || "").trim();
    if (!raw) {
      return { url: raw, credentials: "include" };
    }
    try {
      const windowOrigin =
        window && window.location && typeof window.location.origin === "string"
          ? String(window.location.origin).trim()
          : "";
      if (!windowOrigin) {
        return { url: raw, credentials: "include" };
      }
      const parsed = new URL(raw, windowOrigin);
      return {
        url: raw,
        credentials: parsed.origin === windowOrigin ? "include" : "omit",
      };
    } catch (_error) {
      return { url: raw, credentials: "include" };
    }
  };

  const buildArtifactUploadHeaders = (
    upload = {},
    defaultContentType = "text/plain; charset=utf-8",
  ) => {
    const requiredHeaders =
      pick(upload, "required_headers", "requiredHeaders")
      || {};
    const normalizedHeaders = {};
    if (
      requiredHeaders &&
      typeof requiredHeaders === "object" &&
      !Array.isArray(requiredHeaders)
    ) {
      Object.entries(requiredHeaders).forEach(([headerName, headerValue]) => {
        const normalizedName = String(headerName || "").trim();
        const normalizedValue = String(headerValue ?? "").trim();
        if (!normalizedName || !normalizedValue) {
          return;
        }
        normalizedHeaders[normalizedName] = normalizedValue;
      });
    }
    if (
      defaultContentType &&
      !Object.keys(normalizedHeaders).some(
        (headerName) => headerName.toLowerCase() === "content-type",
      )
    ) {
      normalizedHeaders["Content-Type"] = defaultContentType;
    }
    return normalizedHeaders;
  };

  const createTemporalInputArtifact = async ({ instructions, repository }) => {
    const normalizedInstructions = String(instructions || "");
    const byteSize = new TextEncoder().encode(normalizedInstructions).length;
    const createResponse = await fetchJson(
      temporalSourceConfig.artifactCreate || "/api/artifacts",
      {
        method: "POST",
        body: JSON.stringify({
          content_type: "text/plain; charset=utf-8",
          size_bytes: byteSize,
          metadata: {
            label: "Submitted Instructions",
            repository: String(repository || "").trim() || null,
            source: "task-dashboard-submit",
          },
        }),
      },
    );
    const artifactRef =
      pick(createResponse, "artifact_ref", "artifactRef") || {};
    const artifactId = String(
      pick(artifactRef, "artifact_id", "artifactId")
      || pick(createResponse, "artifact_id", "artifactId")
      || "",
    ).trim();
    if (!artifactId) {
      throw new Error("artifact create response missing artifact id");
    }
    const upload = pick(createResponse, "upload") || {};
    const uploadUrl = String(pick(upload, "upload_url", "uploadUrl") || "").trim()
      || endpoint("/api/artifacts/{artifactId}/content", { artifactId });
    const uploadTarget = resolveArtifactUploadTarget(uploadUrl);
    await fetchJson(uploadTarget.url, {
      method: "PUT",
      credentials: uploadTarget.credentials,
      headers: buildArtifactUploadHeaders(upload),
      body: normalizedInstructions,
    });
    return { artifactId };
  };

  const linkTemporalArtifactToExecution = async ({ artifactId, execution }) => {
    const normalizedArtifactId = String(artifactId || "").trim();
    if (!normalizedArtifactId) {
      return;
    }
    const workflowId = String(pick(execution, "workflowId", "taskId") || "").trim();
    const runId = String(pick(execution, "temporalRunId", "runId") || "").trim();
    if (!workflowId || !runId) {
      return;
    }
    await fetchJson(
      endpoint("/api/artifacts/{artifactId}/links", { artifactId: normalizedArtifactId }),
      {
        method: "POST",
        body: JSON.stringify({
          namespace: String(pick(execution, "namespace") || "default").trim() || "default",
          workflow_id: workflowId,
          run_id: runId,
          link_type: "input.instructions",
          label: "Submitted Instructions",
        }),
      },
    );
  };

  const SUBMIT_DRAFT_STORAGE_KEY = "moonmind.submitWorkDrafts.v1";
  const readSubmitDraftStorage = () => {
    try {
      if (!window.localStorage || typeof window.localStorage.getItem !== "function") {
        return null;
      }
      const raw = window.localStorage.getItem(SUBMIT_DRAFT_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return null;
      }
      return parsed;
    } catch (error) {
      console.warn("Unable to read submit drafts from localStorage.", error);
      return null;
    }
  };

  const writeSubmitDraftStorage = (payload) => {
    try {
      if (!window.localStorage || typeof window.localStorage.setItem !== "function") {
        return;
      }
      const rawPayload =
        payload && typeof payload === "object" && !Array.isArray(payload)
          ? payload
          : {};
      window.localStorage.setItem(
        SUBMIT_DRAFT_STORAGE_KEY,
        JSON.stringify(rawPayload),
      );
    } catch (error) {
      console.warn("Unable to persist submit drafts to localStorage.", error);
    }
  };

  const createDraftPersistenceScheduler = (persistFn, delayMs = 250) => {
    let timeoutId = null;
    return () => {
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
      }
      timeoutId = setTimeout(() => {
        timeoutId = null;
        persistFn();
      }, delayMs);
    };
  };

  const resolveQueueSubmitRuntimeUiState = (_runtimeValue) => ({
    showWorkerPriorityFields: true,
  });

  const applyElementVisibility = (node, isVisible) => {
    if (!node) {
      return;
    }
    const style =
      node.style &&
        typeof node.style === "object" &&
        typeof node.style.setProperty === "function" &&
        typeof node.style.removeProperty === "function"
        ? node.style
        : null;
    if (isVisible) {
      node.hidden = false;
      if (style) {
        style.removeProperty("display");
      }
      if (node.classList && typeof node.classList.remove === "function") {
        node.classList.remove("hidden");
      }
      return;
    }
    node.hidden = true;
    if (style) {
      style.setProperty("display", "none", "important");
    }
    if (node.classList && typeof node.classList.add === "function") {
      node.classList.add("hidden");
    }
  };

  const resolveQueueSubmitPriorityForRuntime = (_runtimeMode, priorityValues = {}) => {
    const priorityValue = Number(priorityValues.priority || 0);
    return Number.isFinite(priorityValue) ? priorityValue : 0;
  };

  const resolveSubmitRuntime = (runtimeValue, fallback) => {
    const normalized = String(runtimeValue || "").trim().toLowerCase();
    if (!normalized) {
      return fallback;
    }
    if (supportedTaskRuntimes.includes(normalized)) {
      return normalized;
    }
    return fallback;
  };

  const validateSubmitRuntime = (runtimeValue) => {
    const normalized = String(runtimeValue || "").trim().toLowerCase();
    if (!normalized) {
      return null;
    }
    if (supportedTaskRuntimes.includes(normalized)) {
      return normalized;
    }
    return null;
  };

  const parseRuntimeSearchParam = (searchParams) => {
    const runtimeValue = searchParams?.get("runtime");
    if (runtimeValue === null) {
      return { provided: false, runtime: undefined, rawValue: "" };
    }
    const runtime = validateSubmitRuntime(runtimeValue);
    return { provided: true, runtime, rawValue: runtimeValue };
  };

  const parseEditJobSearchParam = (searchParams) => {
    const editJobValue = searchParams?.get("editJobId");
    if (editJobValue === null) {
      return { provided: false, jobId: "", rawValue: "" };
    }
    const jobId = normalizeDashboardDetailSegment(editJobValue);
    return { provided: true, jobId, rawValue: editJobValue };
  };

  const parseResubmittedFromSearchParam = (searchParams) => {
    const resubmittedFromValue = searchParams?.get("resubmittedFrom");
    if (resubmittedFromValue === null) {
      return { provided: false, jobId: "", rawValue: "" };
    }
    const jobId = normalizeDashboardDetailSegment(resubmittedFromValue);
    return { provided: true, jobId, rawValue: resubmittedFromValue };
  };

  const isEditableQueuedTaskJob = (job) => {
    if (!job || typeof job !== "object" || Array.isArray(job)) {
      return false;
    }
    const jobType = String(pick(job, "type") || "")
      .trim()
      .toLowerCase();
    const normalizedStatus = normalizeStatus("queue", pick(job, "status"));
    const startedAt = pick(job, "startedAt");
    const hasStarted = startedAt !== null && startedAt !== undefined && String(startedAt).trim() !== "";
    return jobType === "task" && normalizedStatus === "queued" && !hasStarted;
  };

  const isResubmittableTaskJob = (job) => {
    if (!job || typeof job !== "object" || Array.isArray(job)) {
      return false;
    }
    const jobType = String(pick(job, "type") || "")
      .trim()
      .toLowerCase();
    const normalizedStatus = normalizeStatus("queue", pick(job, "status"));
    const rawStatus = String(pick(job, "status") || "")
      .trim()
      .toLowerCase();
    return (
      jobType === "task" &&
      (normalizedStatus === "failed" ||
        normalizedStatus === "canceled" ||
        normalizedStatus === "cancelled" ||
        rawStatus === "failed" ||
        rawStatus === "canceled" ||
        rawStatus === "cancelled")
    );
  };

  const resolveQueuePrefillModeFromJob = (job) => {
    if (isEditableQueuedTaskJob(job)) {
      return "edit";
    }
    if (isResubmittableTaskJob(job)) {
      return "resubmit";
    }
    return "";
  };

  const resolveQueueDetailPrefillAction = (job) => {
    const mode = resolveQueuePrefillModeFromJob(job);
    const jobId = normalizeDashboardDetailSegment(pick(job, "id") || "");
    if (!mode || !jobId) {
      return { mode: "", label: "", route: "" };
    }
    const route = `/tasks/queue/new?editJobId=${encodeURIComponent(jobId)}`;
    return {
      mode,
      label: mode === "resubmit" ? "Resubmit" : "Edit",
      route,
    };
  };

  const resolveQueuePrefillSubmitTarget = (mode, endpoints = {}) => {
    const normalizedMode = String(mode || "").trim().toLowerCase();
    if (normalizedMode === "edit") {
      return {
        method: "PUT",
        endpointTemplate: String(endpoints.update || "/api/queue/jobs/{id}").trim(),
      };
    }
    if (normalizedMode === "resubmit") {
      return {
        method: "POST",
        endpointTemplate: String(endpoints.resubmit || "/api/queue/jobs/{id}/resubmit").trim(),
      };
    }
    return {
      method: "POST",
      endpointTemplate: String(endpoints.create || "/api/queue/jobs").trim(),
    };
  };

  const isWorkerSubmitRuntime = (runtimeValue) => {
    const normalized = String(runtimeValue || "").trim().toLowerCase();
    return supportedTaskRuntimes.includes(normalized);
  };

  const submitDraftSeeds = (() => {
    const stored = readSubmitDraftStorage();
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) {
      return { worker: {} };
    }
    return {
      worker:
        typeof stored.worker === "object" && !Array.isArray(stored.worker)
          ? stored.worker
          : {},
    };
  })();

  const submitDraftController = createSubmitDraftController(submitDraftSeeds.worker || {});
  const persistSubmitDraftsToStorage = () => {
    writeSubmitDraftStorage({
      worker: submitDraftController.loadWorker(),
    });
  };

  if (
    typeof window !== "undefined"
    && window.__MOONMIND_DASHBOARD_TEST
  ) {
    window.__submitRuntimeTest = {
      createSubmitDraftController,
      determineSubmitDestination,
      shouldUseTemporalSubmit,
      validatePrimaryStepSubmission,
      hasExplicitSkillSelection,
      extractRuntimeModelFromPayload,
      extractRuntimeEffortFromPayload,
      cloneStepStateEntries,
      resetWorkerSubmissionFields,
      readSubmitDraftStorage,
      resolveSubmitRuntime,
      isWorkerSubmitRuntime,
      parseEditJobSearchParam,
      parseResubmittedFromSearchParam,
      isResubmittableTaskJob,
      isEditableQueuedTaskJob,
      resolveQueuePrefillModeFromJob,
      resolveQueueDetailPrefillAction,
      resolveQueuePrefillSubmitTarget,
      resolveQueueSubmitRuntimeUiState,
      resolveQueueSubmitPriorityForRuntime,
      validateSubmitRuntime,
      applyElementVisibility,
      persistSubmitDraftsToStorage,
      submitDraftController,
      normalizeDashboardDetailSegment,
      resolvePromotedQueueRoute,
      normalizeDashboardRoutePath,
      stringifySkillArgs,
      buildQueueSubmissionDraftFromJob,
    };
    window.__temporalDashboardTest = {
      buildTemporalActionRequest,
      buildTemporalArtifactCreatePayload,
      buildTemporalArtifactEditUpdatePayload,
      buildTemporalArtifactLinkPayload,
      completeTemporalArtifactUpload,
      createTemporalArtifactPlaceholder,
      fetchTemporalArtifactMetadata,
      normalizeDashboardRoutePath,
      renderTemporalActionButtons,
      renderTemporalDetailMarkup,
      resolveTemporalActionResultMessage,
      resolveTemporalActionSurface,
      resolveTemporalArtifactPresentation,
      resolveTemporalArtifactsRequest,
      resolveTemporalDetailModel,
      resolveTemporalTaskRunId,
      resolveTemporalRunId,
      temporalLogTailingAvailable,
      deriveTemporalTitle,
      temporalWaitingReason,
      toTemporalRows,
      uploadTemporalArtifactContent,
      resolveArtifactUploadTarget,
      buildArtifactUploadHeaders,
      withTemporalSourceFlag,
    };
    window.__queueLayoutTest = {
      taskFieldDefinitions,
      renderTaskFieldValue,
      renderTaskTable,
      renderTaskCards,
      renderTaskLayouts,
      renderActivePageContent,
      renderRowsTable,
      filterProposalsByTag,
      renderProposalTable,
      renderProposalCards,
      renderProposalLayouts,
      renderProposalActionFeedback,
      sortRows,
      sortRowsByColumn,
      rowOrderKey,
      buildRowOrderIndex,
      stabilizeRowsByPreviousOrder,

      toTemporalRows,
      parseQueuePaginationFromSearch,
      applyQueuePaginationToSearch,
      resetQueuePaginationState,
    };
    window.__temporalRunHistoryTest = {
      resolveTemporalDetailContext,
      resolveManifestIngestContext,
    };
  }

  async function apiPromoteProposal(proposalId, overrides = {}) {
    const endpointTemplate =
      proposalsSourceConfig.promote || "/api/proposals/{id}/promote";
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify(overrides),
    });
  }

  async function apiDismissProposal(proposalId, note = null) {
    const endpointTemplate =
      proposalsSourceConfig.dismiss || "/api/proposals/{id}/dismiss";
    const body = {};
    if (note) {
      body.note = note;
    }
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async function apiUpdateProposalPriority(proposalId, priority) {
    const endpointTemplate =
      proposalsSourceConfig.priority || "/api/proposals/{id}/priority";
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify({ priority }),
    });
  }

  function cloneTaskRequest(node) {
    try {
      return JSON.parse(JSON.stringify(node || {}));
    } catch {
      return {};
    }
  }

  function buildEditOverrides(row) {
    const baseRequest = cloneTaskRequest(pick(row, "taskCreateRequest") || {});
    const payload =
      baseRequest.payload && typeof baseRequest.payload === "object"
        ? baseRequest.payload
        : {};
    baseRequest.payload = payload;
    const taskNode =
      payload.task && typeof payload.task === "object" ? payload.task : {};
    payload.task = taskNode;
    const publishNode =
      taskNode.publish && typeof taskNode.publish === "object"
        ? taskNode.publish
        : {};
    taskNode.publish = publishNode;
    const gitNode =
      taskNode.git && typeof taskNode.git === "object" ? taskNode.git : {};
    taskNode.git = gitNode;

    const currentInstructions =
      taskNode.instructions || pick(row, "summary") || "";
    const updatedInstructions = window.prompt(
      "Task instructions",
      currentInstructions,
    );
    if (updatedInstructions === null) {
      return null;
    }
    taskNode.instructions = updatedInstructions;
    const publishMode = window.prompt(
      "Publish mode (branch/pr/none)",
      publishNode.mode || defaultPublishMode,
    );
    if (publishMode === null) {
      return null;
    }
    publishNode.mode = publishMode || publishNode.mode || defaultPublishMode;
    const startingBranch = window.prompt(
      "Starting branch (leave blank to keep current)",
      gitNode.startingBranch || "",
    );
    if (startingBranch === null) {
      return null;
    }
    gitNode.startingBranch = startingBranch || null;

    const runtimeNode =
      taskNode.runtime && typeof taskNode.runtime === "object"
        ? taskNode.runtime
        : {};
    taskNode.runtime = runtimeNode;
    const runtimeMode = window.prompt(
      `Agent runtime (${supportedTaskRuntimes.join("/")}, or leave blank for default)`,
      runtimeNode.mode || "",
    );
    if (runtimeMode === null) {
      return null;
    }
    if (runtimeMode.trim()) {
      runtimeNode.mode = runtimeMode.trim();
    }

    const queuePriority = window.prompt(
      "Queue priority",
      String(baseRequest.priority ?? 0),
    );
    if (queuePriority === null) {
      return null;
    }
    const maxAttempts = window.prompt(
      "Max attempts",
      String(baseRequest.maxAttempts ?? 3),
    );
    if (maxAttempts === null) {
      return null;
    }
    const parsedPriority = Number(queuePriority);
    const parsedAttempts = Number(maxAttempts);
    baseRequest.priority = Number.isFinite(parsedPriority)
      ? parsedPriority
      : baseRequest.priority;
    baseRequest.maxAttempts = Number.isFinite(parsedAttempts)
      ? parsedAttempts
      : baseRequest.maxAttempts;

    return {
      priority: baseRequest.priority,
      maxAttempts: baseRequest.maxAttempts,
      taskCreateRequestOverride: baseRequest,
    };
  }

  function toTemporalRows(items) {
    return (Array.isArray(items) ? items : []).map((item) => {
      const memo = pick(item, "memo") || {};
      const searchAttributes = pick(item, "searchAttributes") || {};
      const workflowId = String(pick(item, "workflowId", "taskId") || "").trim();
      const rawState = String(pick(item, "rawState", "state") || "initializing").trim().toLowerCase();
      const updatedAt = pick(item, "updatedAt") || pick(item, "startedAt");
      const ownerType = String(
        pick(item, "ownerType", "OwnerType") ||
        pick(searchAttributes, "mm_owner_type", "ownerType") ||
        "user",
      ).trim().toLowerCase() || "user";
      return {
        source: "temporal",
        sourceLabel: "Temporal",
        id: workflowId,
        taskId: workflowId,
        workflowId,
        temporalRunId: pick(item, "temporalRunId", "runId") || "",
        namespace: pick(item, "namespace") || "",
        workflowType: pick(item, "workflowType") || "",
        entry: String(
          pick(item, "entry", "Entry") ||
          pick(searchAttributes, "mm_entry", "entry") ||
          pick(memo, "entry") ||
          "",
        ).trim(),
        ownerType,
        ownerId: String(
          pick(item, "ownerId", "OwnerId") || pick(searchAttributes, "mm_owner_id", "ownerId") || "",
        ).trim(),
        repository: String(
          pick(item, "repository", "Repository") ||
          pick(searchAttributes, "mm_repository", "mm_repo", "repository") ||
          "",
        ).trim(),
        integration: String(
          pick(item, "integration", "Integration") ||
          pick(searchAttributes, "mm_integration", "integration") ||
          "",
        ).trim(),
        queueName: "-",
        runtimeMode: String([
          pick(item, "targetRuntime", "target_runtime", "runtime"),
          pick(searchAttributes, "mm_target_runtime", "mm_runtime", "runtime"),
          pick(memo, "targetRuntime", "runtime"),
        ].find(v => v != null && v !== "") || "").trim() || null,
        skillId: String([
          pick(item, "targetSkill", "target_skill", "skillId", "skill_id", "skill"),
          pick(searchAttributes, "mm_target_skill", "mm_skill_id", "mm_skill", "skillId", "skill"),
          pick(memo, "targetSkill", "skillId", "skill"),
        ].find(v => v != null && v !== "") || "").trim() || null,
        rawStatus: rawState,
        rawState,
        temporalStatus: pick(item, "temporalStatus") || "",
        closeStatus: pick(item, "closeStatus") || "",
        summary: String(pick(item, "summary") || "").trim(),
        waitingReason: temporalWaitingReason(item),
        attentionRequired: Boolean(pick(item, "attentionRequired")),
        title: String(pick(item, "title") || "Temporal execution").trim(),
        createdAt: pick(item, "createdAt") || pick(item, "startedAt"),
        scheduledFor: pick(item, "scheduledFor"),
        startedAt: pick(item, "startedAt"),
        finishedAt: pick(item, "closedAt"),
        updatedAt,
        closedAt: pick(item, "closedAt"),
        sortTimestamp: updatedAt || pick(item, "closedAt"),
        link: buildUnifiedTaskDetailRoute(workflowId, "temporal"),
      };
    });
  }

  function sortRows(rows) {
    return rows.sort((left, right) => {
      const leftTime =
        Date.parse(
          left.sortTimestamp ||
          left.updatedAt ||
          left.startedAt ||
          left.createdAt ||
          left.finishedAt ||
          0,
        ) || 0;
      const rightTime =
        Date.parse(
          right.sortTimestamp ||
          right.updatedAt ||
          right.startedAt ||
          right.createdAt ||
          right.finishedAt ||
          0,
        ) || 0;
      if (rightTime !== leftTime) {
        return rightTime - leftTime;
      }
      return String(right.id || "").localeCompare(String(left.id || ""));
    });
  }

  const TIMESTAMP_SORT_FIELDS = new Set(["scheduledFor", "createdAt", "startedAt", "finishedAt"]);

  function sortRowsByColumn(rows, field, direction) {
    const dir = direction === "asc" ? 1 : -1;
    const copy = Array.isArray(rows) ? rows.slice() : [];
    copy.sort((left, right) => {
      let leftVal;
      let rightVal;
      if (TIMESTAMP_SORT_FIELDS.has(field)) {
        const getRawTimestamp = (row) => field === "scheduledFor" ? (row[field] || row.createdAt) : row[field];
        leftVal = Date.parse(getRawTimestamp(left) || 0) || 0;
        rightVal = Date.parse(getRawTimestamp(right) || 0) || 0;
        if (leftVal !== rightVal) {
          return dir * (leftVal - rightVal);
        }
      } else if (field === "type") {
        leftVal = String(left.sourceLabel || "").toLowerCase();
        rightVal = String(right.sourceLabel || "").toLowerCase();
        const cmp = leftVal.localeCompare(rightVal);
        if (cmp !== 0) {
          return dir * cmp;
        }
      } else if (field === "status") {
        leftVal = String(left.rawStatus || "").toLowerCase();
        rightVal = String(right.rawStatus || "").toLowerCase();
        const cmp = leftVal.localeCompare(rightVal);
        if (cmp !== 0) {
          return dir * cmp;
        }
      } else if (field === "finishOutcome") {
        leftVal = String(left.finishOutcomeCode || "").toLowerCase();
        rightVal = String(right.finishOutcomeCode || "").toLowerCase();
        const cmp = leftVal.localeCompare(rightVal);
        if (cmp !== 0) {
          return dir * cmp;
        }
      } else {
        leftVal = String(left[field] || "").toLowerCase();
        rightVal = String(right[field] || "").toLowerCase();
        const cmp = leftVal.localeCompare(rightVal);
        if (cmp !== 0) {
          return dir * cmp;
        }
      }
      return String(left.id || "").localeCompare(String(right.id || ""));
    });
    return copy;
  }

  function rowOrderKey(row) {
    const source = String(pick(row, "source") || "").trim().toLowerCase() || "unknown";
    const id = String(pick(row, "id") || "").trim();
    return `${source}:${id}`;
  }

  function buildRowOrderIndex(rows) {
    const index = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row, position) => {
      index.set(rowOrderKey(row), position);
    });
    return index;
  }

  function stabilizeRowsByPreviousOrder(rows, previousOrderIndex) {
    const sorted = sortRows(Array.isArray(rows) ? rows.slice() : []);
    if (!(previousOrderIndex instanceof Map) || previousOrderIndex.size === 0) {
      return sorted;
    }
    const ranked = sorted.map((row, baseIndex) => {
      const key = rowOrderKey(row);
      const hasPrevious = previousOrderIndex.has(key);
      return {
        row,
        baseIndex,
        hasPrevious,
        previousIndex: hasPrevious
          ? Number(previousOrderIndex.get(key))
          : Number.POSITIVE_INFINITY,
      };
    });
    ranked.sort((left, right) => {
      if (left.hasPrevious && right.hasPrevious) {
        if (left.previousIndex !== right.previousIndex) {
          return left.previousIndex - right.previousIndex;
        }
        return left.baseIndex - right.baseIndex;
      }
      if (left.hasPrevious !== right.hasPrevious) {
        // Let newly discovered rows surface first without reshuffling existing ones.
        return left.hasPrevious ? 1 : -1;
      }
      return left.baseIndex - right.baseIndex;
    });
    return ranked.map((entry) => entry.row);
  }

  async function renderActivePage() {
    const activeSubtitle = `Running and queued work.`;
    setView(
      "Active Tasks",
      activeSubtitle,
      "<p class='loading'>Loading active runs...</p>",
      { showAutoRefreshControls: true },
    );

    let pageActive = true;
    registerDisposer(() => {
      pageActive = false;
    });

    const loader = async () => {
      const errors = [];
      const rows = [];

      const requests = [
        {
          source: "temporal-active",
          call: () =>
            fetchJson(
              withTemporalSourceFlag(
                `${temporalSourceConfig.list || "/api/executions"}?pageSize=${ACTIVE_TEMPORAL_FETCH_LIMIT}`,
              ),
            ),
          transform: (payload) =>
            toTemporalRows(payload?.items || []).filter((row) => {
              const state = String(row.rawState || "").trim().toLowerCase();
              return !["succeeded", "failed", "canceled"].includes(state);
            }),
        }
      ];

      const settled = await Promise.allSettled(requests.map((req) => req.call()));
      settled.forEach((result, index) => {
        const request = requests[index];
        if (result.status === "fulfilled") {
          rows.push(...request.transform(result.value));
        } else {
          console.error("active page data source failed", request.source, result.reason);
          errors.push(request.source);
        }
      });

      if (!pageActive) {
        return;
      }
      setView(
        "Active Tasks",
        activeSubtitle,
        renderActivePageContent(rows, errors),
        { showAutoRefreshControls: true },
      );
    };

    startPolling(loader, pollIntervals.list);
  }

  function parseQueuePaginationFromSearch(queryString) {
    const params = new URLSearchParams(queryString || "");
    const parsedLimit = Number(params.get("limit") || DEFAULT_QUEUE_PAGE_SIZE);
    return {
      limit: QUEUE_PAGE_SIZE_OPTIONS.includes(parsedLimit) ? parsedLimit : DEFAULT_QUEUE_PAGE_SIZE,
      cursor: String(params.get("cursor") || "").trim() || null,
    };
  }

  function applyQueuePaginationToSearch(state, params) {
    params.set("limit", String(state.limit));
    if (state.cursor) {
      params.set("cursor", state.cursor);
    } else {
      params.delete("cursor");
    }
  }

  function resetQueuePaginationState(state) {
    state.cursor = null;
    state.cursorStack = [];
    state.nextCursor = null;
    state.hasMore = false;
    state.pageStart = 0;
    state.pageEnd = 0;
  }

  async function renderQueueListPage() {
    const initialQuery = new URLSearchParams(window.location.search || "");
    const initialSource = String(initialQuery.get("source") || "").trim().toLowerCase();
    const initialPagination = parseQueuePaginationFromSearch(window.location.search || "");
    const initialFilterRuntime = String(initialQuery.get("filterRuntime") || "").trim().toLowerCase();
    const initialTemporalToken = String(initialQuery.get("nextPageToken") || "").trim() || null;
    const allowedSources = ["", "temporal"];
    setView(
      "Tasks List",
      "Unified tasks across available execution sources.",
      "<p class='loading'>Loading tasks...</p>",
      { showAutoRefreshControls: true },
    );

    const filterState = {
      runtime: initialFilterRuntime,
      skill: String(initialQuery.get("skill") || "").trim().toLowerCase(),
      stageStatus: String(initialQuery.get("stageStatus") || "").trim().toLowerCase(),
      publishMode: String(initialQuery.get("publishMode") || "").trim().toLowerCase(),
      source: allowedSources.includes(initialSource) ? initialSource : "",
      workflowType: String(initialQuery.get("workflowType") || "").trim(),
      temporalState: String(initialQuery.get("state") || "").trim().toLowerCase(),
      entry: String(initialQuery.get("entry") || "").trim().toLowerCase(),
      ownerType: String(initialQuery.get("ownerType") || "").trim().toLowerCase(),
      ownerId: String(initialQuery.get("ownerId") || "").trim(),
      repository: String(initialQuery.get("repo") || "").trim(),
      integration: String(initialQuery.get("integration") || "").trim(),
    };
    const telemetryEndpoint =
      (queueSourceConfig.migrationTelemetry || "/api/queue/telemetry/migration") +
      "?windowHours=168";
    const telemetryRefreshMs = Math.max(
      60000,
      Math.max(1000, Number(pollIntervals.list) || 5000) * 12,
    );
    let telemetryPayload = null;
    let telemetryInFlight = null;
    let telemetryLastRequestedAt = 0;
    let currentRows = [];
    let currentTemporalCount = null;
    let currentTemporalCountMode = "";
    const paginationState = {
      limit: initialPagination.limit,
      cursor: initialSource === "temporal" ? initialTemporalToken : initialPagination.cursor,
      cursorStack: [],
      nextCursor: null,
      hasMore: false,
      pageStart: 0,
      pageEnd: 0,
    };
    let stableListOrderIndex = new Map();
    let columnSort = { field: "scheduledFor", direction: "desc" };
    let pageActive = true;
    registerDisposer(() => {
      pageActive = false;
    });

    function syncListQueryParams() {
      const params = new URLSearchParams(window.location.search || "");
      const filterEntries = [
        ["source", filterState.source],
        ["filterRuntime", filterState.runtime],
        ["skill", filterState.skill],
        ["stageStatus", filterState.stageStatus],
        ["publishMode", filterState.publishMode],
        ["workflowType", filterState.workflowType],
        ["state", filterState.temporalState],
        ["entry", filterState.entry],
        ["ownerType", filterState.ownerType],
        ["ownerId", filterState.ownerId],
        ["repo", filterState.repository],
        ["integration", filterState.integration],
      ];
      filterEntries.forEach(([key, value]) => {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      });
      params.set("limit", String(paginationState.limit));
      if (paginationState.cursor) {
        params.set("nextPageToken", paginationState.cursor);
      } else {
        params.delete("nextPageToken");
      }
      params.delete("cursor");
      const queryText = params.toString();
      const nextUrl = queryText
        ? `${window.location.pathname}?${queryText}`
        : window.location.pathname;
      window.history.replaceState({}, "", nextUrl);
    }

    function resetPaginationToFirstPage() {
      stableListOrderIndex = new Map();
      resetQueuePaginationState(paginationState);
      syncListQueryParams();
    }

    function applyQueueFilters(rows) {
      return rows.filter((row) => {
        if (filterState.source) {
          const rowSource = String(row.source || "").trim().toLowerCase();
          if (rowSource !== filterState.source) {
            return false;
          }
        }
        if (filterState.source === "temporal") {
          if (filterState.workflowType) {
            const rowWorkflowType = String(row.workflowType || "").trim();
            if (rowWorkflowType !== filterState.workflowType) {
              return false;
            }
          }
          if (filterState.temporalState) {
            const rowState = String(row.rawState || "").trim().toLowerCase();
            if (rowState !== filterState.temporalState) {
              return false;
            }
          }
          if (filterState.entry) {
            const rowEntry = String(row.entry || "").trim().toLowerCase();
            if (rowEntry !== filterState.entry) {
              return false;
            }
          }
          return true;
        }
        if (filterState.runtime) {
          const rowRuntime = String(row.runtimeMode || "").trim().toLowerCase();
          if (rowRuntime !== filterState.runtime) {
            return false;
          }
        }

        if (filterState.skill) {
          const rowSkill = String(row.skillId || "").trim().toLowerCase();
          if (!rowSkill.includes(filterState.skill)) {
            return false;
          }
        }

        if (filterState.stageStatus) {
          const normalizedStatus = normalizeStatus(row.source || "queue", row.rawStatus);
          if (normalizedStatus !== filterState.stageStatus) {
            return false;
          }
        }

        if (filterState.publishMode) {
          if (row.source !== "queue") {
            return false;
          }
          const publishMode =
            extractPublishModeFromPayload(row.payload || {}) || defaultPublishMode;
          if (publishMode !== filterState.publishMode) {
            return false;
          }
        }

        return true;
      });
    }

    function renderQueueFilters() {
      const sourceOptions = [
        ["", "All sources"],
        ["queue", "Queue"],
        ...(temporalListEnabled ? [["temporal", "Temporal"]] : []),
      ]
        .map(
          ([value, label]) =>
            `<option value="${escapeHtml(value)}" ${filterState.source === value ? "selected" : ""
            }>${escapeHtml(label)}</option>`,
        )
        .join("");
      const pageSizeOptions = QUEUE_PAGE_SIZE_OPTIONS.map(
        (value) =>
          `<option value="${escapeHtml(value)}" ${paginationState.limit === value ? "selected" : ""
          }>${escapeHtml(value)}</option>`,
      ).join("");
      if (filterState.source === "temporal") {
        const workflowTypeOptions = ["MoonMind.Run", "MoonMind.ManifestIngest"]
          .map(
            (value) =>
              `<option value="${escapeHtml(value)}" ${filterState.workflowType === value ? "selected" : ""
              }>${escapeHtml(value)}</option>`,
          )
          .join("");
        const rawStateOptions = [
          "initializing",
          "planning",
          "executing",
          "awaiting_external",
          "finalizing",
          "succeeded",
          "failed",
          "canceled",
        ]
          .map(
            (value) =>
              `<option value="${escapeHtml(value)}" ${filterState.temporalState === value ? "selected" : ""
              }>${escapeHtml(value)}</option>`,
          )
          .join("");
        const entryOptions = ["run", "manifest"]
          .map(
            (value) =>
              `<option value="${escapeHtml(value)}" ${filterState.entry === value ? "selected" : ""
              }>${escapeHtml(value)}</option>`,
          )
          .join("");
        return `
          <form id="queue-filter-form">
            <div class="grid-2">
              <label>Source
                <select name="source">
                  ${sourceOptions}
                </select>
              </label>
              <label>Page Size
                <select name="pageSize">
                  ${pageSizeOptions}
                </select>
              </label>
            </div>
            <div class="grid-2">
              <label>Workflow Type
                <select name="workflowType">
                  <option value="">(all)</option>
                  ${workflowTypeOptions}
                </select>
              </label>
              <label>Temporal State
                <select name="temporalState">
                  <option value="">(all)</option>
                  ${rawStateOptions}
                </select>
              </label>
            </div>
            <label>Entry
              <select name="entry">
                <option value="">(all)</option>
                ${entryOptions}
              </select>
            </label>
          </form>
        `;
      }
      const runtimeOptions = renderRuntimeOptions(supportedTaskRuntimes, filterState.runtime);
      const stageStatusOptions = [
        ["queued", "queued"],
        ["running", "running"],
        ["completed", "completed"],
        ["failed", "failed"],
        ["canceled", "canceled"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${escapeHtml(value)}" ${filterState.stageStatus === value ? "selected" : ""
            }>${escapeHtml(label)}</option>`,
        )
        .join("");
      const publishOptions = ["none", "branch", "pr"]
        .map(
          (mode) =>
            `<option value="${escapeHtml(mode)}" ${filterState.publishMode === mode ? "selected" : ""
            }>${escapeHtml(mode)}</option>`,
        )
        .join("");

      return `
        <form id="queue-filter-form">
          <label>Source
            <select name="source">
              ${sourceOptions}
            </select>
          </label>
          <div class="grid-2">
            <label>Runtime
              <select name="runtime">
                <option value="">(all)</option>
                ${runtimeOptions}
              </select>
            </label>
            <label>Skill
              <input name="skill" placeholder="auto, speckit-orchestrate, ..." value="${escapeHtml(
        filterState.skill,
      )}" />
            </label>
          </div>
          <div class="grid-2">
            <label>Stage Status
              <select name="stageStatus">
                <option value="">(all)</option>
                ${stageStatusOptions}
              </select>
            </label>
            <label>Page Size
              <select name="pageSize">
                ${pageSizeOptions}
              </select>
            </label>
          </div>
          <label>Publish Mode
            <select name="publishMode">
              <option value="">(all)</option>
              ${publishOptions}
            </select>
          </label>
        </form>
      `;
    }

    function renderQueuePaginationSummary(rows, filteredRows) {
      const page = paginationState.cursorStack.length + 1;
      const hasRows = rows.length > 0;
      const showingRange = paginationState.pageEnd > 0
        ? `${paginationState.pageStart}-${paginationState.pageEnd}`
        : "0";

      let totalCountInfo = "";
      if (filterState.source === "temporal" && typeof currentTemporalCount === "number") {
        const modeLabel = currentTemporalCountMode && currentTemporalCountMode !== "exact" 
          ? ` (${currentTemporalCountMode})` 
          : "";
        totalCountInfo = ` of ${currentTemporalCount}${modeLabel}`;
      }

      return `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 0.5rem 0; margin-bottom: 0.75rem;">
          <div style="font-size: 0.85rem; color: rgba(255, 255, 255, 0.7);">
            <strong style="color: rgba(255, 255, 255, 0.95); font-weight: 500;">Page ${escapeHtml(page)}</strong>
            <span style="margin: 0 0.5rem; opacity: 0.3;">|</span>
            Showing ${escapeHtml(showingRange)}${escapeHtml(totalCountInfo)} tasks
            ${filteredRows.length !== rows.length ? `<span style="opacity: 0.8; margin-left: 0.25rem;">(${escapeHtml(filteredRows.length)} filtered on page)</span>` : ""}
          </div>
          <div style="display: flex; gap: 0.4rem;">
            <button type="button" class="secondary" title="Previous page" data-queue-page-prev ${
              paginationState.cursorStack.length === 0 || !hasRows ? "disabled" : ""
            } style="padding: 0.2rem 0.6rem; min-width: unset; display: inline-flex; align-items: center; justify-content: center;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg></button>
            <button type="button" class="secondary" title="Next page" data-queue-page-next ${
              paginationState.hasMore ? "" : "disabled"
            } style="padding: 0.2rem 0.6rem; min-width: unset; display: inline-flex; align-items: center; justify-content: center;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"></polyline></svg></button>
          </div>
        </div>
      `;
    }

    function renderTelemetrySummary(snapshot) {
      if (!snapshot || typeof snapshot !== "object") {
        return "";
      }
      const volumes = pick(snapshot, "jobVolumeByType") || {};
      const publish = pick(snapshot, "publishOutcomes") || {};
      const totalJobs = Number(pick(snapshot, "totalJobs") || 0);
      const publishedRate = Number(pick(publish, "publishedRate") || 0);
      const failedRate = Number(pick(publish, "failedRate") || 0);
      return `
        <div class="grid-2">
          <div class="card"><strong>Total Jobs (Window):</strong> ${escapeHtml(totalJobs)}</div>
          <div class="card"><strong>Task Jobs:</strong> ${escapeHtml(Number(volumes.task || 0))}</div>
          <div class="card"><strong>Publish Success Rate:</strong> ${escapeHtml(
        (publishedRate * 100).toFixed(1),
      )}%</div>
          <div class="card"><strong>Publish Failure Rate:</strong> ${escapeHtml(
        (failedRate * 100).toFixed(1),
      )}%</div>
        </div>
      `;
    }

    function renderQueueList(rows) {
      if (!pageActive) {
        return;
      }
      const filteredRows = applyQueueFilters(rows);
      const sortedFilteredRows = columnSort.field
        ? sortRowsByColumn(filteredRows.slice(), columnSort.field, columnSort.direction)
        : filteredRows;
      const telemetryHtml =
        filterState.source === "temporal" ? "" : renderTelemetrySummary(telemetryPayload);
      const paginationHtml = renderQueuePaginationSummary(rows, filteredRows);
      setView(
        "Tasks List",
        "",
        `${telemetryHtml}${renderQueueFilters()}${paginationHtml}${renderTaskLayouts(
          sortedFilteredRows,
          columnSort.field ? columnSort : null,
        )}`,
        { showAutoRefreshControls: true },
      );
      attachFilterHandlers(rows);
      attachColumnSortHandlers(rows);
    }

    function attachColumnSortHandlers(rows) {
      const sortHeaders = root.querySelectorAll("[data-sort-field]");
      sortHeaders.forEach((th) => {
        th.addEventListener("click", () => {
          const field = String(th.getAttribute("data-sort-field") || "").trim();
          if (!field) {
            return;
          }
          if (columnSort.field === field) {
            columnSort.direction = columnSort.direction === "asc" ? "desc" : "asc";
          } else {
            columnSort.field = field;
            columnSort.direction = "asc";
          }
          renderQueueList(rows);
        });
      });
    }

    function attachFilterHandlers(rows) {
      const filterForm = document.getElementById("queue-filter-form");
      if (!filterForm) {
        return;
      }
      const runtimeField = filterForm.elements.namedItem("runtime");
      const skillField = filterForm.elements.namedItem("skill");
      const stageField = filterForm.elements.namedItem("stageStatus");
      const publishField = filterForm.elements.namedItem("publishMode");
      const sourceField = filterForm.elements.namedItem("source");
      const workflowTypeField = filterForm.elements.namedItem("workflowType");
      const temporalStateField = filterForm.elements.namedItem("temporalState");
      const entryField = filterForm.elements.namedItem("entry");
      const pageSizeField = filterForm.elements.namedItem("pageSize");
      const prevButtons = root.querySelectorAll("[data-queue-page-prev]");
      const nextButtons = root.querySelectorAll("[data-queue-page-next]");

      if (sourceField) {
        sourceField.addEventListener("change", () => {
          const nextSource = String(sourceField.value || "").trim().toLowerCase();
          filterState.source = allowedSources.includes(nextSource) ? nextSource : "";
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue source filter update failed", error);
          });
        });
      }
      if (runtimeField) {
        runtimeField.addEventListener("change", () => {
          filterState.runtime = normalizeTaskRuntimeInput(runtimeField.value);
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue runtime filter update failed", error);
          });
        });
      }
      if (skillField) {
        skillField.addEventListener("input", () => {
          filterState.skill = String(skillField.value || "").trim().toLowerCase();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue skill filter update failed", error);
          });
        });
      }
      if (stageField) {
        stageField.addEventListener("change", () => {
          filterState.stageStatus = String(stageField.value || "").trim().toLowerCase();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue status filter update failed", error);
          });
        });
      }
      if (publishField) {
        publishField.addEventListener("change", () => {
          filterState.publishMode = String(publishField.value || "").trim().toLowerCase();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue publish filter update failed", error);
          });
        });
      }
      if (workflowTypeField) {
        workflowTypeField.addEventListener("change", () => {
          filterState.workflowType = String(workflowTypeField.value || "").trim();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("temporal workflow type filter update failed", error);
          });
        });
      }
      if (temporalStateField) {
        temporalStateField.addEventListener("change", () => {
          filterState.temporalState = String(temporalStateField.value || "").trim().toLowerCase();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("temporal state filter update failed", error);
          });
        });
      }
      if (entryField) {
        entryField.addEventListener("change", () => {
          filterState.entry = String(entryField.value || "").trim().toLowerCase();
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("temporal entry filter update failed", error);
          });
        });
      }
      if (pageSizeField) {
        pageSizeField.addEventListener("change", () => {
          const parsed = Number(pageSizeField.value || DEFAULT_QUEUE_PAGE_SIZE);
          if (!QUEUE_PAGE_SIZE_OPTIONS.includes(parsed)) {
            return;
          }
          paginationState.limit = parsed;
          resetPaginationToFirstPage();
          load().catch((error) => {
            console.error("queue page size update failed", error);
          });
        });
      }

      prevButtons.forEach((button) => {
        button.addEventListener("click", () => {
          if (paginationState.cursorStack.length === 0) {
            return;
          }
          stableListOrderIndex = new Map();
          const previousCursor = paginationState.cursorStack.pop() || null;
          paginationState.cursor = previousCursor || null;
          syncListQueryParams();
          load().catch((error) => {
            console.error("queue previous page load failed", error);
          });
        });
      });

      nextButtons.forEach((button) => {
        button.addEventListener("click", () => {
          if (!paginationState.hasMore || !paginationState.nextCursor) {
            return;
          }
          stableListOrderIndex = new Map();
          paginationState.cursorStack.push(paginationState.cursor || "");
          paginationState.cursor = paginationState.nextCursor;
          syncListQueryParams();
          load().catch((error) => {
            console.error("queue next page load failed", error);
          });
        });
      });
    }

    async function refreshTelemetryIfStale() {
      if (!pageActive) {
        return;
      }
      const now = Date.now();
      if (telemetryInFlight) {
        return telemetryInFlight;
      }
      if (now - telemetryLastRequestedAt < telemetryRefreshMs) {
        return;
      }

      telemetryLastRequestedAt = now;
      telemetryInFlight = (async () => {
        try {
          const nextPayload = await fetchJson(telemetryEndpoint);
          if (!pageActive) {
            return;
          }
          telemetryPayload = nextPayload;
          renderQueueList(currentRows);
        } catch (error) {
          console.warn("queue migration telemetry load failed");
        } finally {
          telemetryInFlight = null;
        }
      })();
      return telemetryInFlight;
    }

    const load = async () => {
      try {
        await _load();
      } catch (error) {
        console.error("Queue list load failed", error);
        if (pageActive) {
          setView(
            "Tasks List",
            "Unified tasks across available execution sources.",
            `<div class="error-notice" style="color: var(--error-text, #d32f2f); background-color: var(--error-bg, #ffebee); padding: 1rem; border-radius: 4px; margin-bottom: 1rem;">Failed to load tasks: ${escapeHtml(String(error.message || error))}</div>`,
            { showAutoRefreshControls: true },
          );
        }
      }
    };

    const _load = async () => {
      syncListQueryParams();
      const params = new URLSearchParams();
      params.set("pageSize", String(paginationState.limit));
      if (paginationState.cursor) {
        params.set("nextPageToken", paginationState.cursor);
      }
      if (filterState.workflowType) {
        params.set("workflowType", filterState.workflowType);
      }
      if (filterState.temporalState) {
        params.set("state", filterState.temporalState);
      }
      if (filterState.entry) {
        params.set("entry", filterState.entry);
      }
      if (filterState.ownerType) {
        params.set("ownerType", filterState.ownerType);
      }
      if (filterState.ownerId) {
        params.set("ownerId", filterState.ownerId);
      }
      if (filterState.repository) {
        params.set("repo", filterState.repository);
      }
      if (filterState.integration) {
        params.set("integration", filterState.integration);
      }
      const temporalListEndpoint = temporalSourceConfig.list || "/api/executions";
      const payload = await fetchJson(
        withTemporalSourceFlag(`${temporalListEndpoint}?${params.toString()}`),
      );
      if (!pageActive) {
        return;
      }
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const filteredTemporalRows = toTemporalRows(items);
      const payloadNextCursor = payload && typeof payload === "object"
        ? String(payload.nextPageToken || "").trim() || null
        : null;
      currentTemporalCount =
        payload
          && typeof payload === "object"
          && typeof payload.count === "number"
          ? payload.count
          : null;
      currentTemporalCountMode =
        currentTemporalCount !== null && payload && typeof payload === "object"
          ? String(payload.countMode || "").trim()
          : "";
      paginationState.nextCursor = payloadNextCursor;
      paginationState.hasMore = Boolean(payloadNextCursor);
      if (paginationState.cursor && items.length === 0 && paginationState.cursorStack.length > 0) {
        const previousCursor = paginationState.cursorStack.pop() || null;
        paginationState.cursor = previousCursor || null;
        syncListQueryParams();
        await load();
        return;
      }
      const pageIndex = paginationState.cursorStack.length;
      paginationState.pageStart = filteredTemporalRows.length > 0
        ? pageIndex * paginationState.limit + 1
        : 0;
      paginationState.pageEnd = pageIndex * paginationState.limit + filteredTemporalRows.length;
      currentRows = stabilizeRowsByPreviousOrder(
        filteredTemporalRows,
        stableListOrderIndex,
      );
      stableListOrderIndex = buildRowOrderIndex(currentRows);
      renderQueueList(currentRows);
      refreshTelemetryIfStale().catch(() => {
        console.warn("queue telemetry refresh failed");
      });
    };

    startPolling(load, pollIntervals.list);
  }
  async function renderManifestListPage() {
    setView(
      "Manifest Runs",
      "All manifest ingestion jobs (type=manifest).",
      "<p class='loading'>Loading manifest jobs...</p>",
      { showAutoRefreshControls: true },
    );

    const load = async () => {
      const endpoint = "/api/executions?entry=manifest&limit=200";
      const payload = await fetchJson(endpoint);
      const rows = sortRows(
        toTemporalRows(payload?.items || []).map((row) => ({
          ...row,
          source: "manifests",
          sourceLabel: "Manifest",
        })),
      );
      setView(
        "Manifest Runs",
        "All manifest ingestion jobs (type=manifest).",
        `<div class="actions"><a href="/tasks/manifests/new"><button type="button">New Manifest Run</button></a></div>${renderRowsTable(rows)}`,
        { showAutoRefreshControls: true },
      );
    };

    startPolling(load, pollIntervals.list);
  }

  function renderManifestSubmitPage() {
    setView(
      "Submit Manifest Run",
      "Queue a manifest ingestion job via the shared Agent Queue.",
      `
      <form id="manifest-submit-form">
        <label>Manifest Name
          <input name="manifestName" required />
        </label>
        <div class="grid-2">
          <label>Action
            <select name="action">
              <option value="run" selected>run</option>
              <option value="plan">plan</option>
            </select>
          </label>
          <label>Source Type
            <select name="sourceKind">
              <option value="inline" selected>Inline YAML</option>
              <option value="registry">Registry</option>
            </select>
          </label>
        </div>
        <label id="manifest-inline-field">
          Manifest YAML
          <textarea name="manifestContent" placeholder="Paste manifest YAML..." rows="12" required></textarea>
        </label>
        <label id="manifest-registry-field" class="hidden">
          Registry Name
          <input name="registryName" placeholder="existing manifest name" />
        </label>
        <div class="grid-3">
          <label class="checkbox">
            <input type="checkbox" name="dryRun" />
            Dry Run
          </label>
          <label class="checkbox">
            <input type="checkbox" name="forceFull" />
            Force Full Sync
          </label>
          <label>Max Docs
            <input type="number" min="1" name="maxDocs" placeholder="optional" />
          </label>
        </div>
        <label>Queue Priority
          <div class="priority-slider-container">
            <input type="range" name="priority" min="-10" max="10" value="0" oninput="this.parentElement.querySelector('output').value = this.value" />
            <output>0</output>
          </div>
        </label>
        <div class="actions">
          <button type="submit" class="queue-submit-primary">Create Manifest Job</button>
          <a href="/tasks/manifests"><button class="secondary" type="button">Cancel</button></a>
        </div>
        <p class="small" id="manifest-submit-message"></p>
      </form>
      `,
    );

    const form = document.getElementById("manifest-submit-form");
    const message = document.getElementById("manifest-submit-message");
    const inlineField = document.getElementById("manifest-inline-field");
    const registryField = document.getElementById("manifest-registry-field");
    const sourceSelect = form.querySelector('select[name="sourceKind"]');

    const syncSourceFields = () => {
      const kind = String(sourceSelect.value || "inline").toLowerCase();
      inlineField.classList.toggle("hidden", kind !== "inline");
      registryField.classList.toggle("hidden", kind !== "registry");
      const contentField = form.elements.namedItem("manifestContent");
      const registryInput = form.elements.namedItem("registryName");
      if (contentField) {
        contentField.required = kind === "inline";
      }
      if (registryInput) {
        registryInput.required = kind === "registry";
      }
    };
    syncSourceFields();
    sourceSelect.addEventListener("change", syncSourceFields);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.textContent = "";

      const formData = new FormData(form);
      const manifestName = String(formData.get("manifestName") || "").trim();
      const action = String(formData.get("action") || "run").trim() || "run";
      const sourceKind = String(formData.get("sourceKind") || "inline").trim().toLowerCase();
      const manifestPayload = {
        manifest: {
          name: manifestName,
          action,
          source: { kind: sourceKind },
        },
      };

      if (!manifestName) {
        message.textContent = "Manifest name is required.";
        return;
      }

      if (sourceKind === "registry") {
        const registryName = String(formData.get("registryName") || "").trim();
        if (!registryName) {
          message.textContent = "Registry name is required for registry submissions.";
          return;
        }
        manifestPayload.manifest.source = { kind: "registry", name: registryName };
      } else {
        const manifestContent = String(formData.get("manifestContent") || "").trim();
        if (!manifestContent) {
          message.textContent = "Manifest YAML is required.";
          return;
        }
        manifestPayload.manifest.source = { kind: "inline", content: manifestContent };
      }

      const options = {};
      if (formData.get("dryRun")) {
        options.dryRun = true;
      }
      if (formData.get("forceFull")) {
        options.forceFull = true;
      }
      const maxDocsRaw = String(formData.get("maxDocs") || "").trim();
      if (maxDocsRaw) {
        const parsed = Number(maxDocsRaw);
        if (Number.isFinite(parsed) && parsed >= 1) {
          options.maxDocs = parsed;
        }
      }
      if (Object.keys(options).length > 0) {
        manifestPayload.manifest.options = options;
      }

      const priorityValue = Number(String(formData.get("priority") || "").trim());
      const priority = Number.isFinite(priorityValue) ? priorityValue : 0;

      try {
        let created;
        if (sourceKind === "registry") {
          const registryName = String(formData.get("registryName") || "").trim();
          const registryRunUrlTemplate =
            queueSourceConfig.registryRun || "/api/manifests/{name}/runs";
          const registryRunUrl = registryRunUrlTemplate.replace(
            "{name}",
            encodeURIComponent(registryName),
          );
          created = await fetchJson(registryRunUrl, {
            method: "POST",
            body: JSON.stringify({
              action,
              options: Object.keys(options).length > 0 ? options : undefined,
            }),
          });
          window.location.href = `/tasks/queue/${encodeURIComponent(
            String(created.jobId || ""),
          )}`;
          return;
        }

        created = await fetchJson(queueSourceConfig.create || "/api/queue/jobs", {
          method: "POST",
          body: JSON.stringify({
            type: "manifest",
            priority,
            payload: manifestPayload,
          }),
        });
        window.location.href = `/tasks/queue/${encodeURIComponent(
          String(created.id || ""),
        )}`;
      } catch (error) {
        console.error("manifest submit failed", error);
        message.textContent = "Failed to create manifest job.";
      }
    });
  }

  function formatScheduleDate(value) {
    const text = String(value || "").trim();
    if (!text) {
      return "—";
    }
    const parsed = new Date(text);
    if (Number.isNaN(parsed.valueOf())) {
      return escapeHtml(text);
    }
    return escapeHtml(parsed.toLocaleString());
  }

  function summarizeScheduleTarget(target) {
    if (!target || typeof target !== "object") {
      return "unknown";
    }
    const kind = String(target.kind || "").trim();
    if (kind === "manifest_run") {
      return `manifest: ${String(target.name || "").trim() || "unnamed"}`;
    }
    if (kind === "queue_task_template") {
      const template = target.template || {};
      return `task template: ${String(template.slug || "").trim() || "unknown"}`;
    }
    if (kind === "queue_task") {
      return "task";
    }
    return kind || "unknown";
  }

  function resolveScheduleEndpoint(template, id) {
    return String(template || "").replace("{id}", encodeURIComponent(String(id || "").trim()));
  }

  async function renderSchedulesListPage() {
    setView(
      "Recurring Schedules",
      "Managed recurring schedules for queue and manifest targets.",
      "<p class='loading'>Loading recurring schedules...</p>",
      { showAutoRefreshControls: true },
    );

    const load = async () => {
      const endpoint =
        schedulesSourceConfig.list ||
        "/api/recurring-tasks?scope=personal";
      const payload = await fetchJson(endpoint);
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const rowsHtml = items
        .map((item) => {
          const id = String(item.id || "").trim();
          const name = escapeHtml(String(item.name || "").trim() || "(unnamed)");
          const targetSummary = escapeHtml(summarizeScheduleTarget(item.target || {}));
          const status = escapeHtml(String(item.lastDispatchStatus || "").trim() || "—");
          const nextRun = formatScheduleDate(item.nextRunAt);
          const enabled = item.enabled ? "Yes" : "No";
          return `
            <tr>
              <td><a href="/tasks/schedules/${encodeURIComponent(id)}">${name}</a></td>
              <td>${targetSummary}</td>
              <td>${enabled}</td>
              <td>${nextRun}</td>
              <td>${status}</td>
            </tr>
          `;
        })
        .join("");
      const tableHtml = items.length
        ? `
          <table class="rows-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Target</th>
                <th>Enabled</th>
                <th>Next Run</th>
                <th>Last Dispatch</th>
              </tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
          </table>
        `
        : "<p class='small'>No recurring schedules found.</p>";
      setView(
        "Recurring Schedules",
        "Managed recurring schedules for queue and manifest targets.",
        `${tableHtml}`,
        { showAutoRefreshControls: true },
      );
    };

    startPolling(load, pollIntervals.list);
  }

  function renderQueueSubmitPage(presetRuntime, editContext = null) {
    const isEditMode =
      Boolean(editContext) &&
      typeof editContext === "object" &&
      !Array.isArray(editContext);
    const editJobId = isEditMode
      ? normalizeDashboardDetailSegment(editContext.jobId || "")
      : "";
    const editExpectedUpdatedAt = isEditMode
      ? String(editContext.expectedUpdatedAt || "").trim()
      : "";
    const editDetailRoute = isEditMode && editJobId
      ? buildUnifiedTaskDetailRoute(editJobId, "queue")
      : "/tasks/list?source=queue";
    const sanitizedWorkerDraft = isEditMode
      ? normalizeSubmissionDraftForTest(editContext.prefillDraft || {})
      : submitDraftController.loadWorker();
    const selectedWorkerRuntime = isEditMode
      ? normalizeTaskRuntimeInput(presetRuntime ?? sanitizedWorkerDraft.runtime) ||
      defaultTaskRuntime
      : resolveSubmitRuntime(
        presetRuntime ?? sanitizedWorkerDraft.runtime,
        defaultTaskRuntime,
      );
    let activeWorkerRuntime = selectedWorkerRuntime;
    const queueDraftModel = String(
      sanitizedWorkerDraft.model || defaultTaskModel,
    ).trim();
    const queueDraftEffort = String(
      sanitizedWorkerDraft.effort || defaultTaskEffort,
    ).trim();
    const queueDraftRepository = String(sanitizedWorkerDraft.repository || "").trim();
    const queueDraftStartingBranch = String(
      sanitizedWorkerDraft.startingBranch || "",
    ).trim();
    const queueDraftNewBranch = String(sanitizedWorkerDraft.newBranch || "").trim();
    const queueDraftPublishMode = (() => {
      const candidate = String(sanitizedWorkerDraft.publishMode || "").trim().toLowerCase();
      return ["none", "branch", "pr"].includes(candidate)
        ? candidate
        : defaultPublishMode;
    })();
    const queueDraftPriority = Number.isInteger(
      Number(sanitizedWorkerDraft.priority),
    )
      ? Math.max(-10, Math.min(10, Number(sanitizedWorkerDraft.priority)))
      : 0;
    const queueDraftMaxAttempts = Number.isInteger(
      Number(sanitizedWorkerDraft.maxAttempts),
    )
      ? Math.max(1, Number(sanitizedWorkerDraft.maxAttempts))
      : 3;
    const queueDraftHasExplicitProposeTasks = Object.prototype.hasOwnProperty.call(
      sanitizedWorkerDraft,
      "proposeTasksUserSet",
    )
      ? Boolean(sanitizedWorkerDraft.proposeTasksUserSet)
      : false;
    const queueDraftProposeTasks =
      queueDraftHasExplicitProposeTasks &&
        Object.prototype.hasOwnProperty.call(sanitizedWorkerDraft, "proposeTasks")
        ? Boolean(sanitizedWorkerDraft.proposeTasks)
        : defaultProposeTasks;
    const queueDraftTemplateFeatureRequest = String(
      sanitizedWorkerDraft.templateFeatureRequest || "",
    ).trim();
    const queueDraftSteps = Array.isArray(sanitizedWorkerDraft.steps)
      ? sanitizedWorkerDraft.steps
      : [];
    const queueDraftAffinityKey = String(sanitizedWorkerDraft.affinityKey || "").trim();
    const queueDraftProviderProfile = String(sanitizedWorkerDraft.providerProfileId || "").trim();
    const attachmentAcceptedTypes = attachmentPolicy.allowedContentTypes.join(",");
    const attachmentSectionHtml =
      attachmentPolicy.enabled && !isEditMode
        ? `
        <section class="card" data-runtime-visibility="worker">
          <label>Image Attachments (optional)
            <input
              type="file"
              id="queue-attachments-input"
              accept="${escapeHtml(attachmentAcceptedTypes)}"
              multiple
            />
          </label>
          <p class="small" id="queue-attachments-message">
            Up to ${escapeHtml(String(attachmentPolicy.maxCount))} files, ${escapeHtml(
          String(attachmentPolicy.maxBytes),
        )} bytes each, ${escapeHtml(
          String(attachmentPolicy.totalBytes),
        )} bytes total.
          </p>
          <ul class="list" id="queue-attachments-list"></ul>
        </section>
        `
        : "";
    const primarySubmitLabel = isEditMode ? "Update" : "Create";
    const runtimeSubmitOptions = isEditMode ? supportedTaskRuntimes : submitRuntimeOptions;

    const runtimeOptions = renderRuntimeOptions(
      runtimeSubmitOptions,
      selectedWorkerRuntime,
    );
    const repositoryFallback = queueDraftRepository || defaultRepository;
    const repositoryHint = repositoryFallback
      ? `Leave blank to use default repository: ${repositoryFallback}.`
      : "Set a repository in this form (no system default repository is configured).";
    const templateControlsHtml = taskTemplateCatalogEnabled
      ? `
        <div class="card">
          <div class="actions">
            <strong>Task Presets (optional)</strong>
          </div>
          <label>Preset
            <select id="queue-template-select">
              <option value="">Select preset...</option>
            </select>
          </label>
          <label>Feature Request / Initial Instructions
            <textarea id="queue-template-feature-request" placeholder="Describe the feature request this preset should execute.">${escapeHtml(
        queueDraftTemplateFeatureRequest,
      )}</textarea>
          </label>
          <div class="actions">
            <button type="button" id="queue-template-apply">Apply</button>
            ${taskTemplateSaveEnabled
        ? '<button type="button" id="queue-template-save-current">Save Current Steps as Preset</button>'
        : ""
      }
          </div>
          <p class="small" id="queue-template-message"></p>
        </div>
        `
      : "";

    setView(
      isEditMode ? "Edit Queue Task" : "Create Task",
      isEditMode ? `Editing queued task ${editJobId}.` : "",
      `
      <form id="queue-submit-form" class="queue-submit-form">
        <section class="queue-steps-section stack">
          <div id="queue-steps-list" class="stack"></div>
        </section>
        ${templateControlsHtml}
        <label>Runtime
          <select name="runtime">
            ${runtimeOptions}
          </select>
        </label>
        <div id="queue-provider-profile-wrap" class="hidden">
          <label>Provider profile
            <select name="providerProfile">
              <option value="">Default (system chooses)</option>
            </select>
          </label>
          <p class="small" id="queue-auth-profile-hint"></p>
        </div>
        <datalist id="queue-model-options">
        </datalist>
        <datalist id="queue-effort-options">
        </datalist>
        <datalist id="queue-skill-options">
          <option value="auto"></option>
        </datalist>
        <div class="grid-2">
          <label>Model
              <input
              name="model"
              value="${escapeHtml(queueDraftModel)}"
              list="queue-model-options"
              placeholder="runtime default"
            />
          </label>
          <label>Effort
              <input
              name="effort"
              value="${escapeHtml(queueDraftEffort)}"
              list="queue-effort-options"
              placeholder="runtime default"
            />
          </label>
        </div>
        <label>GitHub Repo
          <input name="repository" value="${escapeHtml(queueDraftRepository || repositoryFallback)}" placeholder="owner/repo" />
          <span class="small">${escapeHtml(repositoryHint)} Accepted formats: owner/repo, https://&lt;host&gt;/&lt;path&gt;, or git@&lt;host&gt;:&lt;path&gt; (token-free).</span>
        </label>
        <div class="grid-2">
          <label>Starting Branch (optional)
            <input name="startingBranch" value="${escapeHtml(
        queueDraftStartingBranch,
      )}" placeholder="repo default branch" />
          </label>
          <label>Target Branch (optional)
            <input name="newBranch" value="${escapeHtml(
        queueDraftNewBranch,
      )}" placeholder="auto-generated unless starting branch is non-default" />
          </label>
        </div>
        <label>Publish Mode
          <select name="publishMode">
            <option value="pr" ${queueDraftPublishMode === "pr" ? "selected" : ""}>pr</option>
            <option value="branch" ${queueDraftPublishMode === "branch" ? "selected" : ""}>branch</option>
            <option value="none" ${queueDraftPublishMode === "none" ? "selected" : ""}>none</option>
          </select>
        </label>
        ${attachmentSectionHtml}
        <div class="grid-2" data-runtime-visibility="worker">
          <label>Priority
            <div class="priority-slider-container">
              <input type="range" name="priority" min="-10" max="10" value="${Number.isFinite(queueDraftPriority) ? queueDraftPriority : 0}" oninput="this.parentElement.querySelector('output').value = this.value" />
              <output>${Number.isFinite(queueDraftPriority) ? queueDraftPriority : 0}</output>
            </div>
          </label>
          <label>Max Attempts
            <input type="number" min="1" name="maxAttempts" value="${Number.isFinite(queueDraftMaxAttempts) ? queueDraftMaxAttempts : 3}" />
          </label>
        </div>
        <label class="checkbox">
          <input type="checkbox" name="proposeTasks" ${queueDraftProposeTasks ? "checked" : ""
      } />
          Propose Tasks
        </label>
        ${!isEditMode ? `
        <details class="card" id="schedule-panel">
          <summary><strong>Schedule (optional)</strong></summary>
          <label>Schedule Mode
            <select name="scheduleMode" id="schedule-mode-select">
              <option value="immediate" selected>Immediate</option>
              <option value="once">Deferred (run once at a specific time)</option>
              <option value="deferred_minutes">Deferred (run in N minutes)</option>
              <option value="recurring">Recurring (create a cron schedule)</option>
            </select>
          </label>
          <div id="schedule-once-fields" class="hidden">
            <label>Scheduled For
              <input type="datetime-local" name="scheduledFor" id="schedule-datetime" />
            </label>
          </div>
          <div id="schedule-deferred-minutes-fields" class="hidden">
            <label>Minutes from now
              <input type="number" name="scheduleDeferredMinutes" min="1" max="525600" step="1" placeholder="e.g. 15" />
            </label>
          </div>
          <div id="schedule-recurring-fields" class="hidden">
            <label>Cron Expression
              <input name="scheduleCron" placeholder="*/30 * * * *" />
            </label>
            <label>Timezone
              <input name="scheduleTimezone" placeholder="UTC" value="UTC" />
            </label>
            <label>Schedule Name
              <input name="scheduleName" placeholder="My recurring task" />
            </label>
          </div>
        </details>
        ` : ""}
        <div class="actions" role="group" aria-label="Queue submission actions">
          <p class="small queue-submit-message" id="queue-submit-message"></p>
          ${isEditMode
        ? `<a href="${escapeHtml(editDetailRoute)}"><button type="button" class="secondary">Cancel</button></a>`
        : ""
      }
          <button type="submit" class="queue-submit-primary">
            ${escapeHtml(primarySubmitLabel)}
          </button>
        </div>
      </form>
      `,
      { showAutoRefreshControls: false },
    );

    const form = document.getElementById("queue-submit-form");
    const message = document.getElementById("queue-submit-message");
    if (!form || !message) {
      return;
    }
    const attachmentInput = form.querySelector("#queue-attachments-input");
    const attachmentMessage = form.querySelector("#queue-attachments-message");
    const attachmentList = form.querySelector("#queue-attachments-list");

    const formatAttachmentBytes = (value) => {
      const bytes = Math.max(0, Number(value) || 0);
      if (bytes < 1024) {
        return `${bytes} B`;
      }
      if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
      }
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };
    const selectedAttachmentFiles = () =>
      attachmentInput instanceof HTMLInputElement
        ? Array.from(attachmentInput.files || [])
        : [];
    const validateAttachmentFiles = (files) => {
      const normalizedFiles = Array.isArray(files) ? files : [];
      const errors = [];
      if (normalizedFiles.length > attachmentPolicy.maxCount) {
        errors.push(
          `Too many attachments (${normalizedFiles.length}/${attachmentPolicy.maxCount}).`,
        );
      }
      let totalBytes = 0;
      normalizedFiles.forEach((file) => {
        const type = String(file.type || "").trim().toLowerCase();
        if (!attachmentPolicy.allowedContentTypes.includes(type)) {
          errors.push(`Unsupported file type for ${file.name || "attachment"}.`);
        }
        const sizeBytes = Math.max(0, Number(file.size) || 0);
        if (sizeBytes > attachmentPolicy.maxBytes) {
          errors.push(
            `${file.name || "attachment"} exceeds ${formatAttachmentBytes(
              attachmentPolicy.maxBytes,
            )}.`,
          );
        }
        totalBytes += sizeBytes;
      });
      if (totalBytes > attachmentPolicy.totalBytes) {
        errors.push(
          `Total attachment size exceeds ${formatAttachmentBytes(
            attachmentPolicy.totalBytes,
          )}.`,
        );
      }
      return {
        files: normalizedFiles,
        ok: errors.length === 0,
        errors,
        totalBytes,
      };
    };
    const renderAttachmentSelection = () => {
      if (!attachmentPolicy.enabled) {
        return;
      }
      const files = selectedAttachmentFiles();
      const validation = validateAttachmentFiles(files);
      if (attachmentList) {
        if (!files.length) {
          attachmentList.innerHTML = "";
        } else {
          attachmentList.innerHTML = files
            .map((file) => {
              const type = String(file.type || "unknown").trim();
              return `<li>${escapeHtml(file.name || "attachment")} (${escapeHtml(
                type || "unknown",
              )}, ${escapeHtml(formatAttachmentBytes(file.size))})</li>`;
            })
            .join("");
        }
      }
      if (attachmentMessage) {
        if (!files.length) {
          attachmentMessage.textContent = `Up to ${attachmentPolicy.maxCount} files, ${formatAttachmentBytes(
            attachmentPolicy.maxBytes,
          )} each, ${formatAttachmentBytes(attachmentPolicy.totalBytes)} total.`;
        } else if (!validation.ok) {
          attachmentMessage.textContent = validation.errors.join(" ");
        } else {
          attachmentMessage.textContent = `${files.length} attachment(s) selected (${formatAttachmentBytes(
            validation.totalBytes,
          )}).`;
        }
      }
      if (attachmentInput instanceof HTMLInputElement) {
        attachmentInput.setCustomValidity(
          validation.ok ? "" : validation.errors.join(" "),
        );
      }
      return validation;
    };
    if (attachmentInput instanceof HTMLInputElement) {
      attachmentInput.addEventListener("change", () => {
        renderAttachmentSelection();
      });
    }
    renderAttachmentSelection();

    const readQueueTemplateFeatureRequest = () => {
      const input = form.querySelector("#queue-template-feature-request");
      if (!(input instanceof HTMLTextAreaElement)) {
        return "";
      }
      return String(input.value || "").trim();
    };
    const collectWorkerDraftFromForm = () => {
      const formData = new FormData(form);
      const runtimeRaw = String(formData.get("runtime") || defaultTaskRuntime)
        .trim()
        .toLowerCase();
      const runtime = normalizeTaskRuntimeInput(runtimeRaw);
      const priority = Number(formData.get("priority") || 0);
      const maxAttempts = Number(formData.get("maxAttempts") || 3);
      return {
        runtime: runtime || activeWorkerRuntime,
        ...(isEditMode
          ? {
            editJobId,
            expectedUpdatedAt: editExpectedUpdatedAt,
            affinityKey: queueDraftAffinityKey,
          }
          : {}),
        instruction: String(stepState[0]?.instructions || "").trim(),
        repository: String(formData.get("repository") || "").trim(),
        startingBranch: String(formData.get("startingBranch") || "").trim() || null,
        newBranch: String(formData.get("newBranch") || "").trim() || null,
        publishMode: String(formData.get("publishMode") || defaultPublishMode)
          .trim()
          .toLowerCase(),
        model: String(formData.get("model") || "").trim(),
        effort: String(formData.get("effort") || "").trim(),
        providerProfileId: String(formData.get("providerProfile") || "").trim(),
        priority: Number.isFinite(priority) ? priority : 0,
        maxAttempts: Number.isFinite(maxAttempts) ? maxAttempts : 3,
        proposeTasks: formData.get("proposeTasks") !== null,
        proposeTasksUserSet: true,
        steps: cloneStepStateEntries(stepState),
        templateFeatureRequest: readQueueTemplateFeatureRequest(),
      };
    };
    const persistWorkerDraft = () => {
      if (isEditMode) {
        return;
      }
      submitDraftController.saveWorker(collectWorkerDraftFromForm());
      persistSubmitDraftsToStorage();
    };
    const scheduleWorkerDraftPersist = createDraftPersistenceScheduler(
      persistWorkerDraft,
    );
    const runtimeSelect = form.querySelector('select[name="runtime"]');
    const modelInputElement = form.querySelector('input[name="model"]');
    const effortInputElement = form.querySelector('input[name="effort"]');
    const modelDatalistNode = form.querySelector("#queue-model-options");
    const effortDatalistNode = form.querySelector("#queue-effort-options");
    const providerProfileWrap = form.querySelector("#queue-provider-profile-wrap");
    const providerProfileHint = form.querySelector("#queue-auth-profile-hint");
    const providerProfileSelect = form.querySelector('select[name="providerProfile"]');
    let applyQueueDraftProviderProfileOnce = true;
    let providerProfileFetchToken = 0;

    const refreshProviderProfileOptions = async (runtimeMode) => {
      if (!providerProfileWrap || !providerProfileSelect) {
        return;
      }
      if (!providerProfileEndpoints) {
        providerProfileWrap.classList.add("hidden");
        return;
      }
      const mappedRuntimeId = mapTaskRuntimeToAuthProfileRuntimeId(runtimeMode);
      if (!mappedRuntimeId) {
        providerProfileWrap.classList.add("hidden");
        providerProfileSelect.innerHTML =
          '<option value="">Default (system chooses)</option>';
        if (providerProfileHint) {
          providerProfileHint.textContent = "";
        }
        return;
      }
      const previousSelection = String(providerProfileSelect.value || "").trim();
      providerProfileWrap.classList.remove("hidden");
      if (providerProfileHint) {
        providerProfileHint.textContent =
          "Optional. Choose stored credentials for this runtime, or leave default for automatic selection.";
      }
      try {
        providerProfileFetchToken += 1;
        const currentToken = providerProfileFetchToken;
        let listEndpoint = providerProfileEndpoints.providerProfilesList || providerProfileEndpoints.list || "";
        if (listEndpoint.includes("/auth-profiles")) {
          listEndpoint = listEndpoint.replace("/auth-profiles", "/provider-profiles");
        }
        const url = `${listEndpoint}?runtime_id=${encodeURIComponent(
          mappedRuntimeId,
        )}&enabled_only=true`;
        const profiles = await fetchJson(url);
        if (currentToken !== providerProfileFetchToken) {
          return;
        }
        const list = Array.isArray(profiles) ? profiles : [];
        const options = ['<option value="">Default (system chooses)</option>'];
        list.forEach((profile) => {
          const pid = String(profile.profile_id || "").trim();
          if (!pid) {
            return;
          }
          const label = String(profile.account_label || "").trim() || pid;
          options.push(
            `<option value="${escapeHtml(pid)}">${escapeHtml(label)}</option>`,
          );
        });
        providerProfileSelect.innerHTML = options.join("");
        const desired = String(previousSelection || "").trim();
        let pickId = desired;
        if (!pickId && applyQueueDraftProviderProfileOnce) {
          pickId = String(queueDraftProviderProfile || "").trim();
        }
        applyQueueDraftProviderProfileOnce = false;
        if (
          pickId &&
          list.some((p) => String(p.profile_id || "").trim() === pickId)
        ) {
          providerProfileSelect.value = pickId;
        }
      } catch (_error) {
        console.warn("submit form: could not load provider profiles", _error);
        providerProfileWrap.classList.add("hidden");
        providerProfileSelect.innerHTML =
          '<option value="">Default (system chooses)</option>';
        if (providerProfileHint) {
          providerProfileHint.textContent =
            "Could not load provider profiles. The run will use default profile selection.";
        }
      }
    };

    const stepsList = document.getElementById("queue-steps-list");
    const runtimeModelDefaults = {
      ...configuredModelDefaults,
      codex: codexDefaultTaskModel,
    };
    const runtimeEffortDefaults = {
      ...configuredEffortDefaults,
      codex: codexDefaultTaskEffort,
    };
    const runtimeCapabilityByRuntime = {};
    for (const runtime of supportedTaskRuntimes) {
      const runtimeKey = normalizeTaskRuntimeInput(runtime);
      if (runtimeKey) {
        runtimeCapabilityByRuntime[runtimeKey] = { models: [], efforts: [] };
      }
    }
    const resolveDefaultRuntimeChoice = (defaultsByRuntime, runtime, fallbackValues) => {
      const configured = resolveRuntimeDefault(defaultsByRuntime, runtime);
      if (configured) {
        return configured;
      }
      const normalized = normalizeRuntimeOptions(fallbackValues);
      return normalized.length > 0 ? normalized[0] : "";
    };
    const getRuntimeKey = (runtime) => normalizeTaskRuntimeInput(runtime) || defaultTaskRuntime;
    const setDatalist = (datalistNode, values) => {
      if (!(datalistNode instanceof HTMLDataListElement)) {
        return;
      }
      const normalized = normalizeRuntimeOptions(values);
      datalistNode.innerHTML = normalized
        .map((value) => `<option value="${escapeHtml(value)}"></option>`)
        .join("");
    };
    const getRuntimeCapabilities = (runtime) => {
      const runtimeKey = getRuntimeKey(runtime);
      return runtimeCapabilityByRuntime[runtimeKey] || { models: [], efforts: [] };
    };
    let activeDefaultModel = "";
    let activeDefaultEffort = "";
    const applyQueueSubmitRuntimeUiState = (runtimeValue) => {
      const uiState = resolveQueueSubmitRuntimeUiState(runtimeValue);
      const updateVisibility = (mode, isVisible) => {
        const nodes = form.querySelectorAll(`[data-runtime-visibility="${mode}"]`);
        nodes.forEach((node) => {
          applyElementVisibility(node, isVisible);
        });
      };
      updateVisibility("worker", uiState.showWorkerPriorityFields);
    };
    const applyRuntimeDefaults = (runtime) => {
      if (!modelInputElement || !effortInputElement) {
        return;
      }
      const runtimeKey = getRuntimeKey(runtime);
      const runtimeCapabilities = getRuntimeCapabilities(runtimeKey);
      const runtimeModelDefaultsWithFallback = resolveDefaultRuntimeChoice(
        runtimeModelDefaults,
        runtimeKey,
        runtimeCapabilities.models,
      );
      const runtimeEffortDefaultsWithFallback = resolveDefaultRuntimeChoice(
        runtimeEffortDefaults,
        runtimeKey,
        runtimeCapabilities.efforts,
      );
      setDatalist(modelDatalistNode, [
        ...normalizeRuntimeOptions(runtimeCapabilities.models),
      ]);
      setDatalist(effortDatalistNode, [
        ...normalizeRuntimeOptions(runtimeCapabilities.efforts),
      ]);
      let nextDefaultModel = runtimeModelDefaultsWithFallback;
      let nextDefaultEffort = runtimeEffortDefaultsWithFallback;

      if (runtimeKey === JULES_RUNTIME) {
        modelInputElement.value = "";
        effortInputElement.value = "";
        nextDefaultModel = "";
        nextDefaultEffort = "";
      } else {
        if (modelInputElement.value.trim() === activeDefaultModel) {
          modelInputElement.value = nextDefaultModel;
        }
        if (effortInputElement.value.trim() === activeDefaultEffort) {
          effortInputElement.value = nextDefaultEffort;
        }
      }

      activeDefaultModel = nextDefaultModel;
      activeDefaultEffort = nextDefaultEffort;
    };
    const loadRuntimeCapabilities = async (runtime) => {
      try {
        const payload = await loadRuntimeCapabilitiesFromEndpoint();
        Object.entries(payload).forEach(([runtimeKey, capabilityEntry]) => {
          runtimeCapabilityByRuntime[runtimeKey] = {
            models: normalizeRuntimeOptions(capabilityEntry.models),
            efforts: normalizeRuntimeOptions(capabilityEntry.efforts),
          };
        });
      } catch (_error) {
        console.warn(
          "Runtime capability options could not be loaded from the dashboard source.",
        );
      } finally {
        applyRuntimeDefaults(runtime || runtimeSelect?.value || defaultTaskRuntime);
      }
    };
    loadRuntimeCapabilities();
    if (runtimeSelect) {
      applyQueueSubmitRuntimeUiState(runtimeSelect.value);
      applyRuntimeDefaults(runtimeSelect.value);
      runtimeSelect.addEventListener("change", (event) => {
        const selectedRuntime =
          String(event.target.value || "").trim().toLowerCase();
        const nextRuntime = normalizeTaskRuntimeInput(selectedRuntime);
        if (!nextRuntime) {
          message.className = "notice error queue-submit-message";
          message.textContent = "Select a supported worker runtime.";
          runtimeSelect.value = activeWorkerRuntime;
          return;
        }
        activeWorkerRuntime = nextRuntime;
        applyQueueSubmitRuntimeUiState(activeWorkerRuntime);
        loadRuntimeCapabilities(nextRuntime);
        void refreshProviderProfileOptions(nextRuntime);
        refreshSkillDatalist();
        scheduleWorkerDraftPersist();
      });
      void refreshProviderProfileOptions(runtimeSelect.value);
    }
    form.addEventListener("input", scheduleWorkerDraftPersist);
    form.addEventListener("change", scheduleWorkerDraftPersist);
    const createStepStateEntry = (overrides = {}) => ({
      id: "",
      instructions: "",
      skillId: "",
      skillArgs: "",
      skillRequiredCapabilities: "",
      templateStepId: "",
      templateInstructions: "",
      ...overrides,
    });
    const sanitizeWorkerStep = (rawStep = {}) => {
      const normalized = normalizeSubmissionDraftForTest(rawStep);
      return createStepStateEntry({
        id: String(normalized.id || "").trim(),
        instructions: String(normalized.instructions || "").trim(),
        skillId: String(normalized.skillId || "").trim(),
        skillArgs: String(normalized.skillArgs || ""),
        skillRequiredCapabilities: String(
          normalized.skillRequiredCapabilities || "",
        ).trim(),
        templateStepId: String(normalized.templateStepId || "").trim(),
        templateInstructions: String(
          normalized.templateInstructions || "",
        ).trim(),
      });
    };
    const stepState = (Array.isArray(queueDraftSteps) && queueDraftSteps.length > 0
      ? queueDraftSteps.map(sanitizeWorkerStep).filter((entry) => Boolean(entry))
      : [createStepStateEntry()]
    );
    const isDefaultSkillSelection = (value) => {
      const normalized = String(value || "").trim().toLowerCase();
      return normalized === "" || normalized === "auto";
    };
    const shouldShowSkillArgs = (step) => !isDefaultSkillSelection(step?.skillId);
    const clearSkillArgsForStep = (index) => {
      if (index < 0 || index >= stepState.length) {
        return;
      }
      stepState[index].skillArgs = "";
      if (!stepsList) {
        return;
      }
      const textarea = stepsList.querySelector(`[data-step-field="skillArgs"][data-step-index="${index}"]`);
      if (textarea instanceof HTMLTextAreaElement) {
        textarea.value = "";
      }
    };
    const updateSkillArgsVisibility = (index) => {
      if (!stepsList || index < 0 || index >= stepState.length) {
        return;
      }
      const wrapper = stepsList.querySelector(
        `[data-skill-args-index="${index}"]`,
      );
      if (!(wrapper instanceof HTMLElement)) {
        return;
      }
      if (shouldShowSkillArgs(stepState[index])) {
        wrapper.classList.remove("hidden");
      } else {
        clearSkillArgsForStep(index);
        wrapper.classList.add("hidden");
      }
    };
    let appliedTemplateState = [];
    const renderStepEditor = () => {
      if (!stepsList) {
        console.error("[dashboard] #queue-steps-list not found; step editor unavailable");
        return;
      }
      const rows = stepState
        .map((step, index) => {
          const isPrimaryStep = index === 0;
          const stepLabel = isPrimaryStep ? " (Primary)" : "";
          const skillLabel = "Skill (optional)";
          const skillPlaceholder = isPrimaryStep
            ? "auto (default), speckit-orchestrate, ..."
            : "inherit primary step skill";
          const instructionsLabel = "Instructions";
          const instructionsPlaceholder = isPrimaryStep
            ? "Describe the task to execute against the repository."
            : "Step-specific instructions (leave blank to continue from the task objective).";
          const upDisabled = index === 0 ? "disabled" : "";
          const downDisabled = index === stepState.length - 1 ? "disabled" : "";
          const removeDisabled = "";
          const defaultHint = isPrimaryStep
            ? "Primary step must include instructions or an explicit skill."
            : "Leave skill blank to inherit primary step defaults.";
          const showSkillArgsField = shouldShowSkillArgs(step);
          const skillArgsLabelClasses = ["queue-step-skill-args-field"];
          if (!showSkillArgsField) {
            skillArgsLabelClasses.push("hidden");
          }
          return `
            <section class="queue-step-section stack" data-step-index="${index}">
              <div class="queue-step-header">
                <strong>Step ${index + 1}${stepLabel}</strong>
                <div class="queue-step-controls" role="group" aria-label="Step ${index + 1} controls">
                  <button
                    type="button"
                    class="queue-step-icon-button"
                    data-step-action="up"
                    data-step-index="${index}"
                    ${upDisabled}
                    aria-label="Move step up"
                    title="Move step up"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M12 6v12m0-12-4 4m4-4 4 4" />
                    </svg>
                    <span class="sr-only">Move step up</span>
                  </button>
                  <button
                    type="button"
                    class="queue-step-icon-button"
                    data-step-action="down"
                    data-step-index="${index}"
                    ${downDisabled}
                    aria-label="Move step down"
                    title="Move step down"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M12 18V6m0 12 4-4m-4 4-4-4" />
                    </svg>
                    <span class="sr-only">Move step down</span>
                  </button>
                  <button
                    type="button"
                    class="queue-step-icon-button destructive"
                    data-step-action="remove"
                    data-step-index="${index}"
                    ${removeDisabled}
                    aria-label="Remove step"
                    title="Remove step"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M7 7l10 10M17 7l-10 10" />
                    </svg>
                    <span class="sr-only">Remove step</span>
                  </button>
                </div>
              </div>
              <label>${instructionsLabel}
                <textarea class="queue-step-instructions" data-step-field="instructions" data-step-index="${index}" placeholder="${escapeHtml(
            instructionsPlaceholder,
          )}">${escapeHtml(
            step.instructions,
          )}</textarea>
              </label>
              <div class="grid-2">
                <label>${skillLabel}
                  <input data-step-field="skillId" data-step-index="${index}" value="${escapeHtml(
            step.skillId,
          )}" placeholder="${escapeHtml(skillPlaceholder)}" list="queue-skill-options" />
                  <span class="small">${defaultHint}</span>
                </label>
                <label>Skill Required Capabilities (optional CSV)
                  <input data-step-field="skillRequiredCapabilities" data-step-index="${index}" value="${escapeHtml(
            step.skillRequiredCapabilities,
          )}" placeholder="docker,qdrant,unity" />
                </label>
              </div>
              <label class="${skillArgsLabelClasses.join(" ")}" data-skill-args-index="${index}">Skill Args (optional JSON object)
                <textarea class="queue-step-skill-args" data-step-field="skillArgs" data-step-index="${index}" placeholder='{"notes":"optional context"}'>${escapeHtml(
            step.skillArgs,
          )}</textarea>
              </label>
            </section>
          `;
        })
        .join("");
      const addStepButtonRow = `
        <div class="actions queue-step-add">
          <button type="button" data-step-action="add">Add Step</button>
        </div>
      `;
      stepsList.innerHTML = rows + addStepButtonRow;
    };
    const readStepIndex = (target) => {
      if (!(target instanceof Element)) {
        return null;
      }
      const raw = target.getAttribute("data-step-index");
      if (raw === null) {
        return null;
      }
      const index = Number(raw);
      if (!Number.isInteger(index) || index < 0 || index >= stepState.length) {
        return null;
      }
      return index;
    };
    if (stepsList) {
      stepsList.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
          return;
        }
        const actionButton = target.closest("[data-step-action]");
        if (!(actionButton instanceof HTMLButtonElement)) {
          return;
        }
        if (actionButton.disabled) {
          return;
        }
        const action = actionButton.getAttribute("data-step-action");
        if (!action) {
          return;
        }
        if (action === "add") {
          stepState.push(createStepStateEntry());
          renderStepEditor();
          scheduleWorkerDraftPersist();
          return;
        }
        const index = readStepIndex(actionButton);
        if (index === null) {
          return;
        }
        if (action === "remove") {
          stepState.splice(index, 1);
          renderStepEditor();
          scheduleWorkerDraftPersist();
          return;
        }
        if (action === "up" && index > 0) {
          const current = stepState[index];
          stepState[index] = stepState[index - 1];
          stepState[index - 1] = current;
          renderStepEditor();
          scheduleWorkerDraftPersist();
          return;
        }
        if (action === "down" && index < stepState.length - 1) {
          const current = stepState[index];
          stepState[index] = stepState[index + 1];
          stepState[index + 1] = current;
          renderStepEditor();
          scheduleWorkerDraftPersist();
        }
      });
      stepsList.addEventListener("input", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const fieldInput = target.closest("[data-step-field]");
        if (!(fieldInput instanceof HTMLInputElement || fieldInput instanceof HTMLTextAreaElement)) {
          return;
        }
        const field = fieldInput.getAttribute("data-step-field");
        if (!field) {
          return;
        }
        const index = readStepIndex(fieldInput);
        if (index === null) {
          return;
        }
        stepState[index][field] = fieldInput.value || "";
        if (field === "skillId") {
          updateSkillArgsVisibility(index);
        }
        if (
          field === "instructions" &&
          stepState[index].templateStepId &&
          stepState[index].id === stepState[index].templateStepId &&
          fieldInput.value !== stepState[index].templateInstructions
        ) {
          stepState[index].id = "";
        }
        scheduleWorkerDraftPersist();
      });
    }
    renderStepEditor();
    persistWorkerDraft();
    const refreshSkillDatalist = () => {
      loadAvailableSkillIds("worker").then((skillIds) => {
        populateSkillDatalist("queue-skill-options", skillIds);
      });
    };
    refreshSkillDatalist();

    const templateMessage = document.getElementById("queue-template-message");
    const templateSelect = document.getElementById("queue-template-select");
    const templateFeatureRequest = document.getElementById("queue-template-feature-request");
    const templateApply = document.getElementById("queue-template-apply");
    const templateSaveCurrent = document.getElementById("queue-template-save-current");
    let templateItems = [];
    const templateInputMemory = {};
    const preferredTemplateSlug = "speckit-orchestrate";
    const templateScopeLoadOrder = ["global", "personal"];

    const setTemplateMessage = (text, isError = false) => {
      if (!templateMessage) {
        return;
      }
      templateMessage.className = isError ? "notice error" : "small";
      templateMessage.textContent = text;
    };
    const sanitizedErrorMessage = (error, fallback = "request failed") => {
      const message = String(error?.message || "").trim();
      return message || fallback;
    };

    const currentTemplateFeatureRequest = () =>
      templateFeatureRequest instanceof HTMLTextAreaElement
        ? String(templateFeatureRequest.value || "").trim()
        : "";
    const clearWorkerSubmissionDraftAfterCreate = () => {
      const clearedDraft = resetWorkerSubmissionFields(collectWorkerDraftFromForm());
      submitDraftController.saveWorker(clearedDraft);
      persistSubmitDraftsToStorage();

      stepState.splice(0, stepState.length, createStepStateEntry());
      appliedTemplateState = [];
      if (templateFeatureRequest instanceof HTMLTextAreaElement) {
        templateFeatureRequest.value = "";
      }
      renderStepEditor();
    };
    const normalizeTemplateInputKey = (key) =>
      String(key || "")
        .trim()
        .toLowerCase()
        .replaceAll(/[^a-z0-9]/g, "");
    const valueForFeatureRequestInput = (rawInputs) => {
      if (!rawInputs || typeof rawInputs !== "object" || Array.isArray(rawInputs)) {
        return "";
      }
      let fallback = "";
      let preferred = "";
      for (const [rawKey, rawValue] of Object.entries(rawInputs)) {
        if (normalizeTemplateInputKey(rawKey) !== "featurerequest") {
          continue;
        }
        const candidate = String(rawValue || "").trim();
        if (!candidate) {
          continue;
        }
        if (String(rawKey || "").trim().toLowerCase() === "feature_request") {
          preferred = candidate;
          break;
        }
        if (!fallback) {
          fallback = candidate;
        }
      }
      return preferred || fallback;
    };
    const resolveObjectiveInstructions = (primaryInstructions) => {
      const explicitFeatureRequest = currentTemplateFeatureRequest();
      if (explicitFeatureRequest) {
        return explicitFeatureRequest;
      }
      if (primaryInstructions) {
        return primaryInstructions;
      }
      for (let index = appliedTemplateState.length - 1; index >= 0; index -= 1) {
        const candidate = valueForFeatureRequestInput(appliedTemplateState[index]?.inputs);
        if (candidate) {
          return candidate;
        }
      }
      return primaryInstructions;
    };
    const templateItemKey = (item) => {
      const scope = String(item?.scope || "global").trim();
      const scopeRef = String(item?.scopeRef || "").trim();
      const slug = String(item?.slug || "").trim();
      return `${scope}::${scopeRef}::${slug}`;
    };
    const scopeLabelForItem = (item) => {
      const scope = String(item?.scope || "").trim().toLowerCase();
      if (scope === "personal") {
        return "Personal";
      }
      return "Global";
    };
    const preferredTemplateFrom = (items) => {
      const slugMatches = items.filter(
        (item) => String(item?.slug || "").trim() === preferredTemplateSlug,
      );
      if (slugMatches.length === 0) {
        return items[0] || null;
      }
      const globalMatch = slugMatches.find(
        (item) => String(item?.scope || "").trim().toLowerCase() === "global",
      );
      return globalMatch || slugMatches[0];
    };

    const renderTemplateSelect = () => {
      if (!(templateSelect instanceof HTMLSelectElement)) {
        return;
      }
      const previousSelection = String(templateSelect.value || "").trim();
      const options = templateItems.map((item) => {
        const labelScope = scopeLabelForItem(item);
        const key = templateItemKey(item);
        const optionLabel = labelScope ? `${item.title} (${labelScope})` : item.title;
        return `<option value="${escapeHtml(key)}">${escapeHtml(optionLabel)}</option>`;
      });
      templateSelect.innerHTML = ['<option value="">Select preset...</option>', ...options].join("");
      const hasPreviousSelection = templateItems.some(
        (item) => templateItemKey(item) === previousSelection,
      );
      const preferredTemplate = preferredTemplateFrom(templateItems);
      const fallbackSelection = preferredTemplate ? templateItemKey(preferredTemplate) : "";
      const nextSelection = hasPreviousSelection ? previousSelection : fallbackSelection;
      templateSelect.value = nextSelection;
    };

    const fetchTemplateList = async () => {
      if (!taskTemplateCatalogEnabled) {
        return;
      }
      setTemplateMessage("Loading presets...");
      const scopeLoads = templateScopeLoadOrder.map(async (scope) => {
        const params = new URLSearchParams();
        params.set("scope", scope);
        try {
          const payload = await fetchJson(`${taskTemplateEndpoints.list}?${params.toString()}`);
          return {
            scope,
            items: Array.isArray(payload?.items) ? payload.items : [],
            failed: false,
          };
        } catch (error) {
          console.error(
            `template list fetch failed (scope=${scope}): ${sanitizedErrorMessage(error)}`,
          );
          return { scope, items: [], failed: true };
        }
      });
      const scopeResults = await Promise.all(scopeLoads);
      const loadedItems = scopeResults.flatMap((result) => result.items);
      const failedScopes = scopeResults.filter((result) => result.failed).map((result) => result.scope);
      templateItems = loadedItems;
      renderTemplateSelect();
      if (failedScopes.length === templateScopeLoadOrder.length) {
        setTemplateMessage("Failed to load presets: all scopes unavailable.", true);
        return;
      }
      if (failedScopes.length > 0) {
        setTemplateMessage(
          `Loaded ${templateItems.length} presets (missing scopes: ${failedScopes.join(", ")}).`,
          true,
        );
        return;
      }
      setTemplateMessage(
        templateItems.length > 0
          ? `Loaded ${templateItems.length} presets.`
          : "No presets available for your account.",
      );
    };

    const selectedTemplate = () => {
      if (!(templateSelect instanceof HTMLSelectElement)) {
        return null;
      }
      const selectedKey = String(templateSelect.value || "").trim();
      if (!selectedKey) {
        return null;
      }
      return templateItems.find((item) => templateItemKey(item) === selectedKey) || null;
    };

    const resolveTemplateInputs = (inputs) => {
      const values = {};
      const assumptions = [];
      const primaryInstructions = String(stepState[0]?.instructions || "").trim();
      const explicitFeatureRequest = currentTemplateFeatureRequest();
      const repositoryInput = form.querySelector('input[name="repository"]');
      const repositoryValue =
        repositoryInput instanceof HTMLInputElement
          ? String(repositoryInput.value || "").trim()
          : "";

      const normalizeBoolean = (value) => {
        if (typeof value === "boolean") {
          return value;
        }
        const lowered = String(value ?? "").trim().toLowerCase();
        if (["1", "true", "yes", "on"].includes(lowered)) {
          return true;
        }
        if (["0", "false", "no", "off"].includes(lowered)) {
          return false;
        }
        return false;
      };

      for (const definition of Array.isArray(inputs) ? inputs : []) {
        const name = String(definition?.name || "").trim();
        const label = String(definition?.label || name).trim() || name;
        if (!name) {
          continue;
        }
        const required = Boolean(definition?.required);
        const inputType = String(definition?.type || "").toLowerCase();
        const options = Array.isArray(definition?.options)
          ? definition.options.map((option) => String(option).trim()).filter((option) => option)
          : [];
        const key = name.toLowerCase();
        const isFeatureRequestKey =
          key.includes("feature_request") || key === "feature" || key === "request";

        let value = null;
        let valueSource = "";
        const remembered = templateInputMemory[name];
        const defaultValue = definition?.default;

        if (isFeatureRequestKey && explicitFeatureRequest) {
          value = explicitFeatureRequest;
          valueSource = "manual";
        } else if (remembered !== null && remembered !== undefined && String(remembered).trim() !== "") {
          value = remembered;
          valueSource = "memory";
        } else if (defaultValue !== null && defaultValue !== undefined && String(defaultValue).trim() !== "") {
          value = defaultValue;
          valueSource = "default";
        } else if (
          key.includes("instruction") ||
          isFeatureRequestKey
        ) {
          value = explicitFeatureRequest || primaryInstructions;
          valueSource = "draft";
        } else if (key.includes("repo")) {
          value = repositoryValue;
          valueSource = "draft";
        } else if (inputType === "enum") {
          if (key.includes("mode") && options.includes("runtime")) {
            value = "runtime";
          } else if (options.length > 0) {
            value = options[0];
          }
          valueSource = "assumed";
        } else if (inputType === "boolean") {
          value = false;
          valueSource = "assumed";
        }

        const hasValue = value !== null && value !== undefined && String(value).trim() !== "";
        if (!hasValue && required) {
          if (inputType === "enum" && options.length > 0) {
            value = options[0];
          } else if (inputType === "boolean") {
            value = false;
          } else if (explicitFeatureRequest || primaryInstructions) {
            value = explicitFeatureRequest || primaryInstructions;
          } else {
            value = `auto-${key.replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-+|-+$/g, "") || "value"}`;
          }
          valueSource = "assumed";
        } else if (!hasValue) {
          continue;
        }

        let normalized = value;
        if (inputType === "boolean") {
          normalized = normalizeBoolean(value);
        } else {
          normalized = String(value).trim();
        }

        if (inputType === "enum") {
          const candidate = String(normalized).trim();
          if (options.length > 0 && !options.includes(candidate)) {
            normalized = options[0];
            valueSource = "assumed";
          } else {
            normalized = candidate;
          }
        }

        if ((normalized === null || normalized === undefined || String(normalized).trim() === "") && required) {
          throw new Error(`Input '${label}' could not be inferred.`);
        }
        if (normalized === null || normalized === undefined || String(normalized).trim() === "") {
          continue;
        }

        values[name] = normalized;
        templateInputMemory[name] = normalized;
        if (valueSource === "assumed" || valueSource === "draft") {
          assumptions.push(label);
        }
      }
      return { values, assumptions };
    };

    const mapExpandedStepToState = (step) => {
      const tool =
        step && typeof step.tool === "object" && !Array.isArray(step.tool)
          ? step.tool
          : (
            step && typeof step.skill === "object" && !Array.isArray(step.skill)
              ? step.skill
              : null
          );
      const inlineInputs =
        tool && tool.inputs && typeof tool.inputs === "object" && !Array.isArray(tool.inputs)
          ? tool.inputs
          : (
            tool && tool.args && typeof tool.args === "object" && !Array.isArray(tool.args)
              ? tool.args
              : null
          );
      const caps = Array.isArray(tool?.requiredCapabilities)
        ? tool.requiredCapabilities.join(",")
        : "";
      const args = inlineInputs
        ? JSON.stringify(inlineInputs)
        : "";
      const stepId = String(step?.id || "").trim();
      const instructions = String(step?.instructions || "").trim();
      return createStepStateEntry({
        id: stepId,
        instructions,
        skillId: String(tool?.name || tool?.id || "").trim(),
        skillArgs: args,
        skillRequiredCapabilities: caps,
        templateStepId: stepId,
        templateInstructions: instructions,
      });
    };

    const isEmptyStepStateEntry = (step) =>
      !String(step?.id || "").trim() &&
      !String(step?.instructions || "").trim() &&
      !String(step?.skillId || "").trim() &&
      !String(step?.skillArgs || "").trim() &&
      !String(step?.skillRequiredCapabilities || "").trim() &&
      !String(step?.templateStepId || "").trim() &&
      !String(step?.templateInstructions || "").trim();

    const hasOnlyEmptyDefaultStep = () =>
      stepState.length === 1 && isEmptyStepStateEntry(stepState[0]);

    const applySelectedTemplate = async () => {
      const selected = selectedTemplate();
      if (!selected) {
        setTemplateMessage("Choose a preset first.", true);
        return;
      }
      const scope = String(selected.scope || "global").trim() || "global";
      const scopeRef = String(selected.scopeRef || "").trim();
      const scopeParams = new URLSearchParams({ scope });
      if (scopeRef) {
        scopeParams.set("scopeRef", scopeRef);
      }

      setTemplateMessage("Applying preset...");
      try {
        const detail = await fetchJson(
          `${endpoint(taskTemplateEndpoints.detail, { slug: selected.slug })}?${scopeParams.toString()}`,
        );
        const { values: inputs, assumptions } = resolveTemplateInputs(detail?.inputs || []);
        const expanded = await fetchJson(
          `${endpoint(taskTemplateEndpoints.expand, { slug: selected.slug })}?${scopeParams.toString()}`,
          {
            method: "POST",
            body: JSON.stringify({
              version: detail?.version || detail?.latestVersion || selected.latestVersion || "1.0.0",
              inputs,
              options: { enforceStepLimit: true },
            }),
          },
        );
        const expandedSteps = Array.isArray(expanded?.steps) ? expanded.steps : [];
        const mappedSteps = expandedSteps.map(mapExpandedStepToState);
        const shouldReplaceEmptyDefaultStep = hasOnlyEmptyDefaultStep();
        if (shouldReplaceEmptyDefaultStep) {
          stepState.splice(0, stepState.length, ...(mappedSteps.length > 0 ? mappedSteps : [createStepStateEntry()]));
          appliedTemplateState = [];
        } else {
          stepState.push(...mappedSteps);
        }
        if (mappedSteps.length > 0) {
          const appliedTemplate = expanded?.appliedTemplate || {};
          appliedTemplateState.push({
            slug: String(appliedTemplate.slug || selected.slug),
            version: String(
              appliedTemplate.version ||
              detail?.version ||
              selected.latestVersion ||
              "1.0.0",
            ),
            inputs:
              appliedTemplate.inputs && typeof appliedTemplate.inputs === "object"
                ? appliedTemplate.inputs
                : inputs,
            stepIds: Array.isArray(appliedTemplate.stepIds)
              ? appliedTemplate.stepIds
              : mappedSteps.map((step) => step.id).filter((id) => Boolean(id)),
            appliedAt:
              String(appliedTemplate.appliedAt || "").trim() ||
              new Date().toISOString(),
            capabilities: Array.isArray(expanded?.capabilities) ? expanded.capabilities : [],
          });
        }
        renderStepEditor();
        scheduleWorkerDraftPersist();
        const autoFillSuffix =
          assumptions.length > 0
            ? ` Auto-filled ${assumptions.length} input(s): ${assumptions.join(", ")}.`
            : "";
        setTemplateMessage(
          `Applied preset '${selected.title}' (${mappedSteps.length} steps).${autoFillSuffix}`,
        );
      } catch (error) {
        console.error(`template apply failed: ${sanitizedErrorMessage(error)}`);
        setTemplateMessage(
          "Failed to apply preset: " + sanitizedErrorMessage(error),
          true,
        );
      }
    };

    const saveCurrentStepsAsTemplate = async () => {
      if (!taskTemplateSaveEnabled) {
        return;
      }
      const title = window.prompt("Preset title", "");
      if (title === null || !String(title).trim()) {
        setTemplateMessage("Preset save cancelled.");
        return;
      }
      const description = window.prompt("Preset description", `Saved from queue draft: ${title}`) || "";
      // For saving from a task, scope is always personal.
      const scope = "personal";
      const scopeRef = "";

      const steps = stepState
        .map((step) => {
          const instructions = String(step.instructions || "").trim();
          if (!instructions) {
            return null;
          }
          const blueprint = {
            instructions,
          };
          const skillId = String(step.skillId || "").trim();
          const caps = parseCapabilitiesCsv(step.skillRequiredCapabilities || "");
          const skillArgsRaw = String(step.skillArgs || "").trim();
          if (skillId || skillArgsRaw || caps.length > 0) {
            let skillArgs = {};
            if (skillArgsRaw) {
              try {
                const parsed = JSON.parse(skillArgsRaw);
                if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                  skillArgs = parsed;
                }
              } catch (_error) {
                // Best-effort save path: drop invalid JSON skill args instead of aborting.
                skillArgs = {};
              }
            }
            const normalizedTool = {
              type: "skill",
              name: skillId || "auto",
              version: "1.0",
              inputs: skillArgs,
              ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
            };
            blueprint.tool = normalizedTool;
            // Keep legacy shape while templates migrate to tool-first payloads.
            blueprint.skill = {
              id: normalizedTool.name,
              args: skillArgs,
              ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
            };
          }
          return blueprint;
        })
        .filter((item) => Boolean(item));

      if (steps.length === 0) {
        setTemplateMessage("Add at least one step with instructions before saving.", true);
        return;
      }

      setTemplateMessage("Saving preset...");
      try {
        const body = {
          scope,
          ...(scopeRef ? { scopeRef } : {}),
          title: String(title).trim(),
          description: String(description).trim() || String(title).trim(),
          steps,
          suggestedInputs: [],
          tags: [],
        };
        const created = await fetchJson(taskTemplateEndpoints.save, {
          method: "POST",
          body: JSON.stringify(body),
        });
        setTemplateMessage(`Saved preset '${created?.title || body.title}'. Reloading catalog...`);
        await fetchTemplateList();
      } catch (error) {
        console.error(`template save failed: ${sanitizedErrorMessage(error)}`);
        setTemplateMessage(
          "Failed to save preset: " + sanitizedErrorMessage(error),
          true,
        );
      }
    };

    if (templateApply instanceof HTMLButtonElement) {
      templateApply.addEventListener("click", () => {
        applySelectedTemplate().catch((error) => {
          console.error(`template apply failed: ${sanitizedErrorMessage(error)}`);
        });
      });
    }
    if (templateSaveCurrent instanceof HTMLButtonElement) {
      templateSaveCurrent.addEventListener("click", () => {
        saveCurrentStepsAsTemplate().catch((error) => {
          console.error(`template save-current failed: ${sanitizedErrorMessage(error)}`);
        });
      });
    }
    if (taskTemplateCatalogEnabled) {
      fetchTemplateList().catch((error) => {
        console.error(`initial template load failed: ${sanitizedErrorMessage(error)}`);
      });
    }

    // --- Schedule panel toggle ---
    const scheduleModeSelect = form.querySelector("#schedule-mode-select");
    const scheduleOnceFields = form.querySelector("#schedule-once-fields");
    const scheduleDeferredMinutesFields = form.querySelector("#schedule-deferred-minutes-fields");
    const scheduleRecurringFields = form.querySelector("#schedule-recurring-fields");
    if (scheduleModeSelect) {
      scheduleModeSelect.addEventListener("change", () => {
        const mode = String(scheduleModeSelect.value || "immediate").trim();
        if (scheduleOnceFields) {
          scheduleOnceFields.classList.toggle("hidden", mode !== "once");
        }
        if (scheduleDeferredMinutesFields) {
          scheduleDeferredMinutesFields.classList.toggle("hidden", mode !== "deferred_minutes");
        }
        if (scheduleRecurringFields) {
          scheduleRecurringFields.classList.toggle("hidden", mode !== "recurring");
        }
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small queue-submit-message";
      message.textContent = "";
      persistWorkerDraft();
      const submitButton = form.querySelector('button[type="submit"]');

      const formData = new FormData(form);
      const primaryStep = stepState[0] || null;
      const primaryValidation = validatePrimaryStepSubmission(primaryStep);
      if (!primaryValidation.ok) {
        message.className = "notice error queue-submit-message";
        message.textContent = primaryValidation.error;
        return;
      }
      const instructions = primaryValidation.value.instructions;
      const objectiveInstructions = resolveObjectiveInstructions(instructions);

      const repositoryInput = String(formData.get("repository") || "").trim();
      const repository = repositoryInput || defaultRepository;
      if (!repository) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Repository is required because no system default repository is configured.";
        return;
      }
      if (!isValidRepositoryInput(repository)) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Repository must be owner/repo, https://<host>/<path>, or git@<host>:<path> (token-free).";
        return;
      }

      const rawRuntime = String(formData.get("runtime") || "").trim();
      const runtimeCandidate = rawRuntime || defaultTaskRuntime;
      const normalizedRuntimeCandidate = String(runtimeCandidate || "")
        .trim()
        .toLowerCase();
      const runtimeMode = normalizeTaskRuntimeInput(normalizedRuntimeCandidate);
      if (!runtimeMode) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Runtime must be one of: " + listSubmitRuntimes().join(", ") + ".";
        return;
      }

      const publishMode = String(formData.get("publishMode") || defaultPublishMode)
        .trim()
        .toLowerCase();
      if (!["none", "branch", "pr"].includes(publishMode)) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Publish mode must be one of: none, branch, pr.";
        return;
      }

      const priority = resolveQueueSubmitPriorityForRuntime(runtimeMode, {
        priority: formData.get("priority"),
      });
      if (!Number.isInteger(priority)) {
        message.className = "notice error queue-submit-message";
        message.textContent = "Priority must be an integer.";
        return;
      }

      const maxAttempts = Number(formData.get("maxAttempts") || 3);
      if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
        message.className = "notice error queue-submit-message";
        message.textContent = "Max Attempts must be an integer >= 1.";
        return;
      }
      const proposeTasks = formData.get("proposeTasks") !== null;

      const skillId = String(primaryValidation.value.skillId || "").trim() || "auto";
      const skillArgsRaw = shouldShowSkillArgs(primaryStep)
        ? String(primaryStep.skillArgs || "").trim()
        : "";
      const taskSkillRequiredCapabilities = parseCapabilitiesCsv(
        primaryStep.skillRequiredCapabilities || "",
      );
      let skillArgs = {};
      if (skillArgsRaw) {
        try {
          const parsed = JSON.parse(skillArgsRaw);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Skill args must be a JSON object.");
          }
          skillArgs = parsed;
        } catch (error) {
          message.className = "notice error queue-submit-message";
          message.textContent =
            "Primary step Skill Args must be valid JSON object text (for example: {\"featureKey\":\"...\"}).";
          return;
        }
      }
      const model = String(formData.get("model") || "").trim() || null;
      const effort = String(formData.get("effort") || "").trim() || null;
      const providerProfileRef = String(formData.get("providerProfile") || "").trim();
      const startingBranch = String(formData.get("startingBranch") || "").trim() || null;
      const newBranch = String(formData.get("newBranch") || "").trim() || null;
      const affinityKey = queueDraftAffinityKey || null;
      const additionalSteps = [];
      const stepSkillRequiredCapabilities = [];
      for (let index = 1; index < stepState.length; index += 1) {
        const rawStep = stepState[index] || {};
        const stepInstructions = String(rawStep.instructions || "").trim();
        const stepSkillId = String(rawStep.skillId || "").trim();
        const stepSkillArgsRaw = shouldShowSkillArgs(rawStep)
          ? String(rawStep.skillArgs || "").trim()
          : "";
        const stepSkillCaps = parseCapabilitiesCsv(rawStep.skillRequiredCapabilities || "");
        const hasStepContent =
          Boolean(stepInstructions) ||
          Boolean(stepSkillId) ||
          Boolean(stepSkillArgsRaw) ||
          stepSkillCaps.length > 0;
        if (!hasStepContent) {
          continue;
        }
        let stepSkillArgs = {};
        if (stepSkillArgsRaw) {
          try {
            const parsed = JSON.parse(stepSkillArgsRaw);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
              throw new Error("Step skill args must be a JSON object.");
            }
            stepSkillArgs = parsed;
          } catch (_error) {
            message.className = "notice error queue-submit-message";
            message.textContent = `Step ${index + 1} Skill Args must be valid JSON object text.`;
            return;
          }
        }
        const stepPayload = {};
        if (stepInstructions) {
          stepPayload.instructions = stepInstructions;
        }
        if (stepSkillId || stepSkillArgsRaw || stepSkillCaps.length > 0) {
          const normalizedTool = {
            type: "skill",
            name: stepSkillId || skillId,
            version: "1.0",
            inputs: stepSkillArgs,
          };
          const skillPayload = {
            id: normalizedTool.name,
            args: stepSkillArgs,
          };
          if (stepSkillCaps.length > 0) {
            normalizedTool.requiredCapabilities = stepSkillCaps;
            skillPayload.requiredCapabilities = stepSkillCaps;
            stepSkillRequiredCapabilities.push(...stepSkillCaps);
          }
          stepPayload.tool = normalizedTool;
          stepPayload.skill = skillPayload;
        }
        additionalSteps.push({ sourceIndex: index, payload: stepPayload });
      }
      const additionalStepValidation = validatePrimaryStepSubmission(primaryStep, {
        additionalStepsCount: additionalSteps.length,
      });
      if (!additionalStepValidation.ok) {
        message.className = "notice error queue-submit-message";
        message.textContent = additionalStepValidation.error;
        return;
      }
      const includePrimaryStepForObjectiveOverride =
        Boolean(instructions) && objectiveInstructions !== instructions;
      const hasTemplateBoundStep = stepState.some((step) => Boolean(String(step?.id || "").trim()));
      const includeExplicitSteps =
        additionalSteps.length > 0 ||
        includePrimaryStepForObjectiveOverride ||
        hasTemplateBoundStep;
      const normalizedStepEntries = includeExplicitSteps
        ? [
          {
            sourceIndex: 0,
            payload: {
              instructions,
            },
          },
          ...additionalSteps,
        ]
        : [];
      const templateIdToSequential = new Map();
      const normalizedSteps = normalizedStepEntries.map((entry, index) => {
        const assignedId = `step-${index + 1}`;
        const sourceState = stepState[entry.sourceIndex] || {};
        const templateBindingId = String(sourceState.id || "").trim();
        if (templateBindingId) {
          if (!templateIdToSequential.has(templateBindingId)) {
            templateIdToSequential.set(templateBindingId, []);
          }
          templateIdToSequential.get(templateBindingId).push(assignedId);
        }
        return { ...entry.payload, id: assignedId };
      });

      const templateCapabilities = [];
      const appliedStepTemplates = [];
      const templateIdCursor = new Map();
      for (const entry of appliedTemplateState) {
        if (!entry || typeof entry !== "object") {
          continue;
        }
        const slug = String(entry.slug || "").trim();
        const version = String(entry.version || "").trim();
        if (!slug || !version) {
          continue;
        }
        const rawTemplateStepIds = Array.isArray(entry.stepIds) ? entry.stepIds : [];
        const remappedStepIds = [];
        for (const rawId of rawTemplateStepIds) {
          const templateId = String(rawId || "").trim();
          if (!templateId) {
            continue;
          }
          const availableStepIds = templateIdToSequential.get(templateId) || [];
          const nextIndex = templateIdCursor.get(templateId) || 0;
          const remappedStepId = availableStepIds[nextIndex];
          if (!remappedStepId) {
            message.className = "notice error queue-submit-message";
            message.textContent = `Applied template references unknown step binding ID: ${templateId}. Please re-apply the template.`;
            return;
          }
          remappedStepIds.push(remappedStepId);
          templateIdCursor.set(templateId, nextIndex + 1);
        }
        const templateEntry = {
          slug,
          version,
          inputs: entry.inputs && typeof entry.inputs === "object" ? entry.inputs : {},
          stepIds: remappedStepIds,
          appliedAt: String(entry.appliedAt || "").trim() || new Date().toISOString(),
        };
        if (Array.isArray(entry.capabilities)) {
          templateCapabilities.push(...entry.capabilities);
          templateEntry.capabilities = entry.capabilities;
        }
        appliedStepTemplates.push(templateEntry);
      }
      const mergedCapabilities = Array.from(
        new Set(
          deriveRequiredCapabilities({
            runtimeMode,
            publishMode,
            taskSkillRequiredCapabilities,
            stepSkillRequiredCapabilities,
          }).concat(parseCapabilitiesCsv(templateCapabilities.join(","))),
        ),
      );
      const normalizedTaskTool = {
        type: "skill",
        name: skillId,
        version: "1.0",
        inputs: skillArgs,
        ...(taskSkillRequiredCapabilities.length > 0
          ? { requiredCapabilities: taskSkillRequiredCapabilities }
          : {}),
      };

      const payload = {
        repository,
        requiredCapabilities: mergedCapabilities,
        targetRuntime: runtimeMode,
        task: {
          instructions: objectiveInstructions,
          tool: normalizedTaskTool,
          skill: {
            id: normalizedTaskTool.name,
            args: skillArgs,
            ...(taskSkillRequiredCapabilities.length > 0
              ? { requiredCapabilities: taskSkillRequiredCapabilities }
              : {}),
          },
          ...(Object.keys(skillArgs).length > 0 ? { inputs: skillArgs } : {}),
          proposeTasks,
          runtime: {
            mode: runtimeMode,
            model,
            effort,
            ...(providerProfileRef ? { executionProfileRef: providerProfileRef } : {}),
          },
          git: { startingBranch, newBranch },
          publish: {
            mode: publishMode,
            prBaseBranch: null,
            commitMessage: null,
            prTitle: null,
            prBody: null,
          },
          ...(normalizedSteps.length > 0 ? { steps: normalizedSteps } : {}),
          ...(appliedStepTemplates.length > 0
            ? { appliedStepTemplates }
            : {}),
        },
      };

      const requestBody = {
        type: "task",
        payload,
        priority,
        maxAttempts,
        ...(affinityKey ? { affinityKey } : {}),
      };

      // --- Schedule injection ---
      if (!isEditMode) {
        const scheduleMode = String(formData.get("scheduleMode") || "immediate").trim();
        if (scheduleMode === "once") {
          const scheduledForRaw = String(formData.get("scheduledFor") || "").trim();
          if (!scheduledForRaw) {
            message.className = "notice error queue-submit-message";
            message.textContent = "Scheduled time is required for deferred scheduling.";
            return;
          }
          const scheduledForDate = new Date(scheduledForRaw);
          if (isNaN(scheduledForDate.getTime()) || scheduledForDate <= new Date()) {
            message.className = "notice error queue-submit-message";
            message.textContent = "Scheduled time must be a valid future date.";
            return;
          }
          requestBody.payload.schedule = {
            mode: "once",
            scheduledFor: scheduledForDate.toISOString(),
          };
        } else if (scheduleMode === "deferred_minutes") {
          const scheduleDeferredMinutesRaw = String(formData.get("scheduleDeferredMinutes") || "").trim();
          const scheduleDeferredMinutes = Number(scheduleDeferredMinutesRaw);
          if (!Number.isFinite(scheduleDeferredMinutes) || !Number.isInteger(scheduleDeferredMinutes) || scheduleDeferredMinutes <= 0) {
            message.className = "notice error queue-submit-message";
            message.textContent = "A valid positive whole number of minutes is required for deferred scheduling.";
            return;
          }
          if (scheduleDeferredMinutes > 525600) {
            message.className = "notice error queue-submit-message";
            message.textContent = "Deferred minutes cannot exceed 525 600 (one year).";
            return;
          }
          const scheduledForMs = Date.now() + scheduleDeferredMinutes * 60000;
          const scheduledForDate = new Date(scheduledForMs);
          if (!Number.isFinite(scheduledForDate.getTime())) {
            message.className = "notice error queue-submit-message";
            message.textContent = "Unable to compute a valid schedule date from the provided minutes.";
            return;
          }
          requestBody.payload.schedule = {
            mode: "once",
            scheduledFor: scheduledForDate.toISOString(),
          };
        } else if (scheduleMode === "recurring") {
          const scheduleCron = String(formData.get("scheduleCron") || "").trim();
          if (!scheduleCron) {
            message.className = "notice error queue-submit-message";
            message.textContent = "Cron expression is required for recurring scheduling.";
            return;
          }
          requestBody.payload.schedule = {
            mode: "recurring",
            cron: scheduleCron,
            timezone: String(formData.get("scheduleTimezone") || "UTC").trim(),
            name: String(formData.get("scheduleName") || "Inline schedule").trim(),
          };
        }
      }

      const attachmentValidation =
        attachmentPolicy.enabled && !isEditMode
          ? renderAttachmentSelection() || validateAttachmentFiles(selectedAttachmentFiles())
          : { ok: true, files: [], errors: [], totalBytes: 0 };
      if (!attachmentValidation.ok) {
        message.className = "notice error queue-submit-message";
        message.textContent = attachmentValidation.errors.join(" ");
        return;
      }
      const hasAttachments =
        attachmentPolicy.enabled &&
        !isEditMode &&
        Array.isArray(attachmentValidation.files) &&
        attachmentValidation.files.length > 0;
      const submitDestination = determineSubmitDestination(
        runtimeMode,
        {
          queue: queueSourceConfig.create || "/api/queue/jobs",
          temporal: temporalSourceConfig.create || "/api/executions",
        },
        {
          temporalSubmitEnabled,
          isEditMode,
        },
      );
      if (submitDestination.mode === "temporal" && hasAttachments) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Attachments are not supported for Temporal task submission yet. Remove attachments and retry.";
        return;
      }

      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = true;
      }

      try {
        if (submitDestination.mode === "temporal") {
          const temporalRequestBody = cloneTemporalSubmitRequest(requestBody);
          const currentTaskPayload =
            temporalRequestBody.payload &&
              typeof temporalRequestBody.payload === "object" &&
              !Array.isArray(temporalRequestBody.payload) &&
              temporalRequestBody.payload.task &&
              typeof temporalRequestBody.payload.task === "object" &&
              !Array.isArray(temporalRequestBody.payload.task)
              ? temporalRequestBody.payload.task
              : null;
          if (!currentTaskPayload) {
            throw new Error("temporal submit request missing task payload");
          }

          let uploadedArtifactId = "";
          const shouldExternalizeInstructions =
            String(objectiveInstructions || "").length > TEMPORAL_INLINE_INPUT_MAX_CHARS;
          if (shouldExternalizeInstructions) {
            const uploadedArtifact = await createTemporalInputArtifact({
              instructions: objectiveInstructions,
              repository,
            });
            uploadedArtifactId = uploadedArtifact.artifactId;
            currentTaskPayload.inputArtifactRef = uploadedArtifactId;
            currentTaskPayload.instructions =
              "Task instructions were uploaded as an artifact for Temporal execution.";
          }

          const created = await fetchJson(submitDestination.endpoint, {
            method: "POST",
            body: JSON.stringify(temporalRequestBody),
          });

          // --- Recurring schedule response ---
          const definitionId = String(pick(created, "definitionId") || "").trim();
          if (definitionId) {
            try { clearWorkerSubmissionDraftAfterCreate(); } catch (_) {}
            const schedulePath = String(pick(created, "redirectPath") || "").trim();
            window.location.href = schedulePath || `/tasks/schedules/${definitionId}`;
            return;
          }

          if (uploadedArtifactId) {
            await linkTemporalArtifactToExecution({
              artifactId: uploadedArtifactId,
              execution: created,
            });
          }
          const createdTaskId = String(
            pick(created, "taskId") || pick(created, "workflowId") || "",
          ).trim();
          if (!createdTaskId) {
            throw new Error("temporal creation response missing task id");
          }
          try {
            clearWorkerSubmissionDraftAfterCreate();
          } catch (cleanupError) {
            console.warn("worker draft cleanup failed after temporal creation", cleanupError);
          }
          window.location.href =
            String(pick(created, "redirectPath") || "").trim()
            || buildUnifiedTaskDetailRoute(createdTaskId, "temporal");
          return;
        }

        if (isEditMode) {
          const updateEndpointTemplate = queueSourceConfig.update || "/api/queue/jobs/{id}";
          const updated = await fetchJson(endpoint(updateEndpointTemplate, { id: editJobId }), {
            method: "PUT",
            body: JSON.stringify({
              ...requestBody,
              ...(editExpectedUpdatedAt ? { expectedUpdatedAt: editExpectedUpdatedAt } : {}),
            }),
          });
          const updatedId = normalizeDashboardDetailSegment(pick(updated, "id")) || editJobId;
          window.location.href = buildUnifiedTaskDetailRoute(updatedId, "queue");
          return;
        }

        const created = hasAttachments
          ? await fetchJson(
            queueSourceConfig.createWithAttachments || "/api/queue/jobs/with-attachments",
            (() => {
              const requestForm = new FormData();
              requestForm.append("request", JSON.stringify(requestBody));
              attachmentValidation.files.forEach((file) => {
                requestForm.append("files", file, file.name || "attachment");
              });
              return {
                method: "POST",
                body: requestForm,
              };
            })(),
          )
          : await fetchJson(queueSourceConfig.create || "/api/queue/jobs", {
            method: "POST",
            body: JSON.stringify(requestBody),
          });
        const createdJobNode = pick(created, "job");
        const createdJobId =
          createdJobNode &&
            typeof createdJobNode === "object" &&
            !Array.isArray(createdJobNode)
            ? String(pick(createdJobNode, "id") || "").trim()
            : String(pick(created, "id") || "").trim();
        if (!createdJobId) {
          throw new Error("queue creation response missing job id");
        }
        try {
          clearWorkerSubmissionDraftAfterCreate();
        } catch (cleanupError) {
          console.warn("worker draft cleanup failed after queue creation", cleanupError);
        }
        window.location.href = buildUnifiedTaskDetailRoute(
          createdJobId,
          "queue",
        );
      } catch (error) {
        if (submitButton instanceof HTMLButtonElement) {
          submitButton.disabled = false;
        }
        console.error("queue submit failed", error);
        message.className = "notice error queue-submit-message";
        const status = Number(error?.status || 0);
        const queueDetail =
          error?.payload && typeof error.payload === "object" ? error.payload.detail : null;
        const queueDebugMessage =
          queueDetail && typeof queueDetail === "object" && !Array.isArray(queueDetail)
            ? String(queueDetail.debugMessage || "").trim()
            : "";
        if (isEditMode && status === 409) {
          message.textContent = "This task already started or changed. Refresh and try again.";
          return;
        }
        if (isEditMode && status === 403) {
          message.textContent = "You are not authorized to edit this queue task.";
          return;
        }
        if (isEditMode && status === 422) {
          const debugSuffix =
            queueDetail &&
              String(queueDetail.code || "").toLowerCase() === "invalid_queue_payload" &&
              queueDebugMessage
              ? ` (details: ${queueDebugMessage})`
              : "";
          message.textContent =
            `Queue task update is invalid: ${String(error?.message || "validation failed")}${debugSuffix}`;
          return;
        }
        const baseMessage = String(error?.message || "request failed");
        const debugSuffix =
          queueDetail &&
            String(queueDetail.code || "").toLowerCase() === "invalid_queue_payload" &&
            queueDebugMessage
            ? ` (details: ${queueDebugMessage})`
            : "";
        message.textContent = isEditMode
          ? `Failed to update queue task: ${baseMessage}${debugSuffix}`
          : `Failed to create queue task: ${baseMessage}${debugSuffix}`;
      }
    });
  }

  async function renderSubmitWorkPage(presetRuntime, editParam = null) {
    const editProvided = Boolean(editParam?.provided);
    if (editProvided) {
      if (!editParam.jobId) {
        setView(
          "Invalid Edit Job",
          "Unsupported edit job identifier.",
          `<div class="notice error">Invalid editJobId query parameter: <code>${escapeHtml(
            String(editParam.rawValue || ""),
          )}</code>.</div>`,
        );
        return;
      }
      const editJobId = editParam.jobId;
      setView(
        "Edit Queue Task",
        `Loading queued task ${editJobId}...`,
        "<p class='loading'>Loading queue task draft...</p>",
        { showAutoRefreshControls: false },
      );
      try {
        const detail = await fetchJson(
          endpoint(queueSourceConfig.detail || "/api/queue/jobs/{id}", {
            id: editJobId,
          }),
        );
        if (!isEditableQueuedTaskJob(detail)) {
          setView(
            "Queue Task Not Editable",
            `Job ${editJobId} can no longer be edited.`,
            `<div class="notice error">Only queued, never-started task jobs can be edited.</div><div class="actions"><a href="${escapeHtml(
              buildUnifiedTaskDetailRoute(editJobId, "queue"),
            )}"><button type="button" class="secondary">View Details</button></a></div>`,
            { showAutoRefreshControls: false },
          );
          return;
        }
        const prefillDraft = buildQueueSubmissionDraftFromJob(detail);
        const resolvedEditJobId =
          normalizeDashboardDetailSegment(pick(detail, "id")) || editJobId;
        renderQueueSubmitPage(prefillDraft.runtime, {
          jobId: resolvedEditJobId,
          expectedUpdatedAt: String(pick(detail, "updatedAt") || "").trim(),
          prefillDraft,
        });
      } catch (error) {
        console.error("queue edit preload failed", error);
        setView(
          "Edit Queue Task",
          `Unable to load job ${editJobId}.`,
          `<div class="notice error">Failed to load queue task for editing: ${escapeHtml(
            String(error?.message || "request failed"),
          )}</div><div class="actions"><a href="${escapeHtml(
            buildUnifiedTaskDetailRoute(editJobId, "queue"),
          )}"><button type="button" class="secondary">Back to Details</button></a></div>`,
          { showAutoRefreshControls: false },
        );
      }
      return;
    }

    if (presetRuntime == null) {
      renderQueueSubmitPage();
      return;
    }
    const normalizedRuntime = validateSubmitRuntime(presetRuntime);
    if (!normalizedRuntime) {
      setView(
        "Invalid Runtime",
        "Unsupported runtime query parameter.",
        `<div class="notice error">Unsupported runtime value: <code>${escapeHtml(
          String(presetRuntime),
        )}</code>. Use one of: <code>${escapeHtml(
          submitRuntimeOptions.join(", "),
        )}</code>.</div>`,
      );
      return;
    }
    // Use the submit form for every supported runtime so runtime
    // visibility toggles are consistently applied from one code path.
    renderQueueSubmitPage(normalizedRuntime);
  }


  function buildTemporalTimeline(execution) {
    const entries = [];
    if (pick(execution, "scheduledFor")) {
      entries.push({
        label: "Scheduled",
        value: pick(execution, "scheduledFor"),
        detail: "Execution scheduled.",
      });
    }
    entries.push(
      {
        label: "Started",
        value: pick(execution, "startedAt"),
        detail: "Execution created and admitted.",
      },
      {
        label: "Updated",
        value: pick(execution, "updatedAt"),
        detail: String(pick(pick(execution, "memo") || {}, "summary") || "").trim() || "-",
      },
    );
    if (pick(execution, "closedAt")) {
      entries.push({
        label: "Closed",
        value: pick(execution, "closedAt"),
        detail: String(pick(execution, "closeStatus") || "terminal").trim(),
      });
    }
    const waitingReason = temporalWaitingReason(execution);
    if (waitingReason) {
      entries.push({
        label: "Waiting",
        value: pick(execution, "updatedAt"),
        detail: waitingReason,
      });
    }
    return entries
      .map(
        (entry) => `
          <tr>
            <td>${escapeHtml(entry.label)}</td>
            <td>${escapeHtml(formatTimestamp(entry.value))}</td>
            <td>${escapeHtml(entry.detail)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function resolveTemporalRunId(execution) {
    return String(
      pick(execution, "temporalRunId") || pick(execution, "runId") || "",
    ).trim();
  }

  function formatTemporalWorkflowType(workflowType) {
    const raw = String(workflowType || "").trim();
    if (!raw) {
      return "-";
    }
    if (raw === "MoonMind.Run") {
      return "Run";
    }
    if (raw === "MoonMind.ManifestIngest") {
      return "Manifest Ingest";
    }
    return raw;
  }

  function deriveTemporalTitle(execution) {
    return String(
      pick(execution, "title") ||
      pick(execution, "workflowId") ||
      "Temporal execution"
    ).trim();
  }

  function deriveTemporalSummary(execution) {
    return String(pick(execution, "summary") || "").trim();
  }

  function resolveTemporalWaitingContext(execution) {
    const waitingReason = temporalWaitingReason(execution);
    const attentionRequired = Boolean(pick(execution, "attentionRequired"));
    if (!waitingReason && !attentionRequired) {
      return "";
    }
    return `${waitingReason || "Awaiting external input."}${attentionRequired ? " Attention required." : ""}`;
  }

  function renderTemporalTimelineRows(execution) {
    const rows = [
      {
        label: "Started",
        timestamp: pick(execution, "startedAt"),
        detail: "Execution created.",
      },
      {
        label: "Last update",
        timestamp: pick(execution, "updatedAt"),
        detail: `State: ${String(pick(execution, "state") || "-").replaceAll("_", " ")}`,
      },
    ];
    const waitingContext = resolveTemporalWaitingContext(execution);
    if (waitingContext) {
      rows.push({
        label: "Waiting",
        timestamp: pick(execution, "updatedAt"),
        detail: waitingContext,
      });
    }
    if (pick(execution, "closedAt")) {
      rows.push({
        label: "Closed",
        timestamp: pick(execution, "closedAt"),
        detail: `Close status: ${pick(execution, "closeStatus") || pick(execution, "temporalStatus") || "-"}`,
      });
    }
    return rows
      .map(
        (row) => `
          <tr>
            <td>${escapeHtml(row.label)}</td>
            <td>${formatTimestamp(row.timestamp)}</td>
            <td>${escapeHtml(row.detail)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function resolveTemporalArtifactsRequest(execution, workflowId) {
    const namespace = String(pick(execution, "namespace") || "").trim();
    const resolvedWorkflowId = String(
      pick(execution, "workflowId") || workflowId || "",
    ).trim();
    const temporalRunId = resolveTemporalRunId(execution);
    return {
      namespace,
      workflowId: resolvedWorkflowId,
      temporalRunId,
      canFetch: Boolean(namespace && resolvedWorkflowId && temporalRunId),
    };
  }

  function resolveTemporalDetailModel(execution, workflowId, options = {}) {
    const artifactsRequest = resolveTemporalArtifactsRequest(execution, workflowId);
    const rawState = String(pick(execution, "rawState", "state") || "initializing").trim().toLowerCase();
    return {
      attentionRequired: Boolean(pick(execution, "attentionRequired")),
      closeStatus: pick(execution, "closeStatus") || "-",
      debugFieldsEnabled: Object.prototype.hasOwnProperty.call(
        options,
        "debugFieldsEnabled",
      )
        ? Boolean(options.debugFieldsEnabled)
        : Boolean(temporalDebugFieldsEnabled),
      namespace: pick(execution, "namespace") || "-",
      rawState,
      summary: deriveTemporalSummary(execution),
      temporalRunId: artifactsRequest.temporalRunId || "-",
      temporalStatus: pick(execution, "temporalStatus") || "-",
      timelineRows: renderTemporalTimelineRows(execution),
      title: deriveTemporalTitle(execution),
      waitingContext: resolveTemporalWaitingContext(execution),
      workflowId: pick(execution, "workflowId") || workflowId || "-",
      workflowType: formatTemporalWorkflowType(pick(execution, "workflowType")),
    };
  }

  function isUuidLike(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
      .test(String(value || "").trim());
  }

  function resolveTemporalTaskRunId(execution) {
    const explicitTaskRunId = String(
      pick(execution, "taskRunId", "task_run_id", "agentJobId", "agent_job_id") || "",
    ).trim();
    if (explicitTaskRunId) {
      return explicitTaskRunId;
    }
    const runId = String(pick(execution, "temporalRunId", "runId") || "").trim();
    return isUuidLike(runId) ? runId : "";
  }

  function temporalLogTailingAvailable(sourceConfig, execution) {
    if (!sourceConfig || typeof sourceConfig !== "object") {
      return false;
    }
    const hasEndpoint =
      Boolean(String(sourceConfig.events || "").trim()) ||
      Boolean(String(sourceConfig.eventsStream || "").trim());
    return hasEndpoint && Boolean(resolveTemporalTaskRunId(execution));
  }

  const TEMPORAL_ACTION_LABELS = {
    approve: "Approve task",
    cancel: "Cancel task",
    edit_inputs: "Edit inputs",
    pause: "Pause task",
    rename: "Rename task",
    rerun: "Rerun task",
    resume: "Resume task",
  };

  const TEMPORAL_ACTION_MATRIX = {
    awaiting_external: ["approve", "pause", "resume", "cancel"],
    canceled: ["rerun"],

    completed: ["rerun"],
    executing: ["pause", "rename", "cancel"],
    failed: ["rerun"],
    finalizing: ["cancel"],
    initializing: ["rename", "cancel"],
    planning: ["rename", "cancel"],
    queued: ["rename", "cancel"],
    running: ["pause", "rename", "cancel"],
    scheduled: ["rename", "cancel"],
    succeeded: ["rerun"],
  };

  function resolveTemporalActionSurface(execution, options = {}) {
    const actionsEnabled = Object.prototype.hasOwnProperty.call(options, "actionsEnabled")
      ? Boolean(options.actionsEnabled)
      : Boolean(temporalActionsEnabled);
    if (!actionsEnabled) {
      return [];
    }
    const rawState = String(pick(execution, "state") || "").trim().toLowerCase();
    const configuredActions = Array.isArray(pick(execution, "availableActions"))
      ? pick(execution, "availableActions")
        .map((value) => String(value || "").trim().toLowerCase())
        .filter(Boolean)
      : [];
    let actions = TEMPORAL_ACTION_MATRIX[rawState] || [];
    if (configuredActions.length > 0) {
      actions = actions.filter((actionKey) => configuredActions.includes(actionKey));
    }
    return actions.map((actionKey) => ({
      actionKey,
      label: TEMPORAL_ACTION_LABELS[actionKey] || actionKey,
    }));
  }

  function resolveTemporalArtifactPresentation(artifact) {
    const links = Array.isArray(pick(artifact, "links")) ? pick(artifact, "links") : [];
    const firstLink = links[0] || null;
    const defaultReadRef = pick(artifact, "default_read_ref") || {};
    const previewRef = pick(artifact, "preview_artifact_ref") || {};
    const rawAccessAllowed = Boolean(pick(artifact, "raw_access_allowed"));
    const previewArtifactId = pick(previewRef, "artifact_id");
    const defaultArtifactId =
      pick(defaultReadRef, "artifact_id") || pick(artifact, "artifact_id");
    const rawArtifactId = pick(artifact, "artifact_id");
    const rawDownloadArtifactId =
      rawArtifactId === previewArtifactId && defaultArtifactId
        ? defaultArtifactId
        : rawArtifactId;
    const accessNotes = [];
    const actions = [];

    if (previewArtifactId) {
      actions.push({
        artifactId: previewArtifactId,
        label: "Open preview",
        variant: "preview",
      });
      accessNotes.push("Preview available");
    }
    if (rawAccessAllowed && rawDownloadArtifactId) {
      actions.push({
        artifactId: rawDownloadArtifactId,
        label: previewArtifactId ? "Download raw" : "Download",
        variant: "download",
      });
    }
    if (!rawAccessAllowed) {
      accessNotes.push("Raw restricted");
    }
    if (!rawAccessAllowed && !previewArtifactId) {
      accessNotes.push("No safe preview");
    }

    return {
      accessNotes,
      actions,
      artifactId: pick(artifact, "artifact_id") || "",
      artifactLabel:
        (firstLink && (pick(firstLink, "label") || pick(firstLink, "link_type"))) ||
        pick(artifact, "artifact_id") ||
        "artifact",
      contentType:
        pick(defaultReadRef, "content_type") ||
        pick(previewRef, "content_type") ||
        pick(artifact, "content_type") ||
        "-",
      linkType: (firstLink && pick(firstLink, "link_type")) || "-",
      size:
        pick(defaultReadRef, "size_bytes") ||
        pick(previewRef, "size_bytes") ||
        pick(artifact, "size_bytes") ||
        "-",
      status: pick(artifact, "status") || "-",
    };
  }

  function buildTemporalArtifactLinkPayload(execution, options = {}) {
    const namespace = String(pick(execution, "namespace") || "").trim();
    const workflowId = String(pick(execution, "workflowId") || "").trim();
    const runId = resolveTemporalRunId(execution);
    const linkType = String(options.linkType || "").trim();
    if (!namespace || !workflowId || !runId || !linkType) {
      throw new Error("Temporal artifact link payload requires namespace, workflowId, runId, and linkType.");
    }
    const payload = {
      namespace,
      workflow_id: workflowId,
      run_id: runId,
      link_type: linkType,
    };
    if (options.label) {
      payload.label = String(options.label);
    }
    return payload;
  }

  function buildTemporalArtifactCreatePayload(execution, options = {}) {
    const payload = {
      content_type: options.contentType || null,
      metadata:
        options.metadata && typeof options.metadata === "object" && !Array.isArray(options.metadata)
          ? options.metadata
          : {},
    };
    if (Number.isFinite(options.sizeBytes)) {
      payload.size_bytes = Number(options.sizeBytes);
    }
    if (options.linkType) {
      payload.link = buildTemporalArtifactLinkPayload(execution, options);
    }
    return payload;
  }

  function buildTemporalArtifactEditUpdatePayload(
    previousArtifactRef,
    nextArtifactRef,
    options = {},
  ) {
    const previousArtifactId = String(
      pick(previousArtifactRef, "artifact_id") || "",
    ).trim();
    const nextArtifactId = String(pick(nextArtifactRef, "artifact_id") || "").trim();
    if (!nextArtifactId) {
      throw new Error("A replacement artifact reference is required.");
    }
    if (previousArtifactId && previousArtifactId === nextArtifactId) {
      throw new Error("Artifact edits must create a new artifact reference.");
    }
    const payload = {
      updateName: "UpdateInputs",
      inputArtifactRef: nextArtifactRef,
    };
    if (
      options.parametersPatch &&
      typeof options.parametersPatch === "object" &&
      !Array.isArray(options.parametersPatch) &&
      Object.keys(options.parametersPatch).length > 0
    ) {
      payload.parametersPatch = options.parametersPatch;
    }
    return payload;
  }

  async function createTemporalArtifactPlaceholder(execution, options = {}) {
    return fetchJson(temporalSourceConfig.artifactCreate || "/api/artifacts", {
      method: "POST",
      body: JSON.stringify(buildTemporalArtifactCreatePayload(execution, options)),
    });
  }

  async function uploadTemporalArtifactContent(
    artifactId,
    payload,
    contentType = "application/octet-stream",
  ) {
    return fetchJson(
      endpoint("/api/artifacts/{artifactId}/content", { artifactId }),
      {
        method: "PUT",
        body: payload,
        headers: { "Content-Type": contentType },
      },
    );
  }

  async function completeTemporalArtifactUpload(artifactId, parts = []) {
    return fetchJson(
      endpoint("/api/artifacts/{artifactId}/complete", { artifactId }),
      {
        method: "POST",
        body: JSON.stringify({ parts }),
      },
    );
  }

  async function fetchTemporalArtifactMetadata(artifactId, includeDownload = false) {
    const metadataUrl = endpoint(
      temporalSourceConfig.artifactMetadata || "/api/artifacts/{artifactId}",
      { artifactId },
    );
    return fetchJson(
      includeDownload ? `${metadataUrl}?include_download=true` : metadataUrl,
    );
  }

  function buildTemporalApprovalPayload(options = {}) {
    const payload =
      options.payload && typeof options.payload === "object" && !Array.isArray(options.payload)
        ? { ...options.payload }
        : {};
    if (!String(payload.approval_type || "").trim()) {
      payload.approval_type = "human";
    }
    return payload;
  }

  function resolveTemporalActionResultMessage(actionRequest, responsePayload) {
    const payload =
      responsePayload && typeof responsePayload === "object" && !Array.isArray(responsePayload)
        ? responsePayload
        : null;
    if (payload && Object.prototype.hasOwnProperty.call(payload, "accepted")) {
      if (payload.accepted === false) {
        const rejectionMessage =
          String(payload.message || "").trim() ||
          String(actionRequest.rejectedMessage || "").trim() ||
          "Task action was rejected.";
        const error = new Error(rejectionMessage);
        error.code = "temporal_action_rejected";
        error.payload = payload;
        throw error;
      }
      const acceptedMessage = String(payload.message || "").trim();
      if (acceptedMessage) {
        return acceptedMessage;
      }
    }
    return actionRequest.successMessage;
  }

  function buildTemporalActionRequest(workflowId, actionKey, options = {}) {
    const normalizedAction = String(actionKey || "").trim().toLowerCase();
    const normalizedWorkflowId = String(workflowId || "").trim();
    if (!normalizedWorkflowId || !normalizedAction) {
      throw new Error("Temporal action request requires workflowId and action.");
    }

    if (normalizedAction === "rename") {
      const title = String(options.title || "").trim();
      if (!title) {
        throw new Error("Task title is required.");
      }
      return {
        successMessage: "Task title updated.",
        request: {
          url: endpoint(
            temporalSourceConfig.update || "/api/executions/{workflowId}/update",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify({
              updateName: "SetTitle",
              title,
            }),
          },
        },
      };
    }

    if (normalizedAction === "rerun") {
      return {
        successMessage: "Task rerun requested.",
        request: {
          url: endpoint(
            temporalSourceConfig.update || "/api/executions/{workflowId}/update",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify({ updateName: "RequestRerun" }),
          },
        },
      };
    }

    if (normalizedAction === "edit_inputs") {
      return {
        successMessage: "Task inputs updated.",
        request: {
          url: endpoint(
            temporalSourceConfig.update || "/api/executions/{workflowId}/update",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify(
              buildTemporalArtifactEditUpdatePayload(
                options.previousArtifactRef,
                options.nextArtifactRef,
                { parametersPatch: options.parametersPatch },
              ),
            ),
          },
        },
      };
    }

    if (normalizedAction === "approve") {
      return {
        successMessage: "Task approval sent.",
        rejectedMessage: "Task approval was rejected.",
        request: {
          url: endpoint(
            temporalSourceConfig.signal || "/api/executions/{workflowId}/signal",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify({
              signalName: "Approve",
              payload: buildTemporalApprovalPayload(options),
            }),
          },
        },
      };
    }

    if (normalizedAction === "pause" || normalizedAction === "resume") {
      return {
        successMessage: normalizedAction === "pause" ? "Task paused." : "Task resumed.",
        request: {
          url: endpoint(
            temporalSourceConfig.signal || "/api/executions/{workflowId}/signal",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify({
              signalName: normalizedAction === "pause" ? "Pause" : "Resume",
              payload: options.payload || {},
            }),
          },
        },
      };
    }

    if (normalizedAction === "cancel") {
      return {
        successMessage: "Task cancellation requested.",
        request: {
          url: endpoint(
            temporalSourceConfig.cancel || "/api/executions/{workflowId}/cancel",
            { workflowId: normalizedWorkflowId },
          ),
          options: {
            method: "POST",
            body: JSON.stringify({
              graceful: true,
              reason: String(options.reason || "").trim() || "Cancelled from task dashboard",
            }),
          },
        },
      };
    }

    throw new Error(`Unsupported Temporal action: ${normalizedAction}`);
  }

  function resolveTemporalDetailContext(
    execution,
    workflowId,
    sourceConfig = temporalSourceConfig,
  ) {
    const namespace = pick(execution, "namespace");
    const taskId = pick(execution, "taskId") || workflowId;
    const temporalRunId =
      pick(execution, "temporalRunId") || pick(execution, "runId") || null;
    const memo = pick(execution, "memo") || {};
    const continueAsNewCause =
      pick(execution, "continueAsNewCause") ||
      pick(memo, "continue_as_new_cause") ||
      "-";
    const artifactsEndpointTemplate =
      sourceConfig.artifacts ||
      "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts";
    const artifactsEndpoint =
      namespace && temporalRunId
        ? endpoint(artifactsEndpointTemplate, {
          namespace,
          workflowId,
          temporalRunId,
        })
        : "";
    return {
      namespace,
      taskId,
      temporalRunId,
      memo,
      continueAsNewCause,
      artifactsEndpoint,
    };
  }

  function renderTemporalArtifactRows(artifacts) {
    return (Array.isArray(artifacts) ? artifacts : [])
      .map((artifact) => {
        const artifactId = String(pick(artifact, "artifact_id", "artifactId") || "").trim();
        const previewRef = pick(artifact, "preview_artifact_ref", "previewArtifactRef") || {};
        const defaultReadRef = pick(artifact, "default_read_ref", "defaultReadRef") || {};
        const links = Array.isArray(pick(artifact, "links")) ? pick(artifact, "links") : [];
        const previewArtifactId = String(pick(previewRef, "artifact_id", "artifactId") || "").trim();
        const defaultReadArtifactId = String(
          pick(defaultReadRef, "artifact_id", "artifactId") || "",
        ).trim();
        const linkedLabel = String(pick(links[0] || {}, "label") || "").trim();
        const label =
          linkedLabel ||
          String(pick(artifact, "metadata")?.label || "").trim() ||
          artifactId ||
          "artifact";
        const size = pick(artifact, "size_bytes", "sizeBytes");
        const rawAccessAllowed = Boolean(pick(artifact, "raw_access_allowed", "rawAccessAllowed"));
        const readableArtifactId = previewArtifactId || defaultReadArtifactId || artifactId;
        const actionLabel = previewArtifactId ? "Preview" : "Download";
        const action = readableArtifactId
          && (previewArtifactId || defaultReadArtifactId || rawAccessAllowed)
          ? `<a href="${escapeHtml(
            endpoint(
              temporalSourceConfig.artifactDownload || "/api/artifacts/{artifactId}/download",
              { artifactId: readableArtifactId },
            ),
          )}">${escapeHtml(actionLabel)}</a>`
          : "<span class='small'>Restricted</span>";
        return `
          <tr>
            <td><code>${escapeHtml(label)}</code></td>
            <td>${escapeHtml(String(size ?? "-"))}</td>
            <td>${escapeHtml(String(pick(artifact, "status") || "-"))}</td>
            <td>${action}</td>
          </tr>
        `;
      })
      .join("");
  }

  function renderTemporalActionButtons(execution) {
    if (!temporalActionsEnabled) {
      return "";
    }
    const capabilityNode =
      pick(execution, "actions") && typeof pick(execution, "actions") === "object"
        ? pick(execution, "actions")
        : null;
    const rawState = String(pick(execution, "rawState", "state") || "").trim().toLowerCase();
    const terminal = ["succeeded", "failed", "canceled"].includes(rawState);
    const buttons = [];
    if (
      capabilityNode
        ? Boolean(pick(capabilityNode, "canSetTitle"))
        : ["initializing", "planning", "executing", "awaiting_external", "queued"].includes(rawState)
    ) {
      buttons.push('<button type="button" data-temporal-action="set-title">Set Title</button>');
    }
    if (rawState === "scheduled") {
      buttons.push('<button type="button" class="secondary" data-temporal-action="reschedule">Change scheduled time</button>');
    }
    if (capabilityNode ? Boolean(pick(capabilityNode, "canCancel")) : !terminal) {
      buttons.push('<button type="button" class="queue-action queue-action-danger" data-temporal-action="cancel">Cancel</button>');
    }
    if (
      capabilityNode
        ? Boolean(pick(capabilityNode, "canPause"))
        : rawState === "executing" || rawState === "awaiting_external"
    ) {
      buttons.push('<button type="button" class="secondary" data-temporal-action="pause">Pause</button>');
    }
    if (capabilityNode ? Boolean(pick(capabilityNode, "canResume")) : rawState === "awaiting_external") {
      buttons.push('<button type="button" class="secondary" data-temporal-action="resume">Resume</button>');
    }
    if (capabilityNode ? Boolean(pick(capabilityNode, "canApprove")) : rawState === "awaiting_external") {
      buttons.push('<button type="button" class="secondary" data-temporal-action="approve">Approve</button>');
    }
    if (capabilityNode ? Boolean(pick(capabilityNode, "canRerun")) : terminal) {
      buttons.push('<button type="button" class="secondary" data-temporal-action="rerun">Rerun</button>');
    }
    if (!buttons.length) {
      return "";
    }
    return `<div class="actions" data-temporal-actions>${buttons.join("")}</div>`;
  }

  async function fetchTemporalDetailData(workflowId) {
    const execution = await fetchJson(
      withTemporalSourceFlag(
        endpoint(
          temporalSourceConfig.detail || "/api/executions/{workflowId}",
          { workflowId, id: workflowId, taskId: workflowId },
        ),
      ),
    );
    const latestWorkflowId = String(pick(execution, "workflowId") || workflowId).trim();
    const latestRunId = String(pick(execution, "temporalRunId", "runId") || "").trim();
    const artifacts = latestRunId
      ? await fetchJson(
        endpoint(
          temporalSourceConfig.artifacts || "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
          {
            namespace: pick(execution, "namespace") || "default",
            workflowId: latestWorkflowId,
            temporalRunId: latestRunId,
            id: latestWorkflowId,
            taskId: latestWorkflowId,
            runId: latestRunId,
          },
        ),
      ).catch(() => ({ artifacts: [] }))
      : { artifacts: [] };
    return { execution, latestWorkflowId, latestRunId, artifacts };
  }

  function renderTemporalDebugSection(execution, latestWorkflowId) {
    if (!temporalDebugFieldsEnabled) {
      return "";
    }
    return `
      <section>
        <h3>Debug Metadata</h3>
        <div class="grid-2">
          <div class="card"><strong>Workflow ID:</strong> <code>${escapeHtml(latestWorkflowId)}</code></div>
          <div class="card"><strong>Temporal Run ID:</strong> <code>${escapeHtml(String(pick(execution, "temporalRunId", "runId") || "-"))}</code></div>
          <div class="card"><strong>Namespace:</strong> ${escapeHtml(String(pick(execution, "namespace") || "-"))}</div>
          <div class="card"><strong>Temporal Status:</strong> ${escapeHtml(String(pick(execution, "temporalStatus") || "-"))}</div>
          <div class="card"><strong>Raw State:</strong> ${escapeHtml(String(pick(execution, "state") || "-"))}</div>
          <div class="card"><strong>Close Status:</strong> ${escapeHtml(String(pick(execution, "closeStatus") || "-"))}</div>
        </div>
      </section>
    `;
  }

  function renderTemporalDetailMarkup(detail) {
    const {
      execution,
      latestWorkflowId,
      latestRunId,
      artifacts,
      waitingReason,
      detailTitle,
      attentionRequired,
      noticeHtml,
      debugFields,
      liveLogsAvailable,
    } = detail;
    return `
      ${noticeHtml}
      <div class="grid-2">
        <div class="card"><strong>Status:</strong> ${statusBadge("temporal", pick(execution, "status", "state"))}</div>
        <div class="card"><strong>Source:</strong> Temporal</div>
        <div class="card"><strong>Title:</strong> ${escapeHtml(detailTitle)}</div>
        <div class="card"><strong>Workflow Type:</strong> ${escapeHtml(String(pick(execution, "workflowType") || "-"))}</div>
        ${pick(execution, "targetRuntime") ? `<div class="card"><strong>Runtime:</strong> ${escapeHtml(formatRuntimeLabel(pick(execution, "targetRuntime")))}</div>` : ""}
        ${pick(execution, "model") ? `<div class="card"><strong>Model:</strong> <code>${escapeHtml(String(pick(execution, "model")))}</code></div>` : ""}
        ${pick(execution, "effort") ? `<div class="card"><strong>Effort:</strong> ${escapeHtml(String(pick(execution, "effort")))}</div>` : ""}
        ${pick(execution, "startingBranch") ? `<div class="card"><strong>Starting Branch:</strong> <code>${escapeHtml(String(pick(execution, "startingBranch")))}</code></div>` : ""}
        ${pick(execution, "targetBranch") ? `<div class="card"><strong>Target Branch:</strong> <code>${escapeHtml(String(pick(execution, "targetBranch")))}</code></div>` : ""}
        ${pick(execution, "publishMode") ? `<div class="card"><strong>Publish Mode:</strong> <code>${escapeHtml(String(pick(execution, "publishMode")))}</code></div>` : ""}
        ${pick(execution, "scheduledFor") ? `<div class="card"><strong>Scheduled For:</strong> ${escapeHtml(formatTimestamp(pick(execution, "scheduledFor")))}</div>` : ""}
        <div class="card"><strong>Created:</strong> ${escapeHtml(formatTimestamp(pick(execution, "createdAt")))}</div>
        <div class="card"><strong>Latest Run:</strong> <code>${escapeHtml(latestRunId || "-")}</code></div>
        <div class="card"><strong>Started:</strong> ${escapeHtml(formatTimestamp(pick(execution, "startedAt")))}</div>
        <div class="card"><strong>Updated:</strong> ${escapeHtml(formatTimestamp(pick(execution, "updatedAt")))}</div>
        <div class="card"><strong>Closed:</strong> ${escapeHtml(formatTimestamp(pick(execution, "closedAt")))}</div>
        <div class="card"><strong>Workflow ID:</strong> <code>${escapeHtml(latestWorkflowId)}</code></div>
      </div>
      <section>
        <h3>Summary</h3>
        <p>${escapeHtml(deriveTemporalSummary(execution) || "-")}</p>
      </section>
      ${waitingReason
        ? `<section><h3>Waiting Reason</h3><p>${escapeHtml(waitingReason)}</p></section>`
        : ""
      }
      ${attentionRequired
        ? "<section><h3>Attention Required</h3><p>This task is waiting for external input before it can continue.</p></section>"
        : ""
      }
      ${renderTemporalActionButtons(execution)}
      <section>
        <h3>Timeline</h3>
        <table>
          <thead><tr><th>Stage</th><th>Timestamp</th><th>Detail</th></tr></thead>
          <tbody>${buildTemporalTimeline(execution)}</tbody>
        </table>
      </section>
      <section>
        <h3>Artifacts</h3>
        <table>
          <thead><tr><th>Artifact</th><th>Size</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>${renderTemporalArtifactRows(artifacts?.artifacts || []) ||
      "<tr><td colspan='4' class='small'>No artifacts.</td></tr>"
      }</tbody>
        </table>
      </section>
      <section id="temporal-live-output-section"></section>
      <section>
        <h3>Live Logs</h3>
        ${liveLogsAvailable
          ? `<div id="temporal-live-logs-inactive">
          <button type="button" id="temporal-start-tailing" class="secondary">▶ Start Tailing</button>
        </div>
        <div id="temporal-live-logs-active" style="display:none;">
          <div class="queue-live-output-toolbar">
            <label for="temporal-output-filter" class="queue-inline-filter">Filter:
            <select id="temporal-output-filter">
              <option value="all">All</option>
              <option value="stages">Stages</option>
              <option value="logs">Logs</option>
              <option value="warnings">Warnings</option>
            </select>
            </label>
            <label class="queue-inline-toggle">
              <input type="checkbox" id="temporal-follow-output" checked /> Follow
            </label>
            <button type="button" id="temporal-copy-output" class="secondary">Copy</button>
            <button type="button" id="temporal-stop-tailing" class="secondary queue-action-danger">■ Stop</button>
            <span id="temporal-live-transport-status" class="small"></span>
          </div>
          <pre id="temporal-live-output" class="queue-live-output"></pre>
        </div>`
          : "<p class='small'>Live log tailing is not configured for Temporal tasks. Use Live Output.</p>"
        }
      </section>
      ${debugFields}
    `;
  }

  async function renderTemporalDetailPage(workflowId) {
    setView(
      "Temporal Task Detail",
      `Task ${workflowId}`,
      "<p class='loading'>Loading Temporal task...</p>",
      { showAutoRefreshControls: true },
    );

    let detailNotice = "";
    let detailNoticeLevel = "ok";

    const logState = {
      tailing: false,
      events: [],
      eventIds: new Set(),
      after: null,
      afterEventId: null,
      outputFilter: "all",
      followOutput: true,
      eventsTransport: "idle",
      eventsTransportStatus: "idle",
      liveOutputLines: [],
      liveOutputRenderedEventCount: 0,
      liveOutputRenderedFilter: "all",
      forceLiveOutputRebuild: true,
      maxLiveOutputLines: 1500,
      maxEvents: 20000,
      eventsRenderTimer: null,
      eventsRenderIntervalMs: 120,
      liveSession: null,
      liveSessionRouteMissing: false,
      liveOutputPanelOpen: false,
      taskRunId: "",
      liveLogsAvailable: false,
    };

    let logEventSource = null;

    const stopLogEventStream = () => {
      if (!logEventSource) {
        return;
      }
      logEventSource.onmessage = null;
      logEventSource.onerror = null;
      logEventSource.close();
      logEventSource = null;
    };

    registerDisposer(() => {
      stopLogEventStream();
      if (logState.eventsRenderTimer !== null) {
        clearTimeout(logState.eventsRenderTimer);
        logState.eventsRenderTimer = null;
      }
    });

    const renderTemporalLiveOutputPanel = () => {
      const node = document.getElementById("temporal-live-output-section");
      if (!node) {
        return;
      }
      const logTailingEnabled = Boolean(featuresConfig.logTailingEnabled);
      if (!logTailingEnabled) {
        node.innerHTML = "";
        return;
      }
      const liveSession = logState.liveSession;
      const liveSessionRouteMissing = Boolean(logState.liveSessionRouteMissing);
      const liveSessionStatus = liveSession
        ? String(pick(liveSession, "status") || "disabled")
        : liveSessionRouteMissing
          ? "unavailable"
          : "disabled";
      const webRoUrl = liveSession
        ? sanitizeExternalHttpUrl(pick(liveSession, "webRo"))
        : "";
      const panelOpen = logState.liveOutputPanelOpen;

      let panelBody = "";
      if (!panelOpen) {
        panelBody = "";
      } else if (liveSessionStatus === "ready" && webRoUrl) {
        panelBody = `<iframe
          id="temporal-live-output-iframe"
          src="${escapeHtml(webRoUrl)}"
          style="width:100%;height:420px;border:1px solid var(--border-color, #333);border-radius:6px;background:#1a1a2e;"
          sandbox="allow-scripts allow-same-origin"
          title="Live terminal output"
        ></iframe>`;
      } else if (liveSessionStatus === "starting") {
        panelBody = `<div class="notice" style="text-align:center;padding:2rem;">⏳ Live session is starting&hellip;</div>`;
      } else if (liveSessionStatus === "ended" || liveSessionStatus === "revoked") {
        panelBody = `<div class="notice" style="text-align:center;padding:2rem;">Session ended.</div>`;
      } else if (liveSessionStatus === "error") {
        panelBody = `<div class="notice error" style="text-align:center;padding:2rem;">Live output is not available for this task.</div>`;
      } else {
        panelBody = `<div class="notice" style="text-align:center;padding:2rem;">Live output is not available for this task.</div>`;
      }

      node.innerHTML = `
        <div style="border:1px solid var(--border-color, #333);border-radius:8px;overflow:hidden;">
          <button
            type="button"
            id="temporal-live-output-toggle"
            style="width:100%;display:flex;align-items:center;justify-content:space-between;padding:0.6rem 1rem;background:var(--card-bg, #16213e);border:none;color:inherit;cursor:pointer;font-size:0.95rem;font-weight:600;"
          >
            <span>▶ Live Output</span>
            <span style="font-size:0.8rem;opacity:0.7;">${panelOpen ? "▼ collapse" : "▶ expand"}</span>
          </button>
          ${panelOpen ? `<div id="temporal-live-output-body" style="padding:0;">${panelBody}</div>` : ""}
        </div>
      `;

      const toggleButton = document.getElementById("temporal-live-output-toggle");
      if (toggleButton) {
        toggleButton.addEventListener("click", () => {
          logState.liveOutputPanelOpen = !logState.liveOutputPanelOpen;
          renderTemporalLiveOutputPanel();
        });
      }
    };

    const handleTemporalVisibilityChange = () => {
      if (document.hidden && logState.liveOutputPanelOpen) {
        const iframe = document.getElementById("temporal-live-output-iframe");
        if (iframe) {
          iframe.remove();
        }
      } else if (!document.hidden && logState.liveOutputPanelOpen) {
        renderTemporalLiveOutputPanel();
      }
    };
    document.addEventListener("visibilitychange", handleTemporalVisibilityChange);
    registerDisposer(() => document.removeEventListener("visibilitychange", handleTemporalVisibilityChange));

    const renderLogTransportStatus = () => {
      const node = document.getElementById("temporal-live-transport-status");
      if (!node) {
        return;
      }
      const label = logState.eventsTransport === "sse" ? "SSE" : logState.eventsTransport === "polling" ? "Polling" : "Idle";
      node.textContent = `Live transport: ${label} (${logState.eventsTransportStatus})`;
    };

    const updateLogOutputLines = () => {
      const shouldRebuild =
        logState.forceLiveOutputRebuild ||
        logState.liveOutputRenderedFilter !== logState.outputFilter ||
        logState.liveOutputRenderedEventCount > logState.events.length;

      if (shouldRebuild) {
        const lines = [];
        logState.events.forEach((event) => {
          if (eventMatchesOutputFilter(event, logState.outputFilter)) {
            lines.push(formatLiveOutputLine(event));
          }
        });
        if (lines.length > logState.maxLiveOutputLines) {
          lines.splice(0, lines.length - logState.maxLiveOutputLines);
        }
        logState.liveOutputLines = lines;
        logState.liveOutputRenderedEventCount = logState.events.length;
        logState.liveOutputRenderedFilter = logState.outputFilter;
        logState.forceLiveOutputRebuild = false;
        return;
      }

      if (logState.liveOutputRenderedEventCount < logState.events.length) {
        for (
          let index = logState.liveOutputRenderedEventCount;
          index < logState.events.length;
          index += 1
        ) {
          const event = logState.events[index];
          if (eventMatchesOutputFilter(event, logState.outputFilter)) {
            logState.liveOutputLines.push(formatLiveOutputLine(event));
          }
        }
        if (logState.liveOutputLines.length > logState.maxLiveOutputLines) {
          logState.liveOutputLines.splice(
            0,
            logState.liveOutputLines.length - logState.maxLiveOutputLines,
          );
        }
        logState.liveOutputRenderedEventCount = logState.events.length;
      }
    };

    const renderLogOutput = () => {
      const outputNode = document.getElementById("temporal-live-output");
      if (!outputNode) {
        return;
      }
      updateLogOutputLines();
      outputNode.textContent = logState.liveOutputLines.join("\n");
      if (logState.followOutput) {
        outputNode.scrollTop = outputNode.scrollHeight;
      }
    };

    const flushLogRender = () => {
      renderLogOutput();
      renderLogTransportStatus();
    };

    const scheduleLogRender = ({ forceLiveOutputRebuild = false } = {}) => {
      if (forceLiveOutputRebuild) {
        logState.forceLiveOutputRebuild = true;
      }
      if (logState.eventsRenderTimer !== null) {
        return;
      }
      logState.eventsRenderTimer = window.setTimeout(() => {
        logState.eventsRenderTimer = null;
        flushLogRender();
      }, logState.eventsRenderIntervalMs);
    };

    const toSortableLogTs = (value) => Date.parse(String(value || "")) || 0;
    const compareLogEventsAsc = (left, right) => {
      const leftTs = toSortableLogTs(pick(left, "createdAt"));
      const rightTs = toSortableLogTs(pick(right, "createdAt"));
      if (leftTs !== rightTs) {
        return leftTs - rightTs;
      }
      return String(pick(left, "id") || "").localeCompare(String(pick(right, "id") || ""));
    };

    const normalizeLogEventsAsc = (events) =>
      (events || []).slice().sort(compareLogEventsAsc);

    const refreshLogCursors = () => {
      const newestEvent =
        logState.events.length > 0 ? logState.events[logState.events.length - 1] : null;
      logState.after = newestEvent ? pick(newestEvent, "createdAt") || null : null;
      logState.afterEventId = newestEvent ? String(pick(newestEvent, "id") || "") || null : null;
    };

    const trimLogEvents = () => {
      if (logState.events.length <= logState.maxEvents) {
        return;
      }
      const overflow = logState.events.length - logState.maxEvents;
      const removed = logState.events.splice(0, overflow);
      removed.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (eventId) {
          logState.eventIds.delete(eventId);
        }
      });
      logState.forceLiveOutputRebuild = true;
    };

    const appendLogEvents = (incoming) => {
      let changed = false;
      const ordered = normalizeLogEventsAsc(incoming);
      ordered.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (!eventId || logState.eventIds.has(eventId)) {
          return;
        }
        logState.eventIds.add(eventId);
        logState.events.push(event);
        changed = true;
      });
      if (!changed) {
        return false;
      }
      trimLogEvents();
      refreshLogCursors();
      scheduleLogRender();
      return true;
    };

    const buildLogEventsQuery = ({ limit = 200, after = null, afterEventId = null, sort = "asc" }) => {
      const parts = [`limit=${encodeURIComponent(String(limit))}`];
      if (after) {
        parts.push(`after=${encodeURIComponent(String(after))}`);
      }
      if (afterEventId) {
        parts.push(`afterEventId=${encodeURIComponent(String(afterEventId))}`);
      }
      if (sort && sort !== "asc") {
        parts.push(`sort=${encodeURIComponent(String(sort))}`);
      }
      return `?${parts.join("&")}`;
    };

    const loadLogLatestEvents = async () => {
      if (!logState.liveLogsAvailable || !logState.taskRunId) {
        return;
      }
      const query = buildLogEventsQuery({ limit: 200, sort: "desc" });
      try {
        const payload = await fetchJson(
          endpoint(temporalSourceConfig.events, {
            id: logState.taskRunId,
            taskRunId: logState.taskRunId,
            workflowId,
          }) + query,
        );
        logState.events = [];
        logState.eventIds.clear();
        const newestFirst = Array.isArray(payload?.items) ? payload.items : [];
        const orderedAsc = normalizeLogEventsAsc(newestFirst);
        orderedAsc.forEach((event) => {
          const eventId = String(pick(event, "id") || "");
          if (!eventId || logState.eventIds.has(eventId)) {
            return;
          }
          logState.eventIds.add(eventId);
          logState.events.push(event);
        });
        refreshLogCursors();
        scheduleLogRender({ forceLiveOutputRebuild: true });
      } catch (error) {
        console.warn("temporal log initial event load failed", error);
      }
    };

    const loadLogNewEvents = async () => {
      if (!logState.liveLogsAvailable || !logState.taskRunId) {
        return;
      }
      const query = buildLogEventsQuery({
        limit: 200,
        after: logState.after,
        afterEventId: logState.afterEventId,
      });
      try {
        const payload = await fetchJson(
          endpoint(temporalSourceConfig.events, {
            id: logState.taskRunId,
            taskRunId: logState.taskRunId,
            workflowId,
          }) + query,
        );
        appendLogEvents(payload?.items || []);
      } catch (error) {
        console.warn("temporal log event poll failed", error);
      }
    };

    const beginLogPolling = () => {
      logState.eventsTransport = "polling";
      logState.eventsTransportStatus = isAutoRefreshActive() ? "active" : "paused";
      renderLogTransportStatus();
      startPolling(loadLogNewEvents, pollIntervals.events, {
        runImmediately: isAutoRefreshActive(),
      });
    };

    const startLogEventStream = () => {
      if (!logState.liveLogsAvailable || !logState.taskRunId) {
        logState.eventsTransport = "idle";
        logState.eventsTransportStatus = "unsupported";
        renderLogTransportStatus();
        return;
      }
      if (!isAutoRefreshActive()) {
        logState.eventsTransport = "sse";
        logState.eventsTransportStatus = "paused";
        renderLogTransportStatus();
        return;
      }
      if (logEventSource) {
        return;
      }
      const streamTemplate = temporalSourceConfig.eventsStream;
      if (typeof window.EventSource !== "function") {
        logState.eventsTransport = "polling";
        logState.eventsTransportStatus = "unsupported";
        renderLogTransportStatus();
        beginLogPolling();
        return;
      }

      const query = buildLogEventsQuery({
        limit: 200,
        after: logState.after,
        afterEventId: logState.afterEventId,
      });
      const streamUrl = endpoint(streamTemplate, {
        id: logState.taskRunId,
        taskRunId: logState.taskRunId,
        workflowId,
      }) + query;
      logState.eventsTransport = "sse";
      logState.eventsTransportStatus = "connecting";
      renderLogTransportStatus();

      logEventSource = new window.EventSource(streamUrl);

      const handleMessage = (rawData) => {
        if (!rawData || !isAutoRefreshActive()) {
          return;
        }
        try {
          const parsed = JSON.parse(rawData);
          appendLogEvents([parsed]);
        } catch (error) {
          console.error("temporal log event stream parse failed", error);
        }
      };

      logEventSource.addEventListener("open", () => {
        logState.eventsTransport = "sse";
        logState.eventsTransportStatus = "active";
        renderLogTransportStatus();
      });

      logEventSource.addEventListener("queue_event", (event) => {
        if (logState.eventsTransportStatus !== "active") {
          logState.eventsTransport = "sse";
          logState.eventsTransportStatus = "active";
          renderLogTransportStatus();
        }
        handleMessage(event.data);
      });

      logEventSource.onmessage = (event) => {
        if (logState.eventsTransportStatus !== "active") {
          logState.eventsTransport = "sse";
          logState.eventsTransportStatus = "active";
          renderLogTransportStatus();
        }
        handleMessage(event.data);
      };

      logEventSource.onerror = (error) => {
        console.warn("temporal log event stream failed; switching to polling", error);
        logState.eventsTransport = "polling";
        logState.eventsTransportStatus = "error";
        renderLogTransportStatus();
        stopLogEventStream();
        beginLogPolling();
      };
    };

    const stopLogTailing = () => {
      logState.tailing = false;
      stopLogEventStream();
      const inactiveNode = document.getElementById("temporal-live-logs-inactive");
      const activeNode = document.getElementById("temporal-live-logs-active");
      if (inactiveNode) {
        inactiveNode.style.display = "";
      }
      if (activeNode) {
        activeNode.style.display = "none";
      }
      logState.eventsTransport = "idle";
      logState.eventsTransportStatus = "idle";
    };

    const startLogTailing = async () => {
      if (!logState.liveLogsAvailable || !logState.taskRunId) {
        renderLogTransportStatus();
        return;
      }
      logState.tailing = true;
      const inactiveNode = document.getElementById("temporal-live-logs-inactive");
      const activeNode = document.getElementById("temporal-live-logs-active");
      if (inactiveNode) {
        inactiveNode.style.display = "none";
      }
      if (activeNode) {
        activeNode.style.display = "";
      }
      await loadLogLatestEvents();
      startLogEventStream();
    };

    const attachLogHandlers = () => {
      const startBtn = document.getElementById("temporal-start-tailing");
      if (startBtn) {
        startBtn.addEventListener("click", () => {
          startLogTailing();
        });
      }
      const stopBtn = document.getElementById("temporal-stop-tailing");
      if (stopBtn) {
        stopBtn.addEventListener("click", () => {
          stopLogTailing();
        });
      }
      const followToggle = document.getElementById("temporal-follow-output");
      if (followToggle instanceof HTMLInputElement) {
        followToggle.addEventListener("change", () => {
          logState.followOutput = followToggle.checked;
          if (logState.followOutput) {
            const outputNode = document.getElementById("temporal-live-output");
            if (outputNode) {
              outputNode.scrollTop = outputNode.scrollHeight;
            }
          }
        });
      }
      const filterSelect = document.getElementById("temporal-output-filter");
      if (filterSelect instanceof HTMLSelectElement) {
        filterSelect.addEventListener("change", () => {
          logState.outputFilter = filterSelect.value;
          scheduleLogRender({ forceLiveOutputRebuild: true });
        });
      }
      const copyBtn = document.getElementById("temporal-copy-output");
      if (copyBtn) {
        copyBtn.addEventListener("click", () => {
          const outputNode = document.getElementById("temporal-live-output");
          if (outputNode && navigator.clipboard) {
            navigator.clipboard.writeText(outputNode.textContent || "").catch(() => {
              console.warn("temporal log copy failed");
            });
          }
        });
      }
    };

    const restoreLogTailingState = () => {
      if (!logState.tailing) {
        return;
      }
      const inactiveNode = document.getElementById("temporal-live-logs-inactive");
      const activeNode = document.getElementById("temporal-live-logs-active");
      if (inactiveNode) {
        inactiveNode.style.display = "none";
      }
      if (activeNode) {
        activeNode.style.display = "";
      }
      flushLogRender();
    };

    const runTemporalAction = async (execution, action) => {
      const normalizedAction = String(action || "").trim().toLowerCase();
      const detailWorkflowId = String(pick(execution, "workflowId") || workflowId).trim();
      if (!detailWorkflowId) {
        return;
      }
      if (normalizedAction === "set-title") {
        const nextTitle = window.prompt(
          "Task title",
          deriveTemporalTitle(execution),
        );
        if (nextTitle === null) {
          return;
        }
        await fetchJson(
          endpoint(
            temporalSourceConfig.update || "/api/executions/{workflowId}/update",
            { workflowId: detailWorkflowId },
          ),
          {
            method: "POST",
            body: JSON.stringify({
              updateName: "SetTitle",
              title: nextTitle,
              idempotencyKey: `set-title-${Date.now()}`,
            }),
          },
        );
        detailNotice = "Task title updated.";
        detailNoticeLevel = "ok";
        return;
      }
      if (normalizedAction === "reschedule") {
        const currentScheduledFor = execution.scheduledFor || execution.scheduled_for || "";
        const nextTime = window.prompt("New scheduled time (ISO 8601, e.g., 2026-03-24T12:00:00Z)", currentScheduledFor);
        if (nextTime === null || !nextTime.trim()) {
          return;
        }
        await fetchJson(
          `/api/executions/${encodeURIComponent(detailWorkflowId)}/reschedule`,
          {
            method: "POST",
            body: JSON.stringify({
              scheduledFor: nextTime.trim(),
            }),
          },
        );
        detailNotice = "Task reschedule requested.";
        detailNoticeLevel = "ok";
        return;
      }
      if (normalizedAction === "rerun") {
        await fetchJson(
          endpoint(
            temporalSourceConfig.update || "/api/executions/{workflowId}/update",
            { workflowId: detailWorkflowId },
          ),
          {
            method: "POST",
            body: JSON.stringify({
              updateName: "RequestRerun",
              idempotencyKey: `rerun-${Date.now()}`,
            }),
          },
        );
        detailNotice = "Task rerun requested.";
        detailNoticeLevel = "ok";
        return;
      }
      if (normalizedAction === "cancel") {
        const confirmed = window.confirm("Cancel this task?");
        if (!confirmed) {
          return;
        }
        await fetchJson(
          endpoint(
            temporalSourceConfig.cancel || "/api/executions/{workflowId}/cancel",
            { workflowId: detailWorkflowId },
          ),
          {
            method: "POST",
            body: JSON.stringify({ graceful: true }),
          },
        );
        detailNotice = "Task cancellation requested.";
        detailNoticeLevel = "ok";
        return;
      }
      if (normalizedAction === "pause" || normalizedAction === "resume") {
        let payload = {};
        const awaitingAction =
          String(pick(execution, "rawState", "state") || "")
            .trim()
            .toLowerCase() === "awaiting_external";
        const integrationName = String(
          pick(execution, "searchAttributes", "mm_integration") ||
            pick(execution, "targetRuntime") ||
            "",
        )
          .trim()
          .toLowerCase();
        if (normalizedAction === "resume" && awaitingAction && integrationName === "jules") {
          const clarificationReply = window.prompt(
            "Optional clarification reply for Jules",
            "",
          );
          if (clarificationReply === null) {
            return;
          }
          if (clarificationReply.trim()) {
            payload = { message: clarificationReply.trim() };
          }
        }
        await fetchJson(
          endpoint(
            temporalSourceConfig.signal || "/api/executions/{workflowId}/signal",
            { workflowId: detailWorkflowId },
          ),
          {
            method: "POST",
            body: JSON.stringify({
              signalName: normalizedAction === "pause" ? "Pause" : "Resume",
              payload,
            }),
          },
        );
        detailNotice = normalizedAction === "pause" ? "Task paused." : "Task resumed.";
        detailNoticeLevel = "ok";
        return;
      }
      if (normalizedAction === "approve") {
        const approvalType = window.prompt("Approval type", "operator");
        if (approvalType === null) {
          return;
        }
        let payload = { approval_type: approvalType || "operator" };
        const integrationName = String(
          pick(execution, "searchAttributes", "mm_integration") ||
            pick(execution, "targetRuntime") ||
            "",
        )
          .trim()
          .toLowerCase();
        const awaitingAction =
          String(pick(execution, "rawState", "state") || "")
            .trim()
            .toLowerCase() === "awaiting_external";
        if (awaitingAction && integrationName === "jules") {
          const clarificationReply = window.prompt(
            "Optional clarification reply for Jules",
            "",
          );
          if (clarificationReply === null) {
            return;
          }
          if (clarificationReply.trim()) {
            payload.message = clarificationReply.trim();
          }
        }
        await fetchJson(
          endpoint(
            temporalSourceConfig.signal || "/api/executions/{workflowId}/signal",
            { workflowId: detailWorkflowId },
          ),
          {
            method: "POST",
            body: JSON.stringify({
              signalName: "Approve",
              payload,
            }),
          },
        );
        detailNotice = "Approval signal sent.";
        detailNoticeLevel = "ok";
      }
    };

    const attachTemporalActionHandlers = (execution, reload) => {
      const actionRoot = document.querySelector("[data-temporal-actions]");
      if (!actionRoot) {
        return;
      }
      actionRoot.querySelectorAll("[data-temporal-action]").forEach((button) => {
        button.addEventListener("click", async () => {
          const action = button.getAttribute("data-temporal-action");
          if (!action) {
            return;
          }
          button.setAttribute("disabled", "disabled");
          try {
            await runTemporalAction(execution, action);
            await reload(true);
          } catch (error) {
            console.error("temporal action failed", error);
            detailNotice = error instanceof Error && error.message
              ? error.message
              : "Temporal action failed.";
            detailNoticeLevel = "error";
            await reload(true);
          } finally {
            button.removeAttribute("disabled");
          }
        });
      });
    };

    const load = async (silent = false) => {
      try {
        const { execution, latestWorkflowId, latestRunId, artifacts } =
          await fetchTemporalDetailData(workflowId);
        const memo = pick(execution, "memo") || {};
        const waitingReason = temporalWaitingReason(execution);
        const detailTitle = deriveTemporalTitle(execution);
        const attentionRequired = Boolean(pick(execution, "attentionRequired"));
        const debugFields = renderTemporalDebugSection(execution, latestWorkflowId);
        const taskRunId = resolveTemporalTaskRunId(execution);
        logState.taskRunId = taskRunId;
        logState.liveLogsAvailable = temporalLogTailingAvailable(
          temporalSourceConfig,
          execution,
        );
        const noticeHtml = detailNotice
          ? `<div class="notice ${escapeHtml(detailNoticeLevel)}">${escapeHtml(detailNotice)}</div>`
          : "";
        setView(
          "Temporal Task Detail",
          detailTitle,
          renderTemporalDetailMarkup({
            execution,
            latestWorkflowId,
            latestRunId,
            artifacts,
            waitingReason,
            detailTitle,
            attentionRequired,
            noticeHtml,
            debugFields,
            liveLogsAvailable: logState.liveLogsAvailable,
          }),
          { showAutoRefreshControls: true },
        );
        attachTemporalActionHandlers(execution, load);
        attachLogHandlers();
        restoreLogTailingState();

        // Fetch live-session data and render the Live Output panel.
        try {
          if (!taskRunId) {
            throw new Error("Temporal task detail is missing a live-session task run id.");
          }
          const livePayload = await fetchJson(
            endpoint(
              temporalSourceConfig.liveSession || "/api/task-runs/{id}/live-session",
              { id: taskRunId, taskRunId, workflowId: latestWorkflowId },
            ),
          );
          logState.liveSession = pick(livePayload || {}, "session") || null;
          logState.liveSessionRouteMissing = false;
        } catch (liveError) {
          const classification = classifyLiveSessionError(liveError);
          if (classification === "route_missing") {
            logState.liveSessionRouteMissing = true;
          }
          logState.liveSession = null;
        }
        renderTemporalLiveOutputPanel();
      } catch (error) {
        console.error("temporal detail load failed", error);
        if (silent && detailNotice) {
          setView(
            "Temporal Task Detail",
            `Task ${workflowId}`,
            `<div class='notice error'>${escapeHtml(detailNotice)}</div>`,
            { showAutoRefreshControls: true },
          );
          return;
        }
        setView(
          "Temporal Task Detail",
          `Task ${workflowId}`,
          "<div class='notice error'>Failed to load task detail.</div>",
          { showAutoRefreshControls: true },
        );
      }
    };

    await load();
    startPolling(() => load(true), pollIntervals.detail);
  }

  async function resolveUnifiedTaskSource(taskId) {
    const safeTaskId = normalizeDashboardDetailSegment(taskId);
    if (!safeTaskId) {
      return { source: "", resolvedId: "" };
    }
    return { source: "temporal", resolvedId: safeTaskId };
  }

  async function renderProposalsListPage() {
    const repoStorageKey = "task-dashboard-proposals-repo";
    const initialQuery = new URLSearchParams(window.location.search || "");
    const initialOriginSource = String(initialQuery.get("originSource") || "")
      .trim()
      .toLowerCase();
    const initialOriginId = String(initialQuery.get("originId") || "").trim();
    const state = {
      status: "open",
      repository: localStorage.getItem(repoStorageKey) || "",
      category: "",
      tag: "",
      originSource: initialOriginSource,
      originId: initialOriginId,
      rows: [],
      notice: "",
      noticeLevel: "",
      actionFeedback: null,
    };
    const proposalConsumedFlashMs = 320;

    const consumeProposalRow = async (proposalId, actionLabel) => {
      const normalizedProposalId = String(proposalId || "");
      if (!normalizedProposalId) {
        return;
      }
      const consumeTargets = Array.from(
        root.querySelectorAll("tr[data-proposal-id], .queue-card[data-proposal-id]"),
      ).filter(
        (node) => String(node.getAttribute("data-proposal-id") || "") === normalizedProposalId,
      );
      consumeTargets.forEach((node) => {
        node.classList.add("proposal-consuming");
      });
      if (consumeTargets.length) {
        await new Promise((resolve) => {
          window.setTimeout(resolve, proposalConsumedFlashMs);
        });
      }
      consumeTargets.forEach((node) => {
        node.classList.remove("proposal-consuming");
      });
      state.rows = (state.rows || []).filter(
        (row) => String(pick(row, "id") || "") !== normalizedProposalId,
      );
      const shortId = normalizedProposalId.slice(0, 8) || normalizedProposalId;
      state.actionFeedback = {
        message: `Proposal ${shortId} ${actionLabel}.`,
        statusFilter: actionLabel === "dismissed" ? "dismissed" : "promoted",
      };
      renderView();
    };

    const renderFilters = () => {
      const statusOptions = [
        ["", "(all)"],
        ["open", "open"],
        ["promoted", "promoted"],
        ["dismissed", "dismissed"],
        ["accepted", "accepted"],
        ["rejected", "rejected"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${escapeHtml(value)}" ${state.status === value ? "selected" : ""
            }>${escapeHtml(label)}</option>`,
        )
        .join("");
      return `
        <form id="proposals-filter-form" class="stack">
          <div class="grid-2">
            <label>Status
              <select name="status">${statusOptions}</select>
            </label>
            <label>Repository
              <input name="repository" placeholder="owner/repo" value="${escapeHtml(
        state.repository,
      )}" />
            </label>
          </div>
          <label>Category
            <input name="category" placeholder="security, tests, ..." value="${escapeHtml(
        state.category,
      )}" />
          </label>
          <div class="grid-2">
            <label>Origin Source
              <input name="originSource" placeholder="queue" value="${escapeHtml(
        state.originSource,
      )}" />
            </label>
            <label>Origin ID
              <input name="originId" placeholder="job UUID" value="${escapeHtml(
        state.originId,
      )}" />
            </label>
          </div>
          <label>Signal Tag
            <input name="tag" placeholder="loop_detected" value="${escapeHtml(
        state.tag,
      )}" />
          </label>
        </form>
      `;
    };

    const renderTable = () => {
      return renderProposalLayouts(state.rows, state.tag);
    };

    const attachHandlers = () => {
      const filterForm = document.getElementById("proposals-filter-form");
      if (!filterForm) {
        return;
      }
      const statusField = filterForm.elements.namedItem("status");
      const repositoryField = filterForm.elements.namedItem("repository");
      const categoryField = filterForm.elements.namedItem("category");
      const originSourceField = filterForm.elements.namedItem("originSource");
      const originIdField = filterForm.elements.namedItem("originId");
      const tagField = filterForm.elements.namedItem("tag");
      if (statusField) {
        statusField.addEventListener("change", () => {
          state.status = String(statusField.value || "").trim();
          load();
        });
      }
      if (repositoryField) {
        repositoryField.addEventListener("change", () => {
          state.repository = String(repositoryField.value || "").trim();
          localStorage.setItem(repoStorageKey, state.repository);
          load();
        });
      }
      if (categoryField) {
        categoryField.addEventListener("change", () => {
          state.category = String(categoryField.value || "").trim();
          load();
        });
      }
      if (originSourceField) {
        originSourceField.addEventListener("change", () => {
          state.originSource = String(originSourceField.value || "")
            .trim()
            .toLowerCase();
          load();
        });
      }
      if (originIdField) {
        originIdField.addEventListener("change", () => {
          state.originId = String(originIdField.value || "").trim();
          load();
        });
      }
      if (tagField) {
        tagField.addEventListener("change", () => {
          state.tag = String(tagField.value || "").trim();
          load();
        });
      }
      document.querySelectorAll(".proposal-action").forEach((button) => {
        button.addEventListener("click", async () => {
          const proposalId = button.getAttribute("data-proposal-id");
          const action = button.getAttribute("data-action");
          if (!proposalId || !action) {
            return;
          }
          button.disabled = true;
          try {
            if (action === "promote") {
              await apiPromoteProposal(proposalId);
              await consumeProposalRow(proposalId, "promoted");
              return;
            } else if (action === "dismiss") {
              await apiDismissProposal(proposalId);
              await consumeProposalRow(proposalId, "dismissed");
              return;
            }
          } catch (error) {
            console.error(`proposal ${action} failed`, error);
            state.notice = `Failed to ${action} proposal ${proposalId}.`;
            state.noticeLevel = "error";
            renderView();
          } finally {
            button.disabled = false;
          }
        });
      });
    };

    const renderView = () => {
      const noticeClass = state.noticeLevel === "ok" ? "notice ok" : "notice error";
      const noticeHtml = state.notice
        ? `<div class="${noticeClass}">${escapeHtml(state.notice)}</div>`
        : "";
      setView(
        "Task Proposals",
        "Worker follow-up queue (promote to Task jobs).",
        `${noticeHtml}${renderFilters()}${renderTable()}${renderProposalActionFeedback(
          state.actionFeedback,
        )}`,
        { showAutoRefreshControls: true },
      );
      attachHandlers();
    };

    const load = async () => {
      try {
        const params = new URLSearchParams();
        params.set("limit", "200");
        if (state.status) {
          params.set("status", state.status);
        }
        if (state.repository) {
          params.set("repository", state.repository);
        }
        if (state.category) {
          params.set("category", state.category);
        }
        if (state.originSource) {
          params.set("originSource", state.originSource);
        }
        if (state.originId) {
          params.set("originId", state.originId);
        }
        const listEndpoint = proposalsSourceConfig.list || "/api/proposals";
        const payload = await fetchJson(`${listEndpoint}?${params.toString()}`);
        state.rows = payload?.items || [];
        state.notice = "";
        state.noticeLevel = "";
        if (state.actionFeedback && state.status) {
          const currentStatus = String(state.status).trim().toLowerCase();
          const feedbackStatus = String(state.actionFeedback.statusFilter || "")
            .trim()
            .toLowerCase();
          if (currentStatus === feedbackStatus) {
            state.actionFeedback = null;
          }
        }
      } catch (error) {
        console.error("proposals list load failed", error);
        state.rows = [];
        state.notice = "Failed to load proposals.";
        state.noticeLevel = "error";
      }
      renderView();
    };

    setView(
      "Task Proposals",
      "Worker follow-up queue (promote to Task jobs).",
      "<p class='loading'>Loading proposals...</p>",
      { showAutoRefreshControls: true },
    );
    await load();
    startPolling(load, pollIntervals.list);
  }

  async function renderScheduleDetailPage(scheduleId) {
    const normalizedId = normalizeDashboardDetailSegment(scheduleId || "");

    if (!normalizedId) {
      setView(
        "Schedule not found",
        "Recurring schedule details.",
        "<p class='error'>No schedule ID was provided, or the schedule ID is invalid.</p>",
        { showAutoRefreshControls: false },
      );
      return;
    }

    const contentHtml = [
      "<div class='schedule-detail'>",
      "<h2>Schedule Detail</h2>",
      "<dl>",
      "<dt>Schedule ID</dt>",
      `<dd><code>${normalizedId}</code></dd>`,
      "</dl>",
      "<p>Detailed schedule inspection is not available in this build, but the schedule route is active.</p>",
      "</div>",
    ].join("");

    setView(
      "Schedule Detail",
      "Recurring schedule details.",
      contentHtml,
      { showAutoRefreshControls: false },
    );
  }

  async function renderProposalDetailPage(proposalId) {
    setView(
      "Proposal Detail",
      `Proposal ${proposalId}`,
      "<p class='loading'>Loading proposal...</p>",
    );

    let detailNotice = "";

    const renderDetail = (row) => {
      const origin = pick(row, "origin") || {};
      const preview = pick(row, "taskPreview") || {};
      const taskRequest = pick(row, "taskCreateRequest") || {};
      const payloadNode =
        taskRequest && typeof taskRequest === "object" && taskRequest.payload && typeof taskRequest.payload === "object"
          ? taskRequest.payload
          : {};
      const taskNode =
        payloadNode && payloadNode.task && typeof payloadNode.task === "object"
          ? payloadNode.task
          : {};
      const instructions = preview.instructions || taskNode.instructions || "";
      const originSource = pick(origin, "source") || "-";
      const metadata = pick(origin, "metadata") || {};
      const originLink =
        originSource === "queue" && pick(origin, "id")
          ? `<a href="/tasks/queue/${escapeHtml(
            String(pick(origin, "id") || ""),
          )}">queue/${escapeHtml(String(pick(origin, "id") || ""))}</a>`
          : escapeHtml(originSource);
      const priority = (pick(row, "reviewPriority") || "normal").toUpperCase();
      const priorityOverride = pick(row, "priorityOverrideReason") || "";
      const dedupHash = pick(row, "dedupHash") || "-";
      const triggerRepo = pick(metadata, "triggerRepo") || "-";
      const triggerJobId = pick(metadata, "triggerJobId") || "-";
      const signalMetadata = pick(metadata, "signal");
      const signalMarkup = signalMetadata
        ? `<pre>${escapeHtml(JSON.stringify(signalMetadata, null, 2))}</pre>`
        : "<p class='small'>No signal metadata supplied.</p>";
      const similar = pick(row, "similar") || [];
      const similarMarkup = similar.length
        ? `<ul class="stack">${similar
          .map(
            (item) =>
              `<li><a href="/tasks/proposals/${encodeURIComponent(
                String(pick(item, "id") || ""),
              )}">${escapeHtml(pick(item, "title") || "(untitled)")}</a> &middot; ${escapeHtml(
                pick(item, "repository") || "-",
              )} &middot; ${formatTimestamp(pick(item, "createdAt"))}</li>`,
          )
          .join("")}</ul>`
        : "<p class='small'>No similar proposals.</p>";
      const priorityOptions = ["low", "normal", "high", "urgent"]
        .map(
          (value) =>
            `<option value="${escapeHtml(value)}" ${value.toUpperCase() === priority ? "selected" : ""
            }>${escapeHtml(value.toUpperCase())}</option>`,
        )
        .join("");
      const detailNoticeMarkup = detailNotice
        ? `<div class="notice ok">${escapeHtml(detailNotice)}</div>`
        : "";
      setView(
        "Proposal Detail",
        `Proposal ${proposalId}`,
        `
          ${detailNoticeMarkup}
          <div class="grid-2">
            <div class="card"><strong>Status:</strong> ${statusBadge(
          "proposals",
          pick(row, "status"),
        )}</div>
            <div class="card"><strong>Repository:</strong> ${escapeHtml(
          pick(row, "repository") || pick(preview, "repository") || "-",
        )}</div>
            <div class="card"><strong>Runtime:</strong> ${escapeHtml(
          pick(preview, "runtimeMode") || "(default)",
        )}</div>
            <div class="card"><strong>Publish Mode:</strong> ${escapeHtml(
          pick(preview, "publishMode") || "-",
        )}</div>
            <div class="card"><strong>Category:</strong> ${escapeHtml(
          pick(row, "category") || "-",
        )}</div>
            <div class="card"><strong>Origin:</strong> ${originLink}</div>
            <div class="card"><strong>Priority:</strong> ${escapeHtml(priority)}${priorityOverride
          ? `<br/><span class="tiny">Override: ${escapeHtml(priorityOverride)}</span>`
          : ""
        }</div>
            <div class="card"><strong>Dedup Hash:</strong> <code>${escapeHtml(
          dedupHash,
        )}</code></div>
            <div class="card"><strong>Trigger Repo:</strong> ${escapeHtml(
          triggerRepo,
        )}</div>
            <div class="card"><strong>Trigger Job ID:</strong> ${escapeHtml(
          triggerJobId,
        )}</div>
          </div>
          <section>
            <h3>Summary</h3>
            <p>${escapeHtml(pick(row, "summary") || "-")}</p>
          </section>
          <section>
            <h3>Instructions</h3>
            <pre>${escapeHtml(instructions || "-")}</pre>
          </section>
          <section>
            <h3>Signal Metadata</h3>
            ${signalMarkup}
          </section>
          <div class="actions">
            <label style="display:inline-flex;align-items:center;gap:0.5rem;margin-right:0.5rem">
              Runtime
              <select id="proposal-runtime-select">
                ${supportedTaskRuntimes
                  .map(
                    (rt) =>
                      `<option value="${escapeHtml(rt)}" ${
                        rt === (pick(preview, "runtimeMode") || defaultTaskRuntime)
                          ? "selected"
                          : ""
                      }>${escapeHtml(rt)}</option>`,
                  )
                  .join("")}
              </select>
            </label>
            <button
              type="button"
              class="queue-action"
              id="proposal-promote-button"
            >
              Promote to Task
            </button>
            <button type="button" class="secondary" id="proposal-edit-button">Edit & Promote</button>
            <button type="button" class="queue-action queue-action-danger" id="proposal-dismiss-button">Dismiss</button>
            <a href="/tasks/proposals"><button type="button" class="secondary">Back</button></a>
          </div>
          <section class="stack">
            <h3>Priority</h3>
            <div class="grid-2">
              <form id="proposal-priority-form" class="stack card">
                <label>Priority
                  <select name="priority">${priorityOptions}</select>
                </label>
                <button type="submit">Update Priority</button>
              </form>
            </div>
          </section>
          <section>
            <h3>Similar Proposals</h3>
            ${similarMarkup}
          </section>
        `,
      );
      const promoteButton = document.getElementById("proposal-promote-button");
      if (promoteButton) {
        promoteButton.addEventListener("click", async () => {
          promoteButton.disabled = true;
          try {
            const runtimeSelect = document.getElementById("proposal-runtime-select");
            const selectedRuntime = runtimeSelect ? runtimeSelect.value : null;
            const overrides = selectedRuntime ? { runtimeMode: selectedRuntime } : {};
            await apiPromoteProposal(proposalId, overrides);
            detailNotice = "Proposal promoted and consumed. It will disappear from the queue list.";
            await load(true);
            return;
          } catch (error) {
            console.error("proposal promote failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Promotion failed.</div>",
            );
          } finally {
            promoteButton.disabled = false;
          }
        });
      }
      const dismissButton = document.getElementById("proposal-dismiss-button");
      if (dismissButton) {
        dismissButton.addEventListener("click", async () => {
          dismissButton.disabled = true;
          try {
            await apiDismissProposal(proposalId);
            await load(true);
          } catch (error) {
            console.error("proposal dismiss failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Dismissal failed.</div>",
            );
          } finally {
            dismissButton.disabled = false;
          }
        });
      }
      const editButton = document.getElementById("proposal-edit-button");
      if (editButton) {
        editButton.addEventListener("click", async () => {
          const overrides = buildEditOverrides(row);
          if (!overrides) {
            return;
          }
          editButton.disabled = true;
          try {
            await apiPromoteProposal(proposalId, overrides);
            detailNotice = "Proposal promoted and consumed. It will disappear from the queue list.";
            await load(true);
            return;
          } catch (error) {
            console.error("proposal edit-promote failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Edit & Promote failed.</div>",
            );
          } finally {
            editButton.disabled = false;
          }
        });
      }
      const priorityForm = document.getElementById("proposal-priority-form");
      if (priorityForm) {
        priorityForm.addEventListener("submit", async (event) => {
          event.preventDefault();
          const priorityField = priorityForm.elements.namedItem("priority");
          const value = priorityField ? priorityField.value : null;
          if (!value) {
            return;
          }
          priorityForm.classList.add("loading");
          try {
            await apiUpdateProposalPriority(proposalId, value);
            await load(true);
          } catch (error) {
            console.error("priority update failed", error);
            alert("Failed to update priority.");
          } finally {
            priorityForm.classList.remove("loading");
          }
        });
      }
    };

    const load = async (silent = false) => {
      try {
        const detail = await fetchJson(
          endpoint(
            proposalsSourceConfig.detail || "/api/proposals/{id}",
            { id: proposalId },
          ),
        );
        renderDetail(detail);
      } catch (error) {
        console.error("proposal detail load failed", error);
        if (silent && !detailNotice) {
          return;
        }
        const detailNoticeMarkup = detailNotice
          ? `<div class="notice ok">${escapeHtml(detailNotice)}</div>`
          : "";
        const errorMessage = detailNotice
          ? "Failed to refresh proposal details after consume. It may already be removed from the queue."
          : "Failed to load proposal.";
        setView(
          "Proposal Detail",
          `Proposal ${proposalId}`,
          `${detailNoticeMarkup}
           <div class='notice error'>${escapeHtml(errorMessage)}</div>
           <p class='small'><a href='/tasks/proposals'>Back to proposals list</a></p>`,
        );
      }
    };

    await load();
    startPolling(() => load(true), pollIntervals.detail);
  }

  function resolveManifestIngestContext(
    execution,
    workflowId,
    sourceConfig = temporalSourceConfig,
  ) {
    const detailContext = resolveTemporalDetailContext(execution, workflowId, sourceConfig);
    const resolvedWorkflowId = detailContext.taskId || workflowId;
    const statusEndpointTemplate =
      sourceConfig.manifestStatus || "/api/executions/{workflowId}/manifest-status";
    const nodesEndpointTemplate =
      sourceConfig.manifestNodes || "/api/executions/{workflowId}/manifest-nodes";
    return {
      ...detailContext,
      manifestStatusEndpoint: endpoint(statusEndpointTemplate, {
        workflowId: resolvedWorkflowId,
      }),
      manifestNodesEndpoint: endpoint(nodesEndpointTemplate, {
        workflowId: resolvedWorkflowId,
      }),
      runIndexArtifactRef:
        pick(execution, "runIndexArtifactRef")
        || pick(execution, "run_index_artifact_ref")
        || null,
    };
  }
  async function renderSkillsPage() {
    const state = {
      skills: [],
      selectedSkill: null,
      mode: "view", // "view" or "create"
      notice: null,
      requestInFlight: false,
    };

    function setNotice(level, text) {
      if (!text) {
        state.notice = null;
        return;
      }
      state.notice = {
        level: level === "error" ? "error" : "ok",
        text,
      };
    }

    function renderView() {
      const noticeHtml = state.notice
        ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
        : "";

      const skillsListHtml = state.skills.length > 0
        ? `<ul class="queue-card-list" role="list">
            ${state.skills.map(skill => `
              <li class="queue-card ${state.selectedSkill && state.selectedSkill.id === skill.id ? 'active' : ''}" data-skill-id="${escapeHtml(skill.id)}" style="cursor: pointer;">
                <div class="queue-card-header">
                  <span class="queue-card-title">${escapeHtml(skill.id)}</span>
                </div>
              </li>
            `).join("")}
          </ul>`
        : `<p class="small">No skills found.</p>`;

      let detailPaneHtml = "";
      if (state.mode === "create") {
        detailPaneHtml = `
          <div class="mm-glass-strong" style="padding: 1.5rem; border-radius: 8px;">
            <h3>Create New Skill</h3>
            <form id="create-skill-form" class="stack" style="margin-top: 1rem;">
              <label>
                Skill Name
                <input type="text" name="name" required placeholder="e.g. MyNewSkill" pattern="^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$" title="Must start with a letter or number, be 1-64 characters long, and contain only letters, numbers, underscores, and dashes.">
              </label>
              <label>
                Markdown Content (SKILL.md)
                <textarea name="markdown" required rows="15" placeholder="# My New Skill\\n\\nInstructions here..."></textarea>
              </label>
              <div style="margin-top: 1rem;">
                <button type="submit" class="queue-submit-primary">Save Skill</button>
                <button type="button" class="secondary" id="cancel-create-skill">Cancel</button>
              </div>
            </form>
          </div>
        `;
      } else if (state.selectedSkill) {
        const contentHtml = state.selectedSkill.markdown
          ? `<div class="markdown-body">${marked.parse(state.selectedSkill.markdown)}</div>`
          : `<p class="small">No content available for this skill.</p>`;

        detailPaneHtml = `
          <div class="mm-glass-strong" style="padding: 1.5rem; border-radius: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
              <h3 style="margin: 0;">${escapeHtml(state.selectedSkill.id)}</h3>
            </div>
            ${contentHtml}
          </div>
        `;
      } else {
        detailPaneHtml = `
          <div class="mm-glass-strong" style="padding: 1.5rem; border-radius: 8px; display: flex; justify-content: center; align-items: center; height: 100%;">
            <p class="small">Select a skill to view its content, or create a new one.</p>
          </div>
        `;
      }

      const layout = `
        <div data-skills-notice>${noticeHtml}</div>
        <div style="display: grid; grid-template-columns: 300px 1fr; gap: 1.5rem; align-items: start; margin-bottom: 1rem;">
          <div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
              <h3 style="margin: 0;">Available Skills</h3>
              <button type="button" id="btn-create-skill" class="btn-small">Create New Skill</button>
            </div>
            <div class="mm-glass" style="max-height: 70vh; overflow-y: auto; padding: 0.5rem; border-radius: 8px;">
              ${skillsListHtml}
            </div>
          </div>
          <div>
            ${detailPaneHtml}
          </div>
        </div>
      `;

      setView(
        "Skills",
        "Manage and view local skills.",
        layout
      );
      attachHandlers();
    }

    function attachHandlers() {
      const createBtn = document.getElementById("btn-create-skill");
      if (createBtn) {
        createBtn.addEventListener("click", () => {
          state.mode = "create";
          state.selectedSkill = null;
          setNotice(null, "");
          renderView();
        });
      }

      const cancelBtn = document.getElementById("cancel-create-skill");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", () => {
          state.mode = "view";
          setNotice(null, "");
          renderView();
        });
      }

      const skillItems = document.querySelectorAll(".queue-card[data-skill-id]");
      skillItems.forEach(item => {
        item.addEventListener("click", () => {
          const skillId = item.getAttribute("data-skill-id");
          const skill = state.skills.find(s => s.id === skillId);
          if (skill) {
            state.selectedSkill = skill;
            state.mode = "view";
            setNotice(null, "");
            renderView();
          }
        });
      });

      const form = document.getElementById("create-skill-form");
      if (form) {
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (state.requestInFlight) return;

          const formData = new FormData(form);
          const payload = {
            name: formData.get("name").trim(),
            markdown: formData.get("markdown")
          };

          if (!payload.name || !payload.markdown) {
            setNotice("error", "Both name and markdown content are required.");
            renderView();
            return;
          }

          state.requestInFlight = true;
          const submitBtn = form.querySelector('button[type="submit"]');
          if (submitBtn) submitBtn.disabled = true;

          try {
            const response = await fetch("/api/tasks/skills", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify(payload),
            });

            if (!response.ok) {
              const err = await response.json();
              throw new Error(err.detail || response.statusText);
            }

            setNotice("ok", `Skill "${payload.name}" created successfully.`);
            await loadSkills(); // Reload list

            // Auto-select the newly created skill
            const newSkill = state.skills.find(s => s.id === payload.name);
            if (newSkill) {
              state.selectedSkill = newSkill;
            }
            state.mode = "view";

          } catch (error) {
            console.error("Failed to create skill:", error);
            setNotice("error", error.message || "Failed to create skill.");
          } finally {
            state.requestInFlight = false;
            renderView();
          }
        });
      }
    }

    async function loadSkills() {
      try {
        const response = await fetch("/api/tasks/skills?includeContent=true", {
          headers: {
            "Accept": "application/json"
          }
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch skills: ${response.statusText}`);
        }
        const data = await response.json();
        state.skills = data.legacyItems || [];

        if (state.selectedSkill) {
            const updated = state.skills.find(s => s.id === state.selectedSkill.id);
            if (updated) {
                state.selectedSkill = updated;
            } else {
                state.selectedSkill = null;
            }
        }
      } catch (error) {
        console.error("Failed to load skills:", error);
        setNotice("error", "Failed to load skills.");
      }
    }

    setView(
      "Skills",
      "Manage and view local skills.",
      "<p class='loading'>Loading skills...</p>"
    );
    await loadSkills();
    renderView();
  }

  async function renderUserSettingsPage() {
    const state = {
      profile: null,
      notice: null,
      requestInFlight: false,
    };

    function setNotice(level, text) {
      if (!text) {
        state.notice = null;
        return;
      }
      state.notice = {
        level: level === "error" ? "error" : "ok",
        text,
      };
    }

    const managedProviders = ["openai", "google", "anthropic"];

    function renderView() {
      const noticeHtml = state.notice
        ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
        : "";

      let providersMarkup = managedProviders.map(provider => {
        const isSet = state.profile && state.profile[`${provider}_api_key_set`];
        const providerLabel = escapeHtml(provider.charAt(0).toUpperCase() + provider.slice(1));
        const statusHtml = isSet
          ? `<div class="key-status key-status-set">${providerLabel} API Key is SET.</div>
             <p class="form-caption"><em>To change it, enter a new key below. Otherwise, leave blank to keep the current key.</em></p>`
          : `<div class="key-status key-status-not-set">${providerLabel} API Key is NOT SET.</div>`;
        return `
          <div class="form-group">
            <label>
              ${providerLabel} API Key
              ${statusHtml}
              <input type="password" name="${provider}_api_key" placeholder="Enter new ${providerLabel} API Key (optional)">
            </label>
          </div>
        `;
      }).join("");

      const layout = `
        <div data-user-settings-notice>${noticeHtml}</div>
        <div class="system-settings-grid">
          <section class="card system-settings-forms" style="grid-column: 1 / -1;">
            <h3>Manage API Keys</h3>
            <form data-user-settings-form class="stack">
              <fieldset>
                <legend>API Keys</legend>
                ${providersMarkup}
              </fieldset>

              <button type="submit" class="settings-submit-btn">Save API Keys</button>
            </form>
          </section>
        </div>
      `;

      setView(
        "Settings",
        "Manage your profile settings and API keys.",
        layout
      );
      attachHandlers();
    }

    function attachHandlers() {
      const form = document.querySelector('[data-user-settings-form]');
      if (!form) return;

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.requestInFlight) return;

        const formData = new FormData(form);
        const payload = {};
        let hasChanges = false;

        managedProviders.forEach(provider => {
          const val = formData.get(`${provider}_api_key`);
          if (val && val.trim()) {
            payload[`${provider}_api_key`] = val.trim();
            hasChanges = true;
          }
        });

        if (!hasChanges) {
          setNotice("ok", "No changes submitted.");
          renderView();
          return;
        }

        state.requestInFlight = true;
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;

        try {
          const response = await fetch("/me", {
            method: "PUT",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          });

          if (!response.ok) {
            throw new Error(`Failed to update profile: ${response.statusText}`);
          }

          const updatedProfile = await response.json();
          state.profile = updatedProfile;
          setNotice("ok", "API keys updated successfully.");
        } catch (error) {
          console.error("Failed to update user settings:", error);
          setNotice("error", error.message || "Failed to update API keys.");
        } finally {
          state.requestInFlight = false;
          renderView();
        }
      });
    }

    async function loadProfile() {
      try {
        const response = await fetch("/me", {
          credentials: "include",
          headers: {
            "Accept": "application/json"
          }
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch profile: ${response.statusText}`);
        }
        state.profile = await response.json();
      } catch (error) {
        console.error("Failed to load user profile:", error);
        setNotice("error", "Failed to load profile data.");
      }
      renderView();
    }

    setView(
      "Settings",
      "Manage your profile settings and API keys.",
      "<p class='loading'>Loading profile settings...</p>"
    );
    await loadProfile();
  }
  async function renderWorkerControlsPage() {
    const numberFormatter = new Intl.NumberFormat();
    const state = {
      snapshot: null,
      notice: null,
      requestInFlight: false,
    };
    let hasRendered = false;

    function setNotice(level, text) {
      if (!text) {
        state.notice = null;
        return;
      }
      state.notice = {
        level: level === "error" ? "error" : "ok",
        text,
      };
    }

    function buildMetricsMarkup(metrics = {}) {
      const entries = [
        { key: "queued", label: "Queued" },
        { key: "running", label: "Running" },
        { key: "staleRunning", label: "Stale" },
        { key: "isDrained", label: "Drained" },
      ];
      return `
        <div class="system-settings-metrics">
          ${entries
          .map((entry) => {
            const value =
              entry.key === "isDrained"
                ? metrics.isDrained
                  ? "Yes"
                  : "No"
                : numberFormatter.format(
                  typeof metrics[entry.key] === "number" ? metrics[entry.key] : 0,
                );
            return `
                <div class="system-settings-metric">
                  <span class="label">${escapeHtml(entry.label)}</span>
                  <span class="value">${escapeHtml(value)}</span>
                </div>
              `;
          })
          .join("")}
        </div>
      `;
    }

    function buildAuditMarkup(events = []) {
      if (!Array.isArray(events) || events.length === 0) {
        return "<p class='small'>No recent pause or resume actions.</p>";
      }
      return `
        <ul>
          ${events
          .map((event) => {
            const actionLabel =
              String(event?.action || "")
                .trim()
                .toUpperCase() || "-";
            const modeLabel = event?.mode
              ? ` | ${String(event.mode).toUpperCase()}`
              : "";
            const reason = event?.reason ? escapeHtml(event.reason) : "(no reason)";
            const timestamp = formatTimestamp(event?.createdAt);
            return `
                <li>
                  <strong>${escapeHtml(actionLabel)}${escapeHtml(modeLabel)}</strong>
                  <span>${reason}</span>
                  <time datetime="${escapeHtml(event?.createdAt || "")}">${escapeHtml(timestamp)}</time>
                </li>
              `;
          })
          .join("")}
        </ul>
      `;
    }

    function renderView() {
      const noticeHtml = state.notice
        ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
        : "";
      const controlsMarkup = `
        <section class="card system-settings-forms">
          <h3>Worker Controls</h3>
          <p class="form-caption">
            Drain lets running jobs finish; Quiesce stops new claims immediately.
          </p>
          <form data-system-settings-form="pause" class="stack">
            <fieldset>
              <legend>Pause Workers</legend>
              <label>
                Mode
                <select name="mode" required>
                  <option value="drain" selected>Drain (default)</option>
                  <option value="quiesce">Quiesce</option>
                </select>
              </label>
              <label>
                Reason
                <input type="text" name="reason" maxlength="160" required />
              </label>
              <button type="submit">Pause Workers</button>
            </fieldset>
          </form>
          <div class="system-settings-divider"></div>
          <form data-system-settings-form="resume" class="stack">
            <fieldset>
              <legend>Resume Workers</legend>
              <label>
                Reason
                <input type="text" name="reason" maxlength="160" required />
              </label>
              <button type="submit">Resume Workers</button>
            </fieldset>
          </form>
        </section>
      `;
      const providerProfilesMarkup = providerProfileEndpoints ? `
        <section class="card system-settings-provider-profiles">
          <h3>Provider Profiles</h3>
          <p class="form-caption">
            Manage authentication profiles for managed agent runtimes.
          </p>
          <div data-provider-profiles-table>
            <p class="loading">Loading profiles...</p>
          </div>
          <dialog id="oauth-session-modal">
            <form method="dialog">
              <h3>Connect with OAuth</h3>
              <p>Runtime: <span id="oauth-modal-runtime"></span></p>
              <p>Profile: <span id="oauth-modal-profile"></span></p>
              <div id="oauth-modal-status" style="margin: 1rem 0;"></div>
              <menu>
                <button type="button" id="oauth-modal-start" class="btn">Start Session</button>
                <button type="button" id="oauth-modal-cancel" class="btn" style="display:none;">Cancel Session</button>
                <button value="close" class="btn-cancel">Close</button>
              </menu>
            </form>
          </dialog>
          <details class="auth-profile-create-toggle">
            <summary>Create New Profile</summary>
            <form data-provider-profile-form="create" class="stack">
              <fieldset>
                <legend>New Provider Profile</legend>
                <label>
                  Profile ID
                  <input type="text" name="profile_id" maxlength="80" required
                         placeholder="e.g. gemini_oauth_user_a" />
                </label>
                <label>
                  Runtime ID
                  <input type="text" name="runtime_id" maxlength="80"
                         placeholder="e.g. gemini_pro_runtime" />
                </label>
                <label>
                  Auth Mode
                  <select name="auth_mode" required>
                    <option value="oauth" selected>OAuth</option>
                    <option value="api_key">API Key</option>
                  </select>
                </label>
                <label>
                  Volume Ref
                  <input type="text" name="volume_ref" maxlength="120"
                         placeholder="e.g. gemini_auth_volume" />
                </label>
                <label>
                  API Key Ref
                  <input type="text" name="api_key_ref" maxlength="120"
                         placeholder="Worker env var name (e.g. MINIMAX_API_KEY) or vault:// ref" />
                </label>
                <label>
                  API key target env (Claude Code)
                  <input type="text" name="api_key_env_var" maxlength="64"
                         placeholder="ANTHROPIC_API_KEY (default) or ANTHROPIC_AUTH_TOKEN for MiniMax" />
                </label>
                <p class="form-caption">
                  For MiniMax M2.7 via Claude Code, set target to <code>ANTHROPIC_AUTH_TOKEN</code>,
                  point <strong>API Key Ref</strong> at a worker env var that holds your MiniMax key,
                  and paste non-secret Claude Code env below (e.g. <code>ANTHROPIC_BASE_URL</code>, model names).
                </p>
                <label>
                  Runtime env overrides (JSON object, API key mode)
                  <textarea name="runtime_env_overrides_json" rows="8" class="wide"
                    placeholder='{\n  "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",\n  "ANTHROPIC_MODEL": "MiniMax-M2.7",\n  "API_TIMEOUT_MS": "600000",\n  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"\n}'></textarea>
                </label>
                <label>
                  Max Parallel Runs
                  <input type="number" name="max_parallel_runs" min="0" value="1" />
                </label>
                <label>
                  Cooldown After 429 (seconds)
                  <input type="number" name="cooldown_after_429_seconds" min="0" value="900" />
                </label>
                <label>
                  Rate Limit Policy
                  <select name="rate_limit_policy">
                    <option value="backoff" selected>Backoff</option>
                    <option value="queue">Queue</option>
                    <option value="reject">Reject</option>
                  </select>
                </label>
                <label>
                  <input type="checkbox" name="enabled" checked />
                  Enabled
                </label>
                <button type="submit">Create Profile</button>
              </fieldset>
            </form>
          </details>
        </section>
      ` : "";
      const workerPauseMarkup = workerPauseTransport ? `
          <section class="card">
            <div data-system-settings-summary></div>
          </section>
          <div class="system-settings-grid">
            ${controlsMarkup}
            <section class="system-settings-audit">
              <h3>Recent Actions</h3>
              <div data-system-settings-audit></div>
            </section>
          </div>
      ` : `<div class='notice error'>Worker pause controls are not configured for this deployment.</div>`;

      const layout = `
        <div data-system-settings-notice>${noticeHtml}</div>
        <div class="system-settings">
          ${workerPauseMarkup}
          ${providerProfilesMarkup}
        </div>
      `;
      setView(
        "Worker controls",
        "Pause or resume worker processing.",
        layout,
        { showAutoRefreshControls: true },
      );
      attachHandlers();
      syncDynamicView();
      hasRendered = true;
    }

    function syncDynamicView() {
      const noticeNode = document.querySelector("[data-system-settings-notice]");
      if (noticeNode) {
        noticeNode.innerHTML = state.notice
          ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
          : "";
      }

      const summaryNode = document.querySelector("[data-system-settings-summary]");
      const auditNode = document.querySelector("[data-system-settings-audit]");
      if (!summaryNode || !auditNode) {
        return;
      }

      const hasSnapshot = Boolean(state.snapshot);
      const system = (state.snapshot && state.snapshot.system) || {};
      const metrics = (state.snapshot && state.snapshot.metrics) || {};
      const auditEvents =
        (state.snapshot && state.snapshot.audit && state.snapshot.audit.latest) || [];
      const description = describeWorkerPauseState(system, metrics);

      summaryNode.innerHTML = hasSnapshot
        ? `
            <div class="stack">
              <div class="stack">
                <h3>${escapeHtml(description.label)}</h3>
                <p class="page-meta">${escapeHtml(description.reason)}</p>
                <p class="small">
                  Mode: ${escapeHtml((system.mode || description.state).toString())} | Version: ${escapeHtml(
          (system.version ?? "-").toString(),
        )} | Updated: ${escapeHtml(
          system.updatedAt ? formatTimestamp(system.updatedAt) : "-",
        )}
                </p>
              </div>
              ${buildMetricsMarkup(metrics)}
            </div>
          `
        : "<p class='loading'>Loading worker status...</p>";
      auditNode.innerHTML = buildAuditMarkup(auditEvents);
    }

    function attachHandlers() {
      const pauseForm = document.querySelector('[data-system-settings-form="pause"]');
      const resumeForm = document.querySelector('[data-system-settings-form="resume"]');
      const formsRoot = document.querySelector(".system-settings-forms");

      const toggleForms = (disabled) => {
        if (!formsRoot) {
          return;
        }
        const inputs = formsRoot.querySelectorAll("input, select, textarea, button");
        inputs.forEach((node) => {
          if (disabled) {
            node.setAttribute("disabled", "disabled");
          } else {
            node.removeAttribute("disabled");
          }
        });
      };

      if (state.requestInFlight) {
        toggleForms(true);
      }

      pauseForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.requestInFlight) {
          return;
        }
        const formData = new FormData(pauseForm);
        const mode = String(formData.get("mode") || "").trim();
        const reason = String(formData.get("reason") || "").trim();
        if (!mode || !reason) {
          setNotice("error", "Pause mode and reason are required.");
          renderView();
          return;
        }
        state.requestInFlight = true;
        toggleForms(true);
        try {
          const snapshot = await workerPauseTransport.submitAction({
            action: "pause",
            mode,
            reason,
          });
          state.snapshot = snapshot;
          setNotice("ok", "Workers paused successfully.");
          pauseForm.reset();
        } catch (error) {
          console.error("worker pause request failed", error);
          setNotice("error", "Failed to pause workers.");
        } finally {
          state.requestInFlight = false;
          renderView();
        }
      });

      resumeForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.requestInFlight) {
          return;
        }
        const formData = new FormData(resumeForm);
        const reason = String(formData.get("reason") || "").trim();
        if (!reason) {
          setNotice("error", "Resume reason is required.");
          renderView();
          return;
        }
        let forceResume = false;
        if (requiresResumeConfirmation(state.snapshot)) {
          const confirmed = window.confirm(
            "Workers are not drained yet. Resume anyway?",
          );
          if (!confirmed) {
            return;
          }
          forceResume = true;
        }
        state.requestInFlight = true;
        toggleForms(true);
        try {
          const snapshot = await workerPauseTransport.submitAction({
            action: "resume",
            reason,
            forceResume,
          });
          state.snapshot = snapshot;
          setNotice("ok", "Workers resumed successfully.");
          resumeForm.reset();
        } catch (error) {
          console.error("worker resume request failed", error);
          setNotice("error", "Failed to resume workers.");
        } finally {
          state.requestInFlight = false;
          renderView();
        }
      });
    }

    function buildProfileTableMarkup(profiles) {
      if (!Array.isArray(profiles) || profiles.length === 0) {
        return "<p class='small'>No provider profiles configured.</p>";
      }
      return `
        <table class="provider-profiles-table">
          <thead>
            <tr>
              <th>Profile ID</th>
              <th>Runtime</th>
              <th>Auth Mode</th>
              <th>Key env</th>
              <th>Max Runs</th>
              <th>Cooldown</th>
              <th>Enabled</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${profiles.map((p) => `
              <tr data-profile-id="${escapeHtml(p.profile_id)}">
                <td>${escapeHtml(p.profile_id)}</td>
                <td>${escapeHtml(p.runtime_id || "-")}</td>
                <td>${escapeHtml(p.auth_mode || "-")}</td>
                <td>${escapeHtml(p.api_key_env_var || "—")}</td>
                <td>${p.max_parallel_runs ?? "-"}</td>
                <td>${p.cooldown_after_429_seconds ?? "-"}s</td>
                <td>${p.enabled ? "✅" : "❌"}</td>
                <td>
                  <button class="btn-small" data-profile-oauth="${escapeHtml(p.profile_id)}" data-runtime="${escapeHtml(p.runtime_id || "")}" data-volume="${escapeHtml(p.volume_ref || "")}">
                    Connect with OAuth
                  </button>
                  <button class="btn-small" data-profile-toggle="${escapeHtml(p.profile_id)}">
                    ${p.enabled ? "Disable" : "Enable"}
                  </button>
                  <button class="btn-small btn-danger" data-profile-delete="${escapeHtml(p.profile_id)}">
                    Delete
                  </button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    async function loadProviderProfiles() {
      if (!providerProfileEndpoints) {
        return;
      }
      const tableNode = document.querySelector("[data-provider-profiles-table]");
      if (!tableNode) {
        return;
      }
      try {
        const response = await fetch(providerProfileEndpoints.list, {
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          tableNode.innerHTML = "<p class='small'>Failed to load provider profiles.</p>";
          return;
        }
        const profiles = await response.json();
        tableNode.innerHTML = buildProfileTableMarkup(profiles);
        attachProfileHandlers();
      } catch (error) {
        console.error("provider profiles load failed", error);
        tableNode.innerHTML = "<p class='small'>Error loading provider profiles.</p>";
      }
    }

    function attachProfileHandlers() {
      document.querySelectorAll("[data-profile-oauth]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const profileId = btn.dataset.profileOauth;
          const runtimeId = btn.dataset.runtime;
          const volumeRef = btn.dataset.volume;
          const modal = document.getElementById("oauth-session-modal");
          const statusDiv = document.getElementById("oauth-modal-status");
          
          document.getElementById("oauth-modal-profile").textContent = profileId;
          document.getElementById("oauth-modal-runtime").textContent = runtimeId;
          statusDiv.innerHTML = "";
          
          const startBtn = document.getElementById("oauth-modal-start");
          const cancelBtn = document.getElementById("oauth-modal-cancel");
          
          startBtn.style.display = "inline-block";
          cancelBtn.style.display = "none";
          
          let currentSessionId = null;
          
          startBtn.onclick = async () => {
            startBtn.disabled = true;
            statusDiv.textContent = "Starting session...";
            try {
              const res = await fetch("/api/v1/oauth-sessions", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  profile_id: profileId,
                  runtime_id: runtimeId,
                  volume_ref: volumeRef,
                  account_label: profileId,
                }),
              });
              if (!res.ok) {
                const err = await res.json();
                statusDiv.textContent = "Error: " + (err.detail || res.statusText);
                startBtn.disabled = false;
                return;
              }
              const data = await res.json();
              currentSessionId = data.session_id;
              startBtn.style.display = "none";
              cancelBtn.style.display = "inline-block";
              
              // Poll for status
              const pollInterval = setInterval(async () => {
                const pollRes = await fetch(`/api/v1/oauth-sessions/${currentSessionId}`, {
                  credentials: "include"
                });
                if (pollRes.ok) {
                  const pollData = await pollRes.json();
                  statusDiv.textContent = `Status: `;
                  const strong = document.createElement("strong");
                  strong.textContent = pollData.status;
                  statusDiv.appendChild(strong);
                  if (pollData.oauth_web_url) {
                    statusDiv.appendChild(document.createElement("br"));
                    const a = document.createElement("a");
                    a.href = pollData.oauth_web_url;
                    a.target = "_blank";
                    a.textContent = "Open OAuth web session";
                    statusDiv.appendChild(a);
                  }
                  if (["completed", "succeeded", "failed", "canceled", "cancelled", "expired"].includes(pollData.status)) {
                    clearInterval(pollInterval);
                    cancelBtn.style.display = "none";
                  }
                }
              }, 2000);
              
              registerDisposer(() => clearInterval(pollInterval));
              modal.addEventListener("close", () => clearInterval(pollInterval), { once: true });
              
            } catch (e) {
              statusDiv.textContent = "Error: " + e.message;
              startBtn.disabled = false;
            }
          };
          
          cancelBtn.onclick = async () => {
            if (currentSessionId) {
              await fetch(`/api/v1/oauth-sessions/${currentSessionId}/cancel`, {
                method: "POST",
                credentials: "include"
              });
              statusDiv.appendChild(document.createElement("br"));
              statusDiv.appendChild(document.createTextNode("Cancel requested."));
            }
          };
          
          modal.showModal();
        });
      });

      document.querySelectorAll("[data-profile-toggle]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const profileId = btn.dataset.profileToggle;
          const isCurrentlyEnabled = btn.textContent.trim() === "Disable";
          const endpoint = providerProfileEndpoints.update.replace("{profileId}", profileId);
          try {
            await fetch(endpoint, {
              method: "PATCH",
              credentials: "include",
              headers: { "Content-Type": "application/json", Accept: "application/json" },
              body: JSON.stringify({ enabled: !isCurrentlyEnabled }),
            });
            await loadProviderProfiles();
          } catch (error) {
            console.error("toggle profile failed", error);
          }
        });
      });

      document.querySelectorAll("[data-profile-delete]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const profileId = btn.dataset.profileDelete;
          if (!window.confirm(`Delete provider profile "${profileId}"?`)) {
            return;
          }
          const endpoint = providerProfileEndpoints.delete.replace("{profileId}", profileId);
          try {
            await fetch(endpoint, {
              method: "DELETE",
              credentials: "include",
            });
            await loadProviderProfiles();
          } catch (error) {
            console.error("delete profile failed", error);
          }
        });
      });

      const createForm = document.querySelector('[data-provider-profile-form="create"]');
      createForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const formData = new FormData(createForm);
        const rawEnvJson = String(formData.get("runtime_env_overrides_json") || "").trim();
        let runtime_env_overrides = undefined;
        if (rawEnvJson) {
          try {
            runtime_env_overrides = JSON.parse(rawEnvJson);
          } catch (err) {
            setNotice("error", "Runtime env overrides must be valid JSON.");
            syncDynamicView();
            return;
          }
          if (
            typeof runtime_env_overrides !== "object"
            || runtime_env_overrides === null
            || Array.isArray(runtime_env_overrides)
          ) {
            setNotice("error", "Runtime env overrides must be a JSON object.");
            syncDynamicView();
            return;
          }
        }
        const payload = {
          profile_id: String(formData.get("profile_id") || "").trim(),
          runtime_id: String(formData.get("runtime_id") || "").trim() || undefined,
          auth_mode: String(formData.get("auth_mode") || "oauth"),
          volume_ref: String(formData.get("volume_ref") || "").trim() || undefined,
          api_key_ref: String(formData.get("api_key_ref") || "").trim() || undefined,
          api_key_env_var: String(formData.get("api_key_env_var") || "").trim() || undefined,
          runtime_env_overrides,
          max_parallel_runs: Number(formData.get("max_parallel_runs")) || 1,
          cooldown_after_429_seconds: Number(formData.get("cooldown_after_429_seconds")) || 60,
          rate_limit_policy: String(formData.get("rate_limit_policy") || "backoff"),
          enabled: Boolean(formData.get("enabled")),
        };
        if (!payload.profile_id) {
          setNotice("error", "Profile ID is required.");
          syncDynamicView();
          return;
        }
        try {
          const response = await fetch(providerProfileEndpoints.create, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(payload),
          });
          if (response.status === 409) {
            setNotice("error", `Profile "${payload.profile_id}" already exists.`);
            syncDynamicView();
            return;
          }
          if (!response.ok) {
            setNotice("error", "Failed to create provider profile.");
            syncDynamicView();
            return;
          }
          setNotice("ok", `Profile "${payload.profile_id}" created.`);
          createForm.reset();
          await loadProviderProfiles();
          syncDynamicView();
        } catch (error) {
          console.error("create profile failed", error);
          setNotice("error", "Failed to create provider profile.");
          syncDynamicView();
        }
      });
    }

    const load = async (silent = false) => {
      if (workerPauseTransport) {
        try {
          state.snapshot = await workerPauseTransport.fetchState();
          if (!silent) {
            setNotice(null, "");
          }
        } catch (error) {
          console.error("worker controls load failed", error);
          if (!silent) {
            const message =
              error instanceof Error && error.message
                ? error.message
                : "Failed to load worker pause status.";
            setNotice("error", message);
          }
        }
      }
      if (silent && hasRendered) {
        syncDynamicView();
        loadProviderProfiles();
        return;
      }
      renderView();
      loadProviderProfiles();
    };

    setView(
      "Worker controls",
      "Manage worker pause controls and provider profiles.",
      "<p class='loading'>Loading worker controls...</p>",
      { showAutoRefreshControls: true },
    );
    await load();
    if (workerPauseTransport) {
      startPolling(() => load(true), workerPauseTransport.pollInterval);
    }
  }

  function renderNotFound() {
    setView(
      "Route Not Found",
      "The requested dashboard route does not exist.",
      "<div class='notice error'>Unknown dashboard route.</div>",
    );
  }

  function normalizeDashboardRoutePath(pathname) {
    const normalizedPath = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
    if (normalizedPath === "/tasks/new" || normalizedPath === "/tasks/create") {
      return "/tasks/new";
    }
    return normalizedPath;
  }

  async function renderForPath(pathname, searchParams) {
    const normalizedRoute = normalizeDashboardRoutePath(pathname);
    stopPolling();
    activateNav(normalizedRoute);

    const temporalDetailMatch = normalizedRoute.match(/^\/tasks\/temporal\/([^/]+)$/);
    const unifiedDetailMatch = normalizedRoute.match(/^\/tasks\/([^/]+)$/);
    const proposalDetailMatch = normalizedRoute.match(/^\/tasks\/proposals\/([^/]+)$/);
    const scheduleDetailMatch = normalizedRoute.match(/^\/tasks\/schedules\/([^/]+)$/);

    if (normalizedRoute === "/tasks/manifests/new") {
      renderManifestSubmitPage();
      return;
    }




    if (normalizedRoute === "/tasks/skills") {
      await renderSkillsPage();
      return;
    }

    if (normalizedRoute === "/tasks/settings") {
      await renderUserSettingsPage();
      return;
    }

    if (normalizedRoute === "/tasks/new" || normalizedRoute === "/tasks/create") {
      const runtimeParam = parseRuntimeSearchParam(searchParams);
      const editParam = parseEditJobSearchParam(searchParams);
      if (editParam.provided) {
        await renderSubmitWorkPage(runtimeParam.runtime, editParam);
        return;
      }
      if (runtimeParam.provided && !runtimeParam.runtime) {
        await renderSubmitWorkPage(runtimeParam.rawValue);
        return;
      }
      await renderSubmitWorkPage(runtimeParam.runtime);
      return;
    }
    if (normalizedRoute === "/tasks/queue/new") {
      window.history.replaceState({}, "", "/tasks/new");
      await renderSubmitWorkPage(undefined);
      return;
    }

    renderNotFound();
  }

  const workerPauseController = initWorkerPauseBanner(workerPauseTransport);
  if (workerPauseController) {
    startPolling(
      () => workerPauseController.refresh(),
      workerPauseController.pollInterval,
      { runImmediately: true, skipAutoRefresh: true, persistent: true },
    );
  }

  const disposeTheme = initTheme();
  initButtonClickGlow();
  syncTemporalNavVisibility();
  if (typeof window.addEventListener === "function") {
    window.addEventListener("beforeunload", () => {
      stopPolling();
      stopPersistentPolling();
      if (typeof disposeTheme === "function") {
        disposeTheme();
      }
    });
  }

  const skipInitialRender = Boolean(
    window.__MOONMIND_DASHBOARD_TEST && window.__MOONMIND_DASHBOARD_TEST.skipInitialRender,
  );

  if (!skipInitialRender) {
    const currentLocation = new URL(window.location.href);
    renderForPath(
      window.location.pathname,
      currentLocation.searchParams,
    ).catch((error) => {
      console.error("dashboard render failed", error);
      setView(
        "Dashboard Error",
        "Unexpected rendering failure.",
        "<div class='notice error'>Unexpected dashboard rendering failure.</div>",
      );
    });
  }
})();
