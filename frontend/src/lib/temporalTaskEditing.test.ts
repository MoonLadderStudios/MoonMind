import { describe, expect, it } from "vitest";

import { buildTemporalSubmissionDraftFromExecution } from "./temporalTaskEditing";

describe("buildTemporalSubmissionDraftFromExecution runtime command metadata", () => {
  const objectiveRuntimeCommand = {
    kind: "slash_command",
    sourcePath: "objective.instructions",
    command: "review",
    rawCommand: "/review",
    args: "",
    instructionBody: "Check the branch.",
    detectionStatus: "detected",
    hintStatus: "hinted",
    recognitionMode: "hinted_runtime_passthrough",
    targetRuntime: "codex_cli",
    runtimeCapabilityVersion: "2026-05-13",
    hintCatalogVersion: "2026-05-13",
    detectionPhase: "submit",
  };

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

  it("restores top-level snapshot runtimeCommand metadata with authored instructions", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:slash-top-level",
        workflowType: "MoonMind.Run",
        targetRuntime: "codex_cli",
        inputParameters: {
          task: {
            instructions: "Inline fallback should not win.",
            runtime: { mode: "codex_cli" },
          },
        },
      },
      {
        snapshotVersion: 1,
        draft: {
          taskShape: "skill_only",
          instructions: "/review\nCheck the branch.",
          runtime: "codex_cli",
          runtimeCommand: objectiveRuntimeCommand,
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
                targetRuntime: "codex_cli",
                runtimeCapabilityVersion: "2026-05-13",
                hintCatalogVersion: "2026-05-13",
              },
            },
          ],
        },
      },
    );

    expect(draft.taskInstructions).toBe(
      "/review\nCheck the branch.\n\n/simplify\nKeep the patch small.",
    );
    expect(draft.runtimeCommand).toMatchObject({
      command: "review",
      sourcePath: "objective.instructions",
      runtimeCapabilityVersion: "2026-05-13",
      hintCatalogVersion: "2026-05-13",
    });
    expect(draft.steps[0]?.runtimeCommand).toMatchObject({
      command: "simplify",
      targetStepId: "step-review",
    });
  });

  it("preserves historical slash instructions when runtimeCommand metadata is absent", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:slash-legacy",
        workflowType: "MoonMind.Run",
        targetRuntime: "codex_cli",
        inputParameters: {
          task: {
            instructions: "Inline fallback should not replace history.",
            runtime: { mode: "codex_cli" },
          },
        },
      },
      {
        snapshotVersion: 1,
        draft: {
          taskShape: "skill_only",
          instructions: "/future-command\nUse provider behavior.",
          runtime: "codex_cli",
        },
      },
    );

    expect(draft.taskInstructions).toBe(
      "/future-command\nUse provider behavior.",
    );
    expect(draft.runtimeCommand).toBeUndefined();
  });

  it("combines task and step instructions when both are present", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:combined-instructions",
      workflowType: "MoonMind.Run",
      targetRuntime: "codex_cli",
      inputParameters: {
        task: {
          instructions: "Review the overall branch.",
          steps: [
            {
              id: "step-1",
              instructions: "Check the runtime command audit trail.",
            },
          ],
        },
      },
    });

    expect(draft.taskInstructions).toBe(
      "Review the overall branch.\n\nCheck the runtime command audit trail.",
    );
  });
});
