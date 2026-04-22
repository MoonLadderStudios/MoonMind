import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";
import { resolve } from "node:path";

import { missionControlViteBase } from "./vite-base";

const require = createRequire(import.meta.url);
const tailwindConfig = require("../../tailwind.config.cjs") as {
  content?: string[];
};

describe("vite.config", () => {
  it("serves built Mission Control chunks from the mounted static dist root", () => {
    expect(missionControlViteBase("build")).toBe("/static/task_dashboard/dist/");
  });

  it("keeps dev-server asset URLs at the Vite root during local HMR", () => {
    expect(missionControlViteBase("serve")).toBe("/");
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
      "./api_service/static/task_dashboard/dist/**/*",
    );
  });

  it("enforces MM-430 Mission Control source and generated dist boundaries", () => {
    const canonicalStyleSource = resolve(
      process.cwd(),
      "frontend/src/styles/mission-control.css",
    );
    const generatedDistRoot = resolve(
      process.cwd(),
      "api_service/static/task_dashboard/dist",
    );

    expect(canonicalStyleSource).toMatch(
      /frontend\/src\/styles\/mission-control\.css$/,
    );
    expect(generatedDistRoot).toMatch(
      /api_service\/static\/task_dashboard\/dist$/,
    );
    expect(canonicalStyleSource.startsWith(generatedDistRoot)).toBe(false);
  });
});
