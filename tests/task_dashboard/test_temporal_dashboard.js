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
        supportedTaskRuntimes: ["codex", "gemini"],
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
      },
      memo: {
        title: "Temporal task",
      },
      startedAt: "2026-03-06T10:00:00Z",
      updatedAt: "2026-03-06T11:00:00Z",
    },
  ]);

  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].taskId, "mm:workflow-123");
  assert.strictEqual(rows[0].workflowId, "mm:workflow-123");
  assert.strictEqual(rows[0].temporalRunId, "run-999");
  assert.strictEqual(rows[0].link, "/tasks/mm:workflow-123?source=temporal");
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
    "/tasks/queue/new",
  );
  assert.strictEqual(
    helpers.normalizeDashboardRoutePath("/tasks/temporal"),
    "/tasks/temporal",
  );
})();
