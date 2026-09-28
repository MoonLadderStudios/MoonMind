#!/usr/bin/env node
/*
 * MoonLadderStudios/MoonMind#4356: open the dashboard compiled into the
 * image under test and observe the work a journey phase saved.
 *
 * Usage: node tools/single_user_journey_browser.mjs <api-base> <state-file>
 *
 * The state file is written by tools/single_user_journey_checks.py. Every
 * page loads through the deployment's ordinary access path (loopback on the
 * default install) with no seeded person or injected session. Any missing
 * element, uncaught page error, or failed navigation exits non-zero.
 */
import { chromium } from "playwright";
import fs from "node:fs";

const [apiBase, stateFile] = process.argv.slice(2);
if (!apiBase || !stateFile) {
  console.error("usage: single_user_journey_browser.mjs <api-base> <state-file>");
  process.exit(2);
}
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

  await visit("/workflows/new", () =>
    page.getByLabel("Instructions").first().waitFor({ state: "visible" }),
  );
  for (const execution of state.executions || []) {
    await visit(`/workflows/${encodeURIComponent(execution.workflowId)}`, () =>
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
  if (pageErrors.length) {
    throw new Error(`uncaught page errors: ${pageErrors.join(" | ")}`);
  }
} catch (error) {
  failed = true;
  console.error(`single-user-journey-browser: FAILED: ${error.message || error}`);
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
