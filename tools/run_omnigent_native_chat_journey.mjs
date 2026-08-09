#!/usr/bin/env node
/* Repository-owned browser producer for the decisive #3642 native Chat path. */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const required = (name) => {
  const value = (process.env[name] || "").trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
};
const baseUrl = required("MOONMIND_OMNIGENT_DASHBOARD_URL").replace(/\/$/, "");
const workflowId = required("MOONMIND_OMNIGENT_NATIVE_CHAT_WORKFLOW_ID");
const output = required("MOONMIND_OMNIGENT_NATIVE_CHAT_OBSERVATION");
const upstreamOrigin = required("MOONMIND_OMNIGENT_UPSTREAM_ORIGIN");
const producerKind = process.env.MOONMIND_OMNIGENT_NATIVE_CHAT_PRODUCER_KIND ||
  "deterministic-browser-fake-server";
const storageState = process.env.MOONMIND_OMNIGENT_BROWSER_STORAGE_STATE || undefined;
const timeout = Number(process.env.MOONMIND_OMNIGENT_BROWSER_TIMEOUT_MS || "180000");

const browser = await chromium.launch({ headless: true });
const startedAt = new Date().toISOString();
try {
  const context = await browser.newContext(storageState ? { storageState } : {});
  const page = await context.newPage();
  const requests = [];
  const responses = [];
  page.on("request", (request) => requests.push({
    method: request.method(), url: request.url(), resourceType: request.resourceType(),
  }));
  page.on("response", (response) => responses.push({
    status: response.status(), url: response.url(),
  }));

  await page.goto(`${baseUrl}/workflows/${encodeURIComponent(workflowId)}`, {
    waitUntil: "networkidle", timeout,
  });
  await page.getByRole("tab", { name: /^Chat$/ }).click();
  const frameElement = page.getByTestId("workflow-native-chat-frame").locator("iframe");
  await frameElement.waitFor({ state: "visible", timeout });
  const frame = page.frameLocator('[data-testid="workflow-native-chat-frame"] iframe');
  const composer = frame.getByRole("textbox").last();
  await composer.fill("MoonMind deterministic native Chat acceptance message");
  await frame.getByRole("button", { name: /send|submit/i }).click();
  await frame.getByText(/MoonMind deterministic native Chat acceptance message/).waitFor({ timeout });

  // Exercise controls only when the pinned UI exposes them; unsupported
  // controls remain an observation, never a fabricated equivalent.
  const observedControls = {};
  for (const [name, matcher] of Object.entries({
    queueSteer: /queue|steer/i, approval: /approve|reject/i,
    resources: /files|resources|diff/i, terminal: /terminal/i,
    agentsTasks: /agents|tasks/i,
  })) {
    const control = frame.getByRole("button", { name: matcher }).first();
    observedControls[name] = await control.count() > 0;
  }
  await page.reload({ waitUntil: "networkidle", timeout });
  await page.getByRole("tab", { name: /^Chat$/ }).click();
  await page.getByTestId("workflow-native-chat-frame").waitFor({ timeout });

  const directUpstream = requests.filter((item) => new URL(item.url).origin === upstreamOrigin);
  if (directUpstream.length) throw new Error("browser contacted the upstream Omnigent origin directly");
  const scopedRequests = requests.filter((item) =>
    new URL(item.url).pathname.includes("workflow-chat") ||
    new URL(item.url).pathname.includes("workflow-chat-bindings"));
  if (!scopedRequests.length) throw new Error("native Chat did not cross the scoped MoonMind route");

  const payload = {
    schemaVersion: "moonmind.omnigent.native-chat-browser-journey/v1",
    producer: {
      schemaVersion: "moonmind.omnigent.native-chat-producer/v1",
      kind: producerKind,
      command: ["node", "tools/run_omnigent_native_chat_journey.mjs"],
      exitCode: 0, startedAt, completedAt: new Date().toISOString(),
    },
    workflowId,
    assertions: {
      workflowDetailChatOpened: true, bindingScopedIframeMounted: true,
      nativeComposerSubmitted: true, reconnectReloaded: true,
      browserDirectUpstreamRequests: directUpstream.length,
    },
    observedControls,
    moonmindRequests: scopedRequests,
    responses: responses.filter((item) => scopedRequests.some((request) => request.url === item.url)),
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(payload, null, 2) + "\n");
} finally {
  await browser.close();
}
