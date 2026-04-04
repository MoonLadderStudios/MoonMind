import { describe, expect, it } from "vitest";

import config from "../vite.config";

describe("vite.config", () => {
  it("serves built Mission Control chunks from the mounted static dist root", () => {
    expect(config.base).toBe("/static/task_dashboard/dist/");
  });
});
