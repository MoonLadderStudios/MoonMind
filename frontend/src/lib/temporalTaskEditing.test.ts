import { describe, expect, it } from "vitest";

import { buildTemporalSubmissionDraftFromExecution } from "./temporalTaskEditing";

describe("MoonLadderStudios/MoonMind#3452 Omnigent draft round-trip", () => {
  it("preserves canonical execution target and launch policy refs", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:omnigent-edit",
      workflowType: "MoonMind.UserWorkflow",
      targetRuntime: "omnigent",
      inputParameters: {
        targetRuntime: "omnigent",
        profileId: "codex-oauth-team",
        omnigent: {
          executionTargetRef: "omnigent-codex-default",
          launchPolicyRef: "omnigent-codex-on-demand-v1",
        },
        workflow: {
          instructions: "Implement the requested change.",
          runtime: { mode: "omnigent", profileId: "codex-oauth-team" },
        },
      },
    });

    expect(draft).toMatchObject({
      runtime: "omnigent",
      providerProfile: "codex-oauth-team",
      omnigentExecutionTargetRef: "omnigent-codex-default",
      omnigentLaunchPolicyRef: "omnigent-codex-on-demand-v1",
    });
  });
});

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
    hintCatalogVersion: "2026-05-13",
    detectionPhase: "submit",
  };

  it("reconstructs objective and step runtime command metadata for preview restoration", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:slash-preview",
        workflowType: "MoonMind.UserWorkflow",
        targetRuntime: "codex_cli",
        inputParameters: {
          workflow: {
            instructions: "/review\nCheck the branch.",
            runtime: { mode: "codex_cli" },
          },
        },
      },
      {
        workflow: {
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
        workflowType: "MoonMind.UserWorkflow",
        targetRuntime: "codex_cli",
        inputParameters: {
          workflow: {
            instructions: "Inline workflow should not win.",
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
        workflowType: "MoonMind.UserWorkflow",
        targetRuntime: "codex_cli",
        inputParameters: {
          workflow: {
            instructions: "Inline workflow should not replace history.",
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
      workflowType: "MoonMind.UserWorkflow",
      targetRuntime: "codex_cli",
      inputParameters: {
        workflow: {
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

  it("preserves MM-786 per-step runtime model and effort metadata", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:step-runtime",
      workflowType: "MoonMind.UserWorkflow",
      targetRuntime: "codex_cli",
      inputParameters: {
        workflow: {
          instructions: "Coordinate portable steps.",
          runtime: { mode: "codex_cli", model: "gpt-5.4", effort: "medium" },
          steps: [
            {
              id: "step-cheap",
              instructions: "Run this step cheaply.",
              runtime: {
                mode: "claude_code",
                model: "claude-haiku-test",
                effort: "low",
              },
            },
          ],
        },
      },
    });

    expect(draft.steps[0]?.runtime).toEqual({
      mode: "claude_code",
      model: "claude-haiku-test",
      effort: "low",
    });
  });

  it("reconstructs canonical workflow steps from execution parameters", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:canonical-workflow",
      workflowType: "MoonMind.UserWorkflow",
      targetRuntime: "codex_cli",
      inputParameters: {
        workflow: {
          instructions: "Run Jira Implement for MM-901.",
          runtime: { mode: "codex_cli", model: "gpt-5.4" },
          appliedStepTemplates: [
            {
              slug: "jira-implement",
              stepIds: [
                "tpl:jira-implement:1.0.0:01",
                "tpl:jira-implement:1.0.0:02",
              ],
            },
          ],
          steps: [
            {
              id: "tpl:jira-implement:1.0.0:01",
              title: "Load Jira preset brief",
              type: "tool",
              instructions: "Load MM-901.",
              tool: { id: "jira.load_preset_brief", inputs: { issueKey: "MM-901" } },
            },
            {
              id: "tpl:jira-implement:1.0.0:02",
              title: "Assess existing implementation state",
              type: "skill",
              instructions: "Assess MM-901.",
              skill: { id: "auto", args: {} },
            },
          ],
        },
      },
    });

    expect(draft.taskInstructions).toBe(
      "Run Jira Implement for MM-901.\n\nLoad MM-901.\n\nAssess MM-901.",
    );
    expect(draft.steps.map((step) => step.title)).toEqual([
      "Load Jira preset brief",
      "Assess existing implementation state",
    ]);
    expect(draft.appliedTemplates).toEqual([
      {
        slug: "jira-implement",
        inputs: {},
        stepIds: [
          "tpl:jira-implement:1.0.0:01",
          "tpl:jira-implement:1.0.0:02",
        ],
        appliedAt: "",
        capabilities: [],
      },
    ]);
  });

  it("preserves Skill inputs and saved input contract digest from workflow drafts", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:skill-digest",
      workflowType: "MoonMind.UserWorkflow",
      targetRuntime: "codex_cli",
      inputParameters: {
        workflow: {
          instructions: "Run schema skill.",
          steps: [
            {
              id: "schema-step",
              type: "skill",
              skill: {
                id: "schema.skill",
                inputs: { repository: "MoonLadderStudios/MoonMind" },
                inputContractDigest: "sha256:pinned-contract",
              },
            },
          ],
        },
      },
    });

    expect(draft.steps[0]?.skillArgs).toEqual({
      repository: "MoonLadderStudios/MoonMind",
    });
    expect(draft.steps[0]?.skillInputContractDigest).toBe(
      "sha256:pinned-contract",
    );
  });

  it("prefers authoritative draft.workflow when it carries the full preset expansion", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:canonical-workflow-snapshot",
        workflowType: "MoonMind.UserWorkflow",
        taskInputSnapshot: {
          available: true,
          artifactRef: "art-snapshot",
          snapshotVersion: 1,
          sourceKind: "create",
          reconstructionMode: "authoritative",
          disabledReasons: {},
          fallbackEvidenceRefs: [],
        },
        inputParameters: {
          workflow: {
            instructions: "Run Jira Implement for MM-902.",
            steps: [
              {
                id: "tpl:jira-implement:1.0.0:01",
                title: "Load Jira preset brief",
                instructions: "Load MM-902.",
              },
            ],
          },
        },
      },
      {
        snapshotVersion: 1,
        source: { kind: "create" },
        draft: {
          workflowShape: "multi_step",
          workflow: {
            instructions: "Run Jira Implement for MM-902.",
            appliedStepTemplates: [
              {
                slug: "jira-implement",
                stepIds: [
                  "tpl:jira-implement:1.0.0:01",
                  "tpl:jira-implement:1.0.0:02",
                  "tpl:jira-implement:1.0.0:03",
                ],
              },
            ],
            steps: [
              {
                id: "tpl:jira-implement:1.0.0:01",
                title: "Load Jira preset brief",
                instructions: "Load MM-902.",
              },
              {
                id: "tpl:jira-implement:1.0.0:02",
                title: "Assess existing implementation state",
                instructions: "Assess MM-902.",
              },
              {
                id: "tpl:jira-implement:1.0.0:03",
                title: "Finalize Jira status",
                instructions: "Finalize MM-902.",
                skill: { id: "jira-issue-updater", args: {} },
              },
            ],
          },
        },
      },
    );

    expect(draft.steps.map((step) => step.title)).toEqual([
      "Load Jira preset brief",
      "Assess existing implementation state",
      "Finalize Jira status",
    ]);
    expect(draft.appliedTemplates[0]?.slug).toBe("jira-implement");
  });

  it("preserves Skill input contract digests from saved drafts", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:skill-contract-digest",
      workflowType: "MoonMind.UserWorkflow",
      inputParameters: {
        workflow: {
          instructions: "Edit a saved Skill draft.",
          steps: [
            {
              type: "skill",
              instructions: "Use the saved structured values.",
              skill: {
                id: "schema.skill",
                inputContractDigest: "sha256:saved-contract",
                args: { repository: "MoonLadderStudios/MoonMind" },
              },
            },
          ],
        },
      },
    });

    expect(draft.steps[0]?.skillInputContractDigest).toBe(
      "sha256:saved-contract",
    );
    expect(draft.steps[0]?.skillArgs).toEqual({
      repository: "MoonLadderStudios/MoonMind",
    });
  });
});
