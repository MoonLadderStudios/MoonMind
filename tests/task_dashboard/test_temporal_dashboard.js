/* eslint-env node */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DASHBOARD_JS = path.join(
  __dirname,
  "..",
  "..",
  "api_service",
  "static",
  "task_dashboard",
  "dashboard.js",
);

function createVmContext() {
  const configNode = {
    textContent: JSON.stringify({
      features: {
        temporalDashboard: {
          enabled: true,
          listEnabled: true,
          detailEnabled: true,
          actionsEnabled: true,
          submitEnabled: false,
          debugFieldsEnabled: true,
        },
      },
      sources: {
        temporal: {
          detail: "/api/executions/{workflowId}",
        },
      },
      system: {
        supportedTaskRuntimes: ["codex", "gemini_cli"],
      },
    }),
  };
  const rootNode = {
    innerHTML: "",
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  const documentElement = {
    classList: { toggle() {}, add() {}, remove() {} },
    dataset: {},
  };
  const documentStub = {
    documentElement,
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    getElementById(id) {
      if (id === "task-dashboard-config") {
        return configNode;
      }
      if (id === "dashboard-content") {
        return rootNode;
      }
      return null;
    },
  };
  const windowStub = {
    document: documentStub,
    addEventListener() {},
    removeEventListener() {},
    matchMedia() {
      return {
        matches: false,
        addEventListener() {},
        removeEventListener() {},
      };
    },
    localStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    __MOONMIND_DASHBOARD_TEST: { skipInitialRender: true },
    location: { pathname: "/tasks/list", href: "http://example.test/tasks/list" },
  };
  const elementStub = function Element() {};
  const context = {
    window: windowStub,
    document: documentStub,
    console,
    Element: elementStub,
    HTMLElement: elementStub,
    HTMLInputElement: function HTMLInputElement() {},
    HTMLTextAreaElement: function HTMLTextAreaElement() {},
    HTMLSelectElement: function HTMLSelectElement() {},
    HTMLButtonElement: function HTMLButtonElement() {},
    SVGElement: function SVGElement() {},
    URL,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  windowStub.Element = elementStub;
  windowStub.HTMLElement = elementStub;
  windowStub.HTMLInputElement = context.HTMLInputElement;
  windowStub.HTMLTextAreaElement = context.HTMLTextAreaElement;
  windowStub.HTMLSelectElement = context.HTMLSelectElement;
  windowStub.HTMLButtonElement = context.HTMLButtonElement;
  windowStub.SVGElement = context.SVGElement;
  windowStub.URL = URL;
  windowStub.URLSearchParams = URLSearchParams;
  return context;
}

function loadTemporalHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = createVmContext();
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__temporalDashboardTest;
  assert(helpers, "Expected temporal dashboard helpers to be exposed");
  return helpers;
}

const helpers = loadTemporalHelpers();

(function testTemporalWaitingReasonOnlyShowsForBlockedRuns() {
  assert.strictEqual(
    helpers.temporalWaitingReason({
      state: "awaiting_external",
      waitingReason: "Waiting on approval.",
    }),
    "Waiting on approval.",
  );
  assert.strictEqual(
    helpers.temporalWaitingReason({
      state: "executing",
      waitingReason: "",
    }),
    "",
  );
  assert.strictEqual(
    helpers.temporalWaitingReason({
      state: "awaiting_external",
      memo: { waitingReason: "Waiting on memo." },
    }),
    "Waiting on memo.",
  );
  assert.strictEqual(
    helpers.temporalWaitingReason({
      state: "awaiting_external",
      memo: { summary: "Fallback summary reason." },
    }),
    "Fallback summary reason.",
  );
})();

(function testRenderTemporalActionButtonsRespectsExecutionState() {
  const executingHtml = helpers.renderTemporalActionButtons({ state: "executing" });
  assert(executingHtml.includes('data-temporal-action="set-title"'));
  assert(executingHtml.includes('data-temporal-action="cancel"'));
  assert(executingHtml.includes('data-temporal-action="pause"'));
  assert(!executingHtml.includes('data-temporal-action="resume"'));
  assert(!executingHtml.includes('data-temporal-action="rerun"'));

  const blockedHtml = helpers.renderTemporalActionButtons({
    state: "awaiting_external",
  });
  assert(blockedHtml.includes('data-temporal-action="approve"'));
  assert(blockedHtml.includes('data-temporal-action="resume"'));

  const terminalHtml = helpers.renderTemporalActionButtons({ state: "failed" });
  assert(terminalHtml.includes('data-temporal-action="rerun"'));
  assert(!terminalHtml.includes('data-temporal-action="cancel"'));
})();

(function testRenderTemporalActionButtonsIncludesCancelAndSetTitleForQueued() {
  const queuedHtml = helpers.renderTemporalActionButtons({ state: "queued" });
  assert(queuedHtml.includes('data-temporal-action="cancel"'), "Queued state must show cancel button");
  assert(queuedHtml.includes('data-temporal-action="set-title"'), "Queued state must show set-title button");
  assert(!queuedHtml.includes('data-temporal-action="rerun"'), "Queued state must not show rerun");
  assert(!queuedHtml.includes('data-temporal-action="resume"'), "Queued state must not show resume");
})();

(function testTemporalRowsKeepTaskAndRunIdentifiersDistinct() {
  const rows = helpers.toTemporalRows([
    {
      workflowId: "mm:workflow-123",
      runId: "run-999",
      workflowType: "MoonMind.Run",
      state: "executing",
      temporalStatus: "running",
      searchAttributes: {
        mm_owner_id: "user-123",
        mm_owner_type: "user",
        mm_updated_at: "2026-03-06T11:00:00Z",
        mm_entry: "legacy-entry",
        mm_repo: "legacy-repo",
        mm_integration: "legacy-integration",
      },
      memo: { title: "Temporal task", entry: "memo-entry" },
      startedAt: "2026-03-06T10:00:00Z",
      updatedAt: "2026-03-06T11:00:00Z",
    },
  ]);

  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].taskId, "mm:workflow-123");
  assert.strictEqual(rows[0].workflowId, "mm:workflow-123");
  assert.strictEqual(rows[0].temporalRunId, "run-999");
  assert.strictEqual(rows[0].entry, "legacy-entry");
  assert.strictEqual(rows[0].ownerType, "user");
  assert.strictEqual(rows[0].ownerId, "user-123");
  assert.strictEqual(rows[0].repository, "legacy-repo");
  assert.strictEqual(rows[0].integration, "legacy-integration");
  assert.strictEqual(rows[0].link, "/tasks/mm:workflow-123?source=temporal");
  assert.strictEqual(rows[0].rawState, "executing");
})();

(function testTemporalTitleAndRouteNormalizationStayTaskOriented() {
  assert.strictEqual(
    helpers.deriveTemporalTitle({ workflowType: "MoonMind.Run", title: "Review PR" }),
    "Review PR",
  );
  assert.strictEqual(
    helpers.deriveTemporalTitle({ workflowType: "MoonMind.ManifestIngest", workflowId: "mm:abcd" }),
    "mm:abcd",
  );
  assert.strictEqual(
    helpers.normalizeDashboardRoutePath("/tasks/new"),
    "/tasks/new",
  );
  assert.strictEqual(
    helpers.normalizeDashboardRoutePath("/tasks/temporal"),
    "/tasks/temporal",
  );
})();

(function testTemporalSourceFlagPreservesExistingQueryState() {
  assert.strictEqual(
    helpers.withTemporalSourceFlag("/api/executions/mm%3Awf-1"),
    "/api/executions/mm%3Awf-1?source=temporal",
  );
  assert.strictEqual(
    helpers.withTemporalSourceFlag("/api/executions?pageSize=25"),
    "/api/executions?pageSize=25&source=temporal",
  );
  assert.strictEqual(
    helpers.withTemporalSourceFlag("/api/executions/mm%3Awf-1?source=temporal"),
    "/api/executions/mm%3Awf-1?source=temporal",
  );
})();

(function testTemporalDetailMarkupIncludesLiveLogsSection() {
  const html = helpers.renderTemporalDetailMarkup({
    execution: { state: "executing", workflowType: "MoonMind.LegacyWorkflow" },
    latestWorkflowId: "mm:wf-live-logs",
    latestRunId: "run-001",
    artifacts: { artifacts: [] },
    waitingReason: "",
    detailTitle: "Test Task",
    attentionRequired: false,
    noticeHtml: "",
    debugFields: "",
  });
  assert(html.includes('id="temporal-live-logs-section"'), "Expected live logs section");
  assert(html.includes('id="temporal-start-tailing"'), "Expected start tailing button");
  assert(html.includes('id="temporal-live-logs-inactive"'), "Expected inactive container");
  assert(html.includes('id="temporal-live-logs-active"'), "Expected active container");
  assert(html.includes('style="display:none"'), "Expected active container to be hidden by default");
  assert(html.includes('id="temporal-follow-output"'), "Expected follow output toggle");
  assert(html.includes('id="temporal-output-filter"'), "Expected output filter select");
  assert(html.includes('id="temporal-copy-output"'), "Expected copy button");
  assert(html.includes('id="temporal-stop-tailing"'), "Expected stop button");
  assert(html.includes('id="temporal-live-transport-status"'), "Expected transport status span");
  assert(html.includes('id="temporal-live-output"'), "Expected live output pre element");
})();

(function testTemporalDetailMarkupLiveLogsDefaultsToCollapsed() {
  const html = helpers.renderTemporalDetailMarkup({
    execution: { state: "completed", workflowType: "MoonMind.LegacyWorkflow" },
    latestWorkflowId: "mm:wf-collapsed",
    latestRunId: "run-002",
    artifacts: { artifacts: [] },
    waitingReason: "",
    detailTitle: "Collapsed Task",
    attentionRequired: false,
    noticeHtml: "",
    debugFields: "",
  });
  const inactiveIdx = html.indexOf('id="temporal-live-logs-inactive"');
  const activeIdx = html.indexOf('id="temporal-live-logs-active"');
  assert(inactiveIdx > -1, "Inactive container must exist");
  assert(activeIdx > -1, "Active container must exist");
  // The inactive section should NOT have display:none, the active section should
  const inactiveSlice = html.slice(Math.max(0, inactiveIdx - 100), inactiveIdx);
  assert(!inactiveSlice.includes('display:none'), "Inactive container should be visible by default");
  const activeSlice = html.slice(Math.max(0, activeIdx - 100), activeIdx + 100);
  assert(activeSlice.includes('display:none'), "Active container should be hidden by default");
})();

(function testToTemporalRowsExtractsRuntimeModeFromTargetRuntime() {
  const rows = helpers.toTemporalRows([
    {
      workflowId: "mm:rt-camel",
      state: "executing",
      targetRuntime: "codex",
      startedAt: "2026-03-20T10:00:00Z",
    },
  ]);
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].runtimeMode, "codex", "should extract runtimeMode from targetRuntime (camelCase)");
})();

(function testToTemporalRowsExtractsRuntimeModeFromSnakeCaseTargetRuntime() {
  const rows = helpers.toTemporalRows([
    {
      workflowId: "mm:rt-snake",
      state: "executing",
      target_runtime: "gemini_cli",
      startedAt: "2026-03-20T10:00:00Z",
    },
  ]);
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].runtimeMode, "gemini_cli", "should extract runtimeMode from target_runtime (snake_case)");
})();

(function testToTemporalRowsRuntimeModeDefaultsToNullWhenMissing() {
  const rows = helpers.toTemporalRows([
    {
      workflowId: "mm:rt-none",
      state: "executing",
      startedAt: "2026-03-20T10:00:00Z",
    },
  ]);
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].runtimeMode, null, "should default runtimeMode to null when neither property exists");
})();

(function testToTemporalRowsTargetRuntimeTakesPrecedenceOverSnakeCase() {
  const rows = helpers.toTemporalRows([
    {
      workflowId: "mm:rt-both",
      state: "executing",
      targetRuntime: "codex",
      target_runtime: "gemini_cli",
      startedAt: "2026-03-20T10:00:00Z",
    },
  ]);
  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].runtimeMode, "codex", "targetRuntime (camelCase) should take precedence");
})();
