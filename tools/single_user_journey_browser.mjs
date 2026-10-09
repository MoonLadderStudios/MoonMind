#!/usr/bin/env node
/*
 * MoonLadderStudios/MoonMind#4356: drive the dashboard compiled into the
 * image under test on the work a journey phase saved.
 *
 * Usage: node tools/single_user_journey_browser.mjs <api-base> <state-file> [mode]
 *
 *   view    (default) open the new-workflow page, each saved workflow, the
 *           recurring schedule, and settings.
 *   cancel  cancel each execution marked for cancellation from its workflow
 *           detail page, as an operator would, and wait for the page to show
 *           it canceled. The request time is written back to the state file;
 *           `single_user_journey_checks.py canceled` then confirms it came
 *           before the deferred start and that the API reports canceled.
 *
 *   controller-submit
 *           MoonLadderStudios/MoonMind#4502: on Settings Operations with an
 *           installed controller, submit the recorded target as an operator
 *           would. The controller answers with its own operation (no
 *           workflow); the card shows the requested target, the observed
 *           installed state, the original error, the controller logs, and
 *           Retry.
 *   controller-reconnect
 *           after the journey replaced the API: a fresh page load reconnects
 *           to the same operation without submitting again, and Retry asks
 *           the controller for a fresh attempt.
 *
 * The state file is written by tools/single_user_journey_checks.py. Every
 * page loads through the deployment's ordinary access path (loopback on the
 * default install) with no seeded person or injected session. Any missing
 * element, uncaught page error, or failed navigation exits non-zero.
 */
import { chromium } from "playwright";
import fs from "node:fs";

const [apiBase, stateFile, mode = "view"] = process.argv.slice(2);
const MODES = ["view", "cancel", "controller-submit", "controller-reconnect"];
if (!apiBase || !stateFile || !MODES.includes(mode)) {
  console.error(`usage: single_user_journey_browser.mjs <api-base> <state-file> [${MODES.join("|")}]`);
  process.exit(2);
}
const writesState = mode !== "view";
const base = apiBase.replace(/\/$/, "");
const state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
const timeout = Number(process.env.SINGLE_USER_JOURNEY_BROWSER_TIMEOUT_MS || "60000");

const browser = await chromium.launch({ headless: true });
const pageErrors = [];
let failed = false;
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(timeout);
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  const visit = async (path, check) => {
    const response = await page.goto(base + path, { waitUntil: "domcontentloaded" });
    if (!response || !response.ok()) {
      throw new Error(`GET ${path} returned ${response ? response.status() : "no response"}`);
    }
    await check();
    console.log(`single-user-journey-browser: ${path} ok`);
  };

  const workflowPath = (execution) => `/workflows/${encodeURIComponent(execution.workflowId)}`;

  if (mode === "view") {
    await visit("/workflows/new", () =>
      page.getByLabel("Instructions").first().waitFor({ state: "visible" }),
    );
    for (const execution of state.executions || []) {
      await visit(workflowPath(execution), () =>
        page.getByText(execution.title).first().waitFor({ state: "visible" }),
      );
    }
    if (state.recurring) {
      await visit(`/schedules/${encodeURIComponent(state.recurring.definitionId)}`, () =>
        page.getByText(state.recurring.name).first().waitFor({ state: "visible" }),
      );
    }
    await visit("/settings", () =>
      page.locator("main, #root, [data-dashboard-root]").first().waitFor({ state: "visible" }),
    );
  } else if (mode.startsWith("controller-")) {
    await controllerJourney(page, visit, mode === "controller-submit");
  } else {
    const targets = (state.executions || []).filter((execution) => execution.cancel);
    if (!targets.length) {
      throw new Error("no executions recorded to cancel");
    }
    for (const execution of targets) {
      await visit(workflowPath(execution), async () => {
        await page.getByText(execution.title).first().waitFor({ state: "visible" });
        await page.locator(".toolbar").getByRole("button", { name: "Workflow actions" }).click();
        const cancel = page
          .getByRole("menu", { name: "Workflow actions" })
          .getByRole("menuitem", { name: "Cancel", exact: true });
        await cancel.waitFor({ state: "visible" });
        execution.cancelRequestedAt = new Date().toISOString();
        await cancel.click();
        await page.getByText("Cancellation requested.").first().waitFor({ state: "visible" });
        // The detail page polls until the workflow is terminal.
        await page
          .locator(".toolbar")
          .getByText("Canceled", { exact: true })
          .first()
          .waitFor({ state: "visible", timeout: Math.max(timeout, 180000) });
      });
      console.log(`single-user-journey-browser: ${execution.workflowId} canceled from the dashboard`);
    }
  }
  if (pageErrors.length) {
    throw new Error(`uncaught page errors: ${pageErrors.join(" | ")}`);
  }
} catch (error) {
  failed = true;
  console.error(`single-user-journey-browser: FAILED: ${error.message || error}`);
} finally {
  await browser.close();
  if (writesState) {
    fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));
  }
}
process.exit(failed ? 1 : 0);

async function controllerJourney(page, visit, submit) {
  const record = state.controller;
  if (!record?.reference) {
    throw new Error("no controller journey state recorded (run controller_absent first)");
  }
  const dialogs = [];
  page.on("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.accept();
  });
  const updatePath = "/api/v1/operations/deployment/update";
  let submissions = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === updatePath) {
      submissions += 1;
    }
  });
  const card = page.getByRole("region", { name: "MoonMind update" });
  const target = `${record.repository}:${record.reference}`;

  const showsFailedOperation = async (operationId) => {
    const label = `Operation ${operationId}`;
    await card.getByText(label, { exact: true }).waitFor({ state: "visible" });
    // The operation label shares its parent with the requested image,
    // installed image, original error, and controller logs.
    const details = card.getByText(label, { exact: true }).locator("..");
    await details.getByText(target, { exact: true }).waitFor({ state: "visible" });
    await details.getByText("not confirmed", { exact: true }).waitFor({ state: "visible" });
    await details.getByText(/^Error: attempt 1: /).waitFor({ state: "visible" });
    await details.getByText("Controller logs", { exact: true }).click();
    await details.getByText(/^Attempt 1 · /).waitFor({ state: "visible" });
    await card.getByText("Running image", { exact: true }).waitFor({ state: "visible" });
    const retry = card.getByRole("button", { name: "Retry operation" });
    await retry.waitFor({ state: "visible" });
    if (await retry.isDisabled()) {
      throw new Error(`Retry is not offered for failed operation ${operationId}`);
    }
    const listed = await card.getByText(/^Operation /).allTextContents();
    if (listed.length !== 1) {
      throw new Error(`expected one controller operation, the card lists ${listed.join(", ")}`);
    }
  };

  await visit("/settings/operations", async () => {
    await card
      .getByText("Updates run in the standalone deployment controller", { exact: false })
      .waitFor({ state: "visible" });
    if (submit) {
      await card.getByLabel("Update to").fill(record.reference);
      const answered = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === updatePath &&
          response.request().method() === "POST",
      );
      await card.getByRole("button", { name: "Update MoonMind" }).click();
      const response = await answered;
      record.submission = await response.json();
      if (!response.ok()) {
        throw new Error(`update was refused (${response.status()}): ${JSON.stringify(record.submission)}`);
      }
      record.operationId = record.submission.operationId;
      await card
        .getByText(`Deployment update accepted by the controller: operation ${record.operationId}`, {
          exact: false,
        })
        .waitFor({ state: "visible" });
      await showsFailedOperation(record.operationId);
      if (!dialogs.some((message) => message.startsWith("Update MoonMind?"))) {
        throw new Error("the update was submitted without the operator confirmation");
      }
    } else {
      if (!record.operationId) {
        throw new Error("no controller operation recorded to reconnect to");
      }
      await showsFailedOperation(record.operationId);
      record.reloadedOperationId = record.operationId;
      const retryPath = `/api/v1/operations/deployment/operations/${encodeURIComponent(record.operationId)}/retry`;
      const answered = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === retryPath && response.request().method() === "POST",
      );
      await card.getByRole("button", { name: "Retry operation" }).click();
      const response = await answered;
      record.retry = await response.json();
      if (!response.ok()) {
        throw new Error(`retry was refused (${response.status()}): ${JSON.stringify(record.retry)}`);
      }
      await card
        .getByText(`Deployment retry accepted by the controller: operation ${record.operationId}`, {
          exact: false,
        })
        .waitFor({ state: "visible" });
      if (!dialogs.some((message) => message.startsWith("Retry deployment operation?"))) {
        throw new Error("the retry was requested without the operator confirmation");
      }
    }
  });
  record.dashboardSubmissions = (submit ? 0 : record.dashboardSubmissions || 0) + submissions;
}
