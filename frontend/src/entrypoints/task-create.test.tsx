import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";
import {
  buildTemporalArtifactEditUpdatePayload,
  buildTemporalSubmissionDraftFromExecution,
  resolveTaskSubmitPageMode,
} from "../lib/temporalTaskEditing";
import { renderWithClient } from "../utils/test-utils";
import {
  ARTIFACT_COMPLETE_RETRY_DELAYS_MS,
  preferredTemplate,
  resolveObjectiveInstructions,
  TaskCreatePage,
} from "./task-create";

vi.mock("../lib/navigation", () => ({
  navigateTo: vi.fn(),
}));

const mockPayload: BootPayload = {
  page: "task-create",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: {
        temporal: {
          create: "/api/executions",
          artifactCreate: "/api/artifacts",
        },
      },
      system: {
        defaultRepository: "MoonLadderStudios/MoonMind",
        defaultTaskRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "pr",
        defaultProposeTasks: false,
        defaultTaskModelByRuntime: {
          codex_cli: "gpt-5.4",
          gemini_cli: "gemini-2.5-pro",
          claude_code: "claude-3.7-sonnet",
        },
        defaultTaskEffortByRuntime: {
          codex_cli: "medium",
          gemini_cli: "high",
          claude_code: "low",
        },
        supportedTaskRuntimes: ["codex_cli", "gemini_cli", "claude_code"],
        providerProfiles: {
          list: "/api/v1/provider-profiles",
        },
        taskTemplateCatalog: {
          enabled: true,
          templateSaveEnabled: true,
          list: "/api/task-step-templates",
          detail: "/api/task-step-templates/{slug}",
          expand: "/api/task-step-templates/{slug}:expand",
          saveFromTask: "/api/task-step-templates/save-from-task",
        },
      },
      features: {
        temporalDashboard: {
          temporalTaskEditing: true,
        },
      },
    },
  },
};

function withJiraIntegration(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      sources?: Record<string, unknown>;
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        sources: {
          ...initialData.dashboardConfig.sources,
          jira: {
            connections: "/api/jira/connections/verify",
            projects: "/api/jira/projects",
            boards: "/api/jira/projects/{projectKey}/boards",
            columns: "/api/jira/boards/{boardId}/columns",
            issues: "/api/jira/boards/{boardId}/issues",
            issue: "/api/jira/issues/{issueKey}",
          },
        },
        system: {
          ...initialData.dashboardConfig.system,
          jiraIntegration: {
            enabled: true,
            defaultProjectKey: "ENG",
            defaultBoardId: "42",
            rememberLastBoardInSession: true,
          },
        },
      },
    },
  };
}

function withAttachmentPolicy(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          attachmentPolicy: {
            enabled: true,
            maxCount: 4,
            maxBytes: 1024 * 1024,
            totalBytes: 2 * 1024 * 1024,
            allowedContentTypes: ["image/png", "application/pdf"],
          },
        },
      },
    },
  };
}

function withJiraSessionMemory(
  rememberLastBoardInSession: boolean,
  payload: BootPayload = mockPayload,
): BootPayload {
  const nextPayload = withJiraIntegration(payload);
  const initialData = nextPayload.initialData as {
    dashboardConfig: {
      system?: {
        jiraIntegration?: Record<string, unknown>;
      };
    };
  };
  return {
    ...nextPayload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          jiraIntegration: {
            ...initialData.dashboardConfig.system?.jiraIntegration,
            rememberLastBoardInSession,
          },
        },
      },
    },
  };
}

function collectObjectKeys(value: unknown, keys = new Set<string>()): Set<string> {
  if (!value || typeof value !== "object") {
    return keys;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectObjectKeys(item, keys));
    return keys;
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, nested]) => {
    keys.add(key);
    collectObjectKeys(nested, keys);
  });
  return keys;
}

describe("Task Create Entrypoint", () => {
  let fetchSpy: MockInstance;
  let consoleInfoSpy: MockInstance;
  let executionResponseOverride: Response | null;
  let artifactCreateResponseOverride: Response | null;

  function renderForEdit(executionId: string, payload: BootPayload = mockPayload) {
    window.history.pushState(
      {},
      "Task Edit",
      `/tasks/new?editExecutionId=${encodeURIComponent(executionId)}`,
    );
    return renderWithClient(<TaskCreatePage payload={payload} />);
  }

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/tasks/new");
    window.sessionStorage.clear();
    vi.mocked(navigateTo).mockReset();
    consoleInfoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    executionResponseOverride = null;
    artifactCreateResponseOverride = null;
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: { worker: ["speckit-orchestrate", "pr-resolver"] },
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=personal")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [],
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "speckit-demo",
                  scope: "global",
                  title: "Spec Kit Demo",
                  description: "Seed a two-step planning flow.",
                  latestVersion: "1.2.3",
                  version: "1.2.3",
                },
                {
                  slug: "objective-demo",
                  scope: "global",
                  title: "Objective Request Demo",
                  description:
                    "Use template inputs to derive the task objective.",
                  latestVersion: "2.0.0",
                  version: "2.0.0",
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith("/api/task-step-templates/speckit-demo?scope=global")
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "speckit-demo",
              scope: "global",
              title: "Spec Kit Demo",
              description: "Seed a two-step planning flow.",
              latestVersion: "1.2.3",
              version: "1.2.3",
              inputs: [
                {
                  name: "feature_name",
                  label: "Feature Name",
                  type: "text",
                  required: true,
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith("/api/task-step-templates/objective-demo?scope=global")
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "objective-demo",
              scope: "global",
              title: "Objective Request Demo",
              description: "Use template inputs to derive the task objective.",
              latestVersion: "2.0.0",
              version: "2.0.0",
              inputs: [
                {
                  name: "request",
                  label: "Request",
                  type: "text",
                  required: true,
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith(
            "/api/task-step-templates/speckit-demo:expand?scope=global",
          )
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:speckit-demo:1.2.3:01",
                  title: "Clarify spec",
                  instructions: "Clarify the {{ inputs.feature_name }} scope.",
                  skill: {
                    id: "speckit-clarify",
                    args: { feature: "Task Create" },
                  },
                },
                {
                  id: "tpl:speckit-demo:1.2.3:02",
                  title: "Plan implementation",
                  instructions: "Write a plan for the task builder recovery.",
                },
              ],
              appliedTemplate: {
                slug: "speckit-demo",
                version: "1.2.3",
              },
              warnings: [],
            }),
          } as Response);
        }
        if (
          url.startsWith(
            "/api/task-step-templates/objective-demo:expand?scope=global",
          )
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:objective-demo:2.0.0:01",
                  title: "Clarify request",
                  instructions: "",
                  skill: {
                    id: "speckit-clarify",
                    args: { mode: "objective" },
                  },
                },
                {
                  id: "tpl:objective-demo:2.0.0:02",
                  title: "Review objective",
                  instructions: "Review the resulting task objective.",
                },
              ],
              appliedTemplate: {
                slug: "objective-demo",
                version: "2.0.0",
              },
              warnings: [],
            }),
          } as Response);
        }
        if (url.startsWith("/api/v1/provider-profiles")) {
          const runtimeId = new URL(`http://localhost${url}`).searchParams.get(
            "runtime_id",
          );
          const items =
            runtimeId === "gemini_cli"
              ? [
                  {
                    profile_id: "profile:gemini-default",
                    account_label: "Gemini Default",
                    is_default: true,
                  },
                ]
              : runtimeId === "claude_code"
                ? [
                    {
                      profile_id: "profile:claude-default",
                      account_label: "Claude Default",
                      is_default: true,
                    },
                  ]
                : [
                    {
                      profile_id: "profile:codex-default",
                      account_label: "Codex Default",
                      is_default: true,
                    },
                    {
                      profile_id: "profile:codex-secondary",
                      account_label: "Codex Secondary",
                      is_default: false,
                    },
                  ];
          return Promise.resolve({
            ok: true,
            json: async () => items,
          } as Response);
        }
        if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
          const depItems = Array.from({ length: 12 }, (_, i) => ({
            taskId: `mm:dep-${i + 1}`,
            workflowType: "MoonMind.Run",
            entry: "run",
            title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
            state: i % 3 === 0 ? "completed" : "executing",
          }));
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: depItems,
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aedit-123?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:edit-123",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "gemini_cli",
              profileId: "profile:gemini-default",
              model: "gemini-2.5-pro",
              effort: "high",
              repository: "MoonLadderStudios/MoonMind",
              startingBranch: "main",
              targetBranch: "task-editing-phase-2",
              publishMode: "branch",
              targetSkill: "speckit-implement",
              inputParameters: {
                targetRuntime: "gemini_cli",
                operatorNote: "Keep this unedited top-level value.",
                task: {
                  instructions: "Rebuild the Temporal task draft.",
                  proposeTasks: true,
                  runtime: {
                    mode: "gemini_cli",
                    model: "gemini-2.5-pro",
                    effort: "high",
                    profileId: "profile:gemini-default",
                  },
                  git: {
                    startingBranch: "main",
                    targetBranch: "task-editing-phase-2",
                  },
                  publish: { mode: "branch" },
                  tool: { type: "skill", name: "speckit-implement" },
                  appliedStepTemplates: [
                    {
                      slug: "speckit-demo",
                      version: "1.2.3",
                      inputs: { feature_name: "Task Editing" },
                      stepIds: ["tpl:speckit-demo:1.2.3:01"],
                      appliedAt: "2026-04-12T00:00:00Z",
                      capabilities: ["git"],
                    },
                  ],
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:artifact-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputArtifactRef: "historical-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                  publish: { mode: "pr" },
                  tool: { type: "skill", name: "speckit-orchestrate" },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Arerun-123?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:rerun-123",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputArtifactRef: "historical-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                  publish: { mode: "pr" },
                  tool: { type: "skill", name: "speckit-orchestrate" },
                },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aunsupported?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:unsupported",
              workflowType: "MoonMind.ManifestIngest",
              state: "completed",
              inputParameters: {},
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:no-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                task: { instructions: "Existing task instructions." },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:stale-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                task: { instructions: "Editable before submit." },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-rerun?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:no-rerun",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputParameters: {
                task: { instructions: "Terminal task instructions." },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-rerun?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:stale-rerun",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Rerunnable before submit.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Amissing-artifact?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:missing-artifact",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputArtifactRef: "missing-input",
              inputParameters: { task: {} },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (
          url === "/api/executions/mm%3Amalformed-artifact?source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:malformed-artifact",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputArtifactRef: "malformed-input",
              inputParameters: { task: {} },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aincomplete?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:incomplete",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (
          url ===
          "/gateway/api/executions/mm%3Acustom-endpoints?view=detail&source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:custom-endpoints",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputArtifactRef: "custom-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (
          url ===
          "/gateway/api/executions/mm%3Acustom-edit?view=detail&source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:custom-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-default",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Save through configured endpoint.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-default",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions") {
          if (executionResponseOverride) {
            return Promise.resolve(executionResponseOverride);
          }
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:workflow-123",
              runId: "run-123",
              namespace: "moonmind",
              redirectPath: "/tasks/mm:workflow-123?source=temporal",
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aedit-123/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              message: "Inputs updated.",
              execution: { workflowId: "mm:edit-123" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "next_safe_point",
              message: "Inputs scheduled.",
              execution: { workflowId: "mm:artifact-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Arerun-123/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "continue_as_new",
              message: "Rerun requested.",
              execution: { workflowId: "mm:rerun-123" },
              continueAsNewCause: "manual_rerun",
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-rerun/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow state changed and rerun is no longer available.",
                },
              }),
          } as Response);
        }
        if (url === "/gateway/api/executions/mm%3Acustom-edit/updates") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              execution: { workflowId: "mm:custom-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Acontinue-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:continue-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Continue-as-new editable input.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Acontinue-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "continue_as_new",
              message: "Inputs accepted for a refreshed run.",
              execution: { workflowId: "mm:continue-edit-next" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Avalidation-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:validation-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Editable input with backend validation.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Avalidation-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message: "Task input validation failed: target branch is invalid.",
                },
              }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-failure?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:artifact-failure",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputArtifactRef: "historical-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Artifact-backed editable input.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-failure/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              execution: { workflowId: "mm:artifact-failure" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow is in a terminal state and no longer accepts updates.",
                },
              }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow is in a terminal state and no longer accepts updates.",
                },
              }),
          } as Response);
        }
        if (url === "/api/task-step-templates/save-from-task") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "saved-preset",
              scope: "personal",
              title: "Saved preset",
              latestVersion: "1.0.0",
            }),
          } as Response);
        }
        if (url === "/api/artifacts") {
          if (artifactCreateResponseOverride) {
            return Promise.resolve(artifactCreateResponseOverride);
          }
          return Promise.resolve({
            ok: true,
            json: async () => ({
              artifact_ref: {
                artifact_id: "art-001",
              },
              upload: {
                mode: "single",
                upload_url: "/api/artifacts/art-001/content",
                expires_at: "2026-04-02T00:00:00Z",
                max_size_bytes: 100000,
                required_headers: {},
              },
            }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/content") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/complete") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/links") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/historical-input/download") {
          return Promise.resolve({
            ok: true,
            text: async () =>
              JSON.stringify({
                repository: "MoonLadderStudios/MoonMind",
                task: {
                  instructions: "Rerun from artifact-backed instructions.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                  publish: { mode: "pr" },
                  tool: { type: "skill", name: "speckit-orchestrate" },
                },
              }),
          } as Response);
        }
        if (url === "/api/artifacts/missing-input/download") {
          return Promise.resolve({
            ok: false,
            status: 404,
            statusText: "Not Found",
            text: async () => "",
          } as Response);
        }
        if (url === "/api/artifacts/malformed-input/download") {
          return Promise.resolve({
            ok: true,
            text: async () => "not-json",
          } as Response);
        }
        if (url === "/gateway/api/artifacts/custom-input/raw") {
          return Promise.resolve({
            ok: true,
            text: async () =>
              JSON.stringify({
                task: {
                  instructions: "Loaded through configured artifact route.",
                  skill: { id: "speckit-orchestrate" },
                },
              }),
          } as Response);
        }
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { projectKey: "OPS", name: "Operations" },
                { projectKey: "ENG", name: "Engineering" },
                { projectKey: "MY-PROJ", name: "Hyphenated Project" },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/projects/ENG/boards") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { id: "7", name: "Backlog", projectKey: "ENG" },
                { id: "42", name: "Delivery", projectKey: "ENG" },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/projects/MY-PROJ/boards") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { id: "84", name: "Hyphenated Delivery", projectKey: "MY-PROJ" },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/42/columns") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              board: { id: "42", name: "Delivery", projectKey: "ENG" },
              columns: [
                { id: "todo", name: "To Do", count: 1 },
                { id: "doing", name: "Doing", count: 1 },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/84/columns") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              board: { id: "84", name: "Hyphenated Delivery", projectKey: "MY-PROJ" },
              columns: [{ id: "selected", name: "Selected", count: 1 }],
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/42/issues") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              boardId: "42",
              columns: [
                { id: "todo", name: "To Do" },
                { id: "doing", name: "Doing" },
              ],
              itemsByColumn: {
                todo: [
                  {
                    issueKey: "ENG-101",
                    summary: "Plan controls",
                    issueType: "Story",
                    statusName: "Selected",
                    assignee: "Ada",
                    updatedAt: "2026-04-10T19:30:00Z",
                  },
                ],
                doing: [
                  {
                    issueKey: "ENG-202",
                    summary: "Build browser shell",
                    issueType: "Story",
                    statusName: "In Progress",
                    assignee: "Grace",
                    updatedAt: "2026-04-11T19:30:00Z",
                  },
                ],
              },
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/84/issues") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              boardId: "84",
              columns: [{ id: "selected", name: "Selected" }],
              itemsByColumn: {
                selected: [
                  {
                    issueKey: "MY-PROJ-123",
                    summary: "Handle hyphenated project keys",
                    issueType: "Story",
                    statusName: "Selected",
                    assignee: "Lin",
                    updatedAt: "2026-04-12T19:30:00Z",
                  },
                ],
              },
            }),
          } as Response);
        }
        if (url === "/api/jira/issues/ENG-202") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              issueKey: "ENG-202",
              url: "https://jira.example.test/browse/ENG-202",
              summary: "Build browser shell",
              issueType: "Story",
              column: { id: "doing", name: "Doing" },
              status: { id: "3", name: "In Progress" },
              descriptionText: "Let operators browse Jira stories.",
              acceptanceCriteriaText:
                "Given a board, users can select a story preview.",
              recommendedImports: {
                presetInstructions:
                  "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
                stepInstructions:
                  "Complete Jira story ENG-202: Build browser shell",
              },
            }),
          } as Response);
        }
        if (url === "/api/jira/issues/MY-PROJ-123") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              issueKey: "MY-PROJ-123",
              url: "https://jira.example.test/browse/MY-PROJ-123",
              summary: "Handle hyphenated project keys",
              issueType: "Story",
              column: { id: "selected", name: "Selected" },
              status: { id: "1", name: "Selected" },
              descriptionText: "Keep the full Jira project key.",
              acceptanceCriteriaText:
                "Given a hyphenated project key, reopening keeps the project selected.",
              recommendedImports: {
                presetInstructions:
                  "MY-PROJ-123: Handle hyphenated project keys\n\nKeep the full Jira project key.",
                stepInstructions:
                  "Complete Jira story MY-PROJ-123: Handle hyphenated project keys",
              },
            }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      });
  });

  it("prefers the seeded MoonSpec orchestrate template over legacy SpecKit rows", () => {
    const preferred = preferredTemplate([
      {
        key: "global::::speckit-orchestrate",
        slug: "speckit-orchestrate",
        scope: "global",
        title: "SpecKit Orchestrate",
        description: "Legacy preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::moonspec-orchestrate",
        slug: "moonspec-orchestrate",
        scope: "global",
        title: "MoonSpec Orchestrate",
        description: "MoonSpec preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
    ]);

    expect(preferred?.slug).toBe("moonspec-orchestrate");
    expect(preferred?.scope).toBe("global");
  });

  it("falls back to the legacy SpecKit orchestrate template when MoonSpec is absent", () => {
    const preferred = preferredTemplate([
      {
        key: "global::::speckit-orchestrate",
        slug: "speckit-orchestrate",
        scope: "global",
        title: "SpecKit Orchestrate",
        description: "Legacy preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::other-template",
        slug: "other-template",
        scope: "global",
        title: "Other Template",
        description: "Other preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
    ]);

    expect(preferred?.slug).toBe("speckit-orchestrate");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    consoleInfoSpy.mockRestore();
    window.sessionStorage.clear();
  });

  it("resolves task submit mode with rerun taking precedence over edit", () => {
    expect(resolveTaskSubmitPageMode("")).toEqual({
      mode: "create",
      executionId: null,
    });
    expect(resolveTaskSubmitPageMode("?editExecutionId=mm%3Aedit")).toEqual({
      mode: "edit",
      executionId: "mm:edit",
    });
    expect(
      resolveTaskSubmitPageMode(
        "?editExecutionId=mm%3Aedit&rerunExecutionId=mm%3Arerun",
      ),
    ).toEqual({
      mode: "rerun",
      executionId: "mm:rerun",
    });
  });

  it("builds the canonical Temporal UpdateInputs payload without mutating historical artifacts", () => {
    expect(
      buildTemporalArtifactEditUpdatePayload({
        updateName: "UpdateInputs",
        inputArtifactRef: "art-edited-input",
        parametersPatch: {
          repository: "MoonLadderStudios/MoonMind",
          task: { instructions: "Edited instructions." },
        },
      }),
    ).toEqual({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-edited-input",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: { instructions: "Edited instructions." },
      },
    });
  });

  it("builds the canonical Temporal RequestRerun payload with replacement input lineage", () => {
    expect(
      buildTemporalArtifactEditUpdatePayload({
        updateName: "RequestRerun",
        inputArtifactRef: "art-rerun-input",
        parametersPatch: {
          repository: "MoonLadderStudios/MoonMind",
          task: { instructions: "Rerun with reviewed instructions." },
        },
      }),
    ).toEqual({
      updateName: "RequestRerun",
      inputArtifactRef: "art-rerun-input",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: { instructions: "Rerun with reviewed instructions." },
      },
    });
  });

  it("does not load an execution detail draft in create mode", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Create Task" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/tasks/skills",
        expect.objectContaining({
          headers: { Accept: "application/json" },
        }),
      );
    });
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        /^\/api\/executions\/[^?]+\?source=temporal$/.test(String(url)),
      ),
    ).toBe(false);
  });

  it("reconstructs a create-form draft from Temporal execution fields", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:edit-123",
      workflowType: "MoonMind.Run",
      targetRuntime: "gemini_cli",
      profileId: "profile:gemini-default",
      model: "gemini-2.5-pro",
      effort: "high",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "task-editing-phase-2",
      publishMode: "branch",
      targetSkill: "speckit-implement",
      inputParameters: {
        task: {
          instructions: "Rebuild the Temporal task draft.",
          appliedStepTemplates: [
            {
              slug: "speckit-demo",
              version: "1.2.3",
              inputs: { feature_name: "Task Editing" },
              stepIds: ["tpl:speckit-demo:1.2.3:01"],
              appliedAt: "2026-04-12T00:00:00Z",
              capabilities: ["git"],
            },
          ],
        },
      },
    });

    expect(draft).toMatchObject({
      runtime: "gemini_cli",
      providerProfile: "profile:gemini-default",
      model: "gemini-2.5-pro",
      effort: "high",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "task-editing-phase-2",
      publishMode: "branch",
      taskInstructions: "Rebuild the Temporal task draft.",
      primarySkill: "speckit-implement",
    });
    expect(draft.appliedTemplates).toEqual([
      expect.objectContaining({
        slug: "speckit-demo",
        version: "1.2.3",
        inputs: { feature_name: "Task Editing" },
      }),
    ]);
  });

  it("reconstructs a draft from an artifact-backed execution contract", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:rerun-123",
        workflowType: "MoonMind.Run",
        inputArtifactRef: "historical-input",
        inputParameters: {
          targetRuntime: "codex_cli",
          task: {
            runtime: {
              mode: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              profileId: "profile:codex-secondary",
            },
          },
        },
        actions: {
          canUpdateInputs: false,
          canRerun: true,
        },
      },
      {
        repository: "MoonLadderStudios/MoonMind",
        task: {
          instructions: "Rerun from immutable artifact input.",
          git: {
            startingBranch: "main",
            targetBranch: "rerun-target",
          },
          publish: { mode: "pr" },
          skill: { id: "speckit-orchestrate" },
        },
      },
    );

    expect(draft).toMatchObject({
      runtime: "codex_cli",
      providerProfile: "profile:codex-secondary",
      model: "gpt-5.4",
      effort: "medium",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "rerun-target",
      publishMode: "pr",
      taskInstructions: "Rerun from immutable artifact input.",
      primarySkill: "speckit-orchestrate",
    });
  });

  it("reconstructs primary skill from object-shaped task skill selectors", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:skill-selectors",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Resolve the selected PR.",
          skills: {
            include: [{ name: "pr-resolver" }],
          },
        },
      },
    });

    expect(draft.primarySkill).toBe("pr-resolver");
  });

  it("includes step-level instructions when reconstructing a draft", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:step-instructions",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Top-level objective.",
          steps: [
            { instructions: "First step instructions." },
            { instructions: "Second step instructions." },
          ],
        },
      },
    });

    expect(draft.taskInstructions).toBe(
      [
        "Top-level objective.",
        "First step instructions.",
        "Second step instructions.",
      ].join("\n\n"),
    );
  });

  it("uses null for optional draft fields that cannot be reconstructed", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:minimal",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Only instructions are available.",
        },
      },
    });

    expect(draft).toMatchObject({
      runtime: null,
      providerProfile: null,
      model: null,
      effort: null,
      repository: null,
      startingBranch: null,
      targetBranch: null,
      publishMode: null,
      primarySkill: null,
      taskInstructions: "Only instructions are available.",
    });
  });

  it("fails draft reconstruction when instructions are missing", () => {
    expect(() =>
      buildTemporalSubmissionDraftFromExecution({
        workflowId: "mm:incomplete",
        workflowType: "MoonMind.Run",
        inputParameters: {
          targetRuntime: "codex_cli",
          task: {
            runtime: {
              mode: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
            },
          },
        },
      }),
    ).toThrow("Task instructions could not be reconstructed from this execution.");
  });

  it("loads edit mode from an active Temporal execution and prefills the shared form", async () => {
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Aedit-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Edit Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Rebuild the Temporal task draft.");
      expect((screen.getByLabelText("Runtime") as HTMLSelectElement).value).toBe(
        "gemini_cli",
      );
      expect(
        (screen.getByLabelText("Provider profile") as HTMLSelectElement).value,
      ).toBe("profile:gemini-default");
      expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe(
        "gemini-2.5-pro",
      );
      expect((screen.getByLabelText("Effort") as HTMLInputElement).value).toBe(
        "high",
      );
      expect(
        (screen.getByLabelText(/GitHub Repo/) as HTMLInputElement).value,
      ).toBe("MoonLadderStudios/MoonMind");
      expect(
        (screen.getByLabelText("Starting Branch (optional)") as HTMLInputElement)
          .value,
      ).toBe("main");
      expect(
        (screen.getByLabelText("Target Branch (optional)") as HTMLInputElement)
          .value,
      ).toBe("task-editing-phase-2");
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("branch");
      expect(
        (screen.getByLabelText(/Skill \(optional\)/) as HTMLInputElement).value,
      ).toBe("speckit-implement");
    });
    expect(screen.queryByText("Schedule (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeTruthy();
  });

  it("loads rerun mode instructions from an input artifact when inline instructions are absent", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Arerun-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Rerun Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Rerun from artifact-backed instructions.");
      expect(
        (screen.getByLabelText("Provider profile") as HTMLSelectElement).value,
      ).toBe("profile:codex-secondary");
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/artifacts/historical-input/download",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
    expect(screen.queryByText("Schedule (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Rerun Task" })).toBeTruthy();
  });

  it("submits terminal rerun mode through RequestRerun and returns to the Temporal detail view", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Arerun-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerun from artifact-backed instructions.");
    });
    fireEvent.change(instructions, {
      target: { value: "Rerun with reviewed Temporal inputs." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rerun Task" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Arerun-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Arerun-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST",
    );
    const artifactCreateRequest = JSON.parse(
      String(artifactCreateCall?.[1]?.body),
    );
    expect(artifactCreateRequest).toMatchObject({
      metadata: {
        sourceWorkflowId: "mm:rerun-123",
      },
    });
    expect(request).toMatchObject({
      updateName: "RequestRerun",
      inputArtifactRef: "art-001",
      parametersPatch: {
        inputArtifactRef: "art-001",
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Rerun with reviewed Temporal inputs.",
          runtime: {
            mode: "codex_cli",
            model: "gpt-5.4",
            effort: "medium",
            profileId: "profile:codex-secondary",
          },
          publish: {
            mode: "pr",
          },
        },
      },
    });
    expect(request.inputArtifactRef).not.toBe("historical-input");
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) => String(url) === "/api/executions" && init?.method === "POST",
      ),
    ).toBe(false);
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Arerun-123?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Rerun was requested and the latest execution view is ready.");
  });

  it("shows backend stale-state failures without redirecting from rerun mode", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Astale-rerun",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerunnable before submit.");
    });
    fireEvent.change(instructions, {
      target: { value: "Try to rerun after state changed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rerun Task" }));

    expect(
      await screen.findByText(
        "Workflow state changed and rerun is no longer available.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
  });

  it("loads draft inputs through configured detail and artifact download routes", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Acustom-endpoints",
    );
    const customPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      customPayload.initialData as {
        dashboardConfig: {
          sources: {
            temporal: {
              detail: string;
              artifactDownload: string;
            };
          };
        };
      }
    ).dashboardConfig.sources.temporal = {
      ...(
        customPayload.initialData as {
          dashboardConfig: { sources: { temporal: Record<string, string> } };
        }
      ).dashboardConfig.sources.temporal,
      detail: "/gateway/api/executions/{workflowId}?view=detail",
      artifactDownload: "/gateway/api/artifacts/{artifactId}/raw",
    };

    renderWithClient(<TaskCreatePage payload={customPayload} />);

    expect(await screen.findByRole("heading", { name: "Rerun Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Loaded through configured artifact route.");
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/gateway/api/executions/mm%3Acustom-endpoints?view=detail&source=temporal",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      "/gateway/api/artifacts/custom-input/raw",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("submits edit updates through the configured Temporal update route", async () => {
    const customPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      customPayload.initialData as {
        dashboardConfig: {
          sources: {
            temporal: {
              detail: string;
              update: string;
            };
          };
        };
      }
    ).dashboardConfig.sources.temporal = {
      ...(
        customPayload.initialData as {
          dashboardConfig: { sources: { temporal: Record<string, string> } };
        }
      ).dashboardConfig.sources.temporal,
      detail: "/gateway/api/executions/{workflowId}?view=detail",
      update: "/gateway/api/executions/{workflowId}/updates",
    };

    renderForEdit("mm:custom-edit", customPayload);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Save through configured endpoint.");
    });
    fireEvent.change(instructions, {
      target: { value: "Edited through configured update endpoint." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/gateway/api/executions/mm%3Acustom-edit/updates",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls.find(
      ([url]) => String(url) === "/gateway/api/executions/mm%3Acustom-edit/updates",
    );
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Edited through configured update endpoint.",
        },
      },
    });
  });

  it("shows a feature-disabled error without loading execution detail", async () => {
    const disabledPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      disabledPayload.initialData as {
        dashboardConfig: {
          features: { temporalDashboard: { temporalTaskEditing: boolean } };
        };
      }
    ).dashboardConfig.features.temporalDashboard.temporalTaskEditing = false;

    renderForEdit("mm:edit-123", disabledPayload);

    expect(
      await screen.findByText("Temporal task editing is not enabled."),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/executions/mm%3Aedit-123?source=temporal",
      ),
    ).toBe(false);
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error for unsupported Temporal workflow types", async () => {
    renderForEdit("mm:unsupported");

    expect(
      await screen.findByText(
        "This execution cannot be edited here because only MoonMind.Run is supported.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when edit capability is missing", async () => {
    renderForEdit("mm:no-edit");

    expect(
      await screen.findByText(
        "This execution does not currently allow editing its inputs.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when rerun capability is missing", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Ano-rerun",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText("This execution does not currently allow rerun."),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when the input artifact cannot be read", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Amissing-artifact",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        "Task instructions could not be loaded from the input artifact.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows explicit errors for malformed artifacts and incomplete drafts", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Amalformed-artifact",
    );

    const { unmount } = renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText("Task input artifact did not contain valid JSON."),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    unmount();
    renderForEdit("mm:incomplete");

    expect(
      await screen.findByText(
        "Task instructions could not be reconstructed from this execution.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("submits active edit mode through UpdateInputs and returns to the Temporal detail view", async () => {
    const telemetryEvents: Array<Record<string, unknown>> = [];
    const onTelemetry = (event: Event) => {
      telemetryEvents.push((event as CustomEvent).detail);
    };
    window.addEventListener("moonmind:temporal-task-editing", onTelemetry);

    renderForEdit("mm:edit-123");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rebuild the Temporal task draft.");
    });
    fireEvent.change(instructions, {
      target: { value: "Save edited Temporal inputs." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aedit-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Aedit-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        operatorNote: "Keep this unedited top-level value.",
        targetRuntime: "gemini_cli",
        task: {
          instructions: "Save edited Temporal inputs.",
          proposeTasks: true,
          tool: {
            type: "skill",
            name: "speckit-implement",
          },
          skill: {
            id: "speckit-implement",
          },
          skills: {
            include: [{ name: "speckit-implement" }],
          },
          runtime: {
            mode: "gemini_cli",
            model: "gemini-2.5-pro",
            effort: "high",
            profileId: "profile:gemini-default",
          },
          git: {
            startingBranch: "main",
            targetBranch: "task-editing-phase-2",
          },
          publish: {
            mode: "branch",
          },
          appliedStepTemplates: [
            expect.objectContaining({
              slug: "speckit-demo",
              version: "1.2.3",
              inputs: { feature_name: "Task Editing" },
              stepIds: ["tpl:speckit-demo:1.2.3:01"],
              capabilities: ["git"],
            }),
          ],
        },
      },
    });
    expect(request.inputArtifactRef).toBeUndefined();
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Aedit-123?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were saved to this execution.");
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "draft_reconstruction_success",
          mode: "edit",
          workflowId: "mm:edit-123",
        }),
        expect.objectContaining({
          event: "update_submit_attempt",
          mode: "edit",
          workflowId: "mm:edit-123",
          updateName: "UpdateInputs",
        }),
        expect.objectContaining({
          event: "update_submit_result",
          mode: "edit",
          workflowId: "mm:edit-123",
          updateName: "UpdateInputs",
          result: "success",
          applied: "immediate",
        }),
      ]),
    );
    window.removeEventListener("moonmind:temporal-task-editing", onTelemetry);
  });

  it("shows continue-as-new success copy and redirects to the returned execution context", async () => {
    renderForEdit("mm:continue-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Accept these inputs for the refreshed run." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Acontinue-edit-next?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were accepted and will continue in a refreshed run.");
  });

  it("creates a fresh input artifact when editing an artifact-backed execution", async () => {
    renderForEdit("mm:artifact-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerun from artifact-backed instructions.");
    });
    fireEvent.change(instructions, {
      target: { value: "Edited artifact-backed instructions." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aartifact-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const artifactUploadCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts/art-001/content" &&
        init?.method === "PUT",
    );
    expect(String(artifactUploadCall?.[1]?.body)).toContain(
      "Edited artifact-backed instructions.",
    );
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) => String(url) === "/api/executions/mm%3Aartifact-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-001",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: {
          instructions: "Edited artifact-backed instructions.",
        },
      },
    });
    expect(request.inputArtifactRef).not.toBe("historical-input");
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Aartifact-edit?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were scheduled for the next safe point.");
  });

  it("externalizes oversized edited input before sending UpdateInputs", async () => {
    renderForEdit("mm:edit-123");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rebuild the Temporal task draft.");
    });
    const oversizedInstructions = `Edited oversized instructions. ${"x".repeat(9000)}`;
    fireEvent.change(instructions, {
      target: { value: oversizedInstructions },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aedit-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST",
    );
    expect(artifactCreateCall).toBeTruthy();
    const artifactUploadCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts/art-001/content" &&
        init?.method === "PUT",
    );
    expect(String(artifactUploadCall?.[1]?.body)).toContain(
      oversizedInstructions,
    );
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Aedit-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-001",
      parametersPatch: {
        inputArtifactRef: "art-001",
      },
    });
    expect(request.parametersPatch.task.instructions).toBeUndefined();
  });

  it("shows backend stale-state failures without redirecting from edit mode", async () => {
    renderForEdit("mm:stale-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Editable before submit.");
    });
    fireEvent.change(instructions, {
      target: { value: "Try to update after state changed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(
      await screen.findByText(
        "Workflow is in a terminal state and no longer accepts updates.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
  });

  it("shows backend validation failures without redirecting from edit mode", async () => {
    renderForEdit("mm:validation-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Submit invalid target branch." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(
      await screen.findByText(
        "Task input validation failed: target branch is invalid.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBeNull();
  });

  it("shows artifact preparation failures without submitting UpdateInputs", async () => {
    const telemetryEvents: Array<Record<string, unknown>> = [];
    const onTelemetry = (event: Event) => {
      telemetryEvents.push((event as CustomEvent).detail);
    };
    window.addEventListener("moonmind:temporal-task-editing", onTelemetry);
    artifactCreateResponseOverride = {
      ok: false,
      status: 422,
      text: async () =>
        JSON.stringify({
          detail: {
            code: "artifact_create_failed",
            message: "Artifact storage is unavailable.",
          },
        }),
    } as Response;
    renderForEdit("mm:artifact-failure");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Attempt artifact-backed edit." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(await screen.findByText("Artifact storage is unavailable.")).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/executions/mm%3Aartifact-failure/update",
      ),
    ).toBe(false);
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "draft_reconstruction_success",
          workflowId: "mm:artifact-failure",
        }),
      ]),
    );
    expect(
      telemetryEvents.some(
        (event) => String(event.event) === "update_submit_attempt",
      ),
    ).toBe(false);
    expect(navigateTo).not.toHaveBeenCalled();
    window.removeEventListener("moonmind:temporal-task-editing", onTelemetry);
  });

  it("submits the Temporal task payload and redirects on success", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run end-to-end regression flow." },
    });
    fireEvent.change(screen.getByLabelText(/GitHub Repo/), {
      target: { value: "MoonLadderStudios/MoonMind" },
    });
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "speckit-orchestrate" },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request).toMatchObject({
      type: "task",
      priority: 0,
      maxAttempts: 3,
      payload: {
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Run end-to-end regression flow.",
          tool: {
            type: "skill",
            name: "speckit-orchestrate",
            version: "1.0",
          },
          runtime: {
            mode: "codex_cli",
            model: "gpt-5.4",
            effort: "medium",
          },
          publish: {
            mode: "pr",
          },
          proposeTasks: false,
        },
      },
    });
    expect(request.payload.requiredCapabilities).toEqual([
      "codex_cli",
      "git",
      "gh",
    ]);
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm:workflow-123?source=temporal",
      );
    });
  });

  it("uploads a step attachment and includes it with the step instructions", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachments");
    const file = new File(["fake image"], "wireframe.png", {
      type: "image/png",
    });
    fireEvent.change(attachmentInput, {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts" &&
        String(init?.body || "").includes("task-dashboard-step-attachment"),
    );
    expect(JSON.parse(String(artifactCreateCall?.[1]?.body))).toMatchObject({
      content_type: "image/png",
      size_bytes: file.size,
      metadata: {
        filename: "wireframe.png",
        source: "task-dashboard-step-attachment",
        stepLabel: "Step 1",
      },
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toContain(
      "Review the provided screenshot.",
    );
    expect(request.payload.task.instructions).toContain(
      "Step input attachments:",
    );
    expect(request.payload.task.instructions).toContain(
      "wireframe.png (image/png",
    );
    expect(request.payload.task.inputAttachments).toEqual([
      {
        artifactId: "art-001",
        filename: "wireframe.png",
        contentType: "image/png",
        sizeBytes: file.size,
      },
    ]);
    expect(request.payload.task.steps[0].inputAttachments).toEqual(
      request.payload.task.inputAttachments,
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/artifacts/art-001/links",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("input.attachment"),
      }),
    );
  });

  it("does not upload step attachments when later client validation fails", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachments");
    fireEvent.change(attachmentInput, {
      target: {
        files: [
          new File(["fake image"], "wireframe.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const additionalStep = (await screen.findByText("Step 2")).closest(
      "section",
    );
    expect(additionalStep).not.toBeNull();
    fireEvent.change(
      within(additionalStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        within(additionalStep as HTMLElement).getByLabelText(
          /Skill Args \(optional JSON object\)/,
        ),
      ).not.toBeNull();
    });
    fireEvent.change(
      within(additionalStep as HTMLElement).getByLabelText(
        /Skill Args \(optional JSON object\)/,
      ),
      {
        target: { value: '{"broken":' },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getByText("Step 2 Skill Args must be valid JSON object text."),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it("does not upload step attachments when schedule validation fails", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachments");
    fireEvent.change(attachmentInput, {
      target: {
        files: [
          new File(["fake image"], "wireframe.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), {
      target: { value: "deferred_minutes" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getByText(
          "A valid positive whole number of minutes is required for deferred scheduling.",
        ),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it(
    "retries transient step attachment completion conflicts before creating the task",
    async () => {
      let completeAttempts = 0;
      const originalRetryDelays = [...ARTIFACT_COMPLETE_RETRY_DELAYS_MS];
      const defaultFetch = fetchSpy.getMockImplementation();

      try {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          1,
          1,
        );
        fetchSpy.mockImplementation(
          (input: RequestInfo | URL, init?: RequestInit) => {
            const url = String(input);
            if (url === "/api/artifacts/art-001/complete") {
              completeAttempts += 1;
              if (completeAttempts < 3) {
                return Promise.resolve({
                  ok: false,
                  status: 409,
                  text: async () =>
                    JSON.stringify({
                      detail: {
                        code: "artifact_state_error",
                        message: "artifact upload is not complete",
                      },
                    }),
                } as Response);
              }
            }
            return (
              defaultFetch?.(input, init) ??
              Promise.reject(new Error("Unhandled fetch"))
            );
          },
        );

        renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

        fireEvent.change(await screen.findByLabelText("Instructions"), {
          target: { value: "Review the provided screenshot." },
        });
        const attachmentInput =
          await screen.findByLabelText("Step 1 attachments");
        fireEvent.change(attachmentInput, {
          target: {
            files: [
              new File(["fake image"], "wireframe.png", { type: "image/png" }),
            ],
          },
        });
        fireEvent.click(screen.getByRole("button", { name: "Create" }));

        await waitFor(
          () => {
            expect(completeAttempts).toBe(3);
            expect(fetchSpy).toHaveBeenCalledWith(
              "/api/executions",
              expect.objectContaining({ method: "POST" }),
            );
          },
          { timeout: 9000 },
        );
      } finally {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          ...originalRetryDelays,
        );
      }
    },
    10000,
  );

  it("submits selected task dependencies from the picker", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run the dependent stage." },
    });
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(screen.getByText(/Build shared schema/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.dependsOn).toEqual(["mm:dep-1"]);
  });

  it("defaults publish mode to none when selecting pr-resolver skills", async () => {
    type MockInitialData = {
      dashboardConfig: {
        system: {
          defaultPublishMode: string;
        };
      };
    };

    const payload: BootPayload = {
      ...mockPayload,
      initialData: {
        ...(mockPayload.initialData as MockInitialData),
        dashboardConfig: {
          ...(mockPayload.initialData as MockInitialData).dashboardConfig,
          system: {
            ...(mockPayload.initialData as MockInitialData).dashboardConfig
              .system,
            defaultPublishMode: "pr",
          },
        },
      },
    };

    renderWithClient(<TaskCreatePage payload={payload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();

    const publishSelect = screen.getByLabelText(
      "Publish Mode",
    ) as HTMLSelectElement;
    expect(publishSelect.value).toBe(
      (payload.initialData as MockInitialData).dashboardConfig.system
        .defaultPublishMode,
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });

    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "batch-pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });
  });

  it("submits publish mode none when the selected primary skill is pr-resolver", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Resolve the current branch PR." },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.publish).toMatchObject({
      mode: "none",
    });
  });

  it("submits deferred pr-resolver tasks with object-shaped skill selectors", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Resolve the current branch PR." },
      },
    );
    fireEvent.change(screen.getByLabelText("Schedule Mode"), {
      target: { value: "deferred_minutes" },
    });
    fireEvent.change(screen.getByLabelText("Minutes from now"), {
      target: { value: "5" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.skills).toEqual({
      include: [{ name: "pr-resolver" }],
    });
    expect(request.payload.schedule.mode).toBe("once");
    expect(
      new Date(request.payload.schedule.scheduledFor).getTime(),
    ).toBeGreaterThan(Date.now());
  });

  it("renders the restored legacy create-task controls", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByPlaceholderText("owner/repo")).not.toBeNull();
    expect(await screen.findByLabelText("Provider profile")).not.toBeNull();
    expect(
      await screen.findByLabelText("Feature Request / Initial Instructions"),
    ).not.toBeNull();
    expect(
      await screen.findByPlaceholderText(
        "auto-generated unless starting branch is non-default",
      ),
    ).not.toBeNull();
    expect(await screen.findByDisplayValue("3")).not.toBeNull();
    expect(screen.getByText("Task Presets (optional)")).not.toBeNull();
    expect(screen.getByText("Schedule (optional)")).not.toBeNull();
  });

  it("updates provider-profile options when the selected runtime changes", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const providerSelect = await screen.findByLabelText("Provider profile");
    await waitFor(() => {
      const labels = Array.from(
        (providerSelect as HTMLSelectElement).options,
      ).map((option) => option.text);
      expect(labels).toEqual([
        "Codex Default (Default)",
        "Codex Secondary",
      ]);
      expect((providerSelect as HTMLSelectElement).value).toBe(
        "profile:codex-default",
      );
    });

    fireEvent.change(screen.getByLabelText("Runtime"), {
      target: { value: "gemini_cli" },
    });

    await waitFor(() => {
      const labels = Array.from(
        (providerSelect as HTMLSelectElement).options,
      ).map((option) => option.text);
      expect(labels).toEqual(["Gemini Default (Default)"]);
      expect((providerSelect as HTMLSelectElement).value).toBe(
        "profile:gemini-default",
      );
    });
  });

  it("uploads oversized task input as a JSON artifact before submitting the execution", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Large instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/content",
        expect.objectContaining({ method: "PUT" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/complete",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/links",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.inputArtifactRef).toBe("art-001");
    expect(request.payload.task.instructions).toBeUndefined();

    const uploadCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/artifacts/art-001/content")
      .at(-1);
    const uploadHeaders = new Headers(uploadCall?.[1]?.headers);
    expect(uploadHeaders.get("content-type")).toBe(
      "application/json; charset=utf-8",
    );
    expect(JSON.parse(String(uploadCall?.[1]?.body))).toMatchObject({
      repository: "MoonLadderStudios/MoonMind",
      task: {
        instructions: expect.stringContaining(
          "Large instructions Large instructions",
        ),
      },
    });
  });

  it("uploads task input to the returned single-put URL and finalizes the artifact before task creation", async () => {
    const providerProfileItems = [
      {
        profile_id: "profile:codex-default",
        account_label: "Codex Default",
        is_default: true,
      },
      {
        profile_id: "profile:codex-secondary",
        account_label: "Codex Secondary",
        is_default: false,
      },
    ];

    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/provider-profiles")) {
          return Promise.resolve({
            ok: true,
            json: async () => providerProfileItems,
          } as Response);
        }
        if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
          const depItems = Array.from({ length: 12 }, (_, i) => ({
            taskId: `mm:dep-${i + 1}`,
            workflowType: "MoonMind.Run",
            entry: "run",
            title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
            state: i % 3 === 0 ? "completed" : "executing",
          }));
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: depItems,
            }),
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:workflow-123",
              runId: "run-123",
              namespace: "moonmind",
              redirectPath: "/tasks/mm:workflow-123?source=temporal",
            }),
          } as Response);
        }
        if (url === "/api/artifacts") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              artifact_ref: {
                artifact_id: "art-001",
              },
              upload: {
                mode: "single_put",
                upload_url:
                  "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
                expires_at: "2026-04-02T00:00:00Z",
                max_size_bytes: 100000,
                required_headers: {
                  "content-type": "text/plain",
                  "x-amz-server-side-encryption": "AES256",
                },
              },
            }),
          } as Response);
        }
        if (
          url ===
          "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload"
        ) {
          const headers = new Headers(init?.headers);
          expect(headers.get("content-type")).toBe("text/plain");
          expect(headers.get("x-amz-server-side-encryption")).toBe("AES256");
          return Promise.resolve({
            ok: true,
            text: async () => "",
          } as Response);
        }
        if (url === "/api/artifacts/art-001/complete") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/links") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Large instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
        expect.objectContaining({ method: "PUT" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/complete",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const presignedUploadIndex = fetchSpy.mock.calls.findIndex(
      ([url]) =>
        String(url) ===
        "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
    );
    const completeIndex = fetchSpy.mock.calls.findIndex(
      ([url]) => String(url) === "/api/artifacts/art-001/complete",
    );
    const executionIndex = fetchSpy.mock.calls.findIndex(
      ([url]) => String(url) === "/api/executions",
    );
    expect(presignedUploadIndex).toBeGreaterThanOrEqual(0);
    expect(completeIndex).toBeGreaterThan(presignedUploadIndex);
    expect(executionIndex).toBeGreaterThan(completeIndex);
  });

  it(
    "retries transient artifact completion conflicts before creating the task",
    async () => {
      let completeAttempts = 0;
      const originalRetryDelays = [...ARTIFACT_COMPLETE_RETRY_DELAYS_MS];

      try {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          1,
          1,
          1,
          1,
          1,
        );
        fetchSpy.mockImplementation(
          (input: RequestInfo | URL, init?: RequestInit) => {
            const url = String(input);
            if (url.includes("/api/v1/provider-profiles")) {
              return Promise.resolve({
                ok: true,
                json: async () => [],
              } as Response);
            }
            if (url === "/api/artifacts") {
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  artifact_ref: {
                    artifact_id: "art-001",
                  },
                  upload: {
                    mode: "single_put",
                    upload_url: "/api/artifacts/art-001/content",
                    expires_at: "2026-04-02T00:00:00Z",
                    max_size_bytes: 100000,
                    required_headers: {},
                  },
                }),
              } as Response);
            }
            if (url === "/api/artifacts/art-001/content") {
              return Promise.resolve({
                ok: true,
                text: async () => "",
              } as Response);
            }
            if (url === "/api/artifacts/art-001/complete") {
              completeAttempts += 1;
              if (completeAttempts < 5) {
                return Promise.resolve({
                  ok: false,
                  status: 409,
                  text: async () =>
                    JSON.stringify({
                      detail: {
                        code: "artifact_state_error",
                        message: "artifact upload is not complete",
                      },
                    }),
                } as Response);
              }
              return Promise.resolve({
                ok: true,
                json: async () => ({ artifact_id: "art-001" }),
              } as Response);
            }
            if (url === "/api/artifacts/art-001/links") {
              return Promise.resolve({
                ok: true,
                json: async () => ({ artifact_id: "art-001" }),
              } as Response);
            }
            if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
              const depItems = Array.from({ length: 12 }, (_, i) => ({
                taskId: `mm:dep-${i + 1}`,
                workflowType: "MoonMind.Run",
                entry: "run",
                title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
                state: i % 3 === 0 ? "completed" : "executing",
              }));
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  items: depItems,
                }),
              } as Response);
            }
            if (url === "/api/executions") {
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  workflowId: "mm:workflow-123",
                  runId: "run-123",
                  namespace: "moonmind",
                  redirectPath: "/tasks/mm:workflow-123?source=temporal",
                }),
              } as Response);
            }
            return Promise.resolve({
              ok: false,
              status: 404,
              statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
              text: async () => "Unhandled fetch",
            } as Response);
          },
        );

        renderWithClient(<TaskCreatePage payload={mockPayload} />);

        fireEvent.change(await screen.findByLabelText("Instructions"), {
          target: { value: "Large instructions ".repeat(1000) },
        });
        fireEvent.click(screen.getByRole("button", { name: "Create" }));

        await waitFor(
          () => {
            expect(completeAttempts).toBe(5);
            expect(fetchSpy).toHaveBeenCalledWith(
              "/api/executions",
              expect.objectContaining({ method: "POST" }),
            );
          },
          { timeout: 9000 },
        );
      } finally {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          ...originalRetryDelays,
        );
      }
    },
    10000,
  );

  it("uploads oversized step instructions as a JSON artifact and strips inline step text", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Primary objective" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const stepTextarea = await screen.findByPlaceholderText(
      "Step-specific instructions (leave blank to continue from the task objective).",
    );
    fireEvent.change(stepTextarea, {
      target: { value: "Long step instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.inputArtifactRef).toBe("art-001");
    expect(request.payload.task.instructions).toBe("Primary objective");
    expect(request.payload.task.steps).toEqual([
      {
        instructions: "Primary objective",
      },
      {},
    ]);

    const uploadCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/artifacts/art-001/content")
      .at(-1);
    expect(JSON.parse(String(uploadCall?.[1]?.body))).toMatchObject({
      repository: "MoonLadderStudios/MoonMind",
      task: {
        instructions: "Primary objective",
        steps: expect.arrayContaining([
          expect.objectContaining({
            instructions: "Primary objective",
          }),
          expect.objectContaining({
            instructions: expect.stringContaining(
              "Long step instructions Long step instructions",
            ),
          }),
        ]),
      },
    });
  });

  it("applies a preset into task steps and submits them", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });

    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });

    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );
    await screen.findByDisplayValue(
      "Write a plan for the task builder recovery.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toBe('Task Create');
    // Address: Copilot r3034495957 — assert skills and derived title in preset-submit.
    // The first template step has an explicit skill (speckit-clarify), so effectiveSkillId
    // stays as that explicit skill rather than the template slug.
    expect(request.payload.task.skills).toEqual({
      include: [{ name: "speckit-clarify" }],
    });
    expect(request.payload.task.title).toBe('Task Create');
    expect(request.payload.task.tool.name).toBe('speckit-clarify');
    expect(request.payload.task.skill.id).toBe('speckit-clarify');
    expect(request.payload.task.steps).toEqual([
      {
        id: "tpl:speckit-demo:1.2.3:01",
        title: "Clarify spec",
        instructions: "Clarify the {{ inputs.feature_name }} scope.",
        tool: {
          type: "skill",
          name: "speckit-clarify",
          version: "1.0",
          inputs: { feature: "Task Create" },
        },
        skill: {
          id: "speckit-clarify",
          args: { feature: "Task Create" },
        },
      },
      {
        id: "tpl:speckit-demo:1.2.3:02",
        title: "Plan implementation",
        instructions: "Write a plan for the task builder recovery.",
      },
    ]);
    expect(request.payload.task.appliedStepTemplates).toEqual([
      expect.objectContaining({
        slug: "speckit-demo",
        version: "1.2.3",
      }),
    ]);
  });

  it("derives the task objective from feature-request template input aliases", () => {
    expect(
      resolveObjectiveInstructions("", "", [
        {
          slug: "objective-demo",
          version: "2.0.0",
          appliedAt: "2026-04-03T00:00:00Z",
          inputs: {
            request: "Restore the legacy Create Task objective handling.",
          },
          stepIds: [],
          capabilities: [],
        },
      ]),
    ).toBe("Restore the legacy Create Task objective handling.");
  });

  it("surfaces plain-text execution errors without reading the response body twice", async () => {
    executionResponseOverride = new Response("Plaintext execution failure.", {
      status: 400,
      statusText: "Bad Request",
      headers: {
        "Content-Type": "text/plain",
      },
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run end-to-end regression flow." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(screen.getByText("Plaintext execution failure.")).not.toBeNull();
    });
  });

  it("blocks preset saves when step skill args are invalid JSON", async () => {
    const promptSpy = vi
      .spyOn(window, "prompt")
      .mockImplementationOnce(() => "Saved preset title")
      .mockImplementationOnce(() => "Saved preset description");

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();

    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Capture the current draft as a preset." },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        within(primaryStep as HTMLElement).getByLabelText(
          /Skill Args \(optional JSON object\)/,
        ),
      ).not.toBeNull();
    });
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        /Skill Args \(optional JSON object\)/,
      ),
      {
        target: { value: '{"broken":' },
      },
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Save Current Steps as Preset/ }),
    );

    await waitFor(() => {
      expect(
        screen.getByText("Step 1 Skill Args must be valid JSON object text."),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/task-step-templates/save-from-task",
      ),
    ).toBe(false);

    promptSpy.mockRestore();
  });

  // -----------------------------------------------------------------------
  // Dependency picker hardening tests
  // -----------------------------------------------------------------------

  it("prevents adding the same dependency twice", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Add mm:dep-1 first time.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(screen.getByText(/Build shared schema/)).toBeTruthy();
    });

    // After adding, mm:dep-1 should be removed from the dropdown options
    // (filtered out by availableDependencyOptions).
    const select = screen.getByLabelText("Existing run") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).not.toContain("mm:dep-1");

    // The list should have exactly ONE entry.
    const list = document.getElementById("queue-dependency-list");
    expect(list).toBeTruthy();
    expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(1);

    // Add a second dependency to verify the list grows.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
    });
  });

  it("enforces the 10-item dependency limit", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Add 10 dependencies.
    for (let i = 1; i <= 10; i += 1) {
      fireEvent.change(screen.getByLabelText("Existing run"), {
        target: { value: `mm:dep-${i}` },
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Add dependency" }),
      );
    }

    // Wait for all 10 to appear.
    await waitFor(() => {
      const list = document.getElementById("queue-dependency-list");
      expect(list).toBeTruthy();
      expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(10);
    });

    // Try to add an 11th.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-11" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    // Still 10 items.
    const list = document.getElementById("queue-dependency-list");
    expect(list).toBeTruthy();
    expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(10);

    // Limit-exceeded message should be visible.
    expect(
      screen.getByText(/at most 10 direct dependencies/),
    ).toBeTruthy();
  });

  it("shows validation message when adding dependency without selection", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Click Add without selecting a run.
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Choose a prerequisite run before adding/),
      ).toBeTruthy();
    });
  });

  it("hides Jira browser controls when the runtime config does not enable Jira", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("opens the Jira browser from the preset target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira story" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Feature Request / Initial Instructions"))
      .toBeTruthy();
  });

  it("opens the Jira browser from a step target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira story" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Step 1 Instructions")).toBeTruthy();
  });

  it("loads board columns in order and switches visible Jira issues by column", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "To Do 1" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Doing 1" })).toBeTruthy();
    });

    expect(screen.getByText("ENG-101")).toBeTruthy();
    expect(screen.queryByText("ENG-202")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Doing 1" }));

    expect(await screen.findByText("ENG-202")).toBeTruthy();
    expect(screen.queryByText("ENG-101")).toBeNull();
  });

  it("shows project load failures only inside the Jira browser and keeps manual editing available", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: false,
          status: 503,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_provider_unavailable",
                message: "Jira is unavailable.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira is unavailable.",
      ),
    ).toBeTruthy();
    fireEvent.change(stepInstructions, {
      target: { value: "Write the task manually after Jira failed." },
    });

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Write the task manually after Jira failed.",
    );
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled)
      .toBe(false);
  });

  it("renders empty Jira browser states with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects/ENG/boards") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira boards are available for this project. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("renders empty Jira projects with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira projects are available. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("renders empty Jira columns and issue lists with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/boards/42/columns") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            board: { id: "42", name: "Delivery", projectKey: "ENG" },
            columns: [{ id: "todo", name: "To Do", count: 0 }],
          }),
        } as Response);
      }
      if (url === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [{ id: "todo", name: "To Do", count: 0 }],
            itemsByColumn: { todo: [] },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira stories are available in this column. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /ENG-202/ })).toBeNull();
  });

  it("renders empty Jira columns with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/boards/42/columns") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            board: { id: "42", name: "Delivery", projectKey: "ENG" },
            columns: [],
          }),
        } as Response);
      }
      if (url === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [],
            itemsByColumn: {},
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira columns are available for this board. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
  });

  it("keeps the Jira browser disabled when endpoint templates are incomplete", async () => {
    const payload = withJiraIntegration();
    const initialData = payload.initialData as {
      dashboardConfig: {
        sources?: Record<string, unknown>;
      };
    };
    renderWithClient(
      <TaskCreatePage
        payload={{
          ...payload,
          initialData: {
            ...initialData,
            dashboardConfig: {
              ...initialData.dashboardConfig,
              sources: {
                ...initialData.dashboardConfig.sources,
                jira: {
                  projects: "/api/jira/projects",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("keeps the Jira browser disabled when endpoint templates are not MoonMind-owned paths", async () => {
    const payload = withJiraIntegration();
    const initialData = payload.initialData as {
      dashboardConfig: {
        sources?: Record<string, unknown>;
      };
    };
    renderWithClient(
      <TaskCreatePage
        payload={{
          ...payload,
          initialData: {
            ...initialData,
            dashboardConfig: {
              ...initialData.dashboardConfig,
              sources: {
                ...initialData.dashboardConfig.sources,
                jira: {
                  ...(initialData.dashboardConfig.sources?.jira as Record<
                    string,
                    unknown
                  >),
                  projects: "https://jira.example.test/rest/api/3/project",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("keeps the Jira browser disabled when endpoint templates include padding", async () => {
    const payload = withJiraIntegration();
    const initialData = payload.initialData as {
      dashboardConfig: {
        sources?: Record<string, unknown>;
      };
    };
    renderWithClient(
      <TaskCreatePage
        payload={{
          ...payload,
          initialData: {
            ...initialData,
            dashboardConfig: {
              ...initialData.dashboardConfig,
              sources: {
                ...initialData.dashboardConfig.sources,
                jira: {
                  ...(initialData.dashboardConfig.sources?.jira as Record<
                    string,
                    unknown
                  >),
                  projects: "/api/jira/projects ",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("does not restore Jira project or board defaults after a manual clear", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
    });
    fireEvent.change(projectSelect, { target: { value: "" } });

    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("");
    });
    expect(
      (screen.getByLabelText("Board") as HTMLSelectElement).value,
    ).toBe("");

    fireEvent.change(projectSelect, { target: { value: "ENG" } });
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });
    fireEvent.change(boardSelect, { target: { value: "" } });

    await waitFor(() => {
      expect((boardSelect as HTMLSelectElement).value).toBe("");
    });
  });

  it("loads Jira issue preview when an issue is selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(
      await screen.findByText("Let operators browse Jira stories."),
    ).toBeTruthy();
    expect(
      screen.getByText("Given a board, users can select a story preview."),
    ).toBeTruthy();
  });

  it("does not mutate draft fields when selecting a Jira issue preview", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(stepInstructions, {
      target: { value: "Keep existing step instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findAllByText("Let operators browse Jira stories."))
      .not.toHaveLength(0);
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.",
    );
  });

  it("keeps issue-detail failures local and leaves import actions unavailable", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: false,
          status: 502,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_browser_request_failed",
                message: "Jira story detail failed.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(stepInstructions, {
      target: { value: "Keep existing step instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(
      await screen.findByText(
        "Failed to load Jira story. You can continue creating the task manually. Jira story detail failed.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Replace target text" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Append to target text" })).toBeNull();
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.",
    );
  });

  it("replaces preset instructions with selected Jira import text", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(stepInstructions, {
      target: { value: "Keep existing step instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findAllByText("Let operators browse Jira stories."))
      .not.toHaveLength(0);
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("appends Jira import text to preset instructions", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Append to target text" }));

    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.\n\n---\n\nENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("replaces only the selected step instructions with Jira import text", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const primaryStep = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(primaryStep, {
      target: { value: "Keep primary instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep preset instructions." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const stepInstructions = screen.getAllByLabelText("Instructions");
    const secondStep = stepInstructions[1] as HTMLTextAreaElement;
    const thirdStep = stepInstructions[2] as HTMLTextAreaElement;
    fireEvent.change(secondStep, {
      target: { value: "Replace this secondary step." },
    });
    fireEvent.change(thirdStep, {
      target: { value: "Keep tertiary instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 2 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect((primaryStep as HTMLTextAreaElement).value).toBe(
      "Keep primary instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep preset instructions.",
    );
    expect(secondStep.value).toBe("Complete Jira story ENG-202: Build browser shell");
    expect(thirdStep.value).toBe("Keep tertiary instructions.");
  });

  it("defaults step-target Jira imports to execution brief mode", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    expect((screen.getByLabelText("Import mode") as HTMLSelectElement).value)
      .toBe("execution-brief");
    expect(
      screen.getByText("Complete Jira story ENG-202: Build browser shell"),
    ).toBeTruthy();
  });

  it("uses an unnamed Jira story fallback when issue title metadata is empty", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "",
              stepInstructions: "",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Complete Jira story (unnamed)")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Complete Jira story (unnamed)",
    );
  });

  it("preserves existing target text when selected Jira import mode is empty", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "",
              stepInstructions: "",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(stepInstructions, {
      target: { value: "Keep existing step instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.getByLabelText("Import mode")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("Import mode"), {
      target: { value: "acceptance-only" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.getByLabelText("Import mode")).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText("Import mode"), {
      target: { value: "description-only" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Append to target text" }));

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
  });

  it("imports selected Jira text by mode", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findAllByText("Let operators browse Jira stories."))
      .not.toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Import mode"), {
      target: { value: "acceptance-only" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Given a board, users can select a story preview.",
    );
  });

  it("marks preset instructions as needing reapply after Jira import changes an applied preset", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.getByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reapply preset" })).toBeTruthy();
    expect(
      screen.getByDisplayValue("Clarify the {{ inputs.feature_name }} scope."),
    ).toBeTruthy();

    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );
    expect(
      screen.queryByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
  });

  it("does not mark preset instructions as needing reapply when Jira import leaves text unchanged", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });
    const importedText =
      "ENG-202: Build browser shell\n\nLet operators browse Jira stories.";
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: importedText },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.queryByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeNull();
  });

  it("detaches template step identity when Jira import edits a template-bound step", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 2 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.steps).toEqual([
      expect.objectContaining({
        id: "tpl:speckit-demo:1.2.3:01",
        instructions: "Clarify the {{ inputs.feature_name }} scope.",
      }),
      expect.objectContaining({
        instructions: "Complete Jira story ENG-202: Build browser shell",
      }),
    ]);
    expect([undefined, null, ""]).toContain(
      request.payload.task.steps[1]?.id,
    );
  });

  it("warns before importing Jira text into a template-bound step", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 2 instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Importing into this template-bound step will make it manually customized.",
      ),
    ).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.queryByText(
        "Importing into this template-bound step will make it manually customized.",
      ),
    ).toBeNull();
  });

  it("shows Jira provenance chips after importing into preset and step targets", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.getByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ).textContent,
    ).toBe("Jira: ENG-202");

    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Append to target text" }));

    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions")
        .textContent,
    ).toBe("Jira: ENG-202");
  });

  it("reopens Jira from an imported field with the prior issue selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("button", { name: "Replace target text" }),
    ).toBeTruthy();
    expect((screen.getByLabelText("Project") as HTMLSelectElement).value).toBe(
      "ENG",
    );
    expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
      "42",
    );
    expect(
      screen
        .getByRole("button", { name: "Doing 1" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("reopens Jira from an imported field with a hyphenated project key selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    await screen.findByText("Hyphenated Project (MY-PROJ)");
    fireEvent.change(screen.getByLabelText("Project"), {
      target: { value: "MY-PROJ" },
    });
    await screen.findByText("Hyphenated Delivery");
    fireEvent.change(screen.getByLabelText("Board"), {
      target: { value: "84" },
    });
    await waitFor(() => {
      expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
        "84",
      );
    });
    fireEvent.click(await screen.findByRole("button", { name: "Selected 1" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /MY-PROJ-123/ }),
    );
    expect(await screen.findByText("Keep the full Jira project key."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("button", { name: "Replace target text" }),
    ).toBeTruthy();
    expect((screen.getByLabelText("Project") as HTMLSelectElement).value).toBe(
      "MY-PROJ",
    );
    expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
      "84",
    );
  });

  it("clears Jira provenance chips when imported text is manually edited", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    const stepInstructions = screen.getByLabelText("Instructions");

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.getByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeTruthy();
    fireEvent.change(presetInstructions, {
      target: { value: "Manual preset instructions." },
    });
    expect(
      screen.queryByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeTruthy();
    fireEvent.change(stepInstructions, {
      target: { value: "Manual step instructions." },
    });
    expect(
      screen.queryByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeNull();
  });

  it("clears step Jira provenance when the imported step is removed", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove step" }));
    expect(
      screen.queryByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeNull();
  });

  it("does not render a Jira provenance chip when the imported issue has no issue key", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "Let operators browse Jira stories.",
              stepInstructions: "Complete the Jira browser story.",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findAllByText("Let operators browse Jira stories."))
      .not.toHaveLength(0);
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );

    expect(
      screen.queryByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeNull();
  });

  it("keeps Jira provenance out of the task submission payload", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    expect(await screen.findByText("Let operators browse Jira stories."))
      .toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace target text" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    const requestKeys = collectObjectKeys(request);
    expect(requestKeys).not.toContain("jiraProvenance");
    expect(requestKeys).not.toContain("sessionJiraSelection");
    expect(requestKeys).not.toContain("issueKey");
    expect(requestKeys).not.toContain("boardId");
    expect(requestKeys).not.toContain("importMode");
    expect(requestKeys).not.toContain("targetType");
  });

  it("submits a manual task with unchanged payload shape after Jira failure", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: false,
          status: 503,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_provider_unavailable",
                message: "Jira is unavailable.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(presetInstructions, {
      target: { value: "Manual objective after Jira failure." },
    });
    fireEvent.change(stepInstructions, {
      target: { value: "Manual step after Jira failure." },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira is unavailable.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toBe(
      "Manual objective after Jira failure.",
    );
    expect(request.payload.task.steps[0].instructions).toBe(
      "Manual step after Jira failure.",
    );
    const requestKeys = collectObjectKeys(request);
    expect(requestKeys).not.toContain("jiraProvenance");
    expect(requestKeys).not.toContain("jiraFailure");
    expect(requestKeys).not.toContain("issueKey");
  });

  it("restores the last selected Jira project and board from session storage", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("7");
    });
  });

  it("falls back when remembered Jira project and board selections are stale", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "OLD",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "999",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });
    expect(
      window.sessionStorage.getItem("moonmind.task-create.jira.last-project-key"),
    ).toBeNull();
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("does not write or restore Jira project and board session memory when disabled", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(
      <TaskCreatePage payload={withJiraSessionMemory(false)} />,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });

    window.sessionStorage.clear();
    fireEvent.change(boardSelect, { target: { value: "7" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("clears remembered Jira project and board when selections are manually cleared", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("7");
    });

    fireEvent.change(projectSelect, { target: { value: "" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-project-key"))
      .toBeNull();
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();

    fireEvent.change(projectSelect, { target: { value: "ENG" } });
    fireEvent.change(boardSelect, { target: { value: "7" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBe("7");

    fireEvent.change(boardSelect, { target: { value: "" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("keeps Jira browsing and manual task creation available when session storage fails", async () => {
    const getItemSpy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
        return null;
      });
    const setItemSpy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
      });
    const removeItemSpy = vi
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
      });

    try {
      renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

      fireEvent.click(
        await screen.findByRole("button", {
          name: "Browse Jira story for preset instructions",
        }),
      );

      expect(
        await screen.findByRole("dialog", { name: "Browse Jira story" }),
      ).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
      fireEvent.change(screen.getByLabelText("Instructions"), {
        target: { value: "Create this task manually." },
      });
      fireEvent.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() => {
        expect(fetchSpy).toHaveBeenCalledWith(
          "/api/executions",
          expect.objectContaining({ method: "POST" }),
        );
      });
    } finally {
      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
      removeItemSpy.mockRestore();
    }
  });

  it("shows Jira browser failures locally", async () => {
    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: false,
            status: 503,
            statusText: "Service Unavailable",
            text: async () => "Jira unavailable",
          } as Response);
        }
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: [] } }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira unavailable",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Browse Jira story" }))
      .toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("keeps structured Jira endpoint failures local and manual creation available", async () => {
    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: false,
            status: 502,
            statusText: "Bad Gateway",
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "jira_request_failed",
                  message: "Jira was temporarily unavailable.",
                  source: "jira_browser",
                },
              }),
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ workflowId: "mm:workflow-123" }),
          } as Response);
        }
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: [] } }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira was temporarily unavailable.",
      ),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Create this task manually." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
