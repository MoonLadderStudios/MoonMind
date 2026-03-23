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
  const documentStub = {
    getElementById(id) {
      if (id === "task-dashboard-config") {
        return configNode;
      }
      if (id === "dashboard-content") {
        return rootNode;
      }
      return null;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    documentElement: {
      classList: { toggle() {}, add() {}, remove() {} },
      dataset: {},
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
    location: {
      pathname: "/tasks/mm:test-workflow",
      assign() {},
    },
    __MOONMIND_DASHBOARD_TEST: { skipInitialRender: true },
  };
  const context = {
    window: windowStub,
    document: documentStub,
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    fetch: async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      text: async () => "{}",
    }),
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

function loadTemporalHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = createVmContext();
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__temporalDashboardTest;
  assert(helpers, "Expected Temporal dashboard helpers to be exposed");
  return { context, helpers };
}

const { context, helpers } = loadTemporalHelpers();

(function testResolveTemporalArtifactsRequestUsesLatestRunFromDetail() {
  const request = helpers.resolveTemporalArtifactsRequest(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-123",
      runId: "run-latest",
    },
    "mm:stale-row-run",
  );

  assert.strictEqual(request.namespace, "moonmind");
  assert.strictEqual(request.workflowId, "mm:workflow-123");
  assert.strictEqual(request.temporalRunId, "run-latest");
  assert.strictEqual(request.canFetch, true);
})();

(function testResolveTemporalArtifactsRequestSupportsTemporalRunIdAlias() {
  const request = helpers.resolveTemporalArtifactsRequest(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-456",
      temporalRunId: "run-from-alias",
    },
    "mm:workflow-456",
  );

  assert.strictEqual(request.temporalRunId, "run-from-alias");
  assert.strictEqual(request.canFetch, true);
})();

(function testResolveTemporalArtifactsRequestFallsBackToEmptyLatestRunScope() {
  const request = helpers.resolveTemporalArtifactsRequest(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-789",
    },
    "mm:workflow-789",
  );

  assert.strictEqual(request.temporalRunId, "");
  assert.strictEqual(request.canFetch, false);
})();

(function testResolveTemporalDetailModelKeepsWaitingContextAndDebugFlagOverride() {
  const detail = helpers.resolveTemporalDetailModel(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-321",
      workflowType: "MoonMind.Run",
      state: "awaiting_external",
      runId: "run-waiting",
      memo: {
        summary: "Operator review required.",
        waitingReason: "Awaiting operator approval.",
      },
      attentionRequired: true,
    },
    "mm:workflow-321",
    { debugFieldsEnabled: true },
  );

  assert.strictEqual(detail.workflowType, "Run");
  assert.strictEqual(detail.temporalRunId, "run-waiting");
  assert.strictEqual(detail.waitingContext, "Awaiting operator approval. Attention required.");
  assert.strictEqual(detail.debugFieldsEnabled, true);
})();

(function testResolveTemporalActionSurfaceUsesStateMatrixAndConfiguredActions() {
  const actions = helpers.resolveTemporalActionSurface(
    {
      state: "awaiting_external",
      availableActions: ["resume", "cancel"],
    },
    { actionsEnabled: true },
  );

  assert.strictEqual(
    JSON.stringify(actions),
    JSON.stringify([
      { actionKey: "resume", label: "Resume task" },
      { actionKey: "cancel", label: "Cancel task" },
    ]),
  );
})();

(function testResolveTemporalActionSurfaceReturnsCancelAndRenameForQueued() {
  const actions = helpers.resolveTemporalActionSurface(
    { state: "queued" },
    { actionsEnabled: true },
  );

  assert.strictEqual(
    JSON.stringify(actions),
    JSON.stringify([
      { actionKey: "rename", label: "Rename task" },
      { actionKey: "cancel", label: "Cancel task" },
    ]),
  );
})();

(function testBuildTemporalActionRequestMapsApproveRerunAndCancel() {
  const approve = helpers.buildTemporalActionRequest("mm:workflow-123", "approve");
  assert.strictEqual(approve.request.url, "/api/executions/mm%3Aworkflow-123/signal");
  assert.deepStrictEqual(JSON.parse(approve.request.options.body), {
    signalName: "Approve",
    payload: {
      approval_type: "human",
    },
  });

  const rerun = helpers.buildTemporalActionRequest("mm:workflow-123", "rerun");
  assert.strictEqual(rerun.request.url, "/api/executions/mm%3Aworkflow-123/update");
  assert.deepStrictEqual(JSON.parse(rerun.request.options.body), {
    updateName: "RequestRerun",
  });

  const cancel = helpers.buildTemporalActionRequest("mm:workflow-123", "cancel");
  assert.strictEqual(cancel.request.url, "/api/executions/mm%3Aworkflow-123/cancel");
  assert.deepStrictEqual(JSON.parse(cancel.request.options.body), {
    graceful: true,
    reason: "Cancelled from task dashboard",
  });
})();

(function testResolveTemporalActionResultMessageUsesAcceptedPayloadAndRejectsFalse() {
  assert.strictEqual(
    helpers.resolveTemporalActionResultMessage(
      { successMessage: "Task inputs updated." },
      {
        accepted: true,
        message: "Update accepted and will be applied at the next safe point.",
      },
    ),
    "Update accepted and will be applied at the next safe point.",
  );

  assert.throws(
    () =>
      helpers.resolveTemporalActionResultMessage(
        { successMessage: "Task inputs updated." },
        {
          accepted: false,
          message: "Workflow is in a terminal state and no longer accepts updates.",
        },
      ),
    /no longer accepts updates/i,
  );
})();

(function testBuildTemporalArtifactCreatePayloadIncludesExecutionLink() {
  const payload = helpers.buildTemporalArtifactCreatePayload(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-123",
      runId: "run-latest",
    },
    {
      linkType: "input.plan",
      label: "Updated plan",
      contentType: "text/markdown",
      sizeBytes: 256,
      metadata: { source: "dashboard" },
    },
  );

  assert.strictEqual(
    JSON.stringify(payload.link),
    JSON.stringify({
      namespace: "moonmind",
      workflow_id: "mm:workflow-123",
      run_id: "run-latest",
      link_type: "input.plan",
      label: "Updated plan",
    }),
  );
  assert.strictEqual(payload.content_type, "text/markdown");
  assert.strictEqual(payload.size_bytes, 256);
})();

(function testBuildTemporalArtifactEditUpdatePayloadRequiresNewReference() {
  assert.throws(
    () =>
      helpers.buildTemporalArtifactEditUpdatePayload(
        { artifact_id: "art_same" },
        { artifact_id: "art_same" },
      ),
    /new artifact reference/i,
  );

  const payload = helpers.buildTemporalArtifactEditUpdatePayload(
    { artifact_id: "art_old" },
    { artifact_id: "art_new" },
    { parametersPatch: { priority: "high" } },
  );
  assert.strictEqual(
    JSON.stringify(payload),
    JSON.stringify({
      updateName: "UpdateInputs",
      inputArtifactRef: { artifact_id: "art_new" },
      parametersPatch: { priority: "high" },
    }),
  );
})();

(function testResolveTemporalArtifactPresentationPrefersPreviewWhenRawRestricted() {
  const presentation = helpers.resolveTemporalArtifactPresentation({
    artifact_id: "art_raw",
    content_type: "application/json",
    size_bytes: 2048,
    status: "complete",
    raw_access_allowed: false,
    preview_artifact_ref: {
      artifact_id: "art_preview",
      content_type: "text/plain",
      size_bytes: 128,
    },
    default_read_ref: {
      artifact_id: "art_preview",
      content_type: "text/plain",
      size_bytes: 128,
    },
    links: [{ link_type: "output.primary", label: "Final output" }],
  });

  assert.strictEqual(presentation.artifactLabel, "Final output");
  assert.strictEqual(presentation.linkType, "output.primary");
  assert.strictEqual(presentation.contentType, "text/plain");
  assert.strictEqual(presentation.size, 128);
  assert.strictEqual(
    JSON.stringify(presentation.actions),
    JSON.stringify([
      {
        artifactId: "art_preview",
        label: "Open preview",
        variant: "preview",
      },
    ]),
  );
  assert.strictEqual(presentation.accessNotes.includes("Preview available"), true);
  assert.strictEqual(presentation.accessNotes.includes("Raw restricted"), true);
})();

(function testResolveTemporalArtifactPresentationBlocksRestrictedRawWithoutPreview() {
  const presentation = helpers.resolveTemporalArtifactPresentation({
    artifact_id: "art_sensitive",
    content_type: "application/pdf",
    size_bytes: 4096,
    status: "complete",
    raw_access_allowed: false,
    default_read_ref: {
      artifact_id: "art_sensitive",
      content_type: "application/pdf",
      size_bytes: 4096,
    },
    links: [{ link_type: "input.plan" }],
  });

  assert.strictEqual(JSON.stringify(presentation.actions), JSON.stringify([]));
  assert.strictEqual(presentation.accessNotes.includes("Raw restricted"), true);
  assert.strictEqual(presentation.accessNotes.includes("No safe preview"), true);
})();

(function testResolveTemporalArtifactPresentationAllowsRawDownloadWithoutPreview() {
  const presentation = helpers.resolveTemporalArtifactPresentation({
    artifact_id: "art_public",
    content_type: "text/markdown",
    size_bytes: 512,
    status: "complete",
    raw_access_allowed: true,
    links: [{ link_type: "output.summary", label: "Summary" }],
  });

  assert.strictEqual(
    JSON.stringify(presentation.actions),
    JSON.stringify([
      {
        artifactId: "art_public",
        label: "Download",
        variant: "download",
      },
    ]),
  );
  assert.strictEqual(presentation.accessNotes.includes("Raw restricted"), false);
})();

(async function testTemporalArtifactHelpersUseExpectedArtifactEndpoints() {
  const calls = [];
  context.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      text: async () =>
        JSON.stringify({
          artifact_id: "art_created",
          ok: true,
        }),
    };
  };

  await helpers.createTemporalArtifactPlaceholder(
    {
      namespace: "moonmind",
      workflowId: "mm:workflow-999",
      runId: "run-create",
    },
    {
      linkType: "output.primary",
      contentType: "application/json",
    },
  );
  await helpers.uploadTemporalArtifactContent("art_created", "payload", "text/plain");
  await helpers.completeTemporalArtifactUpload("art_created", [{ part_number: 1, etag: "abc" }]);
  await helpers.fetchTemporalArtifactMetadata("art_created", true);

  assert.strictEqual(
    JSON.stringify(calls.map((call) => call.url)),
    JSON.stringify([
      "/api/artifacts",
      "/api/artifacts/art_created/content",
      "/api/artifacts/art_created/complete",
      "/api/artifacts/art_created?include_download=true",
    ]),
  );
})().catch((error) => {
  throw error;
});
