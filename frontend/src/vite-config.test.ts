import { describe, expect, it } from "vitest";

import { missionControlViteBase } from "./vite-base";

describe("vite.config", () => {
  it("serves built Mission Control chunks from the mounted static dist root", () => {
    expect(missionControlViteBase("build")).toBe("/static/task_dashboard/dist/");
  });

  it("keeps dev-server asset URLs at the Vite root during local HMR", () => {
    expect(missionControlViteBase("serve")).toBe("/");
  });
});
