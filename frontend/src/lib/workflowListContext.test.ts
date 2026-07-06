import { describe, expect, it } from "vitest";

import { workflowListContextParams } from "./workflowListContext";

describe("workflowListContextParams", () => {
  it("preserves supported workflow list filters for detail sidebar queries", () => {
    const params = workflowListContextParams(
      new URLSearchParams(
        "source=temporal&integration=jira&repo=MoonMind&state=executing&limit=10&pageSize=10",
      ),
    );

    expect(params.toString()).toBe(
      "source=temporal&integration=jira&repo=MoonMind&state=executing&limit=10&pageSize=10",
    );
  });

  it("drops parameters that are not part of the workflow list context", () => {
    const params = workflowListContextParams(
      new URLSearchParams("integration=jira&unknown=value"),
    );

    expect(params.toString()).toBe("integration=jira");
  });

  it("drops display mode and unsafe detail payload parameters", () => {
    const params = workflowListContextParams(
      new URLSearchParams(
        "stateIn=completed&workflowListDisplayMode=hidden&rawPrompt=secret&draft=full&token=abc&presignedUrl=https%3A%2F%2Fexample.test&logs=large&artifacts=payload&detailPayload=large",
      ),
    );

    expect(params.toString()).toBe("stateIn=completed");
  });
});
