#!/usr/bin/env node
/*
 * Repository-owned browser controller for the protected #3508 matrix.
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

/*
 * Tier-1 exact-artifact UI probe (MoonLadderStudios/MoonMind#3710).
 *
 * Credential-free, self-contained mode used by the exact-artifact gate to prove
 * the compiled native UI baked into the deployable image consumes the hosted
 * bootstrap and sends no direct-upstream traffic in hosted mode: no root
 * `/v1/*` request (which would bypass the `/api/omnigent` mount) and no
 * cross-origin upstream request. It must run without the credentialed journey's
 * environment, so it short-circuits before those `required(...)` reads. On a
 * violation it prints a `root-v1-request` line and exits non-zero so the gate
 * fails closed; on success it prints a summary that never contains that marker.
 */
/*
 * The image's compiled dashboard bundle is served from this mount
 * (`api_service/main.py` mounts the Vite output at this prefix). A request under
 * it proves the browser fetched the compiled UI baked into the deployable image.
 */
const HOSTED_BUNDLE_PATH_PREFIX = "/static/workflow_console/dist/";

async function runHostedNetworkCapture(argv) {
  const urlFlagIndex = argv.indexOf("--hosted-url");
  const hostedUrl = (
    (urlFlagIndex >= 0 ? argv[urlFlagIndex + 1] : "") ||
    process.env.MOONMIND_OMNIGENT_DASHBOARD_URL ||
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
  const hostedOrigin = new URL(hostedUrl).origin;
  const captureTimeout = Number(
    process.env.MOONMIND_OMNIGENT_HOSTED_CAPTURE_TIMEOUT_MS || "60000",
  );
  const browser = await chromium.launch({ headless: true });
  const requests = [];
  const rootV1Requests = [];
  const directUpstreamRequests = [];
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on("request", (request) => {
      let parsed;
      try {
        parsed = new URL(request.url());
      } catch {
        return;
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return;
      requests.push({ origin: parsed.origin, pathname: parsed.pathname });
      if (parsed.origin === hostedOrigin && parsed.pathname.startsWith("/v1/")) {
        rootV1Requests.push(`${parsed.pathname}`);
      } else if (parsed.origin !== hostedOrigin) {
        directUpstreamRequests.push(`${parsed.origin}${parsed.pathname}`);
      }
    });
    const response = await page.goto(hostedUrl, {
      waitUntil: "networkidle",
      timeout: captureTimeout,
    });
    // Allow any deferred client-side bootstrap requests to settle.
    await page.waitForTimeout(1000);
    const documentOk =
      Boolean(response) &&
      response.status() < 400 &&
      new URL(response.url()).origin === hostedOrigin;
    /*
     * Bootstrap consumption is asserted from observable state, not from the
     * presence of a same-origin request. The top-level navigation is itself
     * same-origin, so counting any non-`/v1/` request would let a compiled UI
     * that never loads — or never reads its boot payload — report success and
     * hide exactly the bootstrap regression this gate exists to catch.
     *
     * Two concrete facts are required:
     *   1. the compiled bundle was actually fetched from the deployable origin
     *      (a request under the image's hashed asset mount), and
     *   2. the application executed and rendered from the injected boot payload
     *      (the boot script parses as JSON and the app root has real content).
     */
    const bundleRequested = requests.some(
      (r) =>
        r.origin === hostedOrigin &&
        r.pathname.startsWith(HOSTED_BUNDLE_PATH_PREFIX),
    );
    const bootstrapState = documentOk
      ? await page.evaluate(() => {
          const bootScript = document.querySelector("#moonmind-ui-boot");
          let bootPayloadParsed = false;
          if (bootScript && bootScript.textContent) {
            try {
              bootPayloadParsed =
                typeof JSON.parse(bootScript.textContent) === "object";
            } catch {
              bootPayloadParsed = false;
            }
          }
          const appRoot = document.querySelector("#dashboard-app-root");
          return {
            bootPayloadPresent: Boolean(bootScript),
            bootPayloadParsed,
            appRendered: Boolean(appRoot && appRoot.children.length > 0),
          };
        })
      : { bootPayloadPresent: false, bootPayloadParsed: false, appRendered: false };
    const consumedHostedBootstrap =
      documentOk &&
      bundleRequested &&
      bootstrapState.bootPayloadParsed &&
      bootstrapState.appRendered;
    if (rootV1Requests.length > 0) {
      // The literal marker below is what the Tier-1 probe scans stdout for.
      console.log(
        `root-v1-request observed in hosted mode: ${rootV1Requests
          .slice(0, 10)
          .join(", ")}`,
      );
      return 1;
    }
    if (directUpstreamRequests.length > 0) {
      console.log(
        `direct-upstream request observed in hosted mode: ${directUpstreamRequests
          .slice(0, 10)
          .join(", ")}`,
      );
      return 1;
    }
    if (!consumedHostedBootstrap) {
      console.log(
        "hosted UI did not consume the hosted bootstrap from the deployable " +
          `origin (document=${documentOk} bundleRequested=${bundleRequested} ` +
          `bootPayload=${bootstrapState.bootPayloadParsed} ` +
          `appRendered=${bootstrapState.appRendered})`,
      );
      return 1;
    }
    console.log(
      `hosted UI capture ok: compiled bundle served from ${HOSTED_BUNDLE_PATH_PREFIX}, ` +
        `boot payload parsed and app rendered, ${requests.length} same-origin ` +
        "requests, no direct-upstream calls",
    );
    return 0;
  } catch (error) {
    console.log(`hosted UI capture failed: ${String(error).slice(0, 200)}`);
    return 1;
  } finally {
    await browser.close();
  }
}

const cliArgs = process.argv.slice(2);
if (cliArgs.includes("--hosted-network-capture")) {
  process.exit(await runHostedNetworkCapture(cliArgs));
}

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
const admissionFailureRows = new Set([
  "failed_credential_readiness_admission",
  "failed_host_registration_readiness",
]);
const staticRows = new Set([
  "static_profile_bound",
  "static_restart_replay",
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

  const startPath = "/workflows/new";
  await page.goto(`${baseUrl}${startPath}`, { waitUntil: "networkidle", timeout });
  const runtime = page.getByLabel("Runtime");
  const providerProfile = page.getByLabel("Provider profile");
  if (!admissionFailureRows.has(row)) {
    await runtime.selectOption("omnigent");
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
  const branchInput = page.getByLabel("Branch");
  if (await branchInput.count()) await branchInput.fill(branch);
  await page.getByLabel("Instructions").fill(
    `MoonMind protected Omnigent acceptance row ${row}. Follow the controlled scenario contract.`
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

  process.stdout.write(JSON.stringify({
    schemaVersion: "moonmind.omnigent.browser-observation/v1",
    row,
    workflowId,
    selected: {
      profileId,
      executionTargetRef: selectedTarget,
      launchPolicyRef: selectedPolicy,
      hostMode: staticRows.has(row) ? "static_compose" : "on_demand_docker",
      branch,
    },
    browserOriginated: true,
    normalCreateRequest: true,
    startPath,
    createPath: "/workflows/new",
    createRequest,
    terminalUrl,
    replayUrl: page.url(),
    controlAction,
    hostRemovedBeforeReplay: true,
    janitorReconciled: row === "partial_start_cleanup_janitor" ? true : null,
    repositoryOutcome: row === "repository_read_analysis" ? "read_analysis" : null,
    durableProjection,
    replayComplete: true,
    screenshots: [`${row}-terminal.png`, `${row}-replay.png`],
  }));
} finally {
  await browser.close();
}
