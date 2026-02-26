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

(function testParseEditJobSearchParam() {
  const params = new URLSearchParams("editJobId=123e4567-e89b-12d3-a456-426614174000");
  const parsed = helpers.parseEditJobSearchParam(params);
  assert.strictEqual(parsed.provided, true);
  assert.strictEqual(parsed.jobId, "123e4567-e89b-12d3-a456-426614174000");

  const missing = helpers.parseEditJobSearchParam(new URLSearchParams(""));
  assert.strictEqual(missing.provided, false);
  assert.strictEqual(missing.jobId, "");

  const invalid = helpers.parseEditJobSearchParam(
    new URLSearchParams("editJobId=../../etc/passwd"),
  );
  assert.strictEqual(invalid.provided, true);
  assert.strictEqual(invalid.jobId, "");
})();

(function testIsEditableQueuedTaskJob() {
  const editable = helpers.isEditableQueuedTaskJob({
    type: "task",
    status: "queued",
    startedAt: null,
  });
  assert.strictEqual(editable, true);

  const started = helpers.isEditableQueuedTaskJob({
    type: "task",
    status: "queued",
    startedAt: "2026-02-25T01:23:45.678Z",
  });
  assert.strictEqual(started, false);

  const wrongType = helpers.isEditableQueuedTaskJob({
    type: "manifest",
    status: "queued",
    startedAt: null,
  });
  assert.strictEqual(wrongType, false);

  const wrongStatus = helpers.isEditableQueuedTaskJob({
    type: "task",
    status: "running",
    startedAt: null,
  });
  assert.strictEqual(wrongStatus, false);
})();

(function testStringifySkillArgsPreservesFailureForUnserializableObjects() {
  const circular = {};
  circular.self = circular;
  const rendered = helpers.stringifySkillArgs(circular);
  assert.strictEqual(rendered, "[unserializable skill args]");
})();

(function testBuildQueueSubmissionDraftFromJobKeepsTemplateBoundFirstStep() {
  const draft = helpers.buildQueueSubmissionDraftFromJob({
    payload: {
      repository: "Moon/Test",
      task: {
        instructions: "Build feature branch",
        runtime: {
          mode: "codex",
        },
        publish: {
          mode: "pr",
        },
        steps: [
          {
            id: "step-1",
            instructions: "Build feature branch",
            skill: {
              id: "",
            },
          },
        ],
        appliedStepTemplates: [
          {
            slug: "preset-template",
            version: "1",
            stepIds: ["step-1"],
            appliedAt: "2026-02-26T00:00:00Z",
          },
        ],
      },
    },
    priority: 2,
    maxAttempts: 4,
    createdByUserId: "00000000-0000-4000-8000-000000000001",
    requestedByUserId: "00000000-0000-4000-8000-000000000001",
    updatedAt: "2026-02-26T00:00:00Z",
  });
  assert.strictEqual(draft.steps.length, 1);
  assert.strictEqual(draft.steps[0].id, "step-1");
  assert.strictEqual(draft.steps[0].instructions, "Build feature branch");
  assert.strictEqual(draft.appliedTemplateState.length, 1);
  assert.deepStrictEqual(draft.appliedTemplateState[0].stepIds, ["step-1"]);
  assert.strictEqual(draft.publishMode, "pr");
  assert.strictEqual(draft.model, "");
  assert.strictEqual(draft.effort, "");
})();

(function testBuildQueueSubmissionDraftFromJobPreservesRawEditFields() {
  const draft = helpers.buildQueueSubmissionDraftFromJob({
    payload: {
      repository: "Moon/Test",
      task: {
        runtime: {
          mode: "CustomRuntime",
          model: " model-with-space ",
          effort: " fast ",
        },
        publish: {
          mode: " PR ",
        },
      },
    },
  });
  assert.strictEqual(draft.runtime, "CustomRuntime");
  assert.strictEqual(draft.model, " model-with-space ");
  assert.strictEqual(draft.effort, " fast ");
  assert.strictEqual(draft.publishMode, " PR ");
})();
