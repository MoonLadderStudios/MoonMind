import { describe, expect, it } from "vitest";

import viteConfig from "../vite.config";

describe("vite.config", () => {
  it("serves built Mission Control chunks from the mounted static dist root", async () => {
    const config = await viteConfig({
      command: "build",
      mode: "test",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.base).toBe("/static/task_dashboard/dist/");
  });

  it("keeps dev-server asset URLs at the Vite root during local HMR", async () => {
    const config = await viteConfig({
      command: "serve",
      mode: "test",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.base).toBe("/");
  });
});
