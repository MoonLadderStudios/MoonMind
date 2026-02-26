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
  const configNode = { textContent: JSON.stringify({}) };
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
    location: { pathname: "/tasks/queue" },
  };
  const context = {
    window: windowStub,
    document: documentStub,
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  const elementStub = function Element() {};
  context.Element = elementStub;
  context.HTMLElement = elementStub;
  context.HTMLInputElement = function HTMLInputElement() {};
  context.HTMLTextAreaElement = function HTMLTextAreaElement() {};
  context.HTMLSelectElement = function HTMLSelectElement() {};
  context.HTMLButtonElement = function HTMLButtonElement() {};
  context.SVGElement = function SVGElement() {};
  windowStub.Element = elementStub;
  windowStub.HTMLElement = elementStub;
  windowStub.HTMLInputElement = context.HTMLInputElement;
  windowStub.HTMLTextAreaElement = context.HTMLTextAreaElement;
  windowStub.HTMLSelectElement = context.HTMLSelectElement;
  windowStub.HTMLButtonElement = context.HTMLButtonElement;
  windowStub.SVGElement = context.SVGElement;
  return context;
}

function loadSubmitRuntimeHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = createVmContext();
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__submitRuntimeTest;
  assert(helpers, "Expected submit runtime helpers to be exposed");
  return helpers;
}

const helpers = loadSubmitRuntimeHelpers();

(function testDraftControllerMaintainsSeparateDrafts() {
  const controller = helpers.createSubmitDraftController(
    { instruction: "", steps: helpers.cloneStepStateEntries([{ instructions: "" }]) },
    { instruction: "", targetService: "orchestrator", priority: "normal" },
  );
  controller.saveWorker({
    instruction: "Queue landing",
    repository: "moon/demo",
    steps: helpers.cloneStepStateEntries([{ instructions: "Plan" }]),
    appliedTemplateState: [],
    publishMode: "pr",
    workerPriority: "4",
    maxAttempts: "3",
    proposeTasks: false,
  });
  controller.saveOrchestrator({
    instruction: "Ship release",
    targetService: "deploy",
    priority: "high",
  });
  const workerSnapshot = controller.loadWorker();
  assert.strictEqual(workerSnapshot.instruction, "Queue landing");
  assert.strictEqual(workerSnapshot.repository, "moon/demo");
  assert.strictEqual(workerSnapshot.steps[0].instructions, "Plan");
  workerSnapshot.steps[0].instructions = "mutated";
  const workerReload = controller.loadWorker();
  assert.strictEqual(workerReload.steps[0].instructions, "Plan");
  const orchestratorSnapshot = controller.loadOrchestrator();
  assert.strictEqual(orchestratorSnapshot.instruction, "Ship release");
  assert.strictEqual(orchestratorSnapshot.priority, "high");
})();

(function testResetWorkerSubmissionFieldsClearsStepInputs() {
  const sourceDraft = {
    instruction: "Implement queue task",
    templateFeatureRequest: "Do the work",
    repository: "moon/demo",
    steps: helpers.cloneStepStateEntries([
      { instructions: "Step one" },
      { instructions: "Step two" },
    ]),
  };
  const resetDraft = helpers.resetWorkerSubmissionFields(sourceDraft);
  assert.strictEqual(resetDraft.instruction, "");
  assert.strictEqual(resetDraft.templateFeatureRequest, "");
  assert.strictEqual(Array.isArray(resetDraft.steps), true);
  assert.strictEqual(resetDraft.steps.length, 0);
  assert.strictEqual(resetDraft.repository, "moon/demo");
})();

(function testDetermineSubmitDestinationRoutesPayloads() {
  const endpoints = { queue: "/api/queue/jobs", orchestrator: "/orchestrator/tasks" };
  const workerTarget = helpers.determineSubmitDestination("codex", endpoints);
  assert.strictEqual(workerTarget.mode, "worker");
  assert.strictEqual(workerTarget.endpoint, "/api/queue/jobs");
  const orchestratorTarget = helpers.determineSubmitDestination("orchestrator", endpoints);
  assert.strictEqual(orchestratorTarget.mode, "orchestrator");
  assert.strictEqual(orchestratorTarget.endpoint, "/orchestrator/tasks");
})();

(function testValidateOrchestratorSubmissionEnforcesFields() {
  const empty = helpers.validateOrchestratorSubmission({});
  assert.strictEqual(empty.ok, false);
  assert.ok(/Instruction/i.test(empty.error));
  const missingService = helpers.validateOrchestratorSubmission({ instruction: "Ship" });
  assert.strictEqual(missingService.ok, false);
  assert.ok(/Target service/i.test(missingService.error));
  const valid = helpers.validateOrchestratorSubmission({
    instruction: "Ship",
    targetService: "orchestrator",
    priority: "HIGH",
    skillId: "speckit-orchestrate",
    skillArgs: '{"feature":"drafts"}',
    approvalToken: " token-value ",
  });
  assert.strictEqual(valid.ok, true);
  assert.strictEqual(valid.value.priority, "high");
  assert.strictEqual(valid.value.skillId, "speckit-orchestrate");
  assert.strictEqual(JSON.stringify(valid.value.skillArgs), JSON.stringify({ feature: "drafts" }));
  assert.strictEqual(valid.value.approvalToken, "token-value");

  const invalidSkillArgs = helpers.validateOrchestratorSubmission({
    instruction: "Ship",
    targetService: "deploy",
    skillArgs: "[]",
  });
  assert.strictEqual(invalidSkillArgs.ok, false);
  assert.ok(/Skill Args/i.test(invalidSkillArgs.error));

  const noInstructionWithWrongService = helpers.validateOrchestratorSubmission({
    targetService: "deploy",
    skillId: "speckit-orchestrate",
  });
  assert.strictEqual(noInstructionWithWrongService.ok, false);
  assert.ok(/Target service/i.test(noInstructionWithWrongService.error));

  const noInstructionWithSkill = helpers.validateOrchestratorSubmission({
    targetService: "orchestrator",
    skillId: "speckit-orchestrate",
  });
  assert.strictEqual(noInstructionWithSkill.ok, true);
  assert.strictEqual(noInstructionWithSkill.value.skillId, "speckit-orchestrate");
  assert.strictEqual(noInstructionWithSkill.value.instruction, "");
})();

(function testNormalizeOrchestratorPriority() {
  assert.strictEqual(helpers.normalizeOrchestratorPriority("HIGH"), "high");
  assert.strictEqual(helpers.normalizeOrchestratorPriority("low"), "normal");
  assert.strictEqual(helpers.normalizeOrchestratorPriority(undefined), "normal");
})();

(function testResolvePromotedQueueRoute() {
  const valid = helpers.resolvePromotedQueueRoute({
    job: { id: "123e4567-e89b-12d3-a456-426614174000" },
  });
  assert.strictEqual(valid, "/tasks/123e4567-e89b-12d3-a456-426614174000?source=queue");

  const fromJobIdAlias = helpers.resolvePromotedQueueRoute({
    job: { jobId: "ABCDEF01-2345-6789-ABCD-EF0123456789" },
  });
  assert.strictEqual(fromJobIdAlias, "/tasks/ABCDEF01-2345-6789-ABCD-EF0123456789?source=queue");

  const invalidEncoded = helpers.resolvePromotedQueueRoute({
    job: { id: "%2Ftmp%2Fqueue" },
  });
  assert.strictEqual(invalidEncoded, "/tasks/list?source=queue");

  const reservedCreateRoute = helpers.resolvePromotedQueueRoute({
    job: { id: "new" },
  });
  assert.strictEqual(reservedCreateRoute, "/tasks/list?source=queue");

  const missing = helpers.resolvePromotedQueueRoute({ proposal: { id: "ignored" } });
  assert.strictEqual(missing, "/tasks/list?source=queue");
})();
