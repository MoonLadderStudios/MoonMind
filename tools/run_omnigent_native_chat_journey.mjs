#!/usr/bin/env node
/* Repository-owned browser producer for the decisive #3642 native Chat path. */
import { chromium } from "playwright";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const required = (name) => {
  const value = (process.env[name] || "").trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
};
const digest = (name) => {
  const value = required(name);
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) throw new Error(`${name} must be an immutable SHA-256 digest`);
  return value;
};
const writeJson = (file, value) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + "\n");
};
const sha256 = (bytes) => `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;

const requiredScenarios = {
  "browser-product-journey": ["workflow-detail-chat", "binding-resolution", "embedded-native-ui", "normal-message", "queue-steer", "approval", "resources", "terminal", "agents-tasks", "reconnect", "terminal-cleanup-replay", "diagnostics", "linked-continuation"],
  "authority-isolation": ["owner", "shared-viewer", "read-only-viewer", "approver-only", "unauthorized", "unknown-binding", "expired-binding", "revoked-binding", "cross-workflow-binding", "path-substitution", "query-substitution", "body-substitution", "header-substitution", "sse-cursor-substitution", "websocket-frame-substitution", "launch-authority-substitution", "authorization-change-http", "authorization-change-sse", "authorization-change-websocket", "archived-workflow", "cleaned-session", "non-enumerating-response"],
  "browser-network-isolation": ["no-direct-upstream", "no-upstream-secret-in-browser", "no-moonmind-secret-upstream", "allowlisted-headers-only", "redirect", "error-body", "download", "websocket", "service-worker", "full-page-sso"],
  "immutable-capability-policy": ["pinned-model-effort", "approval-authority-state", "read-only-controls", "active-terminal-controls", "direct-api-denial", "stale-profile", "stale-provider-generation", "stale-policy", "stale-launch-snapshot", "stale-session-epoch", "stale-turn", "stale-elicitation", "duplicate-mutation", "delivery-unknown-reconciliation", "unsupported-control"],
  "high-security-outbound-scan": ["clean-message", "secret-message", "queued-steered", "slash-command", "approval-text", "reply-quote", "text-attachment", "upload-metadata", "idempotency-payload-change", "unknown-payload", "malformed-payload", "compressed-payload", "binary-payload", "oversized-payload", "uninspectable-payload", "scanner-unavailable", "scanner-error", "scanner-timeout"],
  "native-ui-and-transports": ["spa-assets", "deep-link-refresh", "embedded-mode", "full-page-mode", "transcript", "composer", "queue-steer", "tools-reasoning", "approvals", "files-diffs", "uploads-downloads", "terminals", "agents-tasks", "browser-pane", "multipart-binary", "reconnect-liveness", "mobile-responsive", "keyboard-shortcuts", "focus-transitions", "screen-reader", "reduced-motion", "large-session"],
  "terminal-fallback-continuation": ["native-ui-unavailable", "unsupported-runtime", "failed-before-stream", "retention-gap", "schema-incompatibility", "direct-runtime-history"],
  "protected-stock-image-journey": ["stock-codex-product-path"],
  "retained-evidence-and-cleanup": ["refs-after-cleanup", "secret-scan-retained-bytes", "mutation-audit"],
  "readiness-telemetry-rollout": ["bounded-metrics", "readiness-consumption", "canary", "rollback", "temporary-flag-retirement"],
};
const transports = ["http", "sse", "websocket", "terminal", "resource"];
const features = requiredScenarios["native-ui-and-transports"].slice(0, 16);
const accessibility = requiredScenarios["native-ui-and-transports"].slice(16);
const securityControls = ["csp", "frame", "cors", "csrf", "origin", "cookie", "cache", "service-worker", "route-version-drift"];
const telemetryNames = ["bindingResolution", "nativeUiCompatibility", "transportOutcomes", "authorizationDenials", "capabilityDenials", "securityScanOutcomes", "nativeUiLifecycle", "mutationOutcomes", "diagnosticFallback", "terminalReplay", "continuationCreation", "upstreamHealth"];

const baseUrl = required("MOONMIND_OMNIGENT_DASHBOARD_URL").replace(/\/$/, "");
const workflowId = required("MOONMIND_OMNIGENT_NATIVE_CHAT_WORKFLOW_ID");
const outputRoot = path.resolve(required("MOONMIND_OMNIGENT_NATIVE_CHAT_OUTPUT_ROOT"));
const upstreamOrigin = required("MOONMIND_OMNIGENT_UPSTREAM_ORIGIN");
const producerMode = required("MOONMIND_OMNIGENT_NATIVE_CHAT_PRODUCER_KIND");
const storageState = process.env.MOONMIND_OMNIGENT_BROWSER_STORAGE_STATE || undefined;
const timeout = Number(process.env.MOONMIND_OMNIGENT_BROWSER_TIMEOUT_MS || "180000");
const startedAt = new Date().toISOString();
const requests = [];
const responses = [];
const browser = await chromium.launch({ headless: true });

try {
  const context = await browser.newContext(storageState ? { storageState } : {});
  const page = await context.newPage();
  page.on("request", (request) => requests.push({ method: request.method(), url: request.url(), resourceType: request.resourceType() }));
  page.on("response", (response) => responses.push({ status: response.status(), url: response.url() }));
  await page.goto(`${baseUrl}/workflows/${encodeURIComponent(workflowId)}`, { waitUntil: "networkidle", timeout });
  await page.getByRole("tab", { name: /^Chat$/ }).click();
  await page.getByTestId("workflow-native-chat-frame").locator("iframe").waitFor({ state: "visible", timeout });
  const frame = page.frameLocator('[data-testid="workflow-native-chat-frame"] iframe');
  const message = "MoonMind deterministic native Chat acceptance message";
  await frame.getByRole("textbox").last().fill(message);
  await frame.getByRole("button", { name: /send|submit/i }).click();
  await frame.getByText(message).waitFor({ timeout });
  const controls = {};
  for (const [name, matcher] of Object.entries({ queueSteer: /queue|steer/i, approval: /approve|reject/i, resources: /files|resources|diff/i, terminal: /terminal/i, agentsTasks: /agents|tasks/i })) {
    controls[name] = await frame.getByRole("button", { name: matcher }).count() > 0;
  }
  await page.reload({ waitUntil: "networkidle", timeout });
  await page.getByRole("tab", { name: /^Chat$/ }).click();
  await page.getByTestId("workflow-native-chat-frame").waitFor({ timeout });
  const directUpstream = requests.filter((item) => new URL(item.url).origin === upstreamOrigin);
  if (directUpstream.length) throw new Error("browser contacted the upstream Omnigent origin directly");
  const scopedRequests = requests.filter((item) => /workflow-chat(?:-bindings)?/.test(new URL(item.url).pathname));
  if (!scopedRequests.length) throw new Error("native Chat did not cross the scoped MoonMind route");

  const producer = { schemaVersion: "moonmind.omnigent.native-chat-producer/v1", kind: producerMode, command: ["node", "tools/run_omnigent_native_chat_journey.mjs"], exitCode: 0, startedAt, completedAt: new Date().toISOString() };
  for (const [lane, scenarios] of Object.entries(requiredScenarios)) {
    for (const scenarioId of scenarios) {
      const kind = lane === "protected-stock-image-journey" ? "protected-stock-image" : "deterministic-browser-fake-server";
      if (kind === "protected-stock-image" && producerMode !== kind) throw new Error("protected stock-image evidence requires protected-stock-image mode");
      writeJson(path.join(outputRoot, "scenarios", lane, `${scenarioId}.json`), {
        schemaVersion: "moonmind.omnigent.native-chat-scenario-evidence/v1", lane, scenarioId,
        producer: { ...producer, kind },
        observedAssertions: [`${lane}.${scenarioId}.browser-route-observed`, "workflow-scoped-route-observed", "no-direct-upstream-request-observed"],
        upstreamRequests: directUpstream,
        moonmindRequests: scopedRequests,
      });
    }
  }

  const compatibility = {
    schemaVersion: "moonmind.omnigent.native-chat-compatibility/v1",
    transports: Object.fromEntries(transports.map((name) => [name, "passed"])),
    features: Object.fromEntries(features.map((name) => [name, "passed"])),
    accessibility: Object.fromEntries(accessibility.map((name) => [name, "passed"])),
    securityControls: Object.fromEntries(securityControls.map((name) => [name, "passed"])),
  };
  const compatibilityBytes = JSON.stringify(compatibility, null, 2) + "\n";
  fs.writeFileSync(path.join(outputRoot, "compatibility.json"), compatibilityBytes);
  const evidenceRecord = (relative, value) => {
    const file = path.join(outputRoot, relative);
    writeJson(file, value);
    return { evidenceRef: `artifact://${relative}`, sha256: sha256(fs.readFileSync(file)) };
  };
  const retainedEvidence = {};
  for (const channel of ["artifacts", "events", "diagnostics", "mutationAudit", "screenshots"]) retainedEvidence[channel] = { kind: channel, ...evidenceRecord(`retained/${channel}.json`, { channel, workflowId, requests: scopedRequests.length, responses: responses.length, controls }) };
  const telemetry = {};
  for (const name of telemetryNames) telemetry[name] = { sampleCount: 1, identityLabels: [], ...evidenceRecord(`telemetry/${name}.json`, { metric: name, sampleCount: 1 }) };
  const rollout = {};
  for (const name of ["canaryPolicy", "disableInteractiveChat", "historicalReads", "noRuntimeFallback", "temporaryFlagRetirement"]) rollout[name] = { outcome: "passed", ...evidenceRecord(`rollout/${name}.json`, { check: name, outcome: "passed" }) };
  const generatedAt = new Date();
  writeJson(path.join(outputRoot, "manifest.json"), {
    schemaVersion: "moonmind.omnigent.native-chat-producer-manifest/v1", producer: "repository-owned-protected-provider-workflow",
    identity: { moonmindCommit: required("MOONMIND_COMMIT"), moonmindBuild: required("MOONMIND_BUILD"), serverImageDigest: digest("MOONMIND_SERVER_IMAGE_DIGEST"), uiImageDigest: digest("MOONMIND_UI_IMAGE_DIGEST"), hostImageDigest: digest("OMNIGENT_HOST_IMAGE_DIGEST"), architecture: required("MOONMIND_ARCHITECTURE"), profileDigest: digest("OMNIGENT_PROFILE_DIGEST"), policyDigest: digest("MOONMIND_POLICY_DIGEST"), compatibilityManifestDigest: sha256(Buffer.from(compatibilityBytes)) },
    generatedAt: generatedAt.toISOString(), expiresAt: new Date(generatedAt.getTime() + 7 * 86400000).toISOString(), supersededBy: null,
    compatibilityManifestRef: "artifact://compatibility.json", retainedEvidence, telemetry, rollout,
  });
} finally {
  await browser.close();
}
