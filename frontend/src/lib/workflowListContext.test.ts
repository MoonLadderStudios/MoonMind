import { describe, expect, it } from "vitest";

import {
  workflowListApiQueryFromContext,
  workflowListContextParams,
  workflowListHrefFromContext,
} from "./workflowListContext";

describe("workflowListContextParams", () => {
  it("preserves supported workflow list filters for detail sidebar queries", () => {
    const params = workflowListContextParams(
      new URLSearchParams(
        "source=temporal&integration=jira&repo=MoonMind&state=executing&limit=10",
      ),
    );

    expect(params.toString()).toBe(
      "source=temporal&integration=jira&repo=MoonMind&state=executing&limit=10",
    );
  });

  it("drops parameters that are not part of the workflow list context", () => {
    const params = workflowListContextParams(
      new URLSearchParams("integration=jira&unknown=value"),
    );

    expect(params.toString()).toBe("integration=jira");
  });

  it("normalizes a list route query into the matching executions API query", () => {
    const query = workflowListApiQueryFromContext(
      new URLSearchParams(
        "stateIn=executing&limit=50&progressSignalIn=awaiting_external&unsafe=1",
      ),
    );

    expect(query).toBe(
      "source=temporal&pageSize=50&stateIn=executing&progressSignalIn=awaiting_external",
    );
  });

  it("converts API-style pageSize to table limit when linking back to the workflow list", () => {
    const href = workflowListHrefFromContext(
      new URLSearchParams("source=temporal&pageSize=100&stateIn=completed&nextPageToken=page-2"),
      { markDetailReturn: true },
    );

    expect(href).toBe("/workflows?stateIn=completed&limit=100&returnFromWorkflowDetail=1");
  });
});
