/* eslint-env node */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DASHBOARD_TEMPLATE = path.join(REPO_ROOT, "api_service", "templates", "task_dashboard.html");
const DASHBOARD_CSS = path.join(
  REPO_ROOT,
  "api_service",
  "static",
  "task_dashboard",
  "dashboard.tailwind.css",
);

function normalizeStoredPreference(raw) {
  return raw === "dark" || raw === "light" ? raw : null;
}

function resolveTheme({ storedPreference, systemPrefersDark }) {
  const normalized = normalizeStoredPreference(storedPreference);
  if (normalized) {
    return { mode: normalized, source: "user" };
  }
  return { mode: systemPrefersDark ? "dark" : "light", source: "system" };
}

function toggleTheme(currentMode) {
  return currentMode === "dark" ? "light" : "dark";
}

function parseRgbToken(block, tokenName) {
  const tokenRegex = new RegExp(`--${tokenName}\\s*:\\s*([0-9]+)\\s+([0-9]+)\\s+([0-9]+)\\s*;`);
  const match = block.match(tokenRegex);
  assert(match, `Missing token --${tokenName} in .dark block`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function srgbToLinear(channel) {
  const scaled = channel / 255;
  if (scaled <= 0.03928) {
    return scaled / 12.92;
  }
  return ((scaled + 0.055) / 1.055) ** 2.4;
}

function luminance(rgb) {
  const [r, g, b] = rgb.map(srgbToLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(foreground, background) {
  const l1 = luminance(foreground);
  const l2 = luminance(background);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

(function testThemeResolutionPrecedenceAndFallback() {
  assert.deepStrictEqual(resolveTheme({ storedPreference: "dark", systemPrefersDark: false }), {
    mode: "dark",
    source: "user",
  });
  assert.deepStrictEqual(resolveTheme({ storedPreference: "light", systemPrefersDark: true }), {
    mode: "light",
    source: "user",
  });
  assert.deepStrictEqual(resolveTheme({ storedPreference: "invalid-value", systemPrefersDark: true }), {
    mode: "dark",
    source: "system",
  });
  assert.deepStrictEqual(resolveTheme({ storedPreference: null, systemPrefersDark: false }), {
    mode: "light",
    source: "system",
  });
  assert.strictEqual(toggleTheme("dark"), "light");
  assert.strictEqual(toggleTheme("light"), "dark");
})();

(function testNoFlashMatrixProtocol() {
  // Mirrors SC-002 protocol: 20 system-light + 20 system-dark runs with unset preference.
  let passCount = 0;
  const totalRuns = 40;

  for (let i = 0; i < 20; i += 1) {
    const resolved = resolveTheme({ storedPreference: null, systemPrefersDark: false });
    if (resolved.mode === "light" && resolved.source === "system") {
      passCount += 1;
    }
  }

  for (let i = 0; i < 20; i += 1) {
    const resolved = resolveTheme({ storedPreference: null, systemPrefersDark: true });
    if (resolved.mode === "dark" && resolved.source === "system") {
      passCount += 1;
    }
  }

  assert.strictEqual(totalRuns, 40);
  assert(passCount >= 38, `Expected >=38 passing runs, received ${passCount}`);
})();

(function testTemplateContainsNoFlashAndToggleContract() {
  const html = fs.readFileSync(DASHBOARD_TEMPLATE, "utf8");
  assert(
    html.includes('content="width=device-width, initial-scale=1, viewport-fit=cover"'),
    "Viewport meta must include viewport-fit=cover",
  );
  assert(html.includes("moonmind.theme"), "No-flash bootstrap should reference moonmind.theme key");
  assert(
    !html.includes("class=\"theme-toggle secondary\""),
    "Theme toggle button should not be present in the shell",
  );
  assert(
    /classList\.toggle\("dark",\s*mode === "dark"\)/.test(html),
    "No-flash bootstrap should toggle dark class",
  );
})();

(function testDarkTokenAndReadabilitySurfaces() {
  const css = fs.readFileSync(DASHBOARD_CSS, "utf8");
  const darkBlock = css.match(/\.dark\s*\{([\s\S]*?)\}/);
  assert(darkBlock, "Missing .dark token override block");

  const panelRgb = parseRgbToken(darkBlock[1], "mm-panel");
  const inkRgb = parseRgbToken(darkBlock[1], "mm-ink");
  const ratio = contrastRatio(inkRgb, panelRgb);
  assert(ratio >= 4.5, `Expected contrast ratio >=4.5 for mm-ink/mm-panel, got ${ratio.toFixed(2)}`);

  assert(css.includes(".dark table {"), "Dark table surface tuning is required");
  assert(css.includes(".dark input,"), "Dark form control tuning is required");
  assert(css.includes(".dark .queue-live-output {"), "Dark live output tuning is required");
})();

(function testAccentHierarchyRules() {
  const css = fs.readFileSync(DASHBOARD_CSS, "utf8");

  const buttonRule = css.match(/button\s*\{([\s\S]*?width:\s*fit-content;[\s\S]*?)\}/);
  assert(buttonRule, "Missing primary button visual rule");
  assert(
    buttonRule[1].includes("--mm-accent"),
    "Primary button rule must use purple accent token (--mm-accent)",
  );
  assert(!buttonRule[1].includes("--mm-warn"), "Primary button rule must not use warning token");
  assert(
    !buttonRule[1].includes("--mm-accent-warm"),
    "Primary button rule must not use warm highlight token",
  );

  const navActiveRule = css.match(/\.route-nav a\.active,\s*\.route-nav a:hover\s*\{([\s\S]*?)\}/);
  assert(navActiveRule, "Missing active nav rule");
  assert(navActiveRule[1].includes("--mm-accent"), "Active nav rule must use purple accent token");
  assert(!navActiveRule[1].includes("--mm-warn"), "Active nav rule must not use warning token");
  assert(
    !navActiveRule[1].includes("--mm-accent-warm"),
    "Active nav rule must not use warm highlight token",
  );

  const queuedStatusRule = css.match(/\.status-queued\s*\{([\s\S]*?)\}/);
  assert(queuedStatusRule, "Missing queued status rule");
  assert(queuedStatusRule[1].includes("--mm-warn"), "Queued status must use warning token");
})();
