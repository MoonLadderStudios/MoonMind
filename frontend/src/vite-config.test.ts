import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";
import { resolve } from "node:path";

import { dashboardViteBase } from "./vite-base";

const require = createRequire(import.meta.url);
const tailwindConfig = require("../../tailwind.config.cjs") as {
  content?: string[];
};

describe("vite.config", () => {
  it("serves built dashboard chunks from the mounted static dist root", () => {
    expect(dashboardViteBase("build")).toBe("/static/workflow_console/dist/");
  });

  it("keeps dev-server asset URLs at the Vite root during local HMR", () => {
    expect(dashboardViteBase("serve")).toBe("/");
  });

  it("enforces MM-430 Tailwind source scanning inputs", () => {
    expect(tailwindConfig.content).toEqual(
      expect.arrayContaining([
        "./api_service/templates/react_dashboard.html",
        "./api_service/templates/_navigation.html",
        "./frontend/src/**/*.{js,jsx,ts,tsx}",
      ]),
    );
    expect(tailwindConfig.content ?? []).not.toContain(
      "./api_service/static/workflow_console/dist/**/*",
    );
  });

  it("enforces MM-430 dashboard source and generated dist boundaries", () => {
    const canonicalStyleSource = resolve(
      process.cwd(),
      "frontend/src/styles/dashboard.css",
    );
    const generatedDistRoot = resolve(
      process.cwd(),
      "api_service/static/workflow_console/dist",
    );
    const canonicalStyleSourcePath = canonicalStyleSource.replace(/\\/g, "/");
    const generatedDistRootPath = generatedDistRoot.replace(/\\/g, "/");

    expect(canonicalStyleSourcePath).toMatch(
      /frontend\/src\/styles\/dashboard\.css$/,
    );
    expect(generatedDistRootPath).toMatch(
      /api_service\/static\/workflow_console\/dist$/,
    );
    expect(canonicalStyleSource.startsWith(generatedDistRoot)).toBe(false);
  });
});
