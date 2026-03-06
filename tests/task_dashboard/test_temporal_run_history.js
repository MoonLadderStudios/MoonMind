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
      sources: {
        temporal: {
          detail: "/api/executions/{workflowId}",
          artifacts: "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
        },
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
    location: {
      pathname: "/tasks/mm:wf-1",
      href: "http://example.test/tasks/mm:wf-1",
    },
  };
  const context = {
    window: windowStub,
    document: documentStub,
    console,
    URL,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  return context;
}

function loadTemporalHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = createVmContext();
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__temporalRunHistoryTest;
  assert(helpers, "Expected temporal helpers to be exposed");
  return helpers;
}

const helpers = loadTemporalHelpers();

(function testResolveTemporalDetailContextPrefersLatestTemporalRunId() {
  const context = helpers.resolveTemporalDetailContext(
    {
      namespace: "moonmind",
      workflowId: "mm:wf-1",
      taskId: "mm:wf-1",
      runId: "run-stale",
      temporalRunId: "run-latest",
      continueAsNewCause: "manual_rerun",
    },
    "mm:wf-1",
  );

  assert.strictEqual(context.taskId, "mm:wf-1");
  assert.strictEqual(context.temporalRunId, "run-latest");
  assert.strictEqual(context.continueAsNewCause, "manual_rerun");
  assert.strictEqual(
    context.artifactsEndpoint,
    "/api/executions/moonmind/mm%3Awf-1/run-latest/artifacts",
  );
})();

(function testResolveTemporalDetailContextFallsBackToWorkflowIdAndMemoCause() {
  const context = helpers.resolveTemporalDetailContext(
    {
      namespace: "moonmind",
      runId: "run-2",
      memo: {
        continue_as_new_cause: "lifecycle_threshold",
      },
    },
    "mm:wf-2",
  );

  assert.strictEqual(context.taskId, "mm:wf-2");
  assert.strictEqual(context.temporalRunId, "run-2");
  assert.strictEqual(context.continueAsNewCause, "lifecycle_threshold");
  assert.strictEqual(
    context.artifactsEndpoint,
    "/api/executions/moonmind/mm%3Awf-2/run-2/artifacts",
  );
})();

(function testResolveTemporalDetailContextSkipsArtifactFetchWithoutRunMetadata() {
  const context = helpers.resolveTemporalDetailContext(
    {
      namespace: "moonmind",
      memo: {},
    },
    "mm:wf-3",
  );

  assert.strictEqual(context.taskId, "mm:wf-3");
  assert.strictEqual(context.temporalRunId, null);
  assert.strictEqual(context.artifactsEndpoint, "");
})();
