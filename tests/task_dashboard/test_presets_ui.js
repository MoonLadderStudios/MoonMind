/* eslint-env node */
"use strict";

const assert = require("assert");

/**
 * Lightweight smoke tests for preset-mode merge semantics.
 * These tests are intentionally runtime-agnostic and can be executed via node.
 */
function mergeExpandedSteps(existingSteps, expandedSteps, mode) {
  if (mode === "replace") {
    return [...expandedSteps];
  }
  return [...existingSteps, ...expandedSteps];
}

(function run() {
  const existing = [{ id: "s1" }, { id: "s2" }];
  const incoming = [{ id: "tpl:1" }];
  assert.deepStrictEqual(mergeExpandedSteps(existing, incoming, "append"), [
    { id: "s1" },
    { id: "s2" },
    { id: "tpl:1" },
  ]);
  assert.deepStrictEqual(mergeExpandedSteps(existing, incoming, "replace"), [
    { id: "tpl:1" },
  ]);
})();
