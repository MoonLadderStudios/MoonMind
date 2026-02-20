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

function loadWorkerPauseHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = {
    window: {},
    document: {
      getElementById() {
        return null;
      },
    },
    console,
  };
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__workerPauseTest;
  assert(helpers, "Expected worker pause helpers to be exposed on window");
  return helpers;
}

(function run() {
  const { describeWorkerPauseState, requiresResumeConfirmation } =
    loadWorkerPauseHelpers();

  const pausedDrain = describeWorkerPauseState(
    { workersPaused: true, mode: "drain", reason: "Rolling update" },
    { isDrained: false },
  );
  assert.strictEqual(pausedDrain.label, "Workers: Paused (Drain)");
  assert.strictEqual(pausedDrain.reason, "Rolling update");
  assert.strictEqual(pausedDrain.state, "paused");
  assert.strictEqual(pausedDrain.drained, "No");

  const pausedQuiesce = describeWorkerPauseState(
    { workersPaused: true, mode: "quiesce", reason: "Short maintenance" },
    { isDrained: true },
  );
  assert.strictEqual(pausedQuiesce.label, "Workers: Paused (Quiesce)");
  assert.strictEqual(pausedQuiesce.state, "quiesce");
  assert.strictEqual(pausedQuiesce.drained, "Yes");

  const running = describeWorkerPauseState({ workersPaused: false }, {});
  assert.strictEqual(running.label, "Workers: Running");
  assert.strictEqual(running.reason, "Workers are accepting new jobs.");
  assert.strictEqual(running.state, "running");

  assert.strictEqual(requiresResumeConfirmation({ metrics: { isDrained: false } }), true);
  assert.strictEqual(requiresResumeConfirmation({ metrics: { isDrained: true } }), false);
  assert.strictEqual(requiresResumeConfirmation(null), false);
})();
