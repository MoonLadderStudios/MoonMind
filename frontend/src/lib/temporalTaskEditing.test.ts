import { describe, expect, it } from "vitest";

import { buildTemporalSubmissionDraftFromExecution } from "./temporalTaskEditing";

describe("buildTemporalSubmissionDraftFromExecution runtime command metadata", () => {
  it("reconstructs objective and step runtime command metadata for preview restoration", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:slash-preview",
        workflowType: "MoonMind.Run",
        targetRuntime: "codex_cli",
        inputParameters: {
          task: {
            instructions: "/review\nCheck the branch.",
            runtime: { mode: "codex_cli" },
          },
        },
      },
      {
        task: {
          instructions: "/review\nCheck the branch.",
          runtimeCommand: {
            kind: "slash_command",
            sourcePath: "objective.instructions",
            command: "review",
            rawCommand: "/review",
            detectionStatus: "detected",
            recognitionMode: "hinted_runtime_passthrough",
          },
          steps: [
            {
              id: "step-review",
              instructions: "/simplify\nKeep the patch small.",
              runtimeCommand: {
                kind: "slash_command",
                sourcePath: "steps[0].instructions",
                targetStepId: "step-review",
                command: "simplify",
                rawCommand: "/simplify",
                detectionStatus: "detected",
                recognitionMode: "hinted_runtime_passthrough",
              },
            },
          ],
        },
      },
    );

    expect(draft.runtimeCommand).toMatchObject({
      command: "review",
      rawCommand: "/review",
      sourcePath: "objective.instructions",
    });
    expect(draft.steps[0]?.runtimeCommand).toMatchObject({
      command: "simplify",
      rawCommand: "/simplify",
      targetStepId: "step-review",
    });
  });
});
