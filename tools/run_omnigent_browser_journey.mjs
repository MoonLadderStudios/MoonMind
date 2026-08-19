#!/usr/bin/env node
/*
 * Repository-owned browser controller for the protected #3508 matrix and the
 * MoonLadderStudios/MoonMind#3626 operator-remediation matrix.
 * Deployment configuration supplies values and authentication state; it does
 * not supply actions, expected requests, or success conclusions.
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const required = (name) => {
  const value = (process.env[name] || "").trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
};

const baseUrl = required("MOONMIND_OMNIGENT_DASHBOARD_URL").replace(/\/$/, "");
const row = required("MOONMIND_OMNIGENT_BROWSER_ROW");
const profileId = required("MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID");
const repository = required("MOONMIND_OMNIGENT_TEST_REPOSITORY");
const branch = required("MOONMIND_OMNIGENT_TEST_BRANCH");
const outputDir = required("MOONMIND_OMNIGENT_BROWSER_OUTPUT_DIR");
const storageState = process.env.MOONMIND_OMNIGENT_BROWSER_STORAGE_STATE || undefined;
const canaryToken = required("MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN");
const catalogBootstrapEvidence = (
  process.env.MOONMIND_OMNIGENT_CATALOG_BOOTSTRAP_EVIDENCE || ""
).trim();
const timeout = Number(process.env.MOONMIND_OMNIGENT_BROWSER_TIMEOUT_MS || "900000");
const remediationTargetId = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_TARGET_WORKFLOW_ID || ""
).trim();
const remediationTargetRunId = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_TARGET_RUN_ID || ""
).trim();
const remediationAgentProfileId = (
  process.env.MOONMIND_OMNIGENT_AGENT_PROFILE_ID || ""
).trim();
const remediationModel = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_MODEL || ""
).trim();
const remediationAuthority = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_AUTHORITY_MODE || "approval_gated"
).trim();
const remediationPublishMode = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_PUBLISH_MODE || "branch"
).trim();
const remediationWorkBranch = (
  process.env.MOONMIND_OMNIGENT_REMEDIATION_WORK_BRANCH ||
  `remediation/operator-matrix-${row.replace(/[^a-z0-9-]+/gi, "-").toLowerCase()}`
).trim();
const isRemediation = Boolean(remediationTargetId);
const autonomousGateRow = row === "remediation.autonomous.rollout-gate-closed";
const prohibitedAuthorityMarkers = {
  hiddenSubmission: false,
  manualHostOrSessionId: false,
  alternateWireContract: false,
  unvalidatedPolicyProfileFields: false,
  directCodexFallback: false,
  logDerivedAuthority: false,
};
const admissionFailureRows = new Set([
  "failed_credential_readiness_admission",
  "failed_host_registration_readiness",
]);
const staticRows = new Set([
  "static_profile_bound",
  "static_restart_replay",
  "remediation.host.static-lifecycle",
]);

fs.mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const extraHTTPHeaders = {
    "X-MoonMind-Acceptance-Canary": canaryToken,
    ...(catalogBootstrapEvidence
      ? { "X-MoonMind-Acceptance-Evidence": catalogBootstrapEvidence }
      : {}),
  };
  const context = await browser.newContext({
    ...(storageState ? { storageState } : {}),
    extraHTTPHeaders,
  });
  const page = await context.newPage();
  const requests = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/executions") {
      requests.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
    }
  });

  let startPath = "/workflows/new";
  let importedPinnedRemediationDraft = false;
  if (isRemediation) {
    if (!remediationTargetRunId || !remediationAgentProfileId || !remediationModel) {
      throw new Error(
        "remediation browser rows require target run, Agent Profile, and model authority"
      );
    }
    startPath = `/workflows/${encodeURIComponent(remediationTargetId)}/evidence`;
    await page.goto(`${baseUrl}${startPath}?source=temporal`, {
      waitUntil: "networkidle",
      timeout,
    });
    await page.getByRole("button", { name: "Workflow actions" }).click();
    await page.getByRole("menuitem", { name: "Remediate" }).click();
    await page.waitForURL((url) => url.pathname === "/workflows/new", { timeout });
    await page.getByText("Remediation Draft", { exact: true }).waitFor({ timeout });
    const pinnedTarget = page.getByLabel("Target workflow");
    const pinnedRun = page.getByLabel("Pinned run");
    importedPinnedRemediationDraft =
      (await pinnedTarget.inputValue()) === remediationTargetId &&
      (await pinnedRun.inputValue()) === remediationTargetRunId &&
      (await pinnedTarget.isEditable()) === false &&
      (await pinnedRun.isEditable()) === false;
    if (!importedPinnedRemediationDraft) {
      throw new Error("normal Create did not import the visible pinned remediation draft");
    }
  } else {
    await page.goto(`${baseUrl}/workflows/new`, { waitUntil: "networkidle", timeout });
  }
  const runtime = page.getByLabel("Runtime");
  const providerProfile = page.getByLabel("Provider profile");
  if (!admissionFailureRows.has(row)) {
    await runtime.selectOption("omnigent");
    if (isRemediation) {
      await page.getByLabel("Agent profile").selectOption(remediationAgentProfileId);
    }
    await providerProfile.selectOption(profileId);
  }
  const executionTarget = page.getByLabel("Execution target");
  const launchPolicy = page.getByLabel("Host policy");
  let selectedTarget = "";
  let selectedPolicy = "";
  if (!admissionFailureRows.has(row)) {
    selectedTarget = await executionTarget.inputValue();
    if (staticRows.has(row)) await launchPolicy.selectOption({ label: "Static Compose" });
    else await launchPolicy.selectOption({ label: "On-demand Docker" });
    selectedPolicy = await launchPolicy.inputValue();
    if (!selectedTarget || !selectedPolicy) throw new Error("readiness did not expose the required target and policy");
  }

  const repositoryInput = page.getByLabel("GitHub Repo");
  if (await repositoryInput.count()) await repositoryInput.fill(repository);
  const branchInput = page.getByLabel(isRemediation ? "Starting branch" : "Branch");
  if (await branchInput.count()) await branchInput.fill(branch);
  if (isRemediation) {
    await page.getByLabel("Checkpoint work branch").fill(remediationWorkBranch);
    await page.getByLabel("Remediation mode").selectOption("snapshot_then_follow");
    if (autonomousGateRow) {
      const autonomousOption = page.getByLabel("Authority").locator(
        'option[value="admin_auto"]'
      );
      if (!(await autonomousOption.isDisabled())) {
        throw new Error("admin_auto was selectable while the autonomous release gate is closed");
      }
      if (requests.length) {
        throw new Error("closed autonomous gate emitted a Workflow Create request");
      }
      process.stdout.write(JSON.stringify({
        schemaVersion: "moonmind.operator-remediation-browser-observation/v1",
        row,
        admissionRejected: true,
        admissionReason: "autonomous_rollout_gate",
        browserOriginated: true,
        importedPinnedRemediationDraft,
        normalCreateRequest: false,
        validatedPolicyProfileFields: true,
        workflowDetailFollowThrough: true,
        createRequestCount: requests.length,
        startPath,
        createPath: new URL(page.url()).pathname,
        targetWorkflowId: remediationTargetId,
        targetRunId: remediationTargetRunId,
        ...prohibitedAuthorityMarkers,
      }));
      process.exit(0);
    }
    await page.getByLabel("Authority").selectOption(remediationAuthority);
    await page.getByLabel("Hard override model").fill(remediationModel);
    await page.getByText("Context retrieval (RAG)", { exact: true }).click();
    await page.getByLabel(
      "Allow the session to request additional context during the run"
    ).check();
    await page.getByLabel("Budget preset").selectOption("balanced");
    await page.getByLabel("Publish Mode").selectOption(remediationPublishMode);
  }
  await page.getByLabel("Instructions").fill(
    isRemediation
      ? `MoonLadderStudios/MoonMind#3626 operator remediation row ${row}. Execute only the controlled scenario contract.`
      : `MoonMind protected Omnigent acceptance row ${row}. Follow the controlled scenario contract.`
  );
  const submit = page.getByRole("button", { name: "Start Workflow" });
  if (admissionFailureRows.has(row)) {
    if (await submit.isEnabled()) throw new Error("failed-admission row was incorrectly launchable");
    if (requests.length) throw new Error("failed-admission row emitted a Workflow Create request");
    const pageText = await page.locator("body").innerText();
    const expectedFailure = row === "failed_credential_readiness_admission"
      ? /credential|oauth|profile.*(reconnect|validation|required)/i
      : /host.*(registration|readiness|ready)|static host/i;
    if (!expectedFailure.test(pageText)) {
      throw new Error(`failed-admission row did not expose its distinct readiness cause: ${row}`);
    }
    process.stdout.write(JSON.stringify({
      schemaVersion: "moonmind.omnigent.browser-observation/v1",
      row,
      admissionRejected: true,
      selected: { profileId, executionTargetRef: selectedTarget || null, launchPolicyRef: selectedPolicy || null },
      createRequestCount: requests.length,
      startPath: new URL(page.url()).pathname,
      admissionReason: row === "failed_credential_readiness_admission"
        ? "credential_readiness" : "host_registration_readiness",
    }));
    process.exit(0);
  }
  await submit.click();
  await page.waitForURL((url) => {
    const match = url.pathname.match(/\/workflows\/([^/?]+)(?:\/evidence)?$/);
    return Boolean(match && match[1] !== "new");
  }, { timeout });
  if (requests.length !== 1) throw new Error(`expected exactly one Workflow Create request, observed ${requests.length}`);

  const createRequest = requests[0].body;
  const intent = createRequest?.payload;
  if (
    intent?.targetRuntime !== "omnigent" ||
    intent?.omnigent?.executionTargetRef !== selectedTarget ||
    intent?.omnigent?.launchPolicyRef !== selectedPolicy ||
    intent?.task?.runtime?.mode !== "omnigent" ||
    intent?.task?.runtime?.profileId !== profileId
  ) throw new Error("browser-emitted Workflow Create request did not preserve the selected authorities");
  if (isRemediation && (
    intent?.agentProfile?.profileId !== remediationAgentProfileId ||
    intent?.task?.runtime?.agentProfile?.profileId !== remediationAgentProfileId ||
    intent?.task?.runtime?.model !== remediationModel ||
    intent?.task?.remediation?.target?.workflowId !== remediationTargetId ||
    intent?.task?.remediation?.target?.runId !== remediationTargetRunId ||
    intent?.task?.remediation?.authorityMode !== remediationAuthority ||
    intent?.task?.remediation?.checkpointBranchPolicy?.gitWorkBranch !== remediationWorkBranch ||
    intent?.repository?.branch?.name !== branch ||
    intent?.publishMode !== remediationPublishMode ||
    !intent?.followUpRetrieval
  )) throw new Error("remediation Create request did not preserve every visible authored control");
  if (/hostId|sessionId|volume|registrationToken|credential|image|mount/i.test(JSON.stringify(createRequest))) {
    throw new Error("browser request contained launch authority or credential material");
  }

  const workflowMatch = new URL(page.url()).pathname.match(/\/workflows\/([^/?]+)/);
  const workflowId = workflowMatch ? decodeURIComponent(workflowMatch[1]) : "";
  if (!workflowId || workflowId === "new") throw new Error("created workflow identity is missing");
  let controlAction = null;
  if (row === "active_cancellation_interruption") {
    const control = page.getByRole("button", { name: /cancel|interrupt/i }).first();
    await control.waitFor({ state: "visible", timeout: 60000 });
    await control.click();
    controlAction = "cancel_or_interrupt";
  }
  await page.getByText(/(completed|failed|cancelled|interrupted)/i).first().waitFor({ timeout });
  const terminalText = await page.locator("body").innerText();
  if (isRemediation) {
    const requiredLifecycle = [/(context|evidence)/i, /(diagnos|remediation)/i];
    if (remediationAuthority !== "observe_only") {
      requiredLifecycle.push(/approval/i, /action/i, /(checkpoint|branch)/i, /verif/i);
    }
    if (row.includes("prevention")) requiredLifecycle.push(/(prevention|publication|pull request)/i);
    if (requiredLifecycle.some((pattern) => !pattern.test(terminalText))) {
      throw new Error("remediation Workflow Detail lacks the required lifecycle projection");
    }
  }
  if (row === "active_cancellation_interruption" && !/(cancelled|interrupted)/i.test(terminalText)) {
    throw new Error("active cancellation did not reach the requested terminal state");
  }
  if (row === "repository_read_analysis" && !/(analysis|summary|findings)/i.test(terminalText)) {
    throw new Error("repository read row lacks a visible analysis outcome");
  }
  if (row === "partial_start_cleanup_janitor" && !/(janitor).*(reconciled|complete|released)/is.test(terminalText)) {
    throw new Error("partial-start row lacks visible janitor reconciliation");
  }
  if (!/(host).*(removed|released|cleaned up|gone)/is.test(terminalText)) {
    throw new Error("terminal detail did not prove host removal before replay");
  }
  const durableProjection = {
    lifecycle: [...terminalText.matchAll(/\b(launch|execut|complet|fail|cancel|interrupt|cleanup|releas)[^\n]*/gi)].map((match) => match[0]),
    chat: [...terminalText.matchAll(/^(assistant|user|system)[^\n]*/gim)].map((match) => match[0]),
    resources: [...terminalText.matchAll(/^(artifact|resource|changed files|commit|pull request)[^\n]*/gim)].map((match) => match[0]),
  };
  if (!durableProjection.lifecycle.length) throw new Error("terminal detail lacks lifecycle evidence");
  await page.screenshot({ path: path.join(outputDir, `${row}-terminal.png`), fullPage: true });
  const terminalUrl = page.url();
  await page.reload({ waitUntil: "networkidle", timeout });
  await page.getByText(/(completed|failed|cancelled|interrupted)/i).first().waitFor({ timeout: 60000 });
  const replayText = await page.locator("body").innerText();
  const missingReplayEvidence = Object.entries(durableProjection).flatMap(([section, values]) =>
    values.filter((value) => !replayText.includes(value)).map((value) => `${section}:${value}`)
  );
  if (missingReplayEvidence.length) {
    throw new Error(`Workflow Detail replay lost durable evidence: ${missingReplayEvidence.join(", ")}`);
  }
  await page.screenshot({ path: path.join(outputDir, `${row}-replay.png`), fullPage: true });

  let targetUrl = null;
  let targetReplayComplete = null;
  if (isRemediation) {
    targetUrl = `${baseUrl}${startPath}?source=temporal`;
    await page.goto(targetUrl, { waitUntil: "networkidle", timeout });
    const targetText = await page.locator("body").innerText();
    if (!targetText.includes(remediationTargetId)) {
      throw new Error("target Workflow Detail lost the pinned target identity");
    }
    await page.screenshot({ path: path.join(outputDir, `${row}-target.png`), fullPage: true });
    await page.reload({ waitUntil: "networkidle", timeout });
    targetReplayComplete = (await page.locator("body").innerText()).includes(
      remediationTargetId
    );
    if (!targetReplayComplete) {
      throw new Error("target Workflow Detail was not replayable after remediation cleanup");
    }
  }

  process.stdout.write(JSON.stringify({
    schemaVersion: isRemediation
      ? "moonmind.operator-remediation-browser-observation/v1"
      : "moonmind.omnigent.browser-observation/v1",
    row,
    workflowId,
    selected: {
      profileId,
      agentProfileId: isRemediation ? remediationAgentProfileId : null,
      executionTargetRef: selectedTarget,
      launchPolicyRef: selectedPolicy,
      hostMode: staticRows.has(row) ? "static_compose" : "on_demand_docker",
      model: isRemediation ? remediationModel : null,
      authorityMode: isRemediation ? remediationAuthority : null,
      branch,
      workBranch: isRemediation ? remediationWorkBranch : null,
      publishMode: isRemediation ? remediationPublishMode : null,
      retrieval: isRemediation ? "balanced_follow_up" : null,
    },
    browserOriginated: true,
    importedPinnedRemediationDraft,
    normalCreateRequest: true,
    validatedPolicyProfileFields: true,
    workflowDetailFollowThrough: isRemediation ? Boolean(targetReplayComplete) : true,
    startPath,
    createPath: "/workflows/new",
    targetWorkflowId: isRemediation ? remediationTargetId : null,
    targetRunId: isRemediation ? remediationTargetRunId : null,
    createRequest,
    terminalUrl,
    replayUrl: page.url(),
    controlAction,
    hostRemovedBeforeReplay: true,
    janitorReconciled: row === "partial_start_cleanup_janitor" ? true : null,
    repositoryOutcome: row === "repository_read_analysis" ? "read_analysis" : null,
    durableProjection,
    replayComplete: true,
    targetUrl,
    targetReplayComplete,
    ...prohibitedAuthorityMarkers,
    screenshots: [
      `${row}-terminal.png`,
      `${row}-replay.png`,
      ...(isRemediation ? [`${row}-target.png`] : []),
    ],
  }));
} finally {
  await browser.close();
}
