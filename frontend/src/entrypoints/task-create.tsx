import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactElement } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";

import type { BootPayload } from "../boot/parseBootPayload";
import { useLiquidGL } from "../lib/liquidGL/useLiquidGL";
import { navigateTo } from "../lib/navigation";
import {
  buildTemporalArtifactEditUpdatePayload,
  buildTemporalSubmissionDraftFromExecution,
  recordTemporalTaskEditingClientEvent,
  resolveTaskSubmitPageMode,
  type TemporalTaskEditingExecutionContract,
  type TemporalTaskInputAttachmentRef,
} from "../lib/temporalTaskEditing";

// This cutoff is enforced on UTF-8 encoded request bytes, not JavaScript string length.
const INLINE_TASK_INPUT_LIMIT_BYTES = 8_000;
export const ARTIFACT_COMPLETE_RETRY_DELAYS_MS = [250, 500, 1000, 2000, 2000];
const ARTIFACT_COMPLETE_RETRY_MESSAGE = "artifact upload is not complete";
const SKILL_OPTIONS_DATALIST_ID = "queue-skill-options";
const MODEL_OPTIONS_DATALIST_ID = "queue-model-options";
const EFFORT_OPTIONS_DATALIST_ID = "queue-effort-options";
const REPOSITORY_OPTIONS_DATALIST_ID = "queue-repository-options";
const BRANCH_OPTIONS_DATALIST_ID = "queue-branch-options";
const OWNER_REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const PR_RESOLVER_SKILLS = new Set(["pr-resolver", "batch-pr-resolver"]);
const JIRA_BREAKDOWN_PRESET_SLUG = "jira-breakdown";
const JIRA_BREAKDOWN_ORCHESTRATE_PRESET_SLUG = "jira-breakdown-orchestrate";
const JIRA_ORCHESTRATE_PRESET_SLUG = "jira-orchestrate";
const SELF_MANAGED_PUBLISH_SKILLS = new Set([
  ...PR_RESOLVER_SKILLS,
  JIRA_BREAKDOWN_PRESET_SLUG,
  JIRA_BREAKDOWN_ORCHESTRATE_PRESET_SLUG,
]);
const MOONSPEC_ORCHESTRATE_PRESET_SLUG = "moonspec-orchestrate";
const HIDDEN_PRESET_INPUT_KEYS: Record<string, Set<string>> = {
  [JIRA_ORCHESTRATE_PRESET_SLUG]: new Set([
    "orchestrationmode",
    "sourcedesignpath",
    "constraints",
  ]),
  [MOONSPEC_ORCHESTRATE_PRESET_SLUG]: new Set(["orchestrationmode"]),
};
const PROPOSE_TASKS_PREFERENCE_KEY = "moonmind.task-create.propose-tasks";
const LAST_REPOSITORY_OPTION_PREFERENCE_KEY =
  "moonmind.task-create.last-repository-option";
const JIRA_LAST_PROJECT_SESSION_KEY =
  "moonmind.task-create.jira.last-project-key";
const JIRA_LAST_BOARD_SESSION_KEY =
  "moonmind.task-create.jira.last-board-id";
const JIRA_MANUAL_CONTINUATION_MESSAGE =
  "You can continue creating the task manually.";
const DEPENDENCY_LIMIT = 10;
const PRESET_REAPPLY_REQUIRED_MESSAGE =
  "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.";

function readProposeTasksPreference(defaultValue: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(PROPOSE_TASKS_PREFERENCE_KEY);
    if (raw === null) {
      return defaultValue;
    }
    if (raw === "true" || raw === "1") {
      return true;
    }
    if (raw === "false" || raw === "0") {
      return false;
    }
  } catch {
    // Preserve default behavior when localStorage is unavailable.
  }
  return defaultValue;
}

function writeProposeTasksPreference(value: boolean): void {
  try {
    window.localStorage.setItem(
      PROPOSE_TASKS_PREFERENCE_KEY,
      value ? "true" : "false",
    );
  } catch {
    // Ignore localStorage write failures to keep task submission behavior unaffected.
  }
}

function readLocalPreference(key: string): string {
  try {
    return String(window.localStorage.getItem(key) || "").trim();
  } catch {
    return "";
  }
}

function writeLocalPreference(key: string, value: string): void {
  try {
    const normalized = value.trim();
    if (normalized) {
      window.localStorage.setItem(key, normalized);
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Keep browser preferences best-effort.
  }
}

function readSessionPreference(key: string): string {
  try {
    return String(window.sessionStorage.getItem(key) || "").trim();
  } catch {
    return "";
  }
}

function writeSessionPreference(key: string, value: string): void {
  try {
    const normalized = value.trim();
    if (normalized) {
      window.sessionStorage.setItem(key, normalized);
    } else {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // Keep Jira browser preferences best-effort and local to this session.
  }
}

type RepositoryOption = {
  value: string;
  label: string;
};

function normalizeRepositoryOptions(
  items:
    | Array<{
        value?: string | null;
        label?: string | null;
      }>
    | undefined,
): RepositoryOption[] {
  const seen = new Set<string>();
  return (Array.isArray(items) ? items : [])
    .map((item) => ({
      value: String(item?.value || "").trim(),
      label: String(item?.label || item?.value || "").trim(),
    }))
    .filter((item) => {
      if (!item.value) {
        return false;
      }
      const key = item.value.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function repositoryOptionValue(
  repositoryOptions: RepositoryOption[],
  value: string,
): string {
  const normalized = value.trim();
  if (!normalized) {
    return "";
  }
  return (
    repositoryOptions.find(
      (item) => item.value.toLowerCase() === normalized.toLowerCase(),
    )?.value || ""
  );
}

type TemplateScope = "global" | "personal";
type ScheduleMode = "immediate" | "once" | "deferred_minutes" | "recurring";

interface DashboardConfig {
  sources?: {
    temporal?: {
      create?: string;
      update?: string;
      artifactCreate?: string;
      artifactDownload?: string;
      detail?: string;
      list?: string;
    };
    github?: {
      branches?: string;
    };
    jira?: {
      connections?: string;
      projects?: string;
      boards?: string;
      columns?: string;
      issues?: string;
      issue?: string;
    };
  };
  features?: {
    temporalDashboard?: {
      temporalTaskEditing?: boolean;
    };
  };
  system?: {
    defaultRepository?: string;
    defaultTaskRuntime?: string;
    defaultTaskModel?: string;
    defaultTaskEffort?: string;
    defaultPublishMode?: string;
    defaultProposeTasks?: boolean;
    defaultTaskModelByRuntime?: Record<string, string>;
    defaultTaskEffortByRuntime?: Record<string, string>;
    supportedTaskRuntimes?: string[];
    repositoryOptions?: {
      items?: Array<{
        value?: string | null;
        label?: string | null;
        source?: string | null;
      }>;
      error?: string | null;
    };
    providerProfiles?: {
      list?: string;
    };
    taskTemplateCatalog?: {
      enabled?: boolean;
      templateSaveEnabled?: boolean;
      list?: string;
      detail?: string;
      expand?: string;
      saveFromTask?: string;
    };
    attachmentPolicy?: {
      enabled?: boolean;
      maxCount?: number;
      maxBytes?: number;
      totalBytes?: number;
      allowedContentTypes?: string[];
    };
    jiraIntegration?: {
      enabled?: boolean;
      defaultProjectKey?: string;
      defaultBoardId?: string;
      rememberLastBoardInSession?: boolean;
    };
  };
}

interface JiraIntegrationConfig {
  enabled: boolean;
  defaultProjectKey: string;
  defaultBoardId: string;
  rememberLastBoardInSession: boolean;
  endpoints: {
    connections: string;
    projects: string;
    boards: string;
    columns: string;
    issues: string;
    issue: string;
  };
}

type JiraEndpointTemplates = JiraIntegrationConfig["endpoints"];

interface JiraProject {
  projectKey: string;
  name: string;
  id?: string | null;
}

interface JiraBoard {
  id: string;
  name: string;
  projectKey?: string | null;
}

interface JiraColumn {
  id: string;
  name: string;
  count?: number | null;
}

interface JiraIssueSummary {
  issueKey: string;
  summary: string;
  issueType?: string | null;
  statusName?: string | null;
  assignee?: string | null;
  updatedAt?: string | null;
}

interface JiraBoardIssues {
  columns: JiraColumn[];
  itemsByColumn: Record<string, JiraIssueSummary[]>;
}

function isJiraColumn(value: unknown): value is JiraColumn {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const column = value as Record<string, unknown>;
  return (
    typeof column.id === "string" &&
    typeof column.name === "string" &&
    (column.count === undefined ||
      column.count === null ||
      typeof column.count === "number")
  );
}

function parseJiraColumns(value: unknown): JiraColumn[] {
  return Array.isArray(value) ? value.filter(isJiraColumn) : [];
}

interface JiraIssueDetail extends JiraIssueSummary {
  url?: string | null;
  column?: JiraColumn | null;
  status?: {
    id?: string | null;
    name?: string | null;
  } | null;
  descriptionText?: string | null;
  acceptanceCriteriaText?: string | null;
  recommendedImports?: {
    presetInstructions?: string | null;
    stepInstructions?: string | null;
  } | null;
  attachments?: JiraIssueAttachment[];
}

interface JiraIssueAttachment {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes?: number | null;
  downloadUrl: string;
}

type JiraImportTarget =
  | { kind: "preset"; attachmentsOnly?: boolean }
  | { kind: "step"; localId: string; attachmentsOnly?: boolean };

type JiraImportMode =
  | "preset-brief"
  | "execution-brief"
  | "description-only"
  | "acceptance-only";

interface JiraImportProvenance {
  issueKey: string;
  boardId: string;
  columnId: string;
  importMode: JiraImportMode;
  targetType: JiraImportTarget["kind"];
}

interface ProviderProfile {
  profile_id: string;
  account_label?: string | null;
  default_model?: string | null;
  is_default?: boolean;
}

interface BranchOption {
  value: string;
  label: string;
  source: string;
}

interface BranchListResponse {
  items?: Array<{
    value?: string | null;
    label?: string | null;
    source?: string | null;
  }>;
  error?: string | null;
  defaultBranch?: string | null;
}

function resolveDefaultProviderProfileId(
  profiles: ProviderProfile[],
): string {
  const explicitDefault = profiles.find((profile) => profile.is_default);
  if (explicitDefault) {
    return explicitDefault.profile_id;
  }
  const onlyProfile = profiles[0];
  if (profiles.length === 1 && onlyProfile) {
    return onlyProfile.profile_id;
  }
  return profiles[0]?.profile_id || "";
}

interface SkillsResponse {
  items?: {
    worker?: string[];
  };
}

interface DependencyPickerExecution {
  taskId: string;
  workflowType?: string | null;
  entry?: string | null;
  title: string;
  state?: string | null;
}

interface DependencyPickerListResponse {
  items?: DependencyPickerExecution[];
}

interface ExecutionCreateResponse {
  workflowId?: string;
  runId?: string;
  temporalRunId?: string;
  namespace?: string;
  redirectPath?: string;
  definitionId?: string;
}

interface TemporalSubmissionDraftLoadResult {
  execution: TemporalTaskEditingExecutionContract;
  draft: ReturnType<typeof buildTemporalSubmissionDraftFromExecution>;
  artifactInput?: Record<string, unknown> | undefined;
}

interface ResponseErrorDetail {
  code: string | null;
  message: string;
}

interface TaskTemplateInputDefinition {
  name: string;
  label: string;
  type:
    | "text"
    | "textarea"
    | "markdown"
    | "enum"
    | "boolean"
    | "user"
    | "team"
    | "repo_path"
    | "jira_board";
  required?: boolean;
  default?: unknown;
  options?: string[];
  placeholder?: string | null;
}

interface TaskTemplateSummary {
  slug: string;
  scope: TemplateScope;
  scopeRef?: string | null;
  title: string;
  description: string;
  latestVersion: string;
  version: string;
}

interface TaskTemplateDetail extends TaskTemplateSummary {
  inputs?: TaskTemplateInputDefinition[];
}

interface TaskTemplateListResponse {
  items?: TaskTemplateSummary[];
}

interface TaskTemplateStepSkill {
  id?: string;
  name?: string;
  type?: string;
  args?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  requiredCapabilities?: string[];
}

interface ExpandedStepPayload {
  id?: string;
  title?: string;
  instructions?: string;
  skill?: TaskTemplateStepSkill;
  tool?: TaskTemplateStepSkill;
  type?: string;
  source?: Record<string, unknown>;
  inputAttachments?: StepAttachmentRef[];
  attachments?: StepAttachmentRef[];
  storyOutput?: Record<string, unknown>;
  story_output?: Record<string, unknown>;
  jiraOrchestration?: Record<string, unknown>;
  jira_orchestration?: Record<string, unknown>;
}

interface TaskTemplateExpandResponse {
  steps?: ExpandedStepPayload[];
  appliedTemplate?: {
    slug?: string;
    version?: string;
    inputs?: Record<string, unknown>;
    stepIds?: string[];
    appliedAt?: string;
  };
  capabilities?: string[];
  warnings?: string[];
}

interface TemplateOption extends TaskTemplateSummary {
  key: string;
}

interface TemplateCatalogResult {
  items: TemplateOption[];
  failedScopes: TemplateScope[];
}

interface TrustedToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

interface ToolDiscoveryResponse {
  tools?: TrustedToolDefinition[];
}

interface ToolChoiceGroup {
  group: string;
  tools: TrustedToolDefinition[];
}

interface JiraTransitionOption {
  id: string;
  name: string;
}

interface JiraTransitionState {
  isLoading: boolean;
  error: string | null;
  issueKey: string;
  toolId: string;
  options: JiraTransitionOption[];
}

interface AttachmentPolicy {
  enabled: boolean;
  maxCount: number;
  maxBytes: number;
  totalBytes: number;
  allowedContentTypes: string[];
}

interface StepAttachmentRef {
  artifactId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
}

type StepType = "tool" | "skill" | "preset";

const STEP_TYPE_HELP_TEXT: Record<StepType, string> = {
  skill: "Skill asks an agent to perform work using reusable behavior.",
  tool: "Tool runs a typed integration or system operation directly.",
  preset: "Preset inserts a reusable set of configured steps.",
};

const STEP_TYPE_OPTIONS: Array<{
  value: StepType;
  label: string;
  Icon: () => ReactElement;
}> = [
  { value: "skill", label: "Skill", Icon: SkillSparkleIcon },
  { value: "tool", label: "Tool", Icon: ToolWrenchIcon },
  { value: "preset", label: "Preset", Icon: PresetLayersIcon },
];

function usesGenericInstructionsLabel(stepType: StepType): boolean {
  return stepType === "preset" || stepType === "skill";
}

interface StepState {
  localId: string;
  id: string;
  title: string;
  stepType: StepType;
  instructions: string;
  toolId: string;
  toolVersion: string;
  toolInputs: string;
  skillId: string;
  skillArgs: string;
  skillRequiredCapabilities: string;
  presetKey: string;
  presetInputValues: Record<string, string | boolean>;
  presetDetail: TaskTemplateDetail | null;
  submitExpansion?: PresetSubmitExpansionState | null;
  presetMessage: string | null;
  stepTypeMessage: string | null;
  templateStepId: string;
  templateInstructions: string;
  inputAttachments: StepAttachmentRef[];
  templateAttachments: StepAttachmentRef[];
  generatedTool?: TaskTemplateStepSkill;
  generatedSkill?: TaskTemplateStepSkill;
  source?: Record<string, unknown>;
  storyOutput?: Record<string, unknown>;
  jiraOrchestration?: Record<string, unknown>;
}

interface PresetSubmitExpansionState {
  status: "idle" | "queued" | "expanding" | "expanded" | "failed";
  requestId?: string;
  message?: string | null;
  errorMessage?: string | null;
}

interface PresetExpansionState {
  presetKey: string;
  presetTitle: string;
  version: string;
  expandedSteps: ExpandedStepPayload[];
  inputs: Record<string, unknown>;
  assumptions: string[];
  capabilities: string[];
  warnings: string[];
  appliedTemplate?: TaskTemplateExpandResponse["appliedTemplate"];
}

interface AppliedTemplateState {
  slug: string;
  version: string;
  inputs: Record<string, unknown>;
  stepIds: string[];
  appliedAt: string;
  capabilities: string[];
}

function readDashboardConfig(payload: BootPayload): DashboardConfig {
  const raw = payload.initialData as
    | { dashboardConfig?: DashboardConfig }
    | undefined;
  return raw?.dashboardConfig ?? {};
}

function templateKey(
  scope: TemplateScope,
  slug: string,
  scopeRef?: string | null,
): string {
  return `${scope}::${String(scopeRef || "").trim()}::${slug}`;
}

function interpolatePath(
  template: string,
  replacements: Record<string, string>,
): string {
  return Object.entries(replacements).reduce(
    (value, [key, replacement]) =>
      value.replaceAll(`{${key}}`, encodeURIComponent(replacement)),
    template,
  );
}

function withQueryParams(
  baseUrl: string,
  params: Record<string, string | null | undefined>,
): string {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, value);
    }
  });
  const serialized = searchParams.toString();
  if (!serialized) {
    return baseUrl;
  }
  return `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}${serialized}`;
}

function configuredTemporalDetailUrl(
  detailTemplate: string,
  workflowId: string,
): string {
  return withQueryParams(
    interpolatePath(detailTemplate, { workflowId }),
    { source: "temporal" },
  );
}

function configuredTemporalUpdateUrl(
  updateTemplate: string,
  workflowId: string,
): string {
  return interpolatePath(updateTemplate, { workflowId });
}

function configuredBranchLookupUrl(
  branchTemplate: string,
  repository: string,
): string {
  return interpolatePath(branchTemplate, { repository });
}

function configuredArtifactDownloadUrl(
  downloadTemplate: string,
  artifactId: string,
): string {
  return interpolatePath(downloadTemplate, { artifactId });
}

function hasInlineTaskInstructions(task: unknown): boolean {
  if (!task || typeof task !== "object" || Array.isArray(task)) {
    return false;
  }
  const taskRecord = task as Record<string, unknown>;
  if (String(taskRecord.instructions || "").trim()) {
    return true;
  }
  const steps = Array.isArray(taskRecord.steps) ? taskRecord.steps : [];
  return steps.some((step) => {
    if (!step || typeof step !== "object" || Array.isArray(step)) {
      return false;
    }
    return String((step as Record<string, unknown>).instructions || "").trim();
  });
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function nonEmptyRecordValue(value: unknown): Record<string, unknown> | undefined {
  const record = recordValue(value);
  return Object.keys(record).length > 0 ? record : undefined;
}

function cloneJsonRecord(value: Record<string, unknown>): Record<string, unknown> {
  return structuredClone(value) as Record<string, unknown>;
}

function artifactInputTaskRecord(
  artifactInput: Record<string, unknown>,
): Record<string, unknown> {
  const snapshotDraft = recordValue(artifactInput.draft);
  const source =
    Object.keys(snapshotDraft).length > 0 ? snapshotDraft : artifactInput;
  return recordValue(source.task);
}

function artifactInputHasStepInstructionGaps(
  artifactInput: Record<string, unknown>,
): boolean {
  const task = artifactInputTaskRecord(artifactInput);
  const steps = task.steps;
  if (!Array.isArray(steps)) {
    return false;
  }
  const appliedStepIds = new Set<string>();
  const appliedTemplates = Array.isArray(task.appliedStepTemplates)
    ? task.appliedStepTemplates
    : [];
  appliedTemplates.forEach((template) => {
    const templateRecord = recordValue(template);
    const stepIds = Array.isArray(templateRecord.stepIds)
      ? templateRecord.stepIds
      : [];
    stepIds.forEach((stepId) => {
      const normalized = String(stepId || "").trim();
      if (normalized) {
        appliedStepIds.add(normalized);
      }
    });
  });
  let sawInstruction = false;
  return steps.some((step) => {
    const stepRecord = recordValue(step);
    const stepId = String(stepRecord.id || "").trim();
    if (String(stepRecord.instructions || "").trim()) {
      sawInstruction = true;
      return false;
    }
    if (!sawInstruction || !stepId || !appliedStepIds.has(stepId)) {
      return false;
    }
    return (
      stepId.startsWith("tpl:") &&
      (Boolean(recordValue(stepRecord.tool).name) ||
        Boolean(recordValue(stepRecord.skill).id) ||
        Boolean(recordValue(stepRecord.skill).name))
    );
  });
}

function mergeMissingTaskInstructionsFromArtifact(
  artifactInput: Record<string, unknown>,
  sourceArtifactInput: Record<string, unknown>,
): Record<string, unknown> {
  const sourceTask = artifactInputTaskRecord(sourceArtifactInput);
  const sourceSteps = Array.isArray(sourceTask.steps) ? sourceTask.steps : [];
  if (sourceSteps.length === 0) {
    return artifactInput;
  }

  const merged = cloneJsonRecord(artifactInput);
  const mergedDraft = recordValue(merged.draft);
  const mergedSource =
    Object.keys(mergedDraft).length > 0 ? mergedDraft : merged;
  const mergedTask = recordValue(mergedSource.task);
  const mergedSteps = Array.isArray(mergedTask.steps) ? mergedTask.steps : [];
  if (mergedSteps.length === 0) {
    return artifactInput;
  }

  let changed = false;
  const sourceStepsById = new Map<string, Record<string, unknown>>();
  sourceSteps.forEach((step) => {
    const stepRecord = recordValue(step);
    const stepId = String(stepRecord.id || "").trim();
    if (stepId) {
      sourceStepsById.set(stepId, stepRecord);
    }
  });

  const sourceTaskInstructions = String(sourceTask.instructions || "").trim();
  if (
    sourceTaskInstructions &&
    !String(mergedTask.instructions || "").trim()
  ) {
    mergedTask.instructions = sourceTask.instructions;
    changed = true;
  }

  mergedTask.steps = mergedSteps.map((step, index) => {
    const stepRecord = recordValue(step);
    if (Object.keys(stepRecord).length === 0) {
      return step;
    }
    if (String(stepRecord.instructions || "").trim()) {
      return step;
    }
    const stepId = String(stepRecord.id || "").trim();
    const sourceStep = stepId
      ? sourceStepsById.get(stepId)
      : recordValue(sourceSteps[index]);
    const sourceInstructions = String(sourceStep?.instructions || "").trim();
    if (!sourceInstructions) {
      return step;
    }
    changed = true;
    return {
      ...stepRecord,
      instructions: String(sourceStep?.instructions),
    };
  });
  mergedSource.task = mergedTask;
  return changed ? merged : artifactInput;
}

function mergeRecordValues(
  base: Record<string, unknown>,
  overlay: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...base,
    ...overlay,
  };
}

function artifactInputParametersForPatch(
  execution: TemporalTaskEditingExecutionContract,
  artifactInput?: Record<string, unknown> | undefined,
): {
  parameters: Record<string, unknown>;
  fromAuthoritativeDraft: boolean;
} {
  const artifactRecord = recordValue(artifactInput);
  const snapshotDraft = recordValue(artifactRecord.draft);
  if (
    execution.taskInputSnapshot?.reconstructionMode === "authoritative" &&
    Object.keys(snapshotDraft).length > 0
  ) {
    return {
      parameters: snapshotDraft,
      fromAuthoritativeDraft: true,
    };
  }
  return {
    parameters: artifactRecord,
    fromAuthoritativeDraft: false,
  };
}

function buildEditParametersPatch({
  execution,
  artifactInput,
  submittedPayload,
}: {
  execution: TemporalTaskEditingExecutionContract;
  artifactInput?: Record<string, unknown> | undefined;
  submittedPayload: Record<string, unknown>;
}): Record<string, unknown> {
  const artifactBase = artifactInputParametersForPatch(execution, artifactInput);
  const executionParameters = recordValue(execution.inputParameters);
  const baseParameters = mergeRecordValues(
    artifactBase.fromAuthoritativeDraft
      ? executionParameters
      : artifactBase.parameters,
    artifactBase.fromAuthoritativeDraft
      ? artifactBase.parameters
      : executionParameters,
  );
  const submittedTask = recordValue(submittedPayload.task);
  const artifactBaseTask = recordValue(artifactBase.parameters.task);
  const executionTask = recordValue(executionParameters.task);
  const baseTask = mergeRecordValues(
    artifactBase.fromAuthoritativeDraft
      ? executionTask
      : artifactBaseTask,
    recordValue(baseParameters.task),
  );
  const editTask = { ...submittedTask };

  // This field is not reconstructed into the edit form yet. Preserve the
  // existing value instead of letting the create-form default overwrite it.
  if ("proposeTasks" in editTask) {
    delete editTask.proposeTasks;
  }

  const mergedTask: Record<string, unknown> = {
    ...baseTask,
    ...editTask,
    runtime: mergeRecordValues(
      recordValue(baseTask.runtime),
      recordValue(editTask.runtime),
    ),
    git: mergeRecordValues(recordValue(baseTask.git), recordValue(editTask.git)),
    publish: mergeRecordValues(
      recordValue(baseTask.publish),
      recordValue(editTask.publish),
    ),
  };
  const mergedGit = recordValue(mergedTask.git);
  delete mergedGit.startingBranch;
  delete mergedGit.targetBranch;
  if (Object.keys(mergedGit).length > 0) {
    mergedTask.git = mergedGit;
  } else {
    delete mergedTask.git;
  }

  const parametersPatch: Record<string, unknown> = {
    ...baseParameters,
    ...submittedPayload,
    task: mergedTask,
  };
  if (!("mergeAutomation" in submittedPayload)) {
    delete parametersPatch.mergeAutomation;
  }
  delete parametersPatch.startingBranch;
  delete parametersPatch.targetBranch;
  return parametersPatch;
}

function readJiraItems<T>(data: unknown): T[] {
  if (Array.isArray(data)) {
    return data as T[];
  }
  if (!data || typeof data !== "object") {
    return [];
  }
  const items = (data as { items?: unknown }).items;
  return Array.isArray(items) ? (items as T[]) : [];
}

function normalizeMoonMindApiPath(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  if (
    value !== normalized ||
    !normalized ||
    !normalized.startsWith("/api/") ||
    normalized.includes("://")
  ) {
    return null;
  }
  return normalized;
}

function readJiraEndpointTemplates(sourceConfig: {
  [key: string]: unknown;
}): JiraEndpointTemplates | null {
  const connections = normalizeMoonMindApiPath(sourceConfig.connections);
  const projects = normalizeMoonMindApiPath(sourceConfig.projects);
  const boards = normalizeMoonMindApiPath(sourceConfig.boards);
  const columns = normalizeMoonMindApiPath(sourceConfig.columns);
  const issues = normalizeMoonMindApiPath(sourceConfig.issues);
  const issue = normalizeMoonMindApiPath(sourceConfig.issue);
  if (!connections || !projects || !boards || !columns || !issues || !issue) {
    return null;
  }
  return { connections, projects, boards, columns, issues, issue };
}

function jiraTargetLabel(
  target: JiraImportTarget | null,
  steps: StepState[],
): string {
  if (!target) {
    return "No target selected";
  }
  if (target.kind === "preset") {
    return target.attachmentsOnly
      ? "Instructions attachments (Preset)"
      : "Instructions (Preset)";
  }
  const index = steps.findIndex((step) => step.localId === target.localId);
  if (target.attachmentsOnly) {
    return index >= 0 ? `Step ${index + 1} attachments` : "Step attachments";
  }
  return index >= 0 ? `Step ${index + 1} Instructions` : "Step Instructions";
}

function defaultJiraImportMode(target: JiraImportTarget): JiraImportMode {
  return target.kind === "preset" ? "preset-brief" : "execution-brief";
}

function jiraTargetValue(target: JiraImportTarget | null): string {
  if (!target) {
    return "";
  }
  if (target.kind === "preset") {
    return target.attachmentsOnly ? "preset-attachments" : "preset-text";
  }
  return target.attachmentsOnly
    ? `step-attachments:${target.localId}`
    : `step-text:${target.localId}`;
}

function jiraTargetFromValue(value: string): JiraImportTarget | null {
  if (value === "preset-text") {
    return { kind: "preset" };
  }
  if (value === "preset-attachments") {
    return { kind: "preset", attachmentsOnly: true };
  }
  if (value.startsWith("step-text:")) {
    return { kind: "step", localId: value.slice("step-text:".length) };
  }
  if (value.startsWith("step-attachments:")) {
    return {
      kind: "step",
      localId: value.slice("step-attachments:".length),
      attachmentsOnly: true,
    };
  }
  return null;
}

function joinJiraText(parts: Array<string | null | undefined>): string {
  return parts
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join("\n\n");
}

function jiraImportTextForMode(
  issue: JiraIssueDetail,
  mode: JiraImportMode,
): string {
  const issueKey = String(issue.issueKey || "").trim();
  const summary = String(issue.summary || "").trim();
  const description = String(issue.descriptionText || "").trim();
  const acceptanceCriteria = String(issue.acceptanceCriteriaText || "").trim();

  if (mode === "description-only") {
    return description;
  }
  if (mode === "acceptance-only") {
    return acceptanceCriteria;
  }
  if (mode === "preset-brief") {
    const recommended = String(
      issue.recommendedImports?.presetInstructions || "",
    ).trim();
    if (recommended) {
      return recommended;
    }
    return joinJiraText([
      [issueKey, summary].filter(Boolean).join(": "),
      description,
    ]);
  }

  const recommended = String(
    issue.recommendedImports?.stepInstructions || "",
  ).trim();
  if (recommended) {
    return recommended;
  }
  const issueTitle =
    [issueKey, summary].filter(Boolean).join(": ") || "(unnamed)";
  return joinJiraText([
    `Complete Jira issue ${issueTitle}`,
    description ? `Description\n${description}` : "",
    acceptanceCriteria ? `Acceptance criteria\n${acceptanceCriteria}` : "",
  ]);
}

function writeJiraImportedText(
  currentText: string,
  importedText: string,
  writeMode: "replace" | "append",
): string {
  const normalizedImport = importedText.trim();
  if (writeMode === "replace" || !currentText.trim()) {
    return normalizedImport;
  }
  return `${currentText.trimEnd()}\n\n---\n\n${normalizedImport}`;
}

function createJiraProvenance(
  issue: JiraIssueDetail,
  boardId: string,
  importMode: JiraImportMode,
  target: JiraImportTarget,
): JiraImportProvenance | null {
  const issueKey = String(issue.issueKey || "").trim();
  if (!issueKey) {
    return null;
  }
  return {
    issueKey,
    boardId: String(boardId || "").trim(),
    columnId: String(issue.column?.id || "").trim(),
    importMode,
    targetType: target.kind,
  };
}

function jiraProjectKeyFromIssueKey(issueKey: string): string {
  return (
    String(issueKey || "")
      .trim()
      .split("-")
      .slice(0, -1)
      .join("-")
      .trim()
      .toUpperCase() || ""
  );
}

function JiraProvenanceChip({
  label,
  provenance,
}: {
  label: string;
  provenance: JiraImportProvenance | null | undefined;
}) {
  if (!provenance?.issueKey) {
    return null;
  }
  return (
    <span
      className="jira-provenance-chip"
      aria-label={`Jira import provenance for ${label}`}
      title={[
        `Jira issue ${provenance.issueKey}`,
        provenance.boardId ? `board ${provenance.boardId}` : "",
        provenance.columnId ? `column ${provenance.columnId}` : "",
        `mode ${provenance.importMode}`,
        `target ${provenance.targetType}`,
      ]
        .filter(Boolean)
        .join(" / ")}
    >
      {`Jira: ${provenance.issueKey}`}
    </span>
  );
}

function createStepStateEntry(
  index: number,
  overrides: Partial<StepState> = {},
): StepState {
  return {
    localId: `step-${index}`,
    id: "",
    title: "",
    stepType: "skill",
    instructions: "",
    toolId: "",
    toolVersion: "",
    toolInputs: "{}",
    skillId: "",
    skillArgs: "",
    skillRequiredCapabilities: "",
    presetKey: "",
    presetInputValues: {},
    presetDetail: null,
    submitExpansion: null,
    presetMessage: null,
    stepTypeMessage: null,
    templateStepId: "",
    templateInstructions: "",
    inputAttachments: [],
    templateAttachments: [],
    ...overrides,
  };
}

function presetInputValuesFromPayload(
  inputValues: Record<string, unknown> | undefined,
): Record<string, string | boolean> {
  return Object.entries(inputValues || {}).reduce<Record<string, string | boolean>>(
    (values, [key, value]) => {
      if (typeof value === "boolean") {
        values[key] = value;
      } else if (value !== null && value !== undefined) {
        values[key] = String(value);
      }
      return values;
    },
    {},
  );
}

function presetInputValueSignature(
  inputValues: Record<string, string | boolean>,
): string {
  return JSON.stringify(
    Object.entries(inputValues).sort(([left], [right]) =>
      left.localeCompare(right),
    ),
  );
}

function createStepStateEntriesFromTemporalDraft(
  draft: ReturnType<typeof buildTemporalSubmissionDraftFromExecution>,
): StepState[] {
  if (draft.steps.length === 0) {
    return [
      createStepStateEntry(1, {
        instructions: draft.taskInstructions,
        ...(draft.primarySkill ? { skillId: draft.primarySkill } : {}),
      }),
    ];
  }

  return draft.steps.map((step, index) => {
    const primarySkill = draft.primarySkill || "";
    const shouldUsePrimarySkill =
      index === 0 &&
      step.stepType === "skill" &&
      primarySkill !== "" &&
      !hasExplicitSkillSelection(step.skillId);
    const hasJiraOrchestration =
      step.jiraOrchestration &&
      Object.keys(step.jiraOrchestration).length > 0;
    const toolPayload = step.tool || {};
    const presetPayload = step.preset || {};
    const presetKey =
      step.stepType === "preset"
        ? String(
            presetPayload.id ||
              presetPayload.slug ||
              presetPayload.name ||
              "",
          ).trim()
        : "";

    return createStepStateEntry(index + 1, {
      id: step.id,
      title: step.title,
      stepType: step.stepType,
      instructions: step.instructions,
      skillId:
        step.stepType === "skill"
          ? shouldUsePrimarySkill
            ? primarySkill
            : step.skillId
          : "",
      skillArgs:
        step.stepType === "skill" ? stringifySkillArgs(step.skillArgs) : "",
      skillRequiredCapabilities: step.skillRequiredCapabilities.join(","),
      toolId:
        step.stepType === "tool"
          ? String(toolPayload.id || toolPayload.name || step.skillId || "").trim()
          : "",
      toolVersion:
        step.stepType === "tool"
          ? String(toolPayload.version || "").trim()
          : "",
      toolInputs:
        step.stepType === "tool"
          ? JSON.stringify(toolPayload.inputs || step.skillArgs || {}, null, 2)
          : "{}",
      presetKey,
      presetInputValues:
        step.stepType === "preset"
          ? presetInputValuesFromPayload(presetPayload.inputs)
          : {},
      presetDetail: null,
      templateStepId: step.templateStepId,
      templateInstructions: step.templateInstructions,
      inputAttachments: (step.inputAttachments || []).map(
        stepAttachmentRefFromTemporal,
      ),
      templateAttachments:
        step.templateAttachments ||
        (step.inputAttachments || []).map(stepAttachmentRefFromTemporal),
      ...(step.storyOutput && Object.keys(step.storyOutput).length > 0
        ? { storyOutput: step.storyOutput }
        : {}),
      ...(hasJiraOrchestration
        ? { jiraOrchestration: step.jiraOrchestration }
        : {}),
    });
  });
}

function hasAdvancedStepOptionValues(steps: StepState[]): boolean {
  return steps.some(
    (step) =>
      Boolean(step.skillRequiredCapabilities.trim()) ||
      Boolean(step.skillArgs.trim()),
  );
}

function stepAttachmentRefFromTemporal(
  attachment: TemporalTaskInputAttachmentRef,
): StepAttachmentRef {
  return {
    artifactId: attachment.artifactId,
    filename: attachment.filename,
    contentType: attachment.contentType,
    sizeBytes: attachment.sizeBytes,
  };
}

function hasExplicitSkillSelection(skillId: string): boolean {
  const normalized = skillId.trim().toLowerCase();
  return normalized !== "" && normalized !== "auto";
}

function isSelfManagedPublishSkill(skillId: string): boolean {
  return SELF_MANAGED_PUBLISH_SKILLS.has(skillId.trim().toLowerCase());
}

function resolveEffectiveSkillId(
  primarySkillId: string,
  appliedTemplates: AppliedTemplateState[],
): string {
  if (appliedTemplates.length > 0) {
    const lastTemplate = appliedTemplates[appliedTemplates.length - 1];
    return lastTemplate?.slug ?? primarySkillId;
  }
  if (hasExplicitSkillSelection(primarySkillId)) {
    return primarySkillId;
  }
  return primarySkillId;
}

function activeAppliedTemplatesForSteps(
  appliedTemplates: AppliedTemplateState[],
  steps: StepState[],
): AppliedTemplateState[] {
  const activeStepIds = new Set(
    steps
      .map((step) => step.id.trim())
      .filter(Boolean),
  );
  return appliedTemplates.filter((template) => {
    const stepIds = Array.isArray(template.stepIds)
      ? template.stepIds
          .map((stepId) => String(stepId || "").trim())
          .filter(Boolean)
      : [];
    return (
      stepIds.length === 0 ||
      stepIds.some((stepId) => activeStepIds.has(stepId))
    );
  });
}

function parseCapabilitiesCsv(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function stringifySkillArgs(
  args: Record<string, unknown> | null | undefined,
): string {
  if (
    !args ||
    typeof args !== "object" ||
    Array.isArray(args) ||
    Object.keys(args).length === 0
  ) {
    return "";
  }
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return "[unserializable skill args]";
  }
}

function extractCapabilityCsv(value: unknown): string {
  if (!Array.isArray(value)) {
    return "";
  }
  return value
    .map((item) =>
      String(item || "")
        .trim()
        .toLowerCase(),
    )
    .filter(Boolean)
    .join(",");
}

function isEmptyStepStateEntry(step: StepState | null | undefined): boolean {
  if (!step) {
    return true;
  }
  return (
    !step.id.trim() &&
    !step.instructions.trim() &&
    !step.toolId.trim() &&
    !step.toolVersion.trim() &&
    (!step.toolInputs.trim() || step.toolInputs.trim() === "{}") &&
    !step.skillId.trim() &&
    !step.skillArgs.trim() &&
    !step.skillRequiredCapabilities.trim() &&
    !step.presetKey.trim() &&
    !step.templateStepId.trim() &&
    !step.templateInstructions.trim() &&
    step.inputAttachments.length === 0 &&
    step.templateAttachments.length === 0
  );
}

function attachmentIdentity(item: StepAttachmentRef | File): string {
  if (item instanceof File) {
    return ["file", item.name, item.type || "", String(item.size || 0)].join(
      ":",
    );
  }
  if (item.filename || item.contentType || item.sizeBytes) {
    return [
      "file",
      item.filename,
      item.contentType || "",
      String(item.sizeBytes || 0),
    ].join(":");
  }
  return `artifact:${item.artifactId}`;
}

function attachmentSignature(items: Array<StepAttachmentRef | File>): string {
  return items.map(attachmentIdentity).sort().join("|");
}

function isTemplateBoundStepForInstructions(
  step: StepState | null | undefined,
): boolean {
  return Boolean(
    step?.templateStepId &&
      step.id === step.templateStepId &&
      step.instructions === step.templateInstructions,
  );
}

function isTemplateBoundStepForAttachments(
  step: StepState | null | undefined,
  attachments: Array<StepAttachmentRef | File>,
): boolean {
  return Boolean(
    step?.templateStepId &&
      step.id === step.templateStepId &&
      attachmentSignature(attachments) ===
        attachmentSignature(step.templateAttachments),
  );
}

function executableGeneratedToolPayload(
  step: StepState | null | undefined,
): TaskTemplateStepSkill | null {
  if (step?.stepType !== "tool" || !step.generatedTool) {
    return null;
  }
  const toolId = String(
    step.generatedTool.id || step.generatedTool.name || "",
  ).trim();
  if (!toolId) {
    return null;
  }
  return step.generatedTool;
}

function executableGeneratedSkillPayload(
  step: StepState | null | undefined,
): TaskTemplateStepSkill | null {
  if (step?.stepType !== "skill" || !step.generatedSkill) {
    return null;
  }
  const skillId = String(
    step.generatedSkill.id || step.generatedSkill.name || "",
  ).trim();
  if (!skillId) {
    return null;
  }
  return step.generatedSkill;
}

function manualToolPayload(
  step: StepState | null | undefined,
  inputs: Record<string, unknown>,
): TaskTemplateStepSkill | null {
  if (step?.stepType !== "tool") {
    return null;
  }
  const toolId = step.toolId.trim();
  if (!toolId) {
    return null;
  }
  const toolVersion = step.toolVersion.trim();
  return {
    type: "tool",
    id: toolId,
    ...(toolVersion ? { version: toolVersion } : {}),
    inputs,
  };
}

function parseToolInputsText(
  value: string,
): { ok: true; value: Record<string, unknown> } | { ok: false } {
  const raw = value.trim();
  if (!raw) {
    return { ok: true, value: {} };
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  } catch {
    return { ok: false };
  }
}

function toolDefinitionId(tool: TrustedToolDefinition): string {
  return String(tool.name || "").trim();
}

function toolGroupLabel(toolId: string): string {
  const namespace = toolId.split(".")[0] || "other";
  if (namespace.toLowerCase() === "github") {
    return "GitHub";
  }
  return namespace.charAt(0).toUpperCase() + namespace.slice(1);
}

function groupedToolChoices(
  tools: TrustedToolDefinition[],
  searchText: string,
): ToolChoiceGroup[] {
  const search = searchText.trim().toLowerCase();
  const groups = new Map<string, TrustedToolDefinition[]>();
  tools.forEach((tool) => {
    const id = toolDefinitionId(tool);
    if (!id) {
      return;
    }
    const group = toolGroupLabel(id);
    const haystack = `${group} ${id} ${tool.description || ""}`.toLowerCase();
    if (search && !haystack.includes(search)) {
      return;
    }
    const groupTools = groups.get(group);
    if (groupTools) {
      groupTools.push(tool);
    } else {
      groups.set(group, [tool]);
    }
  });
  return Array.from(groups.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([group, groupTools]) => ({
      group,
      tools: groupTools.sort((left, right) =>
        toolDefinitionId(left).localeCompare(toolDefinitionId(right)),
      ),
    }));
}

function toolContractSummary(tool: TrustedToolDefinition | null): string {
  if (!tool) {
    return "Tool definitions declare schema-backed inputs, authorization, worker capability, retry, binding, validation, and error contracts.";
  }
  const schema = tool.inputSchema || {};
  const properties = schema.properties;
  const propertyNames =
    properties && typeof properties === "object" && !Array.isArray(properties)
      ? Object.keys(properties as Record<string, unknown>).sort()
      : [];
  if (propertyNames.length > 0) {
    return `Schema-backed inputs: ${propertyNames.join(", ")}. Tool execution remains governed by authorization, capability, retry, binding, validation, and error contracts.`;
  }
  return "This Tool exposes a governed contract with schema-backed inputs and policy-checked deterministic execution.";
}

function extractIssueKeyFromToolInputs(value: string): string {
  const parsed = parseToolInputsText(value);
  if (!parsed.ok) {
    return "";
  }
  return String(parsed.value.issueKey || "").trim();
}

function updateToolInputsText(
  value: string,
  updates: Record<string, unknown>,
): string {
  const parsed = parseToolInputsText(value);
  const base = parsed.ok ? parsed.value : {};
  return JSON.stringify({ ...base, ...updates }, null, 2);
}

function validatePrimaryStepSubmission(
  primaryStep: StepState | null,
  options: { additionalStepsCount?: number } = {},
):
  | { ok: true; value: { instructions: string; skillId: string } }
  | { ok: false; error: string } {
  if (!primaryStep) {
    return { ok: false, error: "Add at least one step before submitting." };
  }
  if (primaryStep.stepType === "tool") {
    if (
      executableGeneratedToolPayload(primaryStep) ||
      primaryStep.toolId.trim()
    ) {
      return {
        ok: true,
        value: {
          instructions: primaryStep.instructions.trim(),
          skillId: "",
        },
      };
    }
    return {
      ok: false,
      error: "Select a Tool before submitting a Tool step.",
    };
  }
  if (primaryStep.stepType === "preset") {
    return {
      ok: false,
      error: "Expand Preset steps before submitting.",
    };
  }
  const instructions = primaryStep.instructions.trim();
  const skillId = primaryStep.skillId.trim();
  const additionalStepsCount = Number(options.additionalStepsCount) || 0;
  if (!instructions && additionalStepsCount > 0) {
    return {
      ok: false,
      error:
        "Primary step instructions are required when additional steps are provided.",
    };
  }
  if (instructions || hasExplicitSkillSelection(skillId)) {
    return { ok: true, value: { instructions, skillId } };
  }
  return {
    ok: false,
    error: "Primary step must include instructions or an explicit skill.",
  };
}

function isValidRepositoryInput(value: string): boolean {
  const candidate = value.trim();
  if (!candidate) {
    return false;
  }
  if (OWNER_REPO_PATTERN.test(candidate)) {
    return true;
  }
  if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
    try {
      const parsed = new URL(candidate);
      return (
        Boolean(parsed.hostname) &&
        Boolean(parsed.pathname) &&
        parsed.pathname !== "/" &&
        !parsed.username &&
        !parsed.password
      );
    } catch {
      return false;
    }
  }
  return candidate.startsWith("git@");
}

function canLookupRepositoryBranches(value: string): boolean {
  return isValidRepositoryInput(value);
}

function scopeLabel(scope: TemplateScope): string {
  return scope === "personal" ? "Personal" : "Global";
}

export function preferredTemplate(items: TemplateOption[]): TemplateOption | null {
  const preferredSlugs = [
    JIRA_ORCHESTRATE_PRESET_SLUG,
    MOONSPEC_ORCHESTRATE_PRESET_SLUG,
  ];

  for (const slug of preferredSlugs) {
    const preferred =
      items.find((item) => item.slug === slug && item.scope === "global") ||
      items.find((item) => item.slug === slug);
    if (preferred) {
      return preferred;
    }
  }

  return items[0] || null;
}

function formatAttachmentBytes(value: number): string {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentFileKey(file: File): string {
  return [
    file.name || "attachment",
    file.size,
    file.type || "application/octet-stream",
    file.lastModified,
  ].join(":");
}

function appendDedupedAttachmentFiles(
  currentFiles: File[],
  filesToAdd: File[],
): File[] {
  const nextFiles = [...currentFiles];
  const seenKeys = new Set(currentFiles.map(attachmentFileKey));
  filesToAdd.forEach((file) => {
    const key = attachmentFileKey(file);
    if (!seenKeys.has(key)) {
      seenKeys.add(key);
      nextFiles.push(file);
    }
  });
  return nextFiles;
}

function attachmentTargetKey(target: "objective" | string): string {
  return target === "objective" ? "objective" : `step:${target}`;
}

function isImageOnlyPolicy(policy: AttachmentPolicy): boolean {
  return (
    policy.allowedContentTypes.length > 0 &&
    policy.allowedContentTypes.every((type) => type.startsWith("image/"))
  );
}

function attachmentControlLabel(policy: AttachmentPolicy): string {
  return isImageOnlyPolicy(policy) ? "Images" : "Input Attachments";
}

function attachmentAddButtonLabel(
  policy: AttachmentPolicy,
  stepNumber: number,
): string {
  const noun = isImageOnlyPolicy(policy) ? "images" : "attachments";
  return `Add ${noun} to Step ${stepNumber}`;
}

function attachmentFilePickerLabel(
  policy: AttachmentPolicy,
  stepNumber: number,
): string {
  const noun = isImageOnlyPolicy(policy) ? "image" : "attachment";
  return `Step ${stepNumber} ${noun} file picker`;
}

function attachmentLimitMessage(policy: AttachmentPolicy): string {
  return `Up to ${policy.maxCount} files across all steps, ${formatAttachmentBytes(policy.maxBytes)} each, ${formatAttachmentBytes(policy.totalBytes)} total.`;
}

function validateAttachmentFiles(
  files: File[],
  policy: AttachmentPolicy,
  persistedRefs: StepAttachmentRef[] = [],
): {
  ok: boolean;
  errors: string[];
  totalBytes: number;
} {
  const errors: string[] = [];
  const totalCount = files.length + persistedRefs.length;
  if (totalCount > policy.maxCount) {
    errors.push(`Too many attachments (${totalCount}/${policy.maxCount}).`);
  }
  let totalBytes = 0;
  persistedRefs.forEach((attachment) => {
    totalBytes += Math.max(0, Number(attachment.sizeBytes) || 0);
  });
  files.forEach((file) => {
    const type = String(file.type || "")
      .trim()
      .toLowerCase();
    if (!policy.allowedContentTypes.includes(type)) {
      errors.push(`Unsupported file type for ${file.name || "attachment"}.`);
    }
    const sizeBytes = Math.max(0, Number(file.size) || 0);
    if (sizeBytes > policy.maxBytes) {
      errors.push(
        `${file.name || "attachment"} exceeds ${formatAttachmentBytes(policy.maxBytes)}.`,
      );
    }
    totalBytes += sizeBytes;
  });
  if (totalBytes > policy.totalBytes) {
    errors.push(
      `Total attachment size exceeds ${formatAttachmentBytes(policy.totalBytes)}.`,
    );
  }
  return { ok: errors.length === 0, errors, totalBytes };
}

function validateAttachmentTargets(
  targets: Array<{ key: string; label: string; files: File[] }>,
  policy: AttachmentPolicy,
  persistedRefs: StepAttachmentRef[] = [],
): {
  ok: boolean;
  errors: Record<string, string>;
  messages: string[];
} {
  const errors: Record<string, string> = {};
  const messages: string[] = [];
  const files = targets.flatMap((target) =>
    target.files.map((file) => ({ ...target, file })),
  );
  const totalCount = files.length + persistedRefs.length;
  if (totalCount > policy.maxCount) {
    const message = `Too many attachments (${totalCount}/${policy.maxCount}).`;
    errors.attachments = message;
    messages.push(message);
  }

  let totalBytes = persistedRefs.reduce(
    (sum, attachment) => sum + Math.max(0, Number(attachment.sizeBytes) || 0),
    0,
  );
  for (const entry of files) {
    const type = String(entry.file.type || "")
      .trim()
      .toLowerCase();
    if (!policy.allowedContentTypes.includes(type)) {
      const message = `${entry.label}: Unsupported file type for ${
        entry.file.name || "attachment"
      }.`;
      errors[entry.key] = message;
      messages.push(message);
    }
    const sizeBytes = Math.max(0, Number(entry.file.size) || 0);
    if (sizeBytes > policy.maxBytes) {
      const message = `${entry.label}: ${
        entry.file.name || "attachment"
      } exceeds ${formatAttachmentBytes(policy.maxBytes)}.`;
      errors[entry.key] = message;
      messages.push(message);
    }
    totalBytes += sizeBytes;
  }

  if (totalBytes > policy.totalBytes) {
    const message = `Total attachment size exceeds ${formatAttachmentBytes(
      policy.totalBytes,
    )}.`;
    errors.attachments = message;
    messages.push(message);
  }

  return { ok: messages.length === 0, errors, messages };
}

function AttachmentPreview({
  file,
  alt,
  onError,
}: {
  file: File;
  alt: string;
  onError: () => void;
}) {
  const [previewUrl, setPreviewUrl] = useState("");

  useEffect(() => {
    if (
      !file.type.startsWith("image/") ||
      typeof URL === "undefined" ||
      typeof URL.createObjectURL !== "function"
    ) {
      setPreviewUrl("");
      return;
    }

    const nextPreviewUrl = URL.createObjectURL(file);
    setPreviewUrl(nextPreviewUrl);
    return () => {
      if (typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(nextPreviewUrl);
      }
    };
  }, [file]);

  if (!previewUrl) {
    return null;
  }

  return (
    <img
      alt={alt}
      className="queue-attachment-preview"
      src={previewUrl}
      onError={onError}
    />
  );
}

function validateJiraImageAttachment(
  attachment: JiraIssueAttachment,
  policy: AttachmentPolicy,
): string | null {
  const type = String(attachment.contentType || "")
    .trim()
    .toLowerCase();
  if (!policy.allowedContentTypes.includes(type)) {
    return `${attachment.filename || "Jira image"} uses an unsupported image type.`;
  }
  const sizeBytes = Math.max(0, Number(attachment.sizeBytes) || 0);
  if (sizeBytes > policy.maxBytes) {
    return `${attachment.filename || "Jira image"} exceeds ${formatAttachmentBytes(
      policy.maxBytes,
    )}.`;
  }
  return null;
}

function deriveRequiredCapabilities(args: {
  runtimeMode: string;
  publishMode: string;
  taskSkillRequiredCapabilities: string[];
  stepSkillRequiredCapabilities: string[];
  templateCapabilities: string[];
}): string[] {
  return Array.from(
    new Set(
      [
        args.runtimeMode,
        "git",
        ...(args.publishMode === "pr" ? ["gh"] : []),
        ...args.taskSkillRequiredCapabilities,
        ...args.stepSkillRequiredCapabilities,
        ...args.templateCapabilities
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean),
      ].filter(Boolean),
    ),
  );
}

const PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE = "pr_with_merge_automation";

function normalizePublishModeForSubmit(value: string): string {
  const normalized = value.trim().toLowerCase();
  return normalized === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE
    ? "pr"
    : normalized;
}

function isMergeAutomationPublishMode(value: string): boolean {
  return value.trim().toLowerCase() === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE;
}

function templateEnumOptionLabel(
  definition: TaskTemplateInputDefinition,
  option: string,
): string {
  const normalizedName = definition.name.trim().toLowerCase();
  const normalizedOption = option.trim().toLowerCase();
  if (normalizedName === "publish_mode") {
    if (normalizedOption === "pr") {
      return "PR";
    }
    if (normalizedOption === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE) {
      return "PR with Merge Automation";
    }
  }
  return option;
}

function mapExpandedStepToState(
  index: number,
  step: ExpandedStepPayload,
): StepState {
  const rawType = String(step.type || "").trim().toLowerCase();
  const isToolStep =
    rawType === "tool" ||
    Boolean(step.tool && !step.skill) ||
    String(step.tool?.type || "").trim().toLowerCase() === "tool";
  const tool = step.tool || step.skill || {};
  const inlineInputs =
    tool.inputs && typeof tool.inputs === "object"
      ? tool.inputs
      : tool.args && typeof tool.args === "object"
        ? tool.args
        : {};
  const stepId = String(step.id || "").trim();
  const instructions = String(step.instructions || "").trim();
  const storyOutput =
    step.storyOutput && typeof step.storyOutput === "object"
      ? step.storyOutput
      : step.story_output && typeof step.story_output === "object"
        ? step.story_output
        : undefined;
  const jiraOrchestration =
    nonEmptyRecordValue(step.jiraOrchestration) ||
    nonEmptyRecordValue(step.jira_orchestration);
  const source = nonEmptyRecordValue(step.source);
  const normalizedSource = source
    ? {
        ...source,
        ...(!source.presetVersion && source.version
          ? { presetVersion: source.version }
          : {}),
      }
    : undefined;
  if (normalizedSource && "version" in normalizedSource) {
    delete normalizedSource.version;
  }
  const templateAttachments = Array.isArray(step.inputAttachments)
    ? step.inputAttachments
    : Array.isArray(step.attachments)
      ? step.attachments
      : [];
  return createStepStateEntry(index, {
    id: stepId,
    title: String(step.title || "").trim(),
    stepType: isToolStep ? "tool" : "skill",
    instructions,
    skillId: String(tool.name || tool.id || "").trim(),
    skillArgs: stringifySkillArgs(inlineInputs),
    skillRequiredCapabilities: extractCapabilityCsv(tool.requiredCapabilities),
    templateStepId: stepId,
    templateInstructions: instructions,
    templateAttachments,
    ...(step.tool ? { generatedTool: step.tool } : {}),
    ...(step.skill ? { generatedSkill: step.skill } : {}),
    ...(normalizedSource ? { source: normalizedSource } : {}),
    ...(storyOutput ? { storyOutput } : {}),
    ...(jiraOrchestration ? { jiraOrchestration } : {}),
  });
}

function normalizeTemplateInputKey(rawKey: string): string {
  return rawKey
    .trim()
    .toLowerCase()
    .replaceAll(/[^a-z0-9]/g, "");
}

function isFeatureRequestInputKey(rawKey: string): boolean {
  const normalizedKey = normalizeTemplateInputKey(rawKey);
  return (
    normalizedKey === "featurerequest" ||
    normalizedKey === "feature" ||
    normalizedKey === "request"
  );
}

function isJiraProjectInputKey(rawKey: string): boolean {
  const normalizedKey = normalizeTemplateInputKey(rawKey);
  return normalizedKey === "jiraprojectkey";
}

function isRepositoryInputKey(rawKey: string): boolean {
  const normalizedKey = normalizeTemplateInputKey(rawKey);
  return normalizedKey === "repository" || normalizedKey === "repo";
}

function isVisiblePresetInput(
  presetSlug: string | undefined,
  definition: TaskTemplateInputDefinition,
): boolean {
  if (isFeatureRequestInputKey(definition.name)) {
    return false;
  }
  const hiddenKeys = presetSlug
    ? HIDDEN_PRESET_INPUT_KEYS[presetSlug]
    : undefined;
  if (hiddenKeys?.has(normalizeTemplateInputKey(definition.name))) {
    return false;
  }
  return true;
}

export function resolveObjectiveInstructions(
  featureRequest: string,
  primaryInstructions: string,
  appliedTemplates: AppliedTemplateState[],
): string {
  const explicit = featureRequest.trim();
  if (explicit) {
    return explicit;
  }
  if (primaryInstructions.trim()) {
    return primaryInstructions.trim();
  }
  for (let index = appliedTemplates.length - 1; index >= 0; index -= 1) {
    const candidate = appliedTemplates[index];
    if (!candidate) {
      continue;
    }
    const value = Object.entries(candidate.inputs || {}).find(
      ([rawKey, rawValue]) => {
        return (
          isFeatureRequestInputKey(rawKey) && String(rawValue || "").trim()
        );
      },
    );
    if (value) {
      return String(value[1] || "").trim();
    }
  }
  return "";
}

async function responseErrorDetail(
  response: Response,
  fallback: string,
): Promise<ResponseErrorDetail> {
  try {
    const rawText = (await response.text()).trim();
    if (!rawText) {
      return { code: null, message: fallback };
    }
    try {
      const payload = JSON.parse(rawText) as {
        detail?: string | { code?: string; message?: string };
      };
      if (typeof payload.detail === "string" && payload.detail.trim()) {
        return { code: null, message: payload.detail.trim() };
      }
      if (payload.detail && typeof payload.detail === "object") {
        const detailCode = String(payload.detail.code || "").trim() || null;
        const detailMessage = String(payload.detail.message || "").trim();
        if (detailMessage) {
          return { code: detailCode, message: detailMessage };
        }
      }
    } catch {
      return { code: null, message: rawText };
    }
  } catch {
    return { code: null, message: fallback };
  }
  return { code: null, message: fallback };
}

async function responseErrorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  return (await responseErrorDetail(response, fallback)).message;
}

async function readBranchOptions(
  branchLookupEndpoint: string,
  repository: string,
): Promise<{ items: BranchOption[]; defaultBranch: string }> {
  const response = await fetch(
    configuredBranchLookupUrl(branchLookupEndpoint, repository),
    { headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    throw new Error(
      await responseErrorMessage(response, "Failed to load branches."),
    );
  }
  const payload = (await response.json()) as BranchListResponse;
  if (payload.error) {
    throw new Error(payload.error);
  }
  const items = (payload.items || [])
    .map((item) => {
      const value = String(item.value || "").trim();
      if (!value) {
        return null;
      }
      return {
        value,
        label: String(item.label || value).trim() || value,
        source: String(item.source || "github").trim() || "github",
      };
    })
    .filter((item): item is BranchOption => item !== null);
  return {
    items,
    defaultBranch: String(payload.defaultBranch || "").trim(),
  };
}

function localJiraErrorMessage(error: unknown, fallback: string): string {
  const detail = error instanceof Error ? error.message.trim() : "";
  const suffix = detail && detail !== fallback ? ` ${detail}` : "";
  return `${fallback} ${JIRA_MANUAL_CONTINUATION_MESSAGE}${suffix}`;
}

function localJiraEmptyStateMessage(message: string): string {
  return `${message} ${JIRA_MANUAL_CONTINUATION_MESSAGE}`;
}

async function readTemporalInputArtifact(
  artifactDownloadEndpoint: string,
  artifactId: string,
): Promise<unknown> {
  const response = await fetch(
    configuredArtifactDownloadUrl(artifactDownloadEndpoint, artifactId),
    { headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    throw new Error(
      await responseErrorMessage(
        response,
        "Task instructions could not be loaded from the input artifact.",
      ),
    );
  }
  const rawText = await response.text();
  try {
    return JSON.parse(rawText) as unknown;
  } catch {
    throw new Error("Task input artifact did not contain valid JSON.");
  }
}

async function createInputArtifact(
  createEndpoint: string,
  body: string,
  repository: string,
  options: { sourceWorkflowId?: string | null } = {},
): Promise<{ artifactId: string }> {
  const sourceWorkflowId = String(options.sourceWorkflowId || "").trim();
  let createResponse: Response;
  try {
    createResponse = await fetch(createEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        content_type: "application/json; charset=utf-8",
        size_bytes: new TextEncoder().encode(body).length,
        metadata: {
          label: "Submitted Task Input",
          repository: repository || null,
          source: "task-dashboard-submit",
          ...(sourceWorkflowId ? { sourceWorkflowId } : {}),
        },
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[TaskCreate] Network failure during artifact creation.", {
        endpoint: createEndpoint,
        possibleCauses:
          "API service unreachable, CORS block, or network issue.",
      });
      throw new Error(
        `Failed to reach the artifact creation API (endpoint: ${createEndpoint}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!createResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        createResponse,
        "Failed to create input artifact.",
      ),
    );
  }
  const created = (await createResponse.json()) as {
    artifact_ref?: { artifact_id?: string };
    upload?: {
      mode?: string;
      upload_url?: string;
      required_headers?: Record<string, string>;
    };
  };
  const artifactId = String(created.artifact_ref?.artifact_id || "").trim();
  const uploadMode = String(created.upload?.mode || "single_put")
    .trim()
    .toLowerCase();
  if (!artifactId) {
    throw new Error("Artifact upload details were incomplete.");
  }
  if (uploadMode === "multipart") {
    throw new Error(
      "Task input artifact is too large for the current browser submission flow. " +
        "Reduce the submitted instructions or task step payload and retry.",
    );
  }

  const uploadUrl =
    String(created.upload?.upload_url || "").trim() ||
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`;
  const requiredHeaders =
    created.upload?.required_headers &&
    typeof created.upload.required_headers === "object"
      ? created.upload.required_headers
      : {};

  let uploadResponse: Response;
  try {
    const uploadHeaders = new Headers(requiredHeaders);
    if (!uploadHeaders.has("content-type")) {
      uploadHeaders.set("content-type", "application/json; charset=utf-8");
    }
    uploadResponse = await fetch(uploadUrl, {
      method: "PUT",
      headers: uploadHeaders,
      body,
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error(
        "[TaskCreate] Network failure during artifact content upload.",
        {
          uploadUrl,
          possibleCauses:
            "API service unreachable, CORS block, or network issue.",
        },
      );
      throw new Error(
        `Failed to upload artifact content (upload URL: ${uploadUrl}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!uploadResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        uploadResponse,
        "Failed to upload task input artifact content.",
      ),
    );
  }

  await completeArtifactUpload(
    artifactId,
    "Failed to finalize task input artifact upload.",
  );
  return { artifactId };
}

async function completeArtifactUpload(
  artifactId: string,
  failureMessage: string,
): Promise<void> {
  const completeUrl = `/api/artifacts/${encodeURIComponent(artifactId)}/complete`;
  let completeError: Error | null = null;
  const completeRetryScheduleMs = [0, ...ARTIFACT_COMPLETE_RETRY_DELAYS_MS];
  for (const [attempt, delayMs] of completeRetryScheduleMs.entries()) {
    if (delayMs > 0) {
      await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    }
    let completeResponse: Response;
    try {
      completeResponse = await fetch(completeUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ parts: [] }),
      });
    } catch (error) {
      if (error instanceof TypeError && error.message === "Failed to fetch") {
        console.error(
          "[TaskCreate] Network failure during artifact completion.",
          {
            endpoint: completeUrl,
            artifactId,
            possibleCauses:
              "API service unreachable, CORS block, or network issue.",
          },
        );
        throw new Error(
          `Failed to finalize artifact upload (endpoint: ${completeUrl}). ` +
            "The API service may be unreachable or a CORS policy is blocking the request.",
          { cause: error },
        );
      }
      throw error;
    }
    if (completeResponse.ok) {
      return;
    }

    const detail = await responseErrorDetail(
      completeResponse,
      failureMessage,
    );
    completeError = new Error(detail.message);
    if (
      completeResponse.status !== 409 ||
      detail.code !== "artifact_state_error" ||
      detail.message !== ARTIFACT_COMPLETE_RETRY_MESSAGE ||
      attempt === completeRetryScheduleMs.length - 1
    ) {
      throw completeError;
    }
  }

  throw completeError ?? new Error(failureMessage);
}

async function createInputAttachmentArtifact(
  createEndpoint: string,
  file: File,
  repository: string,
  context:
    | { kind: "objective" }
    | { kind: "step"; stepLabel: string },
): Promise<StepAttachmentRef> {
  const filename = file.name || "attachment";
  const contentType = String(file.type || "application/octet-stream").trim();
  const isObjective = context.kind === "objective";
  const label = isObjective
    ? "Objective Attachment"
    : `${context.stepLabel} Attachment`;
  let createResponse: Response;
  try {
    createResponse = await fetch(createEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        content_type: contentType,
        size_bytes: Math.max(0, Number(file.size) || 0),
        metadata: {
          label,
          filename,
          repository: repository || null,
          source: isObjective
            ? "task-dashboard-objective-attachment"
            : "task-dashboard-step-attachment",
          ...(isObjective
            ? { target: "objective" }
            : { stepLabel: context.stepLabel }),
        },
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      throw new Error(
        `Failed to reach the artifact creation API (endpoint: ${createEndpoint}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!createResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        createResponse,
        `Failed to create artifact for ${filename}.`,
      ),
    );
  }
  const created = (await createResponse.json()) as {
    artifact_ref?: { artifact_id?: string };
    upload?: {
      mode?: string;
      upload_url?: string;
      required_headers?: Record<string, string>;
    };
  };
  const artifactId = String(created.artifact_ref?.artifact_id || "").trim();
  const uploadMode = String(created.upload?.mode || "single_put")
    .trim()
    .toLowerCase();
  if (!artifactId) {
    throw new Error(`Artifact upload details were incomplete for ${filename}.`);
  }
  if (uploadMode === "multipart") {
    throw new Error(
      `${filename} is too large for the current browser attachment upload flow.`,
    );
  }

  const uploadUrl =
    String(created.upload?.upload_url || "").trim() ||
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`;
  const requiredHeaders =
    created.upload?.required_headers &&
    typeof created.upload.required_headers === "object"
      ? created.upload.required_headers
      : {};
  const uploadHeaders = new Headers(requiredHeaders);
  if (!uploadHeaders.has("content-type")) {
    uploadHeaders.set("content-type", contentType);
  }
  const uploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    headers: uploadHeaders,
    body: file,
  });
  if (!uploadResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        uploadResponse,
        `Failed to upload attachment ${filename}.`,
      ),
    );
  }

  await completeArtifactUpload(
    artifactId,
    `Failed to finalize attachment ${filename}.`,
  );

  return {
    artifactId,
    filename,
    contentType,
    sizeBytes: Math.max(0, Number(file.size) || 0),
  };
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

function stripOversizedInlineInstructions(
  requestBody: Record<string, unknown>,
): void {
  const payload = requestBody.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return;
  }

  const payloadRecord = payload as Record<string, unknown>;
  const task = payloadRecord.task;
  if (!task || typeof task !== "object" || Array.isArray(task)) {
    return;
  }

  const taskRecord = task as Record<string, unknown>;
  const steps = Array.isArray(taskRecord.steps) ? taskRecord.steps : [];

  const fitsInlineLimit = () =>
    utf8ByteLength(JSON.stringify(requestBody)) <=
    INLINE_TASK_INPUT_LIMIT_BYTES;
  const removeTypeOnlyStep = (step: Record<string, unknown>) => {
    const nonTypeKeys = Object.entries(step).filter(([key, value]) => {
      if (key === "type" || value === undefined || value === null) {
        return false;
      }
      if (typeof value === "string") {
        return value.trim().length > 0;
      }
      if (Array.isArray(value)) {
        return value.length > 0;
      }
      if (typeof value === "object") {
        return Object.keys(value).length > 0;
      }
      return true;
    });
    if (nonTypeKeys.length === 0) {
      delete step.type;
    }
  };
  if (fitsInlineLimit()) {
    return;
  }

  for (let index = steps.length - 1; index >= 1; index -= 1) {
    const step = steps[index];
    if (
      !step ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      !("instructions" in step)
    ) {
      continue;
    }
    const stepRecord = step as Record<string, unknown>;
    delete stepRecord.instructions;
    removeTypeOnlyStep(stepRecord);
    if (fitsInlineLimit()) {
      return;
    }
  }

  if ("instructions" in taskRecord) {
    delete taskRecord.instructions;
    if (fitsInlineLimit()) {
      return;
    }
  }

  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index];
    if (
      !step ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      !("instructions" in step)
    ) {
      continue;
    }
    const stepRecord = step as Record<string, unknown>;
    delete stepRecord.instructions;
    removeTypeOnlyStep(stepRecord);
    if (fitsInlineLimit()) {
      return;
    }
  }
}

async function linkInputArtifact(
  artifactId: string,
  execution: ExecutionCreateResponse,
  options: { linkType?: string; label?: string } = {},
): Promise<void> {
  const workflowId = String(execution.workflowId || "").trim();
  const runId = String(execution.runId || execution.temporalRunId || "").trim();
  const namespace = String(execution.namespace || "").trim();
  if (!artifactId || !workflowId || !runId || !namespace) {
    return;
  }
  const linkEndpoint = `/api/artifacts/${encodeURIComponent(artifactId)}/links`;
  let response: Response;
  try {
    response = await fetch(linkEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        namespace,
        workflow_id: workflowId,
        run_id: runId,
        link_type: options.linkType || "input.instructions",
        label: options.label || "Submitted Task Input",
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[TaskCreate] Network failure during artifact linking.", {
        endpoint: linkEndpoint,
        artifactId,
        possibleCauses:
          "API service unreachable, CORS block, or network issue.",
      });
      throw new Error(
        `Failed to reach the artifact linking API (endpoint: ${linkEndpoint}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!response.ok) {
    throw new Error(
      await responseErrorMessage(
        response,
        "Failed to link input artifact to execution.",
      ),
    );
  }
}

function ArrowUpIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 6v12m0-12-4 4m4-4 4 4" />
    </svg>
  );
}

function ArrowDownIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 18V6m0 12 4-4m-4 4-4-4" />
    </svg>
  );
}

function ArrowRightIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 12h14m0 0-5-5m5 5-5 5" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M7 5v10a4 4 0 0 0 4 4h6" />
      <path d="M7 5a2 2 0 1 0-2 2 2 2 0 0 0 2-2z" />
      <path d="M17 19a2 2 0 1 0 2-2 2 2 0 0 0-2 2z" />
      <path d="M11 9h2a4 4 0 0 0 4-4" />
    </svg>
  );
}

function RepoIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 4h11a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2z" />
      <path d="M9 8h5" />
      <path d="M9 12h5" />
    </svg>
  );
}

function PublishIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 18V6" />
      <path d="M8 10l4-4 4 4" />
      <path d="M6 18h12" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M7 7l10 10M17 7l-10 10" />
    </svg>
  );
}

function SaveIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M6 4h10l2 2v14H6z" />
      <path d="M9 4v6h6V4" />
      <path d="M9 17h6" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 7h14" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M6 7l1 13h10l1-13" />
      <path d="M9 7V4h6v3" />
    </svg>
  );
}

function SkillSparkleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6L12 4z" />
      <path d="M18.5 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" />
    </svg>
  );
}

function ToolWrenchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M14.7 6.3a4 4 0 0 1 5 5l-2.3-.6-1.4 1.4.6 2.3a4 4 0 0 1-5-5l2.3.6 1.4-1.4-.6-2.3z" />
      <path d="M13 11l-7 7a1.8 1.8 0 1 0 2.5 2.5l7-7" />
    </svg>
  );
}

function PresetLayersIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 4l8 4-8 4-8-4 8-4z" />
      <path d="M4 12l8 4 8-4" />
      <path d="M4 16l8 4 8-4" />
    </svg>
  );
}

export const LIQUID_GL_OPTIONS = {
  target: ".queue-floating-bar--liquid-glass",
  snapshot: "body",
  resolution: 2,
  refraction: 0.018,
  bevelDepth: 0.12,
  bevelWidth: 0.16,
  frost: 6,
  shadow: true,
  specular: true,
  reveal: false,
  tilt: false,
  magnify: 1,
} as const;

export function TaskCreatePage({ payload }: { payload: BootPayload }) {
  useLiquidGL({ options: LIQUID_GL_OPTIONS });
  const dashboardConfig = readDashboardConfig(payload);
  const pageMode = useMemo(
    () => resolveTaskSubmitPageMode(window.location.search),
    [],
  );
  const temporalTaskEditingEnabled = Boolean(
    dashboardConfig.features?.temporalDashboard?.temporalTaskEditing,
  );
  const temporalCreateEndpoint = String(
    dashboardConfig.sources?.temporal?.create || "/api/executions",
  );
  const temporalUpdateEndpoint = String(
    dashboardConfig.sources?.temporal?.update ||
      "/api/executions/{workflowId}/update",
  );
  const temporalListEndpoint = String(
    dashboardConfig.sources?.temporal?.list || "/api/executions",
  );
  const temporalDetailEndpoint = String(
    dashboardConfig.sources?.temporal?.detail || "/api/executions/{workflowId}",
  );
  const artifactCreateEndpoint = String(
    dashboardConfig.sources?.temporal?.artifactCreate || "/api/artifacts",
  );
  const artifactDownloadEndpoint = String(
    dashboardConfig.sources?.temporal?.artifactDownload ||
      "/api/artifacts/{artifactId}/download",
  );
  const providerProfilesEndpoint = String(
    dashboardConfig.system?.providerProfiles?.list ||
      "/api/v1/provider-profiles",
  );
  const taskTemplateCatalog = dashboardConfig.system?.taskTemplateCatalog;
  const taskTemplateCatalogEnabled = Boolean(taskTemplateCatalog?.enabled);
  const taskTemplateSaveEnabled = Boolean(
    taskTemplateCatalog?.templateSaveEnabled,
  );
  const taskTemplateListEndpoint = String(
    taskTemplateCatalog?.list || "/api/task-step-templates",
  );
  const taskTemplateDetailEndpoint = String(
    taskTemplateCatalog?.detail || "/api/task-step-templates/{slug}",
  );
  const taskTemplateExpandEndpoint = String(
    taskTemplateCatalog?.expand || "/api/task-step-templates/{slug}:expand",
  );
  const taskTemplateSaveEndpoint = String(
    taskTemplateCatalog?.saveFromTask ||
      "/api/task-step-templates/save-from-task",
  );
  const jiraIntegration = useMemo<JiraIntegrationConfig | null>(() => {
    const systemConfig = dashboardConfig.system?.jiraIntegration;
    const sourceConfig = dashboardConfig.sources?.jira;
    if (!systemConfig?.enabled || !sourceConfig) {
      return null;
    }
    const endpoints = readJiraEndpointTemplates(sourceConfig);
    if (!endpoints) {
      return null;
    }
    return {
      enabled: true,
      defaultProjectKey: String(systemConfig.defaultProjectKey || "").trim(),
      defaultBoardId: String(systemConfig.defaultBoardId || "").trim(),
      rememberLastBoardInSession: Boolean(
        systemConfig.rememberLastBoardInSession,
      ),
      endpoints,
    };
  }, [
    dashboardConfig.sources?.jira,
    dashboardConfig.system?.jiraIntegration,
  ]);

  const attachmentPolicy = useMemo<AttachmentPolicy>(() => {
    const config = dashboardConfig.system?.attachmentPolicy;
    const allowedContentTypes =
      Array.isArray(config?.allowedContentTypes) &&
      config.allowedContentTypes.length > 0
        ? config.allowedContentTypes
            .map((item) =>
              String(item || "")
                .trim()
                .toLowerCase(),
            )
            .filter(Boolean)
        : ["image/png", "image/jpeg", "image/webp"];
    return {
      enabled: Boolean(config?.enabled),
      maxCount: Math.max(1, Number(config?.maxCount) || 10),
      maxBytes: Math.max(1, Number(config?.maxBytes) || 10 * 1024 * 1024),
      totalBytes: Math.max(1, Number(config?.totalBytes) || 25 * 1024 * 1024),
      allowedContentTypes,
    };
  }, [dashboardConfig.system?.attachmentPolicy]);

  const defaultRuntime = String(
    dashboardConfig.system?.defaultTaskRuntime || "codex_cli",
  );
  const defaultRepository = String(
    dashboardConfig.system?.defaultRepository || "",
  );
  const repositoryOptions = useMemo(
    () =>
      normalizeRepositoryOptions(
        dashboardConfig.system?.repositoryOptions?.items,
      ),
    [dashboardConfig.system?.repositoryOptions?.items],
  );
  const initialRepository = useMemo(() => {
    const rememberedRepository = repositoryOptionValue(
      repositoryOptions,
      readLocalPreference(LAST_REPOSITORY_OPTION_PREFERENCE_KEY),
    );
    return rememberedRepository || defaultRepository;
  }, [defaultRepository, repositoryOptions]);
  const defaultPublishMode = String(
    dashboardConfig.system?.defaultPublishMode || "pr",
  );
  const defaultProposeTasks = Boolean(
    dashboardConfig.system?.defaultProposeTasks,
  );
  const defaultTaskModelByRuntime =
    dashboardConfig.system?.defaultTaskModelByRuntime || {};
  const defaultTaskEffortByRuntime =
    dashboardConfig.system?.defaultTaskEffortByRuntime || {};
  const supportedTaskRuntimes = dashboardConfig.system
    ?.supportedTaskRuntimes || ["codex_cli", "gemini_cli", "claude_code"];

  const [steps, setSteps] = useState<StepState[]>([createStepStateEntry(1)]);
  const stepsRef = useRef<StepState[]>(steps);
  const [nextStepNumber, setNextStepNumber] = useState(2);
  const [showAdvancedStepOptions, setShowAdvancedStepOptions] = useState(false);
  const [runtime, setRuntime] = useState(defaultRuntime);
  const [model, setModel] = useState(
    String(
      defaultTaskModelByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultTaskModel ||
        "",
    ),
  );
  const [modelManualOverride, setModelManualOverride] = useState(false);
  const [effort, setEffort] = useState(
    String(
      defaultTaskEffortByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultTaskEffort ||
        "",
    ),
  );
  const [repository, setRepository] = useState(initialRepository);
  const [providerProfile, setProviderProfile] = useState("");
  const [branch, setBranch] = useState("");
  const [branchTouched, setBranchTouched] = useState(false);
  const [publishMode, setPublishMode] = useState(
    normalizePublishModeForSubmit(defaultPublishMode),
  );
  const [produceReport, setProduceReport] = useState(false);
  const [priority, setPriority] = useState(0);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [proposeTasks, setProposeTasks] = useState(() =>
    readProposeTasksPreference(defaultProposeTasks),
  );
  const isInitialMount = useRef(true);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("immediate");
  const [scheduledFor, setScheduledFor] = useState("");
  const [scheduleDeferredMinutes, setScheduleDeferredMinutes] = useState("");
  const [scheduleCron, setScheduleCron] = useState("");
  const [scheduleTimezone, setScheduleTimezone] = useState("UTC");
  const [scheduleName, setScheduleName] = useState("");
  const [templateFeatureRequest, setTemplateFeatureRequest] = useState("");
  const [selectedDependencyWorkflowId, setSelectedDependencyWorkflowId] = useState("");
  const [selectedDependencies, setSelectedDependencies] = useState<string[]>([]);
  const [dependencyMessage, setDependencyMessage] = useState<string | null>(null);
  const [selectedPresetKey, setSelectedPresetKey] = useState("");
  const [presetManagementName, setPresetManagementName] = useState("");
  const [templateMessage, setTemplateMessage] = useState<string | null>(null);
  const [presetReapplyNeeded, setPresetReapplyNeeded] = useState(false);
  const [appliedTemplateFeatureRequest, setAppliedTemplateFeatureRequest] =
    useState("");
  const [
    appliedTemplateObjectiveAttachmentSignature,
    setAppliedTemplateObjectiveAttachmentSignature,
  ] = useState("");
  const [appliedTemplates, setAppliedTemplates] = useState<
    AppliedTemplateState[]
  >([]);
  const [jiraBrowserOpen, setJiraBrowserOpen] = useState(false);
  const [jiraImportTarget, setJiraImportTarget] =
    useState<JiraImportTarget | null>(null);
  const [selectedJiraProjectKey, setSelectedJiraProjectKey] = useState("");
  const [selectedJiraBoardId, setSelectedJiraBoardId] = useState("");
  const [activeJiraColumnId, setActiveJiraColumnId] = useState("");
  const [selectedJiraIssueKey, setSelectedJiraIssueKey] = useState("");
  const [pendingJiraImportIssueKey, setPendingJiraImportIssueKey] =
    useState("");
  const [toolSearchTextByStep, setToolSearchTextByStep] = useState<
    Record<string, string>
  >({});
  const [jiraTransitionStateByStep, setJiraTransitionStateByStep] = useState<
    Record<string, JiraTransitionState>
  >({});
  const [jiraImportMode, setJiraImportMode] =
    useState<JiraImportMode>("preset-brief");
  const [jiraWriteMode, setJiraWriteMode] =
    useState<"append" | "replace">("append");
  const [presetJiraProvenance, setPresetJiraProvenance] =
    useState<JiraImportProvenance | null>(null);
  const [stepJiraProvenance, setStepJiraProvenance] = useState<
    Record<string, JiraImportProvenance>
  >({});
  const [selectedObjectiveAttachmentFiles, setSelectedObjectiveAttachmentFiles] =
    useState<File[]>([]);
  const [selectedStepAttachmentFiles, setSelectedStepAttachmentFiles] = useState<
    Record<string, File[]>
  >({});
  const [persistedObjectiveAttachments, setPersistedObjectiveAttachments] =
    useState<StepAttachmentRef[]>([]);
  const [attachmentTargetErrors, setAttachmentTargetErrors] = useState<
    Record<string, string>
  >({});
  const [previewFailureMessages, setPreviewFailureMessages] = useState<
    Record<string, string>
  >({});
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [isApplyingPreset, setIsApplyingPreset] = useState(false);
  const [isDeletingPreset, setIsDeletingPreset] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const submitExpansionInFlightRef = useRef(false);
  const submitExpansionRequestIdRef = useRef(0);
  const [submitRippleKey, setSubmitRippleKey] = useState(0);
  const [submitRippleRect, setSubmitRippleRect] = useState<DOMRect | null>(null);
  const [isSubmitArrowExiting, setIsSubmitArrowExiting] = useState(false);
  const submitArrowExitHeldRef = useRef(false);
  const submitArrowExitTimeoutRef = useRef<number | null>(null);
  const submitButtonRef = useRef<HTMLButtonElement | null>(null);
  const templateInputMemoryRef = useRef<Record<string, unknown>>({});
  const prevRuntimeRef = useRef(runtime);
  const prevProviderProfileRef = useRef(providerProfile);
  const temporalDraftAppliedRef = useRef<string | null>(null);
  const jiraProjectSelectionInitializedRef = useRef(false);
  const jiraBoardSelectionInitializedRef = useRef(false);

  useEffect(() => {
    stepsRef.current = steps;
  }, [steps]);

  useEffect(
    () => () => {
      if (submitArrowExitTimeoutRef.current !== null) {
        window.clearTimeout(submitArrowExitTimeoutRef.current);
      }
    },
    [],
  );

  const temporalDraftQuery = useQuery({
    queryKey: [
      "task-create",
      "temporal-editing-draft",
      pageMode.mode,
      pageMode.executionId,
      temporalDetailEndpoint,
      artifactDownloadEndpoint,
    ],
    enabled:
      pageMode.mode !== "create" &&
      Boolean(pageMode.executionId) &&
      temporalTaskEditingEnabled,
    queryFn: async (): Promise<TemporalSubmissionDraftLoadResult> => {
      const workflowId = String(pageMode.executionId || "");
      try {
        const response = await fetch(
          configuredTemporalDetailUrl(temporalDetailEndpoint, workflowId),
          { headers: { Accept: "application/json" } },
        );
        if (!response.ok) {
          throw new Error(
            await responseErrorMessage(
              response,
              "Failed to load the Temporal execution.",
            ),
          );
        }
        const execution =
          (await response.json()) as TemporalTaskEditingExecutionContract;
        if (String(execution.workflowType || "") !== "MoonMind.Run") {
          throw new Error(
            "This execution cannot be edited here because only MoonMind.Run is supported.",
          );
        }
        if (pageMode.intent === "edit" && execution.actions?.canUpdateInputs !== true) {
          throw new Error(
            "This execution does not currently allow editing its inputs.",
          );
        }
        if (
          pageMode.intent === "edit-for-rerun" &&
          execution.actions?.canEditForRerun !== true
        ) {
          throw new Error(
            "This execution does not currently allow editing for rerun.",
          );
        }
        if (pageMode.mode === "rerun" && execution.actions?.canRerun !== true) {
          throw new Error("This execution does not currently allow rerun.");
        }

        let artifactInput: Record<string, unknown> | undefined;
        const snapshotArtifactRef = String(
          execution.taskInputSnapshot?.artifactRef || "",
        ).trim();
        const inputArtifactRef = String(execution.inputArtifactRef || "").trim();
        const inlineTask = execution.inputParameters?.task;
        if (
          execution.taskInputSnapshot?.reconstructionMode === "authoritative" &&
          snapshotArtifactRef
        ) {
          artifactInput = recordValue(
            await readTemporalInputArtifact(
              artifactDownloadEndpoint,
              snapshotArtifactRef,
            ),
          );
          if (
            inputArtifactRef &&
            artifactInputHasStepInstructionGaps(artifactInput)
          ) {
            try {
              const sourceArtifactInput = recordValue(
                await readTemporalInputArtifact(
                  artifactDownloadEndpoint,
                  inputArtifactRef,
                ),
              );
              artifactInput = mergeMissingTaskInstructionsFromArtifact(
                artifactInput,
                sourceArtifactInput,
              );
            } catch {
              // The authoritative snapshot remains usable if the older source
              // input artifact has expired or is no longer accessible.
            }
          }
        } else if (inputArtifactRef && !hasInlineTaskInstructions(inlineTask)) {
          artifactInput = recordValue(
            await readTemporalInputArtifact(
              artifactDownloadEndpoint,
              inputArtifactRef,
            ),
          );
        }

        const draft = buildTemporalSubmissionDraftFromExecution(
          execution,
          artifactInput,
        );
        recordTemporalTaskEditingClientEvent({
          event: "draft_reconstruction_success",
          mode: pageMode.intent,
          workflowId,
        });
        return {
          execution,
          artifactInput,
          draft,
        };
      } catch (error) {
        recordTemporalTaskEditingClientEvent({
          event: "draft_reconstruction_failure",
          mode: pageMode.intent,
          workflowId,
          reason: error instanceof Error ? error.message : "unknown",
        });
        throw error;
      }
    },
  });

  const providerProfilesQuery = useQuery({
    queryKey: ["task-create", "provider-profiles", runtime],
    queryFn: async (): Promise<ProviderProfile[]> => {
      const separator = providerProfilesEndpoint.includes("?") ? "&" : "?";
      const response = await fetch(
        `${providerProfilesEndpoint}${separator}runtime_id=${encodeURIComponent(runtime)}`,
        {
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to load provider profiles.",
          ),
        );
      }
      return (await response.json()) as ProviderProfile[];
    },
    enabled: Boolean(runtime),
  });

  useEffect(() => {
    const profiles = providerProfilesQuery.data || [];
    if (providerProfilesQuery.isLoading || providerProfilesQuery.isFetching) {
      return;
    }
    if (pageMode.mode !== "create" && temporalDraftAppliedRef.current) {
      return;
    }
    if (profiles.length === 0) {
      if (providerProfile) {
        setProviderProfile("");
      }
      return;
    }

    const stillValid = profiles.some(
      (profile) => profile.profile_id === providerProfile,
    );
    if (stillValid) {
      return;
    }

    const defaultProfileId = resolveDefaultProviderProfileId(profiles);
    if (defaultProfileId && providerProfile !== defaultProfileId) {
      setProviderProfile(defaultProfileId);
    }
  }, [
    pageMode.mode,
    providerProfile,
    providerProfilesQuery.data,
    providerProfilesQuery.isFetching,
    providerProfilesQuery.isLoading,
  ]);

  useEffect(() => {
    const runtimeChanged = prevRuntimeRef.current !== runtime;
    const profileChanged = prevProviderProfileRef.current !== providerProfile;

    if (runtimeChanged || profileChanged) {
      setModelManualOverride(false);
    }

    if (runtimeChanged) {
      setProviderProfile("");
      prevRuntimeRef.current = runtime;
    }

    if (profileChanged) {
      prevProviderProfileRef.current = providerProfile;
    }

    if (
      pageMode.mode !== "create" &&
      temporalDraftAppliedRef.current &&
      !runtimeChanged &&
      !profileChanged
    ) {
      return;
    }

    setEffort(
      String(
        defaultTaskEffortByRuntime[runtime] ||
          dashboardConfig.system?.defaultTaskEffort ||
          "",
      ),
    );

    if (modelManualOverride && !runtimeChanged && !profileChanged) {
      return;
    }

    const profileIdForModel = runtimeChanged ? "" : providerProfile;
    const profiles = providerProfilesQuery.data || [];
    const selectedProfile = profiles.find(
      (p) => p.profile_id === profileIdForModel,
    );
    if (selectedProfile?.default_model) {
      setModel(selectedProfile.default_model);
    } else {
      setModel(
        String(
          defaultTaskModelByRuntime[runtime] ||
            dashboardConfig.system?.defaultTaskModel ||
            "",
        ),
      );
    }
  }, [
    dashboardConfig.system?.defaultTaskEffort,
    dashboardConfig.system?.defaultTaskModel,
    defaultTaskEffortByRuntime,
    defaultTaskModelByRuntime,
    modelManualOverride,
    pageMode.mode,
    providerProfilesQuery.data,
    providerProfile,
    runtime,
  ]);

  useEffect(() => {
    const primarySkill = String(steps[0]?.skillId || "")
      .trim()
      .toLowerCase();
    if (isSelfManagedPublishSkill(primarySkill)) {
      setPublishMode("none");
    }
  }, [steps[0]?.skillId]);

  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    writeProposeTasksPreference(proposeTasks);
  }, [proposeTasks]);

  useEffect(() => {
    if (pageMode.mode === "create" || !temporalDraftQuery.data) {
      return;
    }
    const applyKey = `${pageMode.mode}:${temporalDraftQuery.data.execution.workflowId}`;
    if (temporalDraftAppliedRef.current === applyKey) {
      return;
    }
    const draft = temporalDraftQuery.data.draft;
    temporalDraftAppliedRef.current = applyKey;

    if (draft.runtime) {
      prevRuntimeRef.current = draft.runtime;
      setRuntime(draft.runtime);
    }
    if (draft.providerProfile) {
      prevProviderProfileRef.current = draft.providerProfile;
      setProviderProfile(draft.providerProfile);
    }
    if (draft.model) {
      setModel(draft.model);
      setModelManualOverride(true);
    }
    if (draft.effort) {
      setEffort(draft.effort);
    }
    if (draft.repository) {
      setRepository(draft.repository);
    }
    if (draft.branch) {
      setBranch(draft.branch);
      setBranchTouched(false);
    }
    if (draft.legacyBranchWarning) {
      setSubmitMessage(draft.legacyBranchWarning);
    }
    if (draft.publishMode) {
      const normalizedDraftPublishMode = normalizePublishModeForSubmit(
        draft.publishMode,
      );
      setPublishMode(
        normalizedDraftPublishMode === "pr" && draft.mergeAutomationEnabled
          ? PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE
          : normalizedDraftPublishMode,
      );
    }
    setProduceReport(draft.reportOutputEnabled);
    const reconstructedSteps = createStepStateEntriesFromTemporalDraft(draft);
    setSteps(reconstructedSteps);
    setShowAdvancedStepOptions(hasAdvancedStepOptionValues(reconstructedSteps));
    setNextStepNumber(reconstructedSteps.length + 1);
    setPersistedObjectiveAttachments(
      draft.inputAttachments.map(stepAttachmentRefFromTemporal),
    );
    setSelectedObjectiveAttachmentFiles([]);
    setSelectedStepAttachmentFiles({});
    setAppliedTemplates(draft.appliedTemplates);
    setAppliedTemplateFeatureRequest("");
    setScheduleMode("immediate");
    setScheduledFor("");
    setScheduleDeferredMinutes("");
    setScheduleCron("");
    setScheduleName("");
  }, [
    defaultPublishMode,
    pageMode.mode,
    temporalDraftQuery.data,
  ]);

  const dependencyOptionsQuery = useQuery({
    queryKey: ["task-create", "dependency-options", temporalListEndpoint],
    queryFn: async (): Promise<DependencyPickerExecution[]> => {
      const response = await fetch(
        withQueryParams(temporalListEndpoint, {
          source: "temporal",
          pageSize: "50",
          workflowType: "MoonMind.Run",
          entry: "run",
        }),
        {
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to load dependency options.",
          ),
        );
      }
      const data = (await response.json()) as DependencyPickerListResponse;
      return (data.items || []).filter(
        (item) =>
          String(item.workflowType || "MoonMind.Run") === "MoonMind.Run" &&
          String(item.entry || "run") === "run",
      );
    },
  });

  const skillsQuery = useQuery({
    queryKey: ["task-create", "skills"],
    queryFn: async (): Promise<string[]> => {
      const response = await fetch("/api/tasks/skills", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load skills."),
        );
      }
      const data = (await response.json()) as SkillsResponse;
      return data.items?.worker || [];
    },
  });

  const hasToolStep = steps.some((step) => step.stepType === "tool");
  const trustedToolsQuery = useQuery({
    queryKey: ["task-create", "trusted-tools"],
    enabled: hasToolStep,
    retry: false,
    queryFn: async (): Promise<TrustedToolDefinition[]> => {
      const response = await fetch("/mcp/tools", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to load trusted Tool discovery.",
          ),
        );
      }
      const data = (await response.json()) as ToolDiscoveryResponse;
      return (data.tools || [])
        .map((tool) => {
          const inputSchema =
            tool.inputSchema &&
            typeof tool.inputSchema === "object" &&
            !Array.isArray(tool.inputSchema)
              ? (tool.inputSchema as Record<string, unknown>)
              : undefined;
          return {
            name: String(tool.name || "").trim(),
            description: String(tool.description || "").trim(),
            ...(inputSchema ? { inputSchema } : {}),
          };
        })
        .filter((tool) => Boolean(tool.name));
    },
  });

  const templateOptionsQuery = useQuery({
    queryKey: [
      "task-create",
      "task-template-catalog",
      taskTemplateListEndpoint,
    ],
    enabled: taskTemplateCatalogEnabled,
    queryFn: async (): Promise<TemplateCatalogResult> => {
      const scopes: TemplateScope[] = ["global", "personal"];
      const results = await Promise.all(
        scopes.map(async (scope) => {
          try {
            const response = await fetch(
              withQueryParams(taskTemplateListEndpoint, { scope }),
              {
                headers: { Accept: "application/json" },
              },
            );
            if (!response.ok) {
              throw new Error(
                await responseErrorMessage(response, "Failed to load presets."),
              );
            }
            const data = (await response.json()) as TaskTemplateListResponse;
            const items = (data.items || []).map((item) => ({
              ...item,
              key: templateKey(item.scope, item.slug, item.scopeRef),
            }));
            return { scope, items, failed: false };
          } catch {
            return { scope, items: [] as TemplateOption[], failed: true };
          }
        }),
      );
      return {
        items: results
          .flatMap((result) => result.items)
          .sort((left, right) => left.title.localeCompare(right.title)),
        failedScopes: results
          .filter((result) => result.failed)
          .map((result) => result.scope),
      };
    },
  });

  const jiraProjectsQuery = useQuery({
    queryKey: ["task-create", "jira", "projects", jiraIntegration?.endpoints.projects],
    enabled: Boolean(jiraIntegration?.enabled && jiraBrowserOpen),
    queryFn: async (): Promise<JiraProject[]> => {
      const endpoint = jiraIntegration?.endpoints.projects || "";
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load Jira projects."),
        );
      }
      return readJiraItems<JiraProject>(await response.json());
    },
  });

  const jiraBoardsQuery = useQuery({
    queryKey: [
      "task-create",
      "jira",
      "boards",
      jiraIntegration?.endpoints.boards,
      selectedJiraProjectKey,
    ],
    enabled: Boolean(
      jiraIntegration?.enabled && jiraBrowserOpen && selectedJiraProjectKey,
    ),
    queryFn: async (): Promise<JiraBoard[]> => {
      const endpoint = interpolatePath(
        jiraIntegration?.endpoints.boards || "",
        { projectKey: selectedJiraProjectKey },
      );
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load Jira boards."),
        );
      }
      return readJiraItems<JiraBoard>(await response.json());
    },
  });

  const jiraColumnsQuery = useQuery({
    queryKey: [
      "task-create",
      "jira",
      "columns",
      jiraIntegration?.endpoints.columns,
      selectedJiraBoardId,
      selectedJiraProjectKey,
    ],
    enabled: Boolean(
      jiraIntegration?.enabled && jiraBrowserOpen && selectedJiraBoardId,
    ),
    queryFn: async (): Promise<JiraColumn[]> => {
      const endpoint = withQueryParams(
        interpolatePath(jiraIntegration?.endpoints.columns || "", {
          boardId: selectedJiraBoardId,
        }),
        { projectKey: selectedJiraProjectKey },
      );
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load Jira columns."),
        );
      }
      const data = (await response.json()) as { columns?: unknown } | null;
      return parseJiraColumns(data?.columns);
    },
  });

  const jiraIssuesQuery = useQuery({
    queryKey: [
      "task-create",
      "jira",
      "issues",
      jiraIntegration?.endpoints.issues,
      selectedJiraBoardId,
      selectedJiraProjectKey,
    ],
    enabled: Boolean(
      jiraIntegration?.enabled && jiraBrowserOpen && selectedJiraBoardId,
    ),
    queryFn: async (): Promise<JiraBoardIssues> => {
      const endpoint = withQueryParams(
        interpolatePath(jiraIntegration?.endpoints.issues || "", {
          boardId: selectedJiraBoardId,
        }),
        { projectKey: selectedJiraProjectKey },
      );
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load Jira issues."),
        );
      }
      const data = (await response.json()) as {
        columns?: unknown;
        itemsByColumn?: Record<string, JiraIssueSummary[]>;
      } | null;
      return {
        columns: parseJiraColumns(data?.columns),
        itemsByColumn: data?.itemsByColumn || {},
      };
    },
  });

  const jiraIssueDetailQuery = useQuery({
    queryKey: [
      "task-create",
      "jira",
      "issue",
      jiraIntegration?.endpoints.issue,
      selectedJiraIssueKey,
      selectedJiraBoardId,
      selectedJiraProjectKey,
    ],
    enabled: Boolean(
      jiraIntegration?.enabled &&
        jiraBrowserOpen &&
        selectedJiraIssueKey,
    ),
    queryFn: async (): Promise<JiraIssueDetail> => {
      const endpoint = withQueryParams(
        interpolatePath(jiraIntegration?.endpoints.issue || "", {
          issueKey: selectedJiraIssueKey,
        }),
        {
          boardId: selectedJiraBoardId,
          projectKey: selectedJiraProjectKey,
        },
      );
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load Jira issue."),
        );
      }
      return (await response.json()) as JiraIssueDetail;
    },
  });

  const jiraBrowserColumns = useMemo(() => {
    const configuredColumns = jiraColumnsQuery.data || [];
    const countedColumns = jiraIssuesQuery.data?.columns || [];
    if (countedColumns.length === 0) {
      return configuredColumns;
    }
    const countedById = new Map(
      countedColumns.map((column) => [column.id, column]),
    );
    const mergedColumns =
      configuredColumns.length > 0
        ? configuredColumns.map((column) => {
            const counted = countedById.get(column.id);
            if (!counted) {
              return column;
            }
            return {
              ...column,
              count: counted.count ?? 0,
            };
          })
        : countedColumns;
    const mergedIds = new Set(mergedColumns.map((column) => column.id));
    return [
      ...mergedColumns,
      ...countedColumns.filter((column) => !mergedIds.has(column.id)),
    ];
  }, [jiraColumnsQuery.data, jiraIssuesQuery.data?.columns]);

  const templateItems = templateOptionsQuery.data?.items || [];

  useEffect(() => {
    if (!jiraBrowserOpen || !jiraIntegration) {
      return;
    }
    const projects = jiraProjectsQuery.data || [];
    if (selectedJiraProjectKey) {
      const selectedProjectExists = projects.some(
        (project) => project.projectKey === selectedJiraProjectKey,
      );
      if (jiraProjectsQuery.data && !selectedProjectExists) {
        if (jiraIntegration.rememberLastBoardInSession) {
          writeSessionPreference(JIRA_LAST_PROJECT_SESSION_KEY, "");
          writeSessionPreference(JIRA_LAST_BOARD_SESSION_KEY, "");
        }
        setSelectedJiraProjectKey("");
        setSelectedJiraBoardId("");
        setActiveJiraColumnId("");
        setSelectedJiraIssueKey("");
        jiraProjectSelectionInitializedRef.current = false;
        jiraBoardSelectionInitializedRef.current = false;
        return;
      }
      jiraProjectSelectionInitializedRef.current = true;
      return;
    }
    if (projects.length === 0 || jiraProjectSelectionInitializedRef.current) {
      return;
    }
    const configured = projects.find(
      (project) => project.projectKey === jiraIntegration.defaultProjectKey,
    );
    setSelectedJiraProjectKey((configured || projects[0])?.projectKey || "");
    jiraProjectSelectionInitializedRef.current = true;
    jiraBoardSelectionInitializedRef.current = false;
  }, [
    jiraBrowserOpen,
    jiraIntegration,
    jiraProjectsQuery.data,
    selectedJiraProjectKey,
  ]);

  useEffect(() => {
    if (!jiraBrowserOpen || !jiraIntegration) {
      return;
    }
    const boards = jiraBoardsQuery.data || [];
    if (selectedJiraBoardId) {
      const selectedBoardExists = boards.some(
        (board) => board.id === selectedJiraBoardId,
      );
      if (jiraBoardsQuery.data && !selectedBoardExists) {
        if (jiraIntegration.rememberLastBoardInSession) {
          writeSessionPreference(JIRA_LAST_BOARD_SESSION_KEY, "");
        }
        setSelectedJiraBoardId("");
        setActiveJiraColumnId("");
        setSelectedJiraIssueKey("");
        jiraBoardSelectionInitializedRef.current = false;
        return;
      }
      jiraBoardSelectionInitializedRef.current = true;
      return;
    }
    if (boards.length === 0 || jiraBoardSelectionInitializedRef.current) {
      return;
    }
    const configured = boards.find(
      (board) => board.id === jiraIntegration.defaultBoardId,
    );
    setSelectedJiraBoardId((configured || boards[0])?.id || "");
    jiraBoardSelectionInitializedRef.current = true;
  }, [
    jiraBoardsQuery.data,
    jiraBrowserOpen,
    jiraIntegration,
    selectedJiraBoardId,
  ]);

  useEffect(() => {
    if (!jiraBrowserOpen) {
      return;
    }
    const columns = jiraBrowserColumns;
    const activeStillExists = columns.some(
      (column) => column.id === activeJiraColumnId,
    );
    if (activeStillExists) {
      return;
    }
    setActiveJiraColumnId(columns[0]?.id || "");
  }, [activeJiraColumnId, jiraBrowserOpen, jiraBrowserColumns]);

  useEffect(() => {
    if (!taskTemplateCatalogEnabled || !selectedPresetKey) {
      return;
    }
    const selectedStillExists = templateItems.some(
      (item) => item.key === selectedPresetKey,
    );
    if (selectedStillExists) {
      return;
    }
    setSelectedPresetKey("");
  }, [selectedPresetKey, taskTemplateCatalogEnabled, templateItems]);

  const selectedPreset =
    templateItems.find((item) => item.key === selectedPresetKey) || null;
  const selectedPresetDeleteEnabled =
    taskTemplateSaveEnabled && selectedPreset?.scope === "personal";
  const deletePresetTooltip = selectedPreset
    ? selectedPresetDeleteEnabled
      ? "Delete the selected preset"
      : "Only personal presets can be deleted"
    : "Choose a preset to delete";
  const effectiveSkillId = useMemo(
    () =>
      resolveEffectiveSkillId(
        String(steps[0]?.skillId || "").trim() || "auto",
        activeAppliedTemplatesForSteps(appliedTemplates, steps),
      ),
    [appliedTemplates, steps],
  );

  useEffect(() => {
    if (
      pageMode.mode === "create" &&
      selectedPreset &&
      isSelfManagedPublishSkill(selectedPreset.slug)
    ) {
      setPublishMode("none");
    }
  }, [pageMode.mode, selectedPreset?.slug]);

  const mergeAutomationAvailable = !isSelfManagedPublishSkill(effectiveSkillId);

  useEffect(() => {
    if (!mergeAutomationAvailable && publishMode !== "none") {
      setPublishMode("none");
    }
  }, [mergeAutomationAvailable, publishMode]);

  const availableDependencyOptions = useMemo(
    () =>
      (dependencyOptionsQuery.data || []).filter(
        (item) => !selectedDependencies.includes(item.taskId),
      ),
    [dependencyOptionsQuery.data, selectedDependencies],
  );

  const activeJiraIssues =
    (activeJiraColumnId &&
      jiraIssuesQuery.data?.itemsByColumn[activeJiraColumnId]) ||
    [];
  const selectedJiraIssue = jiraIssueDetailQuery.isError
    ? null
    : jiraIssueDetailQuery.data || null;
  const selectedJiraImportText = useMemo(() => {
    if (!selectedJiraIssue) {
      return "";
    }
    return jiraImportTextForMode(selectedJiraIssue, jiraImportMode);
  }, [jiraImportMode, selectedJiraIssue]);
  const jiraProjectsError = jiraProjectsQuery.isError
    ? localJiraErrorMessage(
        jiraProjectsQuery.error,
        "Failed to load Jira projects.",
      )
    : null;
  const jiraBoardsError = jiraBoardsQuery.isError
    ? localJiraErrorMessage(
        jiraBoardsQuery.error,
        "Failed to load Jira boards.",
      )
    : null;
  const jiraBoardIssuesError =
    jiraColumnsQuery.isError || jiraIssuesQuery.isError
      ? localJiraErrorMessage(
          jiraColumnsQuery.error || jiraIssuesQuery.error,
          "Failed to load Jira issues.",
        )
      : null;
  const jiraIssueError = jiraIssueDetailQuery.isError
    ? localJiraErrorMessage(
        jiraIssueDetailQuery.error,
        "Failed to load Jira issue.",
      )
    : null;
  const jiraProjectsEmpty =
    jiraProjectsQuery.isSuccess && (jiraProjectsQuery.data || []).length === 0
      ? localJiraEmptyStateMessage("No Jira projects are available.")
      : null;
  const jiraBoardsEmpty =
    selectedJiraProjectKey &&
    jiraBoardsQuery.isSuccess &&
    (jiraBoardsQuery.data || []).length === 0
      ? localJiraEmptyStateMessage("No Jira boards are available for this project.")
      : null;
  const jiraColumnsEmpty =
    selectedJiraBoardId &&
    jiraColumnsQuery.isSuccess &&
    (jiraColumnsQuery.data || []).length === 0
      ? localJiraEmptyStateMessage("No Jira columns are available for this board.")
      : null;
  const jiraActiveColumnEmpty =
    selectedJiraBoardId &&
    activeJiraColumnId &&
    jiraIssuesQuery.isSuccess &&
    activeJiraIssues.length === 0
      ? localJiraEmptyStateMessage("No Jira issues are available in this column.")
      : null;
  const jiraTargetText = jiraTargetLabel(jiraImportTarget, steps);
  const jiraTargetStep =
    jiraImportTarget?.kind === "step"
      ? steps.find((step) => step.localId === jiraImportTarget.localId) || null
      : null;
  const jiraImportWillCustomizeTemplateStep =
    jiraImportTarget?.attachmentsOnly
      ? isTemplateBoundStepForAttachments(
          jiraTargetStep,
          jiraTargetStep
            ? selectedStepAttachmentFiles[jiraTargetStep.localId] || []
            : [],
        )
      : isTemplateBoundStepForInstructions(jiraTargetStep);

  useEffect(() => {
    if (
      !jiraBrowserOpen ||
      !pendingJiraImportIssueKey ||
      selectedJiraIssueKey !== pendingJiraImportIssueKey
    ) {
      return;
    }
    if (jiraIssueDetailQuery.isError) {
      setPendingJiraImportIssueKey("");
      return;
    }
    if (jiraIssueDetailQuery.isFetching || !selectedJiraIssue) {
      return;
    }
    setPendingJiraImportIssueKey("");
    void importSelectedJiraIssue();
  }, [
    jiraIssueDetailQuery.isError,
    jiraIssueDetailQuery.isFetching,
    jiraBrowserOpen,
    pendingJiraImportIssueKey,
    selectedJiraIssue,
    selectedJiraIssueKey,
  ]);

  function jiraProvenanceForTarget(
    target: JiraImportTarget,
  ): JiraImportProvenance | null {
    if (target.kind === "preset") {
      return presetJiraProvenance;
    }
    return stepJiraProvenance[target.localId] || null;
  }

  function openJiraBrowser(target: JiraImportTarget) {
    const provenance = jiraProvenanceForTarget(target);
    const rememberedProjectKey =
      jiraIntegration?.rememberLastBoardInSession && !selectedJiraProjectKey
        ? readSessionPreference(JIRA_LAST_PROJECT_SESSION_KEY)
        : "";
    const rememberedBoardId =
      jiraIntegration?.rememberLastBoardInSession && !selectedJiraBoardId
        ? readSessionPreference(JIRA_LAST_BOARD_SESSION_KEY)
        : "";
    const provenanceProjectKey = jiraProjectKeyFromIssueKey(
      provenance?.issueKey || "",
    );
    const nextProjectKey =
      provenanceProjectKey ||
      rememberedProjectKey ||
      selectedJiraProjectKey ||
      jiraIntegration?.defaultProjectKey ||
      "";
    const nextBoardId =
      provenance?.boardId ||
      rememberedBoardId ||
      selectedJiraBoardId ||
      jiraIntegration?.defaultBoardId ||
      "";
    if (nextProjectKey) {
      setSelectedJiraProjectKey(nextProjectKey);
    }
    if (nextBoardId) {
      setSelectedJiraBoardId(nextBoardId);
    }
    if (provenance?.columnId) {
      setActiveJiraColumnId(provenance.columnId);
    }
    jiraProjectSelectionInitializedRef.current = Boolean(
      nextProjectKey,
    );
    jiraBoardSelectionInitializedRef.current = Boolean(
      nextBoardId,
    );
    setJiraImportTarget(target);
    setJiraImportMode(defaultJiraImportMode(target));
    setJiraWriteMode("append");
    setJiraBrowserOpen(true);
    setSelectedJiraIssueKey(provenance?.issueKey || "");
    setPendingJiraImportIssueKey("");
  }

  function selectJiraImportTarget(value: string) {
    const target = jiraTargetFromValue(value);
    if (!target) {
      return;
    }
    setJiraImportTarget(target);
    setJiraImportMode(defaultJiraImportMode(target));
  }

  function closeJiraBrowser() {
    setJiraBrowserOpen(false);
  }

  function selectJiraProject(projectKey: string) {
    jiraProjectSelectionInitializedRef.current = true;
    jiraBoardSelectionInitializedRef.current = false;
    if (jiraIntegration?.rememberLastBoardInSession) {
      writeSessionPreference(JIRA_LAST_PROJECT_SESSION_KEY, projectKey);
      writeSessionPreference(JIRA_LAST_BOARD_SESSION_KEY, "");
    }
    setSelectedJiraProjectKey(projectKey);
    setSelectedJiraBoardId("");
    setActiveJiraColumnId("");
    setSelectedJiraIssueKey("");
  }

  function selectJiraBoard(boardId: string) {
    jiraBoardSelectionInitializedRef.current = true;
    if (jiraIntegration?.rememberLastBoardInSession) {
      writeSessionPreference(JIRA_LAST_BOARD_SESSION_KEY, boardId);
    }
    setSelectedJiraBoardId(boardId);
    setActiveJiraColumnId("");
    setSelectedJiraIssueKey("");
  }

  function selectJiraColumn(columnId: string) {
    setActiveJiraColumnId(columnId);
    setSelectedJiraIssueKey("");
    setPendingJiraImportIssueKey("");
  }

  function selectJiraIssue(issueKey: string) {
    setPendingJiraImportIssueKey(issueKey);
    setSelectedJiraIssueKey(issueKey);
  }

  function resetTemplateStepIdForAttachmentChange(
    localId: string,
    attachments: Array<StepAttachmentRef | File>,
  ) {
    setSteps((current) =>
      current.map((step) => {
        if (
          step.localId !== localId ||
          !step.templateStepId ||
          step.id !== step.templateStepId ||
          isTemplateBoundStepForAttachments(step, attachments)
        ) {
          return step;
        }
        return { ...step, id: "" };
      }),
    );
  }

  async function importSelectedJiraImages(
    issue: JiraIssueDetail,
    target: JiraImportTarget,
    objectiveTextForReapply?: string,
  ): Promise<void> {
    const attachments = Array.isArray(issue.attachments) ? issue.attachments : [];
    if (!attachmentPolicy.enabled || attachments.length === 0) {
      return;
    }
    const eligible = attachments.filter(
      (attachment) => !validateJiraImageAttachment(attachment, attachmentPolicy),
    );
    if (eligible.length === 0) {
      setSubmitMessage(
        "Jira images are not supported by the current attachment policy.",
      );
      return;
    }
    const existingFiles =
      target.kind === "preset"
        ? selectedObjectiveAttachmentFiles
        : selectedStepAttachmentFiles[target.localId] || [];
    const existingKeys = new Set(
      existingFiles.map((file) => `${file.name}:${file.size}:${file.type}`),
    );
    const room = Math.max(
      0,
      attachmentPolicy.maxCount -
        (selectedObjectiveAttachmentFiles.length +
          Object.values(selectedStepAttachmentFiles).flat().length +
          persistedAttachmentRefs.length),
    );
    const toDownload = eligible.slice(0, room);
    if (toDownload.length === 0) {
      setSubmitMessage("Attachment limit reached before Jira images could be added.");
      return;
    }
    try {
      const downloaded = await Promise.allSettled(
        toDownload.map(async (attachment) => {
          const response = await fetch(attachment.downloadUrl);
          if (!response.ok) {
            throw new Error(
              await responseErrorMessage(response, "Failed to download Jira image."),
            );
          }
          const blob = await response.blob();
          const type = String(
            blob.type || attachment.contentType || "",
          ).toLowerCase();
          const file = new File([blob], attachment.filename, { type });
          return existingKeys.has(`${file.name}:${file.size}:${file.type}`)
            ? null
            : file;
        }),
      );
      const files = downloaded
        .filter(
          (result): result is PromiseFulfilledResult<File | null> =>
            result.status === "fulfilled",
        )
        .map((result) => result.value)
        .filter((file): file is File => file !== null);
      const failures = downloaded
        .filter(
          (result): result is PromiseRejectedResult =>
            result.status === "rejected",
        )
        .map((result) =>
          result.reason instanceof Error
            ? result.reason.message
            : "Failed to download Jira image.",
        );
      if (files.length > 0) {
        const nextObjectiveFiles =
          target.kind === "preset"
            ? [...existingFiles, ...files]
            : selectedObjectiveAttachmentFiles;
        const nextFilesByStep: Record<string, File[]> =
          target.kind === "step"
            ? {
                ...selectedStepAttachmentFiles,
                [target.localId]: [...existingFiles, ...files],
              }
            : selectedStepAttachmentFiles;
        const validation = validateAttachmentFiles(
          [...nextObjectiveFiles, ...Object.values(nextFilesByStep).flat()],
          attachmentPolicy,
          persistedAttachmentRefs,
        );
        if (!validation.ok) {
          setSubmitMessage(validation.errors.join(" "));
          return;
        }
        if (target.kind === "preset") {
          setSelectedObjectiveAttachmentFiles(nextObjectiveFiles);
          updatePresetReapplyStateForObjective(
            objectiveTextForReapply ?? templateFeatureRequest,
            nextObjectiveFiles,
          );
        } else {
          resetTemplateStepIdForAttachmentChange(
            target.localId,
            nextFilesByStep[target.localId] || [],
          );
          setSelectedStepAttachmentFiles(nextFilesByStep);
        }
      }
      const messages: string[] = [];
      if (eligible.length > toDownload.length) {
        messages.push(
          "Some Jira images were skipped because the attachment limit was reached.",
        );
      }
      if (failures.length > 0) {
        const uniqueFailures = Array.from(new Set(failures));
        messages.push(
          uniqueFailures.length === 1
            ? (uniqueFailures[0] ?? "Failed to download Jira image.")
            : `${failures.length} Jira images failed to download. ${uniqueFailures
                .slice(0, 3)
                .join(" ")}`,
        );
      }
      if (messages.length > 0) {
        setSubmitMessage(messages.join(" "));
      }
    } catch (error) {
      const failure =
        error instanceof Error
          ? error
          : new Error("Failed to download Jira images.");
      setSubmitMessage(failure.message);
    }
  }

  async function importSelectedJiraImagesWithReporting(
    issue: JiraIssueDetail,
    target: JiraImportTarget,
    objectiveTextForReapply?: string,
  ): Promise<void> {
    try {
      await importSelectedJiraImages(issue, target, objectiveTextForReapply);
    } catch (error) {
      const failure =
        error instanceof Error
          ? error
          : new Error("Failed to download Jira images.");
      setSubmitMessage(failure.message);
    }
  }

  async function importSelectedJiraIssue() {
    closeJiraBrowser();
    const issue = selectedJiraIssue;
    const importTarget = jiraImportTarget;
    if (!issue || !importTarget) {
      return;
    }
    if (importTarget.attachmentsOnly) {
      const provenance = createJiraProvenance(
        issue,
        selectedJiraBoardId,
        jiraImportMode,
        importTarget,
      );
      if (importTarget.kind === "preset") {
        setPresetJiraProvenance(provenance);
      } else {
        setStepJiraProvenance((current) => {
          if (provenance) {
            return { ...current, [importTarget.localId]: provenance };
          }
          if (!current[importTarget.localId]) {
            return current;
          }
          const { [importTarget.localId]: _removed, ...rest } = current;
          return rest;
        });
        updateStep(importTarget.localId, { id: "" });
      }
      await importSelectedJiraImagesWithReporting(issue, importTarget);
      return;
    }
    if (!selectedJiraImportText.trim()) {
      return;
    }
    if (importTarget.kind === "preset") {
      const nextText = writeJiraImportedText(
        templateFeatureRequest,
        selectedJiraImportText,
        jiraWriteMode,
      );
      const provenance = createJiraProvenance(
        issue,
        selectedJiraBoardId,
        jiraImportMode,
        importTarget,
      );
      if (nextText.trim() !== templateFeatureRequest.trim()) {
        setTemplateFeatureRequest(nextText);
        updatePresetReapplyStateForObjective(
          nextText,
          selectedObjectiveAttachmentFiles,
        );
      }
      setPresetJiraProvenance(provenance);
      await importSelectedJiraImagesWithReporting(issue, importTarget, nextText);
      return;
    }

    const targetStep = steps.find((step) => step.localId === importTarget.localId);
    if (!targetStep) {
      return;
    }
    updateStep(importTarget.localId, {
      instructions: writeJiraImportedText(
        targetStep.instructions,
        selectedJiraImportText,
        jiraWriteMode,
      ),
    });
    const provenance = createJiraProvenance(
      issue,
      selectedJiraBoardId,
      jiraImportMode,
      importTarget,
    );
    setStepJiraProvenance((current) => {
      if (provenance) {
        return {
          ...current,
          [importTarget.localId]: provenance,
        };
      }
      if (!current[importTarget.localId]) {
        return current;
      }
      const { [importTarget.localId]: _removed, ...rest } = current;
      return rest;
    });
    await importSelectedJiraImagesWithReporting(issue, importTarget);
  }

  function updatePresetReapplyStateForObjective(
    nextText: string,
    nextFiles: File[],
  ) {
    if (appliedTemplates.length === 0) {
      setPresetReapplyNeeded(false);
      return;
    }
    setPresetReapplyNeeded(
      nextText.trim() !== appliedTemplateFeatureRequest.trim() ||
        attachmentSignature(nextFiles) !==
          appliedTemplateObjectiveAttachmentSignature,
    );
  }

  function addDependency(workflowId: string) {
    const normalizedId = workflowId.trim();
    if (!normalizedId) {
      setDependencyMessage("Choose a prerequisite run before adding it.");
      return;
    }
    if (selectedDependencies.includes(normalizedId)) {
      setDependencyMessage("That prerequisite is already selected.");
      return;
    }
    if (selectedDependencies.length >= DEPENDENCY_LIMIT) {
      setDependencyMessage(
        `You can add at most ${DEPENDENCY_LIMIT} direct dependencies.`,
      );
      return;
    }
    setSelectedDependencies((current) => [...current, normalizedId]);
    setSelectedDependencyWorkflowId("");
    setDependencyMessage(null);
  }

  function removeDependency(workflowId: string) {
    setSelectedDependencies((current) =>
      current.filter((item) => item !== workflowId),
    );
    setDependencyMessage(null);
  }

  function updateStepAttachments(localId: string, files: File[]) {
    const targetKey = attachmentTargetKey(localId);
    const mergedFilesForBinding = appendDedupedAttachmentFiles(
      selectedStepAttachmentFiles[localId] || [],
      files,
    );
    const previousStepFiles = selectedStepAttachmentFiles[localId] || [];
    const nextTotalCount =
      selectedAttachmentFiles.length -
      previousStepFiles.length +
      mergedFilesForBinding.length +
      persistedAttachmentRefs.length;
    const limitMessage = attachmentLimitMessage(attachmentPolicy);
    if (nextTotalCount > attachmentPolicy.maxCount) {
      setSubmitMessage(limitMessage);
      return;
    }
    if (submitMessage === limitMessage) {
      setSubmitMessage(null);
    }
    setAttachmentTargetErrors((current) => {
      const next = { ...current };
      delete next[targetKey];
      return next;
    });
    resetTemplateStepIdForAttachmentChange(localId, mergedFilesForBinding);
    setSelectedStepAttachmentFiles((current) => {
      const mergedFiles = appendDedupedAttachmentFiles(
        current[localId] || [],
        files,
      );
      const next = { ...current };
      if (mergedFiles.length > 0) {
        next[localId] = mergedFiles;
      } else {
        delete next[localId];
      }
      return next;
    });
  }

  function removePersistedObjectiveAttachment(artifactId: string) {
    setPersistedObjectiveAttachments((current) =>
      current.filter((attachment) => attachment.artifactId !== artifactId),
    );
  }

  function removePersistedStepAttachment(localId: string, artifactId: string) {
    setSteps((current) =>
      current.map((step) =>
        step.localId === localId
          ? {
              ...step,
              inputAttachments: step.inputAttachments.filter(
                (attachment) => attachment.artifactId !== artifactId,
              ),
            }
          : step,
      ),
    );
  }

  function removeStepAttachment(localId: string, fileToRemove: File) {
    const targetKey = attachmentTargetKey(localId);
    const previewKey = `${targetKey}:${attachmentFileKey(fileToRemove)}`;
    setAttachmentTargetErrors((current) => {
      const next = { ...current };
      delete next[targetKey];
      return next;
    });
    setPreviewFailureMessages((current) => {
      const next = { ...current };
      delete next[previewKey];
      return next;
    });
    setSelectedStepAttachmentFiles((current) => {
      const files = current[localId] || [];
      const nextFiles = files.filter((file) => file !== fileToRemove);
      const next = { ...current };
      if (nextFiles.length > 0) {
        next[localId] = nextFiles;
      } else {
        delete next[localId];
      }
      return next;
    });
  }

  function clearAttachmentTargetError(targetKey: string) {
    setAttachmentTargetErrors((current) => {
      const next = { ...current };
      delete next[targetKey];
      return next;
    });
  }

  function markAttachmentPreviewFailed(
    targetKey: string,
    targetLabel: string,
    file: File,
  ) {
    const message = `${targetLabel}: Preview unavailable for ${
      file.name || "attachment"
    }. Attachment metadata remains available.`;
    setPreviewFailureMessages((current) => ({
      ...current,
      [`${targetKey}:${attachmentFileKey(file)}`]: message,
    }));
  }

  const selectedAttachmentFiles = useMemo(
    () => [
      ...selectedObjectiveAttachmentFiles,
      ...Object.values(selectedStepAttachmentFiles).flat(),
    ],
    [selectedObjectiveAttachmentFiles, selectedStepAttachmentFiles],
  );

  const persistedAttachmentRefs = useMemo(
    () => [
      ...persistedObjectiveAttachments,
      ...steps.flatMap((step) => step.inputAttachments),
    ],
    [persistedObjectiveAttachments, steps],
  );

  const providerOptions = [...(providerProfilesQuery.data || [])]
    .sort((left, right) => {
      const leftDefault = Boolean(left.is_default);
      const rightDefault = Boolean(right.is_default);
      if (leftDefault !== rightDefault) {
        return leftDefault ? -1 : 1;
      }
      return (left.account_label || left.profile_id).localeCompare(
        right.account_label || right.profile_id,
      );
    })
    .map((profile) => ({
      id: profile.profile_id,
      label: profile.account_label || profile.profile_id,
      isDefault: Boolean(profile.is_default),
    }));

  const attachmentLabel = attachmentControlLabel(attachmentPolicy);
  const attachmentTargetValidation = useMemo(
    () =>
      validateAttachmentTargets(
        [
          {
            key: attachmentTargetKey("objective"),
            label: "Instructions",
            files: selectedObjectiveAttachmentFiles,
          },
          ...steps.map((step, index) => ({
            key: attachmentTargetKey(step.localId),
            label: `Step ${index + 1}`,
            files: selectedStepAttachmentFiles[step.localId] || [],
          })),
        ],
        attachmentPolicy,
        persistedAttachmentRefs,
      ),
    [
      attachmentPolicy,
      persistedAttachmentRefs,
      selectedObjectiveAttachmentFiles,
      selectedStepAttachmentFiles,
      steps,
    ],
  );

  const modelOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [
            String(defaultTaskModelByRuntime[runtime] || ""),
            String(dashboardConfig.system?.defaultTaskModel || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultTaskModel,
      defaultTaskModelByRuntime,
      runtime,
    ],
  );

  const effortOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [
            "low",
            "medium",
            "high",
            "xhigh",
            String(defaultTaskEffortByRuntime[runtime] || ""),
            String(dashboardConfig.system?.defaultTaskEffort || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultTaskEffort,
      defaultTaskEffortByRuntime,
      runtime,
    ],
  );

  const branchLookupEndpoint = normalizeMoonMindApiPath(
    dashboardConfig.sources?.github?.branches,
  );
  const selectedRepositoryForBranchLookup =
    repository.trim() || defaultRepository;
  const branchLookupRepository = canLookupRepositoryBranches(
    selectedRepositoryForBranchLookup,
  )
    ? selectedRepositoryForBranchLookup.trim()
    : "";
  const branchOptionsQuery = useQuery({
    queryKey: ["task-create", "github-branches", branchLookupRepository],
    enabled: Boolean(branchLookupEndpoint && branchLookupRepository),
    queryFn: async () =>
      readBranchOptions(branchLookupEndpoint || "", branchLookupRepository),
  });
  const branchOptions = useMemo(() => {
    const items = branchOptionsQuery.data?.items || [];
    const seen = new Set<string>();
    return items.filter((item) => {
      const key = item.value;
      if (!key || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }, [branchOptionsQuery.data]);
  const defaultBranch = useMemo(() => {
    const value = String(branchOptionsQuery.data?.defaultBranch || "").trim();
    return value;
  }, [branchOptionsQuery.data?.defaultBranch]);
  const effectiveBranch =
    branch.trim() ||
    (!branchTouched && pageMode.mode === "create" ? defaultBranch : "");
  const selectedBranchIsStale = Boolean(
    branch.trim() &&
      branchOptionsQuery.isSuccess &&
      !(branchOptionsQuery.data?.items || []).some(
        (item) => item.value === branch.trim(),
      ),
  );
  const branchControlDisabled =
    !selectedRepositoryForBranchLookup.trim() ||
    !branchLookupEndpoint ||
    !branchLookupRepository ||
    branchOptionsQuery.isLoading;
  const branchStatusMessage = (() => {
    if (!selectedRepositoryForBranchLookup.trim()) {
      return "Select a repository to load branches.";
    }
    if (!branchLookupEndpoint) {
      return "Branch lookup is not configured.";
    }
    if (!branchLookupRepository) {
      return "Branch lookup requires a valid GitHub repository value.";
    }
    if (branchOptionsQuery.isLoading) {
      return branch.trim() ? "Loading branches..." : "";
    }
    if (branchOptionsQuery.isFetching) {
      return "";
    }
    if (branchOptionsQuery.isError) {
      const error = branchOptionsQuery.error;
      return error instanceof Error ? error.message : "Failed to load branches.";
    }
    if (selectedBranchIsStale) {
      return "Selected branch is not in the latest list for this repository.";
    }
    if (branchOptionsQuery.isSuccess && branchOptions.length === 0) {
      return "No branches returned for this repository.";
    }
    return "";
  })();
  const handleRepositoryChange = (value: string) => {
    setRepository(value);
    const selectedOption = repositoryOptionValue(repositoryOptions, value);
    writeLocalPreference(LAST_REPOSITORY_OPTION_PREFERENCE_KEY, selectedOption);
  };

  const presetStatusText = useMemo(() => {
    if (presetReapplyNeeded) {
      return PRESET_REAPPLY_REQUIRED_MESSAGE;
    }
    if (templateMessage) {
      return templateMessage;
    }
    if (templateOptionsQuery.isLoading) {
      return "Loading presets...";
    }
    if (templateOptionsQuery.isError) {
      return "Failed to load presets.";
    }
    if (templateItems.length === 0) {
      return "No presets available for your account.";
    }
    if ((templateOptionsQuery.data?.failedScopes || []).length > 0) {
      return `Loaded ${templateItems.length} presets (missing scopes: ${(templateOptionsQuery.data?.failedScopes || []).join(", ")}).`;
    }
    return `Loaded ${templateItems.length} presets.`;
  }, [
    templateItems,
    templateMessage,
    presetReapplyNeeded,
    templateOptionsQuery.data?.failedScopes,
    templateOptionsQuery.isError,
    templateOptionsQuery.isLoading,
  ]);

  function stepPresetStatusText(step: StepState): string {
    if (step.presetMessage) {
      return step.presetMessage;
    }
    if (templateOptionsQuery.isLoading) {
      return "Loading presets...";
    }
    if (templateOptionsQuery.isError) {
      return "Failed to load presets.";
    }
    if (templateItems.length === 0) {
      return "No presets available for your account.";
    }
    return "";
  }

  function updateStepPreset(
    localId: string,
    presetKey: string,
    presetDetail: TaskTemplateDetail | null = null,
  ) {
    updateStep(localId, {
      presetKey,
      presetInputValues: {},
      presetDetail,
      presetMessage: null,
    });
  }

  function updateStepPresetIfCurrent(
    localId: string,
    presetKey: string,
    updates: Partial<StepState>,
  ) {
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId || step.presetKey !== presetKey) {
          return step;
        }
        return { ...step, ...updates };
      }),
    );
  }

  function currentStepPresetMatches(
    localId: string,
    presetKey: string,
    expectedInstructions?: string,
    expectedInputValues?: Record<string, string | boolean>,
  ): boolean {
    const currentStep = stepsRef.current.find((step) => step.localId === localId);
    const inputValuesMatch =
      expectedInputValues === undefined ||
      presetInputValueSignature(currentStep?.presetInputValues || {}) ===
        presetInputValueSignature(expectedInputValues);
    return Boolean(
      currentStep &&
        currentStep.stepType === "preset" &&
        currentStep.presetKey === presetKey &&
        (expectedInstructions === undefined ||
          currentStep.instructions === expectedInstructions) &&
        inputValuesMatch,
    );
  }

  async function handleStepPresetSelectionChange(
    localId: string,
    presetKey: string,
  ) {
    const preset = templateItems.find((item) => item.key === presetKey);
    updateStepPreset(localId, presetKey);
    if (!preset) {
      return;
    }
    if (
      pageMode.mode === "create" &&
      isSelfManagedPublishSkill(preset.slug)
    ) {
      setPublishMode("none");
    }
    updateStep(localId, { presetMessage: "Loading preset options..." });
    try {
      const detail = await loadPresetDetail(preset);
      updateStepPresetIfCurrent(localId, presetKey, {
        presetDetail: detail,
        presetMessage: null,
      });
    } catch (error) {
      const failure =
        error instanceof Error
          ? error
          : new Error("Failed to load preset options.");
      updateStepPresetIfCurrent(localId, presetKey, {
        presetDetail: null,
        presetMessage: `Failed to load preset options: ${failure.message}`,
      });
    }
  }

  function updateStep(localId: string, updates: Partial<StepState>) {
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId) {
          return step;
        }
        const nextStep = { ...step, ...updates };
        if (
          Object.prototype.hasOwnProperty.call(updates, "instructions") &&
          nextStep.templateStepId &&
          nextStep.id === nextStep.templateStepId &&
          nextStep.instructions !== nextStep.templateInstructions
        ) {
          nextStep.id = "";
        }
        if (
          Object.prototype.hasOwnProperty.call(updates, "skillId") &&
          !showAdvancedStepOptions
        ) {
          nextStep.skillArgs = "";
        }
        return nextStep;
      }),
    );
  }

  function handleStepInstructionsChange(localId: string, value: string) {
    updateStep(localId, { instructions: value });
    setStepJiraProvenance((current) => {
      if (!current[localId]) {
        return current;
      }
      const { [localId]: _removed, ...rest } = current;
      return rest;
    });
  }

  function selectTrustedTool(localId: string, toolId: string) {
    updateStep(localId, { toolId });
    setJiraTransitionStateByStep((current) => {
      if (!current[localId]) {
        return current;
      }
      const { [localId]: _removed, ...rest } = current;
      return rest;
    });
  }

  async function loadJiraTransitionOptions(step: StepState) {
    const issueKey = extractIssueKeyFromToolInputs(step.toolInputs);
    if (!issueKey) {
      setJiraTransitionStateByStep((current) => ({
        ...current,
        [step.localId]: {
          isLoading: false,
          error: "Enter issueKey in Tool Inputs before loading Jira target statuses.",
          issueKey: "",
          toolId: step.toolId.trim(),
          options: [],
        },
      }));
      return;
    }
    setJiraTransitionStateByStep((current) => ({
      ...current,
      [step.localId]: {
        isLoading: true,
        error: null,
        issueKey,
        toolId: step.toolId.trim(),
        options: [],
      },
    }));
    try {
      const response = await fetch("/mcp/tools/call", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          tool: "jira.get_transitions",
          arguments: { issueKey, expandFields: true },
        }),
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to load Jira target statuses.",
          ),
        );
      }
      const data = (await response.json()) as {
        result?: { transitions?: Array<Record<string, unknown>> };
      };
      const seen = new Set<string>();
      const options = (data.result?.transitions || [])
        .map((transition) => {
          const to = transition.to;
          const target =
            to && typeof to === "object" && !Array.isArray(to)
              ? String((to as Record<string, unknown>).name || "").trim()
              : "";
          const name = target || String(transition.name || "").trim();
          const id = String(transition.id || name).trim();
          return { id, name };
        })
        .filter((option) => {
          if (!option.name || seen.has(option.name)) {
            return false;
          }
          seen.add(option.name);
          return true;
        });
      setJiraTransitionStateByStep((current) => ({
        ...current,
        [step.localId]: {
          isLoading: false,
          error:
            options.length > 0
              ? null
              : "No Jira target statuses were returned for this issue.",
          issueKey,
          toolId: step.toolId.trim(),
          options,
        },
      }));
    } catch (error) {
      setJiraTransitionStateByStep((current) => ({
        ...current,
        [step.localId]: {
          isLoading: false,
          error:
            error instanceof Error
              ? error.message
              : "Failed to load Jira target statuses.",
          issueKey,
          toolId: step.toolId.trim(),
          options: [],
        },
      }));
    }
  }

  function applyJiraTransitionId(localId: string, transitionId: string) {
    if (!transitionId) {
      return;
    }
    const step = stepsRef.current.find((item) => item.localId === localId);
    if (!step) {
      return;
    }
    updateStep(localId, {
      toolInputs: updateToolInputsText(step.toolInputs, {
        transitionId,
        targetStatus: undefined,
      }),
    });
  }

  function handleStepTypeChange(localId: string, value: string) {
    const nextType: StepType =
      value === "tool" || value === "preset" ? value : "skill";
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId || step.stepType === nextType) {
          return step;
        }

        const discardedLabels: string[] = [];
        const nextStep: StepState = {
          ...step,
          stepType: nextType,
          stepTypeMessage: null,
        };

        if (
          step.stepType === "skill" &&
          (step.skillId.trim() ||
            step.skillArgs.trim() ||
            step.skillRequiredCapabilities.trim())
        ) {
          discardedLabels.push("Skill configuration");
          nextStep.skillId = "";
          nextStep.skillArgs = "";
          nextStep.skillRequiredCapabilities = "";
        }

        if (
          step.stepType === "tool" &&
          (step.toolId.trim() ||
            step.toolVersion.trim() ||
            (step.toolInputs.trim() && step.toolInputs.trim() !== "{}"))
        ) {
          discardedLabels.push("Tool configuration");
          nextStep.toolId = "";
          nextStep.toolVersion = "";
          nextStep.toolInputs = "{}";
        }

        if (
          step.stepType === "preset" &&
          (step.presetKey ||
            Object.keys(step.presetInputValues).length > 0)
        ) {
          discardedLabels.push("Preset configuration");
          nextStep.presetKey = "";
          nextStep.presetInputValues = {};
          nextStep.presetDetail = null;
          nextStep.presetMessage = null;
        }

        if (discardedLabels.length > 0) {
          nextStep.stepTypeMessage = `${discardedLabels.join(
            ", ",
          )} discarded after changing Step Type. Shared instructions were preserved.`;
        }

        return nextStep;
      }),
    );
  }

  function updateStepPresetInputValue(
    localId: string,
    definition: TaskTemplateInputDefinition,
    value: string | boolean,
  ) {
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId) {
          return step;
        }
        return {
          ...step,
          presetInputValues: {
            ...step.presetInputValues,
            [definition.name]: value,
          },
        };
      }),
    );
  }

  function handlePresetManagementNameChange(value: string) {
    setPresetManagementName(value);
    const normalized = value.trim().toLowerCase();
    const matchedPreset =
      templateItems.find((item) => item.title.trim().toLowerCase() === normalized) ||
      templateItems.find((item) => item.slug.trim().toLowerCase() === normalized);
    setSelectedPresetKey(matchedPreset?.key || "");
    setTemplateMessage(null);
    setPresetReapplyNeeded(false);
  }

  function addStep() {
    setSteps((current) => [...current, createStepStateEntry(nextStepNumber)]);
    setNextStepNumber((current) => current + 1);
  }

  function moveStep(index: number, direction: -1 | 1) {
    setSteps((current) => {
      const nextIndex = index + direction;
      if (
        index < 0 ||
        index >= current.length ||
        nextIndex < 0 ||
        nextIndex >= current.length
      ) {
        return current;
      }
      const updated = current.slice();
      const currentStep = updated[index];
      const nextStep = updated[nextIndex];
      if (!currentStep || !nextStep) {
        return current;
      }
      updated[index] = nextStep;
      updated[nextIndex] = currentStep;
      return updated;
    });
  }

  function removeStep(index: number) {
    const removedStep = steps[index];
    if (removedStep) {
      setStepJiraProvenance((provenance) => {
        if (!provenance[removedStep.localId]) {
          return provenance;
        }
        const { [removedStep.localId]: _removed, ...rest } = provenance;
        return rest;
      });
      setSelectedStepAttachmentFiles((current) => {
        if (!current[removedStep.localId]) {
          return current;
        }
        const { [removedStep.localId]: _removed, ...rest } = current;
        return rest;
      });
    }
    setSteps((current) =>
      current.filter((_, currentIndex) => currentIndex !== index),
    );
  }

  function resolveTemplateInputs(
    inputs: TaskTemplateInputDefinition[],
    explicitInputValues: Record<string, unknown> = {},
    featureRequestOverride?: string,
  ): {
    values: Record<string, unknown>;
    assumptions: string[];
  } {
    const values: Record<string, unknown> = {};
    const assumptions: string[] = [];
    const primaryInstructions = String(
      featureRequestOverride ?? steps[0]?.instructions ?? "",
    ).trim();
    const explicitFeatureRequest = String(
      featureRequestOverride ?? templateFeatureRequest,
    ).trim();
    const repositoryValue = repository.trim();

    inputs.forEach((definition) => {
      const name = String(definition.name || "").trim();
      const label = String(definition.label || name).trim() || name;
      if (!name) {
        return;
      }
      const required = Boolean(definition.required);
      const inputType = String(definition.type || "").toLowerCase();
      const options = Array.isArray(definition.options)
        ? definition.options
            .map((option) => String(option).trim())
            .filter(Boolean)
        : [];
      const key = name.toLowerCase();
      const isFeatureRequestKey = isFeatureRequestInputKey(name);
      const isJiraProjectKey = isJiraProjectInputKey(name);
      const isRepositoryInput = isRepositoryInputKey(name);

      let value: unknown = null;
      let valueSource = "";
      const remembered = templateInputMemoryRef.current[name];
      const defaultValue = definition.default;
      const explicitInputValue = explicitInputValues[name];

      if (isFeatureRequestKey && explicitFeatureRequest) {
        value = explicitFeatureRequest;
        valueSource = "manual";
      } else if (
        explicitInputValue !== undefined &&
        explicitInputValue !== null &&
        String(explicitInputValue).trim() !== ""
      ) {
        value = explicitInputValue;
        valueSource = "manual";
      } else if (isRepositoryInput && repositoryValue) {
        value = repositoryValue;
        valueSource = "draft";
      } else if (
        !isJiraProjectKey &&
        !isRepositoryInput &&
        remembered !== undefined &&
        remembered !== null &&
        String(remembered).trim() !== ""
      ) {
        value = remembered;
        valueSource = "memory";
      } else if (
        defaultValue !== undefined &&
        defaultValue !== null &&
        String(defaultValue).trim() !== ""
      ) {
        value = defaultValue;
        valueSource = "default";
      } else if (key.includes("instruction") || isFeatureRequestKey) {
        value = explicitFeatureRequest || primaryInstructions;
        valueSource = "draft";
      } else if (key.includes("repo")) {
        value = repositoryValue;
        valueSource = "draft";
      } else if (inputType === "enum") {
        value = options[0] || "";
        valueSource = "assumed";
      } else if (inputType === "boolean") {
        value = false;
        valueSource = "assumed";
      }

      const hasValue =
        value !== null && value !== undefined && String(value).trim() !== "";
      if (!hasValue && required && (isJiraProjectKey || isRepositoryInput)) {
        return;
      }
      if (!hasValue && required) {
        if (inputType === "enum" && options.length > 0) {
          value = options[0];
        } else if (inputType === "boolean") {
          value = false;
        } else if (explicitFeatureRequest || primaryInstructions) {
          value = explicitFeatureRequest || primaryInstructions;
        } else {
          value = `auto-${key.replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-+|-+$/g, "") || "value"}`;
        }
        valueSource = "assumed";
      }

      if (
        value === null ||
        value === undefined ||
        String(value).trim() === ""
      ) {
        return;
      }

      let normalized: unknown;
      if (inputType === "boolean") {
        if (typeof value === "boolean") {
          normalized = value;
        } else {
          const lowered = String(value).trim().toLowerCase();
          normalized = ["1", "true", "yes", "on"].includes(lowered);
        }
      } else {
        normalized = String(value).trim();
      }
      if (inputType === "enum") {
        const candidate = String(normalized).trim();
        normalized =
          options.length > 0 && !options.includes(candidate)
            ? options[0]
            : candidate;
      }

      values[name] = normalized;
      if (!isRepositoryInput) {
        templateInputMemoryRef.current[name] = normalized;
      }
      if (valueSource === "assumed" || valueSource === "draft") {
        assumptions.push(label);
      }
    });

    return { values, assumptions };
  }

  function stepTemplateInputDisplayValue(
    step: StepState,
    definition: TaskTemplateInputDefinition,
  ): string {
    const explicit = step.presetInputValues[definition.name];
    if (explicit !== undefined) {
      return String(explicit);
    }
    if (definition.type === "jira_board") {
      return String(definition.default || jiraIntegration?.defaultBoardId || "").trim();
    }
    if (isJiraProjectInputKey(definition.name)) {
      return String(
        definition.default || jiraIntegration?.defaultProjectKey || "",
      ).trim();
    }
    if (isRepositoryInputKey(definition.name)) {
      return String(definition.default || repository || defaultRepository || "").trim();
    }
    return String(definition.default ?? "").trim();
  }

  async function loadPresetDetail(
    preset: TemplateOption,
  ): Promise<TaskTemplateDetail> {
    const response = await fetch(
      withQueryParams(
        interpolatePath(taskTemplateDetailEndpoint, {
          slug: preset.slug,
        }),
        {
          scope: preset.scope,
          scopeRef: preset.scopeRef || undefined,
        },
      ),
      { headers: { Accept: "application/json" } },
    );
    if (!response.ok) {
      throw new Error(
        await responseErrorMessage(response, "Failed to load preset details."),
      );
    }
    return (await response.json()) as TaskTemplateDetail;
  }

  async function expandPresetForDraft({
    preset,
    detail,
    inputValues,
    submitIntent,
  }: {
    preset: TemplateOption;
    detail: TaskTemplateDetail;
    inputValues: Record<string, unknown>;
    submitIntent?: string;
  }): Promise<PresetExpansionState> {
    const scopeParams = {
      scope: preset.scope,
      scopeRef: preset.scopeRef || undefined,
    };
    const { values: inputs, assumptions } = resolveTemplateInputs(
      detail.inputs || [],
      inputValues,
    );
    const version =
      detail.version || detail.latestVersion || preset.latestVersion || "1.0.0";
    const presetRuntime = runtime.trim().toLowerCase();
    const expandResponse = await fetch(
      withQueryParams(
        interpolatePath(taskTemplateExpandEndpoint, {
          slug: preset.slug,
        }),
        scopeParams,
      ),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          version,
          inputs,
          context: {
            repository: repository.trim() || defaultRepository,
            repo: repository.trim() || defaultRepository,
            branch: branch.trim() || undefined,
            publishMode: normalizePublishModeForSubmit(publishMode),
            targetRuntime: presetRuntime,
            ...(submitIntent ? { submitIntent } : {}),
          },
          options: {
            enforceStepLimit: true,
            ...(submitIntent ? { intent: "submit-auto-expand" } : {}),
          },
        }),
      },
    );
    if (!expandResponse.ok) {
      throw new Error(
        await responseErrorMessage(expandResponse, "Failed to expand preset."),
      );
    }
    const expanded = (await expandResponse.json()) as TaskTemplateExpandResponse;
    const expandedSteps = expanded.steps || [];
    return {
      presetKey: preset.key,
      presetTitle: preset.title,
      version,
      expandedSteps,
      inputs,
      assumptions,
      capabilities: Array.isArray(expanded.capabilities)
        ? expanded.capabilities
        : [],
      warnings: Array.isArray(expanded.warnings)
        ? expanded.warnings
            .map((warning) => String(warning).trim())
            .filter(Boolean)
        : [],
      appliedTemplate: expanded.appliedTemplate,
    };
  }

  function appliedPresetStateFromExpansion(
    preset: TemplateOption,
    detail: TaskTemplateDetail,
    expansion: PresetExpansionState,
    expandedSteps: StepState[],
  ): AppliedTemplateState | null {
    if (expandedSteps.length === 0) {
      return null;
    }
    const appliedTemplate = expansion.appliedTemplate || {};
    return {
      slug: String(appliedTemplate.slug || preset.slug),
      version: String(
        appliedTemplate.version ||
          detail.version ||
          preset.latestVersion ||
          "1.0.0",
      ),
      inputs:
        appliedTemplate.inputs && typeof appliedTemplate.inputs === "object"
          ? appliedTemplate.inputs
          : expansion.inputs,
      stepIds: Array.isArray(appliedTemplate.stepIds)
        ? appliedTemplate.stepIds
        : expandedSteps.map((step) => step.id).filter(Boolean),
      appliedAt:
        String(appliedTemplate.appliedAt || "").trim() ||
        new Date().toISOString(),
      capabilities: expansion.capabilities,
    };
  }

  function recordAppliedPreset(
    preset: TemplateOption,
    detail: TaskTemplateDetail,
    expansion: PresetExpansionState,
    expandedSteps: StepState[],
  ) {
    const appliedTemplate = appliedPresetStateFromExpansion(
      preset,
      detail,
      expansion,
      expandedSteps,
    );
    if (!appliedTemplate) {
      return;
    }
    setAppliedTemplates((current) => [...current, appliedTemplate]);
  }

  function applyPresetExpansionToDraft({
    preset,
    detail,
    expansion,
    setMessage,
    replaceLocalId,
  }: {
    preset: TemplateOption;
    detail: TaskTemplateDetail;
    expansion: PresetExpansionState;
    setMessage: (message: string) => void;
    replaceLocalId?: string;
  }) {
    const expandedSteps = expansion.expandedSteps.map((step, index) =>
      mapExpandedStepToState(nextStepNumber + index, step),
    );
    if (hasAdvancedStepOptionValues(expandedSteps)) {
      setShowAdvancedStepOptions(true);
    }
    const replaceEmptyDefault =
      steps.length === 1 && isEmptyStepStateEntry(steps[0]);

    setSteps((current) => {
      if (replaceLocalId) {
        const targetIndex = current.findIndex(
          (step) => step.localId === replaceLocalId,
        );
        if (targetIndex >= 0) {
          const next = current.slice();
          next.splice(targetIndex, 1, ...expandedSteps);
          return next.length > 0 ? next : [createStepStateEntry(nextStepNumber)];
        }
      }
      if (replaceEmptyDefault) {
        return expandedSteps.length > 0
          ? expandedSteps
          : [createStepStateEntry(nextStepNumber)];
      }
      return [...current, ...expandedSteps];
    });
    setNextStepNumber((current) => current + Math.max(expandedSteps.length, 1));
    setAppliedTemplateFeatureRequest(templateFeatureRequest.trim());
    setAppliedTemplateObjectiveAttachmentSignature(
      attachmentSignature(selectedObjectiveAttachmentFiles),
    );
    recordAppliedPreset(preset, detail, expansion, expandedSteps);
    const autoFillSuffix =
      expansion.assumptions.length > 0
        ? ` Auto-filled ${expansion.assumptions.length} input(s): ${expansion.assumptions.join(", ")}.`
        : "";
    const warningSuffix =
      expansion.warnings.length > 0
        ? ` ${expansion.warnings.join(" ")}`
        : "";
    const publishConstraintSuffix = isSelfManagedPublishSkill(
      String(expansion.appliedTemplate?.slug || preset.slug),
    )
      ? ` ${preset.title} manages its own PR/publish flow, so Publish Mode is forced to None and merge automation is unavailable.`
      : "";
    setMessage(
      `Applied preset '${preset.title}' (${expandedSteps.length} steps).${autoFillSuffix}${warningSuffix}${publishConstraintSuffix}`,
    );
  }

  async function handleExpandStepPreset(localId: string) {
    if (isApplyingPreset) return;
    const step = steps.find((candidate) => candidate.localId === localId);
    const preset = templateItems.find((item) => item.key === step?.presetKey);
    if (!step || !preset) {
      updateStep(localId, { presetMessage: "Choose a preset first." });
      return;
    }
    const requestedInstructions = step.instructions;
    const requestedInputValues = { ...step.presetInputValues };
    setIsApplyingPreset(true);
    updateStep(localId, {
      presetMessage: "Expanding preset...",
    });
    try {
      const detail =
        step.presetDetail && step.presetKey === preset.key
          ? step.presetDetail
          : await loadPresetDetail(preset);
      const expansion = await expandPresetForDraft({
        preset,
        detail,
        inputValues: resolveTemplateInputs(
          detail.inputs || [],
          step.presetInputValues,
          step.instructions,
        ).values,
      });
      if (
        !currentStepPresetMatches(
          localId,
          preset.key,
          requestedInstructions,
          requestedInputValues,
        )
      ) {
        return;
      }
      applyPresetExpansionToDraft({
        preset,
        detail,
        expansion,
        replaceLocalId: localId,
        setMessage: (message) => setTemplateMessage(message),
      });
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to expand preset.");
      if (
        currentStepPresetMatches(
          localId,
          preset.key,
          requestedInstructions,
          requestedInputValues,
        )
      ) {
        updateStepPresetIfCurrent(localId, preset.key, {
          presetMessage: `Failed to expand preset: ${failure.message}`,
        });
      }
    } finally {
      setIsApplyingPreset(false);
    }
  }

  async function handleSaveCurrentStepsAsPreset() {
    if (!taskTemplateSaveEnabled) {
      return;
    }
    const title = presetManagementName.trim();
    if (!title) {
      setTemplateMessage("Enter a preset name before saving.");
      return;
    }

    const presetSteps: Record<string, unknown>[] = [];
    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index];
      if (!step) {
        continue;
      }
      const instructions = step.instructions.trim();
      if (!instructions) {
        continue;
      }
      const blueprint: Record<string, unknown> = { instructions };
      const skillId = step.skillId.trim();
      const caps = showAdvancedStepOptions
        ? parseCapabilitiesCsv(step.skillRequiredCapabilities)
        : [];
      const skillArgsRaw = showAdvancedStepOptions ? step.skillArgs.trim() : "";
      if (skillId || skillArgsRaw || caps.length > 0) {
        let skillArgs: Record<string, unknown> = {};
        if (skillArgsRaw) {
          try {
            const parsed = JSON.parse(skillArgsRaw) as unknown;
            if (
              !parsed ||
              typeof parsed !== "object" ||
              Array.isArray(parsed)
            ) {
              throw new Error("Skill args must be an object.");
            }
            skillArgs = parsed as Record<string, unknown>;
          } catch {
            setTemplateMessage(
              `Step ${index + 1} Skill Args must be valid JSON object text.`,
            );
            return;
          }
        }
        const normalizedTool = {
          type: "skill",
          name: skillId || "auto",
          version: "1.0",
          inputs: skillArgs,
          ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
        };
        blueprint.tool = normalizedTool;
        blueprint.skill = {
          id: normalizedTool.name,
          args: skillArgs,
          ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
        };
      }
      presetSteps.push(blueprint);
    }

    if (presetSteps.length === 0) {
      setTemplateMessage(
        "Add at least one step with instructions before saving.",
      );
      return;
    }

    setTemplateMessage("Saving preset...");
    try {
      const response = await fetch(taskTemplateSaveEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          scope: "personal",
          title,
          description: title,
          steps: presetSteps,
          suggestedInputs: [],
          tags: [],
        }),
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to save preset."),
        );
      }
      const created = (await response.json()) as { title?: string };
      setTemplateMessage(`Saved preset '${created.title || title}'.`);
      await templateOptionsQuery.refetch();
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to save preset.");
      setTemplateMessage(`Failed to save preset: ${failure.message}`);
    }
  }

  async function handleDeleteSelectedPreset() {
    if (!selectedPreset || !selectedPresetDeleteEnabled || isDeletingPreset) {
      return;
    }
    const confirmed = window.confirm(
      `Delete preset '${selectedPreset.title}'? This cannot be undone.`,
    );
    if (!confirmed) {
      setTemplateMessage("Preset delete cancelled.");
      return;
    }

    setIsDeletingPreset(true);
    setTemplateMessage("Deleting preset...");
    try {
      const response = await fetch(
        withQueryParams(
          interpolatePath(taskTemplateDetailEndpoint, {
            slug: selectedPreset.slug,
          }),
          {
            scope: selectedPreset.scope,
            scopeRef: selectedPreset.scopeRef || undefined,
          },
        ),
        {
          method: "DELETE",
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to delete preset."),
        );
      }
      setSelectedPresetKey("");
      setPresetManagementName("");
      setPresetReapplyNeeded(false);
      setTemplateMessage(`Deleted preset '${selectedPreset.title}'.`);
      await templateOptionsQuery.refetch();
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to delete preset.");
      setTemplateMessage(`Failed to delete preset: ${failure.message}`);
    } finally {
      setIsDeletingPreset(false);
    }
  }

  async function handleTemporalTaskEditingSubmit({
    workflowId,
    updateName,
    inputArtifactRef,
    parametersPatch,
  }: {
    workflowId: string;
    updateName: "UpdateInputs" | "RequestRerun";
    inputArtifactRef: string | null;
    parametersPatch: Record<string, unknown>;
  }): Promise<void> {
    const updatePayload = buildTemporalArtifactEditUpdatePayload({
      updateName,
      inputArtifactRef,
      parametersPatch,
    });
    const isRerun = updateName === "RequestRerun";
    const attemptEvent = isRerun ? "rerun_submit_attempt" : "update_submit_attempt";
    const resultEvent = isRerun ? "rerun_submit_result" : "update_submit_result";
    recordTemporalTaskEditingClientEvent({
      event: attemptEvent,
      mode: isRerun ? "rerun" : "edit",
      workflowId,
      updateName,
    });
    try {
      const response = await fetch(
        configuredTemporalUpdateUrl(temporalUpdateEndpoint, workflowId),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(updatePayload),
        },
      );
      if (!response.ok) {
        const detail = await responseErrorDetail(
          response,
          isRerun ? "Failed to request task rerun." : "Failed to save task changes.",
        );
        throw new Error(detail.message);
      }
      const result = (await response.json()) as {
        accepted?: boolean;
        applied?: string | null;
        message?: string | null;
        execution?: { workflowId?: string | null } | null;
      };
      if (result.accepted === false) {
        throw new Error(
          String(result.message || "").trim() ||
            (isRerun
              ? "The workflow no longer accepts rerun requests."
              : "The workflow no longer accepts input updates."),
        );
      }
      const applied = String(result.applied || "").trim();
      recordTemporalTaskEditingClientEvent({
        event: resultEvent,
        mode: isRerun ? "rerun" : "edit",
        workflowId,
        updateName,
        result: "success",
        applied,
      });
      const statusText = isRerun
        ? applied === "continue_as_new"
          ? "Rerun was requested and the latest execution view is ready."
          : "Rerun request was accepted."
        : applied === "safe_point" || applied === "next_safe_point"
          ? "Changes were scheduled for the next safe point."
          : applied === "continue_as_new"
            ? "Changes were accepted and will continue in a refreshed run."
            : "Changes were saved to this execution.";
      try {
        window.sessionStorage.setItem(
          "moonmind.temporalTaskEditing.notice",
          statusText,
        );
      } catch {
        // Navigation should not depend on session storage availability.
      }
      const redirectWorkflowId =
        String(result.execution?.workflowId || "").trim() || workflowId;
      navigateTo(
        `/tasks/${encodeURIComponent(redirectWorkflowId)}?source=temporal`,
      );
    } catch (error) {
      recordTemporalTaskEditingClientEvent({
        event: resultEvent,
        mode: isRerun ? "rerun" : "edit",
        workflowId,
        updateName,
        result: "failure",
        reason: error instanceof Error ? error.message : "unknown",
      });
      throw error;
    }
  }

  function updatePresetSubmitExpansion(
    localId: string,
    updates: Pick<StepState, "presetMessage" | "submitExpansion">,
  ) {
    setSteps((current) =>
      current.map((step) =>
        step.localId === localId ? { ...step, ...updates } : step,
      ),
    );
  }

  async function expandUnresolvedPresetsForSubmit({
    requestId,
    submitIntent,
  }: {
    requestId: string;
    submitIntent: string;
  }): Promise<{
    steps: StepState[];
    appliedTemplates: AppliedTemplateState[];
    warnings: string[];
  }> {
    let nextSubmissionSteps = steps.map((step) => ({ ...step }));
    const nextAppliedTemplates = [...appliedTemplates];
    const warnings: string[] = [];
    let generatedStepIndex = nextStepNumber;

    for (let index = 0; index < nextSubmissionSteps.length; index += 1) {
      const step = nextSubmissionSteps[index];
      if (!step || step.stepType !== "preset") {
        continue;
      }
      const preset = templateItems.find((item) => item.key === step.presetKey);
      if (!preset) {
        const message = "Choose a preset first.";
        updatePresetSubmitExpansion(step.localId, {
          presetMessage: message,
          submitExpansion: {
            status: "failed",
            requestId,
            errorMessage: message,
          },
        });
        throw new Error(message);
      }

      updatePresetSubmitExpansion(step.localId, {
        presetMessage: "Expanding preset...",
        submitExpansion: {
          status: "expanding",
          requestId,
          message: "Expanding preset...",
        },
      });
      setSubmitMessage("Expanding preset...");

      try {
        const detail =
          step.presetDetail && step.presetKey === preset.key
            ? step.presetDetail
            : await loadPresetDetail(preset);
        const hasPresetStepAttachments =
          step.inputAttachments.length > 0 ||
          step.templateAttachments.length > 0 ||
          (selectedStepAttachmentFiles[step.localId] || []).length > 0;
        if (hasPresetStepAttachments) {
          throw new Error(
            "Preset attachment retargeting requires manual review before submission.",
          );
        }
        if (
          submitExpansionRequestIdRef.current !== Number(requestId.split("-").at(-1))
        ) {
          throw new Error("Preset expansion was superseded by another submit attempt.");
        }
        const expansion = await expandPresetForDraft({
          preset,
          detail,
          inputValues: resolveTemplateInputs(
            detail.inputs || [],
            step.presetInputValues,
            step.instructions,
          ).values,
          submitIntent,
        });
        const blockingWarning = expansion.warnings.find((warning) =>
          /requires? (manual )?review|requires? acknowledgement|must review/i.test(
            warning,
          ),
        );
        if (blockingWarning) {
          throw new Error(blockingWarning);
        }
        if (
          submitExpansionRequestIdRef.current !== Number(requestId.split("-").at(-1))
        ) {
          throw new Error("Preset expansion was superseded by another submit attempt.");
        }
        const expandedSteps = expansion.expandedSteps.map((expandedStep) => {
          const mapped = mapExpandedStepToState(
            generatedStepIndex,
            expandedStep,
          );
          generatedStepIndex += 1;
          return mapped;
        });
        const appliedTemplate = appliedPresetStateFromExpansion(
          preset,
          detail,
          expansion,
          expandedSteps,
        );
        if (appliedTemplate) {
          nextAppliedTemplates.push(appliedTemplate);
        }
        warnings.push(...expansion.warnings);
        nextSubmissionSteps = [
          ...nextSubmissionSteps.slice(0, index),
          ...expandedSteps,
          ...nextSubmissionSteps.slice(index + 1),
        ];
        index += expandedSteps.length - 1;
        updatePresetSubmitExpansion(step.localId, {
          presetMessage:
            expansion.warnings.length > 0
              ? expansion.warnings.join(" ")
              : "Preset will expand during submission.",
          submitExpansion: {
            status: "expanded",
            requestId,
            message: "Preset expanded for submission.",
          },
        });
      } catch (error) {
        const failure =
          error instanceof Error ? error : new Error("Failed to expand preset.");
        const message = `Failed to expand preset: ${failure.message}`;
        updatePresetSubmitExpansion(step.localId, {
          presetMessage: message,
          submitExpansion: {
            status: "failed",
            requestId,
            errorMessage: message,
          },
        });
        throw new Error(message, { cause: error });
      }
    }

    return {
      steps: nextSubmissionSteps,
      appliedTemplates: nextAppliedTemplates,
      warnings,
    };
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting || submitExpansionInFlightRef.current) {
      return;
    }
    const requestSerial = submitExpansionRequestIdRef.current + 1;
    submitExpansionRequestIdRef.current = requestSerial;
    const requestId = `submit-${requestSerial}`;
    submitExpansionInFlightRef.current = true;
    setIsSubmitting(true);
    setSubmitMessage(null);

    let submissionSteps = steps;
    let submissionAppliedTemplates = appliedTemplates;
    const clearSubmitBusy = () => {
      submitExpansionInFlightRef.current = false;
      setIsSubmitting(false);
      releaseSubmitArrowExit();
    };
    const normalizedRepository = repository.trim() || defaultRepository;
    if (!normalizedRepository) {
      setSubmitMessage(
        "Repository is required because no system default repository is configured.",
      );
      clearSubmitBusy();
      return;
    }
    if (!isValidRepositoryInput(normalizedRepository)) {
      setSubmitMessage(
        "Repository must be owner/repo, https://<host>/<path>, or git@<host>:<path> (token-free).",
      );
      clearSubmitBusy();
      return;
    }
    if (selectedAttachmentFiles.length > 0 || persistedAttachmentRefs.length > 0) {
      if (!attachmentPolicy.enabled) {
        setSubmitMessage("Attachments are disabled for this runtime.");
        clearSubmitBusy();
        return;
      }
      if (!attachmentTargetValidation.ok) {
        setAttachmentTargetErrors(attachmentTargetValidation.errors);
        setSubmitMessage(attachmentTargetValidation.messages.join(" "));
        clearSubmitBusy();
        return;
      }
    }
    setAttachmentTargetErrors({});

    const normalizedRuntime = runtime.trim().toLowerCase();
    if (!supportedTaskRuntimes.includes(normalizedRuntime)) {
      setSubmitMessage(
        `Runtime must be one of: ${supportedTaskRuntimes.join(", ")}.`,
      );
      clearSubmitBusy();
      return;
    }

    const normalizedPublishMode = normalizePublishModeForSubmit(publishMode);
    if (!["none", "branch", "pr"].includes(normalizedPublishMode)) {
      setSubmitMessage("Publish mode must be one of: none, branch, pr.");
      clearSubmitBusy();
      return;
    }

    if (!Number.isInteger(priority)) {
      setSubmitMessage("Priority must be an integer.");
      clearSubmitBusy();
      return;
    }
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
      setSubmitMessage("Max Attempts must be an integer >= 1.");
      clearSubmitBusy();
      return;
    }

    const unresolvedPresetSteps = steps.filter(
      (step) => step.stepType === "preset",
    );
    if (unresolvedPresetSteps.length > 0) {
      try {
        const expanded = await expandUnresolvedPresetsForSubmit({
          requestId,
          submitIntent: pageMode.mode,
        });
        submissionSteps = expanded.steps;
        submissionAppliedTemplates = expanded.appliedTemplates;
        if (expanded.warnings.length > 0) {
          setSubmitMessage(expanded.warnings.join(" "));
        } else {
          setSubmitMessage("Creating task...");
        }
      } catch (error) {
        const failure =
          error instanceof Error ? error : new Error("Failed to expand preset.");
        setSubmitMessage(failure.message);
        clearSubmitBusy();
        return;
      }
    }

    const primaryStep = submissionSteps[0] || null;
    const primaryValidation = validatePrimaryStepSubmission(primaryStep);
    if (!primaryValidation.ok) {
      setSubmitMessage(primaryValidation.error);
      clearSubmitBusy();
      return;
    }

    const primaryInstructions = primaryValidation.value.instructions;
    const objectiveInstructions = resolveObjectiveInstructions(
      templateFeatureRequest,
      primaryInstructions,
      submissionAppliedTemplates,
    );

    const primaryStepIsSkill = primaryStep?.stepType === "skill";
    const primarySkillId = primaryStepIsSkill
      ? primaryValidation.value.skillId.trim() || "auto"
      : "auto";
    const activeSubmissionAppliedTemplates = activeAppliedTemplatesForSteps(
      submissionAppliedTemplates,
      submissionSteps,
    );
    submissionAppliedTemplates = activeSubmissionAppliedTemplates;
    const effectiveSubmissionSkillId = resolveEffectiveSkillId(
      primarySkillId,
      activeSubmissionAppliedTemplates,
    );
    const effectivePublishMode =
      isSelfManagedPublishSkill(effectiveSubmissionSkillId)
        ? "none"
        : normalizedPublishMode;
    const primarySkillArgsRaw = primaryStepIsSkill && showAdvancedStepOptions
      ? String(primaryStep?.skillArgs || "").trim()
      : "";
    const taskSkillRequiredCapabilities = primaryStepIsSkill && showAdvancedStepOptions
      ? parseCapabilitiesCsv(String(primaryStep?.skillRequiredCapabilities || ""))
      : [];

    let primarySkillArgs: Record<string, unknown> = {};
    if (primarySkillArgsRaw) {
      try {
        const parsed = JSON.parse(primarySkillArgsRaw) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("Skill args must be an object.");
        }
        primarySkillArgs = parsed as Record<string, unknown>;
      } catch {
        setSubmitMessage(
          'Primary step Skill Args must be valid JSON object text (for example: {"featureKey":"..."}).',
        );
        clearSubmitBusy();
        return;
      }
    }
    let primaryToolInputs: Record<string, unknown> = {};
    if (primaryStep?.stepType === "tool" && !executableGeneratedToolPayload(primaryStep)) {
      if (!primaryStep.toolId.trim()) {
        setSubmitMessage("Select a Tool before submitting a Tool step.");
        clearSubmitBusy();
        return;
      }
      const parsedToolInputs = parseToolInputsText(primaryStep.toolInputs);
      if (!parsedToolInputs.ok) {
        setSubmitMessage("Step 1 Tool Inputs must be valid JSON object text.");
        clearSubmitBusy();
        return;
      }
      primaryToolInputs = parsedToolInputs.value;
    }

    const primaryStepTool = {
      type: "skill",
      name: primarySkillId,
      version: "1.0",
      ...(Object.keys(primarySkillArgs).length > 0
        ? { inputs: primarySkillArgs }
        : {}),
      ...(taskSkillRequiredCapabilities.length > 0
        ? { requiredCapabilities: taskSkillRequiredCapabilities }
        : {}),
    };
    const primaryStepSkill = {
      id: primarySkillId,
      args: primarySkillArgs,
      ...(taskSkillRequiredCapabilities.length > 0
        ? { requiredCapabilities: taskSkillRequiredCapabilities }
        : {}),
    };
    const primaryStepHasSkillOverride =
      hasExplicitSkillSelection(primarySkillId) ||
      Object.keys(primarySkillArgs).length > 0 ||
      taskSkillRequiredCapabilities.length > 0;

    const parsedAdditionalStepInputs: Array<{
      sourceIndex: number;
      step: StepState;
      skillId: string;
      skillArgsRaw: string;
      skillArgs: Record<string, unknown>;
      skillCaps: string[];
      toolInputs: Record<string, unknown>;
      hasStepContent: boolean;
    }> = [];
    for (let index = 1; index < submissionSteps.length; index += 1) {
      const step = submissionSteps[index];
      if (!step) {
        continue;
      }
      if (step.stepType === "preset") {
        setSubmitMessage("Expand Preset steps before submitting.");
        clearSubmitBusy();
        return;
      }
      const stepIsSkill = step.stepType === "skill";
      const stepIsTool = step.stepType === "tool";
      const generatedToolPayload = executableGeneratedToolPayload(step);
      const generatedSkillPayload = executableGeneratedSkillPayload(step);
      const stepSkillId = stepIsSkill ? step.skillId.trim() : "";
      const stepSkillArgsRaw = stepIsSkill && showAdvancedStepOptions
        ? step.skillArgs.trim()
        : "";
      const stepSkillCaps = stepIsSkill && showAdvancedStepOptions
        ? parseCapabilitiesCsv(step.skillRequiredCapabilities)
        : [];
      const hasAuthoredToolInputs =
        stepIsTool &&
        Boolean(step.toolInputs.trim()) &&
        step.toolInputs.trim() !== "{}";
      const stepAttachmentFiles = selectedStepAttachmentFiles[step.localId] || [];
      const hasStepContent =
        Boolean(step.instructions) ||
        stepAttachmentFiles.length > 0 ||
        step.inputAttachments.length > 0 ||
        (stepIsTool && Boolean(step.toolId.trim())) ||
        (stepIsTool && Boolean(step.toolVersion.trim())) ||
        hasAuthoredToolInputs ||
        Boolean(stepSkillId) ||
        Boolean(stepSkillArgsRaw) ||
        stepSkillCaps.length > 0 ||
        Boolean(generatedToolPayload) ||
        Boolean(generatedSkillPayload);
      let stepSkillArgs: Record<string, unknown> = {};
      let stepToolInputs: Record<string, unknown> = {};
      if (stepIsTool && !generatedToolPayload) {
        if (hasStepContent && !step.toolId.trim()) {
          setSubmitMessage(`Select a Tool before submitting Step ${index + 1}.`);
          clearSubmitBusy();
          return;
        }
        const parsedToolInputs = parseToolInputsText(step.toolInputs);
        if (!parsedToolInputs.ok) {
          setSubmitMessage(
            `Step ${index + 1} Tool Inputs must be valid JSON object text.`,
          );
          clearSubmitBusy();
          return;
        }
        stepToolInputs = parsedToolInputs.value;
      }
      if (stepSkillArgsRaw) {
        try {
          const parsed = JSON.parse(stepSkillArgsRaw) as unknown;
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Step skill args must be an object.");
          }
          stepSkillArgs = parsed as Record<string, unknown>;
        } catch {
          setSubmitMessage(
            `Step ${index + 1} Skill Args must be valid JSON object text.`,
          );
          clearSubmitBusy();
          return;
        }
      }
      parsedAdditionalStepInputs.push({
        sourceIndex: index,
        step,
        skillId: stepSkillId,
        skillArgsRaw: stepSkillArgsRaw,
        skillArgs: stepSkillArgs,
        skillCaps: stepSkillCaps,
        toolInputs: stepToolInputs,
        hasStepContent,
      });
    }

    const additionalStepValidation = validatePrimaryStepSubmission(
      primaryStep,
      {
        additionalStepsCount: parsedAdditionalStepInputs.filter(
          (entry) => entry.hasStepContent,
        ).length,
      },
    );
    if (!additionalStepValidation.ok) {
      setSubmitMessage(additionalStepValidation.error);
      clearSubmitBusy();
      return;
    }

    let schedulePayload: Record<string, unknown> | null = null;
    if (scheduleMode === "once") {
      if (!scheduledFor.trim()) {
        setSubmitMessage("Scheduled time is required for deferred scheduling.");
        clearSubmitBusy();
        return;
      }
      const scheduleDate = new Date(scheduledFor);
      if (Number.isNaN(scheduleDate.getTime()) || scheduleDate <= new Date()) {
        setSubmitMessage("Scheduled time must be a valid future date.");
        clearSubmitBusy();
        return;
      }
      schedulePayload = {
        mode: "once",
        scheduledFor: scheduleDate.toISOString(),
      };
    } else if (scheduleMode === "deferred_minutes") {
      const deferredMinutes = Number(scheduleDeferredMinutes.trim());
      if (
        !Number.isFinite(deferredMinutes) ||
        !Number.isInteger(deferredMinutes) ||
        deferredMinutes <= 0
      ) {
        setSubmitMessage(
          "A valid positive whole number of minutes is required for deferred scheduling.",
        );
        clearSubmitBusy();
        return;
      }
      if (deferredMinutes > 525600) {
        setSubmitMessage("Deferred minutes cannot exceed 525 600 (one year).");
        clearSubmitBusy();
        return;
      }
      schedulePayload = {
        mode: "once",
        scheduledFor: new Date(
          Date.now() + deferredMinutes * 60_000,
        ).toISOString(),
      };
    } else if (scheduleMode === "recurring") {
      if (!scheduleCron.trim()) {
        setSubmitMessage(
          "Cron expression is required for recurring scheduling.",
        );
        clearSubmitBusy();
        return;
      }
      schedulePayload = {
        mode: "recurring",
        cron: scheduleCron.trim(),
        timezone: scheduleTimezone.trim() || "UTC",
        name: scheduleName.trim() || "Inline schedule",
      };
    }

    const uploadedObjectiveAttachments: StepAttachmentRef[] = [];
    const uploadedStepAttachments: Record<string, StepAttachmentRef[]> = {};
    let didNavigateAfterCreate = false;
    holdSubmitArrowExitUntilNavigation();
    try {
      if (selectedAttachmentFiles.length > 0) {
        type AttachmentUploadResult =
          | {
              ok: true;
              targetKey: string;
              ref: StepAttachmentRef;
            }
          | {
              ok: false;
              targetKey: string;
              message: string;
            };
        const objectiveTargetKey = attachmentTargetKey("objective");
        const uploadPromises: Promise<AttachmentUploadResult>[] =
          selectedObjectiveAttachmentFiles.map(async (file) => {
            try {
              const ref = await createInputAttachmentArtifact(
                artifactCreateEndpoint,
                file,
                normalizedRepository,
                { kind: "objective" },
              );
              return {
                ok: true,
                targetKey: objectiveTargetKey,
                ref,
              } satisfies AttachmentUploadResult;
            } catch (error) {
              const failure =
                error instanceof Error
                  ? error
                  : new Error("Failed to upload objective attachment.");
              const message = `Instructions: ${failure.message}`;
              setAttachmentTargetErrors((current) => ({
                ...current,
                [objectiveTargetKey]: message,
              }));
              return {
                ok: false,
                targetKey: objectiveTargetKey,
                message,
              } satisfies AttachmentUploadResult;
            }
          });

        for (const [index, step] of submissionSteps.entries()) {
          const files = selectedStepAttachmentFiles[step.localId] || [];
          const targetKey = attachmentTargetKey(step.localId);
          uploadPromises.push(
            ...files.map(async (file) => {
              try {
                const ref = await createInputAttachmentArtifact(
                  artifactCreateEndpoint,
                  file,
                  normalizedRepository,
                  { kind: "step", stepLabel: `Step ${index + 1}` },
                );
                return {
                  ok: true,
                  targetKey,
                  ref,
                } satisfies AttachmentUploadResult;
              } catch (error) {
                const failure =
                  error instanceof Error
                    ? error
                    : new Error("Failed to upload step attachment.");
                const message = `Step ${index + 1}: ${failure.message}`;
                setAttachmentTargetErrors((current) => ({
                  ...current,
                  [targetKey]: message,
                }));
                return {
                  ok: false,
                  targetKey,
                  message,
                } satisfies AttachmentUploadResult;
              }
            }),
          );
        }

        const uploadResults = await Promise.all(uploadPromises);
        const uploadFailure = uploadResults.find((result) => !result.ok);
        if (uploadFailure && !uploadFailure.ok) {
          setSubmitMessage(uploadFailure.message);
          clearSubmitBusy();
          return;
        }

        for (const result of uploadResults) {
          if (!result.ok) {
            continue;
          }
          if (result.targetKey === objectiveTargetKey) {
            uploadedObjectiveAttachments.push(result.ref);
            continue;
          }
          const stepAttachmentRefs =
            uploadedStepAttachments[result.targetKey] || [];
          stepAttachmentRefs.push(result.ref);
          uploadedStepAttachments[result.targetKey] = stepAttachmentRefs;
        }

        for (const step of submissionSteps) {
          const targetKey = attachmentTargetKey(step.localId);
          if (uploadedStepAttachments[targetKey]) {
            uploadedStepAttachments[step.localId] =
              uploadedStepAttachments[targetKey];
            delete uploadedStepAttachments[targetKey];
          }
        }
      }
    } catch (error) {
      const failure =
        error instanceof Error
          ? error
          : new Error("Failed to upload attachments.");
      setSubmitMessage(failure.message);
      clearSubmitBusy();
      return;
    }

    const uploadedPrimaryAttachmentRefs = primaryStep
      ? uploadedStepAttachments[primaryStep.localId] || []
      : [];
    const persistedPrimaryStepAttachmentRefs = primaryStep
      ? primaryStep.inputAttachments || []
      : [];
    const primaryStepAttachmentRefs = [
      ...persistedPrimaryStepAttachmentRefs,
      ...uploadedPrimaryAttachmentRefs,
    ];
    const taskLevelAttachmentRefs = [
      ...persistedObjectiveAttachments,
      ...uploadedObjectiveAttachments,
    ];
    const objectiveInstructionsForSubmit = objectiveInstructions.trim();
    const primaryInstructionsForSubmit = primaryInstructions.trim();

    const additionalSteps: Array<{
      sourceIndex: number;
      payload: Record<string, unknown>;
    }> = [];
    const stepSkillRequiredCapabilities: string[] = [];
    for (const {
      sourceIndex,
      step,
      skillId: stepSkillId,
      skillArgsRaw: stepSkillArgsRaw,
      skillArgs: stepSkillArgs,
      skillCaps: stepSkillCaps,
      toolInputs: stepToolInputs,
      hasStepContent: hasPreUploadStepContent,
    } of parsedAdditionalStepInputs) {
      const uploadedStepAttachmentsForStep =
        uploadedStepAttachments[step.localId] || [];
      const stepAttachments = [
        ...(step.inputAttachments || []),
        ...uploadedStepAttachmentsForStep,
      ];
      const stepInstructions = step.instructions.trim();
      if (!hasPreUploadStepContent && !stepInstructions) {
        continue;
      }

      const stepPayload: Record<string, unknown> = {};
      if (stepInstructions) {
        stepPayload.instructions = stepInstructions;
      }
      if (stepAttachments.length > 0) {
        stepPayload.inputAttachments = stepAttachments;
      } else if (pageMode.mode !== "create") {
        stepPayload.inputAttachments = [];
      }
      if (step.title.trim()) {
        stepPayload.title = step.title.trim();
      }
      const generatedToolPayload = executableGeneratedToolPayload(step);
      const generatedSkillPayload = executableGeneratedSkillPayload(step);
      if (generatedToolPayload) {
        stepPayload.tool = generatedToolPayload;
      } else if (generatedSkillPayload) {
        stepPayload.skill = generatedSkillPayload;
      } else if (step.stepType === "tool") {
        const toolPayload = manualToolPayload(step, stepToolInputs);
        if (toolPayload) {
          stepPayload.tool = toolPayload;
        }
      } else if (stepSkillId || stepSkillArgsRaw || stepSkillCaps.length > 0) {
        stepPayload.tool = {
          type: "skill",
          name: stepSkillId || primarySkillId,
          version: "1.0",
          inputs: stepSkillArgs,
          ...(stepSkillCaps.length > 0
            ? { requiredCapabilities: stepSkillCaps }
            : {}),
        };
        stepPayload.skill = {
          id: stepSkillId || primarySkillId,
          args: stepSkillArgs,
          ...(stepSkillCaps.length > 0
            ? { requiredCapabilities: stepSkillCaps }
            : {}),
        };
        stepSkillRequiredCapabilities.push(...stepSkillCaps);
      }
      additionalSteps.push({ sourceIndex, payload: stepPayload });
    }

    const includePrimaryStepForObjectiveOverride =
      Boolean(primaryInstructionsForSubmit) &&
      objectiveInstructionsForSubmit !== primaryInstructionsForSubmit;
    const hasTemplateBoundStep = submissionSteps.some((step) =>
      Boolean(step.id.trim()),
    );
    const primaryGeneratedToolPayload =
      executableGeneratedToolPayload(primaryStep) ||
      manualToolPayload(primaryStep, primaryToolInputs);
    const includeExplicitSteps =
      additionalSteps.length > 0 ||
      includePrimaryStepForObjectiveOverride ||
      hasTemplateBoundStep ||
      Boolean(primaryGeneratedToolPayload) ||
      primaryStepAttachmentRefs.length > 0;

    const normalizedSteps = includeExplicitSteps
      ? [
          {
            sourceIndex: 0,
            payload: {
              ...(primaryInstructionsForSubmit
                ? { instructions: primaryInstructionsForSubmit }
                : {}),
              ...(primaryStepAttachmentRefs.length > 0
                ? { inputAttachments: primaryStepAttachmentRefs }
                : pageMode.mode !== "create"
                  ? { inputAttachments: [] }
                : {}),
              ...(primaryGeneratedToolPayload
                ? { tool: primaryGeneratedToolPayload }
                : primaryStepHasSkillOverride
                ? { tool: primaryStepTool, skill: primaryStepSkill }
                : {}),
            },
          },
          ...additionalSteps,
        ].map((entry) => {
          const sourceStep = submissionSteps[entry.sourceIndex];
          if (!sourceStep) {
            return entry.payload;
          }
          const payloadAttachments = Array.isArray(
            entry.payload.inputAttachments,
          )
            ? (entry.payload.inputAttachments as StepAttachmentRef[])
            : [];
          const effectivePayloadAttachments =
            Array.isArray(entry.payload.inputAttachments) ||
            !sourceStep.templateStepId ||
            sourceStep.id !== sourceStep.templateStepId
              ? payloadAttachments
              : sourceStep.templateAttachments;
          const shouldPreserveStepId =
            !sourceStep.templateStepId ||
            sourceStep.id !== sourceStep.templateStepId ||
            (String(entry.payload.instructions || sourceStep.instructions) ===
              sourceStep.templateInstructions &&
              isTemplateBoundStepForAttachments(
                sourceStep,
                effectivePayloadAttachments,
              ));
          const submittedStepType: Exclude<StepType, "preset"> =
            sourceStep.stepType === "tool" ? "tool" : "skill";
          const hasPayloadContent = Object.entries(entry.payload).some(
            ([key, value]) =>
              !(
                key === "inputAttachments" &&
                Array.isArray(value) &&
                value.length === 0
              ),
          );
          const hasSubmittedStepShape =
            hasPayloadContent ||
            Boolean(sourceStep.id.trim()) ||
            Boolean(sourceStep.title.trim()) ||
            Boolean(sourceStep.storyOutput) ||
            Boolean(
              sourceStep.source && Object.keys(sourceStep.source).length > 0,
            ) ||
            Boolean(
              sourceStep.jiraOrchestration &&
                Object.keys(sourceStep.jiraOrchestration).length > 0,
            );
          const submittedStep = {
            ...(shouldPreserveStepId && sourceStep.id.trim()
              ? { id: sourceStep.id.trim() }
              : {}),
            ...(sourceStep.title.trim()
              ? { title: sourceStep.title.trim() }
              : {}),
            ...(hasSubmittedStepShape ? { type: submittedStepType } : {}),
            ...(sourceStep.storyOutput
              ? { storyOutput: sourceStep.storyOutput }
              : {}),
            ...(sourceStep.source && Object.keys(sourceStep.source).length > 0
              ? { source: sourceStep.source }
              : {}),
            ...(sourceStep.jiraOrchestration &&
            Object.keys(sourceStep.jiraOrchestration).length > 0
              ? { jiraOrchestration: sourceStep.jiraOrchestration }
              : {}),
            ...entry.payload,
          };
          const hasNonTypeContent = Object.entries(submittedStep).some(
            ([key, value]) => {
              if (key === "type" || value === undefined || value === null) {
                return false;
              }
              if (typeof value === "string") {
                return value.trim().length > 0;
              }
              if (Array.isArray(value)) {
                return value.length > 0;
              }
              if (typeof value === "object") {
                return Object.keys(value).length > 0;
              }
              return true;
            },
          );
          if (!hasNonTypeContent) {
            return {};
          }
          return submittedStep;
        })
      : [];

    const templateCapabilities = submissionAppliedTemplates.flatMap(
      (entry) => entry.capabilities || [],
    );
    const mergedCapabilities = deriveRequiredCapabilities({
      runtimeMode: normalizedRuntime,
      publishMode: effectivePublishMode,
      taskSkillRequiredCapabilities,
      stepSkillRequiredCapabilities,
      templateCapabilities,
    });

    const normalizedTaskTool = primaryStepTool;

    // Derive title from feature request / resolved objective when a preset is applied
    // Address: Gemini r3034477058 (trim before split), Copilot r3034495920
    // (derive from objectiveInstructions), Codex r3034482711 / Copilot r3034495938
    // (clamp to backend max of 150).
    const _MAX_EXPLICIT_TITLE_LENGTH = 150;

    const explicitTitle = ((): string | undefined => {
      // Prefer the resolved objective text (which already falls back through
      // feature request, primary instructions, and template inputs) so that
      // preset-driven tasks derive titles from the same source the backend
      // would fall back to.
      const source = objectiveInstructions.trim();
      if (!source) {
        return undefined;
      }
      const firstLine = source.split('\n')[0]?.trim();
      if (!firstLine) {
        return undefined;
      }
      // Strip markdown heading prefix (e.g., "# Title" -> "Title")
      const cleaned = firstLine.replace(/^#+\s*/, '').trim();
      if (!cleaned) {
        return undefined;
      }
      return cleaned.length > _MAX_EXPLICIT_TITLE_LENGTH
        ? `${cleaned.slice(0, _MAX_EXPLICIT_TITLE_LENGTH).trimEnd()}…`
        : cleaned;
    })();

    // Only include task-level agent skill selectors when we have an explicit skill or a template slug.
    const taskSkillSelectors =
      hasExplicitSkillSelection(primarySkillId) ||
      submissionAppliedTemplates.length > 0
        ? { include: [{ name: effectiveSubmissionSkillId }] }
        : undefined;

    // Address: Gemini r3034477068 — keep tool/skill objects in sync with effectiveSkillId
    const resolvedTool = effectiveSubmissionSkillId !== primarySkillId
      ? { ...normalizedTaskTool, name: effectiveSubmissionSkillId }
      : normalizedTaskTool;
    const resolvedSkill = effectiveSubmissionSkillId !== primarySkillId
      ? { ...primaryStepSkill, id: effectiveSubmissionSkillId }
      : primaryStepSkill;

    const shouldSubmitMergeAutomation =
      isMergeAutomationPublishMode(publishMode) &&
      effectivePublishMode === "pr" &&
      !isSelfManagedPublishSkill(effectiveSubmissionSkillId);

    const taskPayload: Record<string, unknown> = {
      instructions: objectiveInstructionsForSubmit,
      tool: resolvedTool,
      skill: resolvedSkill,
      ...(taskLevelAttachmentRefs.length > 0
        ? { inputAttachments: taskLevelAttachmentRefs }
        : pageMode.mode !== "create"
          ? { inputAttachments: [] }
        : {}),
      ...(taskSkillSelectors ? { skills: taskSkillSelectors } : {}),
      ...(Object.keys(primarySkillArgs).length > 0 ? { inputs: primarySkillArgs } : {}),
      ...(explicitTitle ? { title: explicitTitle } : {}),
      proposeTasks,
      runtime: {
        mode: normalizedRuntime,
        ...(model.trim() ? { model: model.trim() } : {}),
        ...(effort.trim() ? { effort: effort.trim() } : {}),
        ...(providerProfile ? { profileId: providerProfile } : {}),
      },
      publish: {
        mode: effectivePublishMode,
      },
      ...(produceReport || pageMode.mode !== "create"
        ? {
            reportOutput: {
              enabled: produceReport,
              ...(produceReport
                ? {
                    required: true,
                    reportType: "agent_run_report",
                  }
                : {}),
            },
          }
        : {}),
      ...(effectiveBranch
        ? {
            git: {
              branch: effectiveBranch,
            },
          }
        : {}),
      ...(normalizedSteps.length > 0 ? { steps: normalizedSteps } : {}),
      ...(submissionAppliedTemplates.length > 0
        ? { appliedStepTemplates: submissionAppliedTemplates }
        : {}),
      ...(selectedDependencies.length > 0
        ? { dependsOn: selectedDependencies }
        : {}),
    };

    const requestBody: Record<string, unknown> = {
      type: "task",
      priority,
      maxAttempts,
      payload: {
        repository: normalizedRepository,
        ...(mergedCapabilities.length > 0
          ? { requiredCapabilities: mergedCapabilities }
          : {}),
        targetRuntime: normalizedRuntime,
        publishMode: effectivePublishMode,
        ...(produceReport || pageMode.mode !== "create"
          ? {
              reportOutput: {
                enabled: produceReport,
                ...(produceReport
                  ? {
                      required: true,
                      reportType: "agent_run_report",
                    }
                  : {}),
              },
            }
          : {}),
        ...(shouldSubmitMergeAutomation
          ? { mergeAutomation: { enabled: true } }
          : {}),
        task: taskPayload,
      },
    };

    if (schedulePayload) {
      (requestBody.payload as Record<string, unknown>).schedule =
        schedulePayload;
    }

    try {
      let inputArtifactRef: string | null = null;
      const submittedPayload = requestBody.payload as Record<string, unknown>;
      const temporalDraftData =
        pageMode.mode !== "create" ? temporalDraftQuery.data : undefined;
      if (pageMode.mode !== "create" && !temporalDraftData) {
        throw new Error(
          pageMode.mode === "rerun"
            ? "Cannot request rerun because the execution draft is missing."
            : "Cannot save changes because the execution draft is missing.",
        );
      }
      const editParametersPatch =
        temporalDraftData
          ? buildEditParametersPatch({
              execution: temporalDraftData.execution,
              artifactInput: temporalDraftData.artifactInput,
              submittedPayload,
            })
          : null;
      const artifactPayload = editParametersPatch ?? submittedPayload;
      const taskInputArtifactBody = JSON.stringify({
        repository: artifactPayload.repository ?? normalizedRepository,
        task: artifactPayload.task ?? taskPayload,
      });
      const taskInputArtifactBytes = utf8ByteLength(taskInputArtifactBody);
      const existingInputArtifactRef = String(
        temporalDraftQuery.data?.execution.inputArtifactRef || "",
      ).trim();
      const shouldCreateInputArtifact =
        taskInputArtifactBytes > INLINE_TASK_INPUT_LIMIT_BYTES ||
        (pageMode.mode !== "create" && Boolean(existingInputArtifactRef));
      if (shouldCreateInputArtifact) {
        const sourceWorkflowId =
          pageMode.mode === "rerun" ? String(pageMode.executionId || "").trim() : null;
        const artifact = await createInputArtifact(
          artifactCreateEndpoint,
          taskInputArtifactBody,
          normalizedRepository,
          { sourceWorkflowId },
        );
        inputArtifactRef = artifact.artifactId;
        artifactPayload.inputArtifactRef = inputArtifactRef;
        stripOversizedInlineInstructions({ payload: artifactPayload });
      }

      if (pageMode.mode === "edit" || pageMode.mode === "rerun") {
        const workflowId = String(pageMode.executionId || "").trim();
        if (!workflowId) {
          throw new Error(
            pageMode.mode === "rerun"
              ? "Cannot request rerun because the execution id is missing."
              : "Cannot save changes because the execution id is missing.",
          );
        }
        await handleTemporalTaskEditingSubmit({
          workflowId,
          updateName:
            pageMode.mode === "rerun" ? "RequestRerun" : "UpdateInputs",
          inputArtifactRef,
          parametersPatch: artifactPayload,
        });
        return;
      }

      const response = await fetch(temporalCreateEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) {
        const detail = await responseErrorDetail(response, "Failed to create task.");
        if (detail.code?.startsWith("dependency_")) {
          setDependencyMessage(detail.message);
          return;
        }
        throw new Error(detail.message);
      }
      const created = (await response.json()) as ExecutionCreateResponse;
      if (inputArtifactRef) {
        await linkInputArtifact(inputArtifactRef, created);
      }
      for (const attachment of [
        ...uploadedObjectiveAttachments,
        ...Object.values(uploadedStepAttachments).flat(),
      ]) {
        await linkInputArtifact(attachment.artifactId, created, {
          linkType: "input.attachment",
          label: attachment.filename,
        });
      }
      const redirectPath =
        String(created.redirectPath || "").trim() ||
        (created.definitionId
          ? `/tasks/schedules/${encodeURIComponent(created.definitionId)}`
          : created.workflowId
            ? `/tasks/${encodeURIComponent(created.workflowId)}?source=temporal`
            : "");
      if (!redirectPath) {
        throw new Error("Task was created but no redirect path was returned.");
      }
      navigateTo(redirectPath);
      didNavigateAfterCreate = true;
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to create task.");
      // Detect network-level fetch failures (TypeError: "Failed to fetch")
      // and log additional diagnostics to help troubleshoot.
      if (
        failure instanceof TypeError &&
        failure.message === "Failed to fetch"
      ) {
        console.error(
          "[TaskCreate] Network-level fetch failure during task creation.",
          {
            endpoint: temporalCreateEndpoint,
            errorName: failure.name,
            errorMessage: failure.message,
            possibleCauses: [
              "API service may be unreachable or not running",
              "CORS policy is blocking the request (check browser devtools Network tab for CORS errors)",
              "Network connectivity issue or proxy misconfiguration",
              "TLS/SSL certificate error (if using HTTPS)",
            ].join("; "),
          },
        );
        setSubmitMessage(
          "Failed to reach the task creation API. " +
            `Endpoint: ${temporalCreateEndpoint}. ` +
            "This usually means the API service is unreachable, a CORS policy is blocking the request, " +
            "or there is a network connectivity issue. Check the browser console for more details.",
        );
      } else {
        setSubmitMessage(failure.message);
      }
    } finally {
      if (!didNavigateAfterCreate) {
        clearSubmitBusy();
      }
    }
  }

  const pageTitle =
    pageMode.intent === "edit" || pageMode.intent === "edit-for-rerun"
      ? "Edit Task"
      : pageMode.mode === "rerun"
        ? "Rerun Task"
        : "Create Task";
  const primaryCta =
    pageMode.intent === "edit"
      ? "Save Changes"
      : pageMode.intent === "edit-for-rerun"
        ? "Run edited task"
      : pageMode.mode === "rerun"
        ? "Rerun Task"
        : "Create";
  const showPrimaryCtaArrow = pageMode.mode === "create";
  const primaryCtaTooltip =
    pageMode.intent === "edit"
      ? "Save changes to this task draft"
      : pageMode.intent === "edit-for-rerun"
        ? "Start a new run from this edited task draft"
      : pageMode.mode === "rerun"
        ? "Start a new run from this task draft"
        : "Create this task";
  const repositoryTooltip = "Select the GitHub repository for this task";
  const branchTooltip = branchOptionsQuery.isLoading
    ? "Loading branches for the selected repository..."
    : branchControlDisabled
      ? branchStatusMessage ||
        "Choose a valid GitHub repository before selecting a branch"
      : "Select the branch to check out before the task starts";
  const publishModeTooltip = !mergeAutomationAvailable
    ? "Publishing is forced to None because the selected preset or resolver manages its own publish flow"
    : "Select how MoonMind publishes task changes";
  const expandStepPresetTooltip =
    "Expand the selected preset into editable steps at this position";
  const modeLoadError =
    pageMode.mode !== "create" && !temporalTaskEditingEnabled
      ? "Temporal task editing is not enabled."
      : temporalDraftQuery.isError
        ? temporalDraftQuery.error instanceof Error
          ? temporalDraftQuery.error.message
          : "Failed to reconstruct the task draft."
        : null;
  const isTemporalFormBlocked =
    pageMode.mode !== "create" &&
    (temporalDraftQuery.isLoading || Boolean(modeLoadError));

  useEffect(() => {
    if (!showPrimaryCtaArrow || isTemporalFormBlocked) {
      submitArrowExitHeldRef.current = false;
      setIsSubmitArrowExiting(false);
    }
  }, [isTemporalFormBlocked, showPrimaryCtaArrow]);

  function clearSubmitArrowExit() {
    if (submitArrowExitHeldRef.current) {
      return;
    }
    if (submitArrowExitTimeoutRef.current !== null) {
      window.clearTimeout(submitArrowExitTimeoutRef.current);
      submitArrowExitTimeoutRef.current = null;
    }
    setIsSubmitArrowExiting(false);
  }

  function scheduleSubmitArrowExitClear() {
    if (submitArrowExitTimeoutRef.current !== null) {
      window.clearTimeout(submitArrowExitTimeoutRef.current);
    }
    submitArrowExitTimeoutRef.current = window.setTimeout(() => {
      submitArrowExitTimeoutRef.current = null;
      if (submitArrowExitHeldRef.current) {
        return;
      }
      setIsSubmitArrowExiting(false);
    }, 230);
  }

  function holdSubmitArrowExitUntilNavigation() {
    submitArrowExitHeldRef.current = true;
    if (submitArrowExitTimeoutRef.current !== null) {
      window.clearTimeout(submitArrowExitTimeoutRef.current);
      submitArrowExitTimeoutRef.current = null;
    }
    setIsSubmitArrowExiting(true);
  }

  function releaseSubmitArrowExit() {
    submitArrowExitHeldRef.current = false;
    clearSubmitArrowExit();
  }

  return (
    <div className="stack task-create-page">
      <section data-canonical-create-section="Header" aria-label="Header">
        <h2 className="page-title">{pageTitle}</h2>
      </section>

      {pageMode.mode !== "create" && temporalDraftQuery.isLoading ? (
        <p className="notice" role="status">
          Loading Temporal execution draft...
        </p>
      ) : null}

      {modeLoadError ? (
        <p className="notice error" role="alert">
          {modeLoadError}
        </p>
      ) : null}

      {pageMode.intent === "edit-for-rerun" && !modeLoadError ? (
        <p className="notice" role="status">
          You are editing a previous task. Your changes will create a new run.
          The original run will remain unchanged.
        </p>
      ) : null}

      {jiraIntegration?.enabled && jiraBrowserOpen ? (
        <div className="jira-browser-backdrop">
          <section
            className="jira-browser-panel stack"
            role="dialog"
            aria-modal="true"
            aria-labelledby="jira-browser-title"
          >
            <div className="queue-step-header">
              <div>
                <h3 id="jira-browser-title">Browse Jira issue</h3>
                <p className="small">{`Target: ${jiraTargetText}`}</p>
                {jiraImportWillCustomizeTemplateStep ? (
                  <p className="notice small">
                    Importing into this template-bound step will make it manually
                    customized.
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                className="queue-step-icon-button"
                aria-label="Close Jira browser"
                title="Close Jira browser"
                onClick={closeJiraBrowser}
              >
                <CloseIcon />
                <span className="sr-only">Close Jira browser</span>
              </button>
            </div>

            <div className="grid-2">
              <label>
                Project
                <select
                  value={selectedJiraProjectKey}
                  disabled={
                    jiraProjectsQuery.isLoading || jiraProjectsQuery.isError
                  }
                  onChange={(event) => selectJiraProject(event.target.value)}
                >
                  <option value="">Select project...</option>
                  {(jiraProjectsQuery.data || []).map((project) => (
                    <option key={project.projectKey} value={project.projectKey}>
                      {project.name
                        ? `${project.name} (${project.projectKey})`
                        : project.projectKey}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Board
                <select
                  value={selectedJiraBoardId}
                  disabled={
                    !selectedJiraProjectKey ||
                    jiraBoardsQuery.isLoading ||
                    jiraBoardsQuery.isError
                  }
                  onChange={(event) => selectJiraBoard(event.target.value)}
                >
                  <option value="">Select board...</option>
                  {(jiraBoardsQuery.data || []).map((board) => (
                    <option key={board.id} value={board.id}>
                      {board.name || board.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="grid-2">
              <label>
                Import target
                <select
                  value={jiraTargetValue(jiraImportTarget)}
                  onChange={(event) => selectJiraImportTarget(event.target.value)}
                >
                  <option value="preset-text">
                    Instructions (Preset)
                  </option>
                  {attachmentPolicy.enabled ? (
                    <option value="preset-attachments">
                      Instructions attachments (Preset)
                    </option>
                  ) : null}
                  {steps.map((step, index) => (
                    <option
                      key={`jira-target-step-text-${step.localId}`}
                      value={`step-text:${step.localId}`}
                    >
                      {`Step ${index + 1} Instructions`}
                    </option>
                  ))}
                  {attachmentPolicy.enabled
                    ? steps.map((step, index) => (
                        <option
                          key={`jira-target-step-attachments-${step.localId}`}
                          value={`step-attachments:${step.localId}`}
                        >
                          {`Step ${index + 1} attachments`}
                        </option>
                      ))
                    : null}
                </select>
              </label>

              {!jiraImportTarget?.attachmentsOnly ? (
                <label>
                  Text import
                  <select
                    value={jiraWriteMode}
                    onChange={(event) =>
                      setJiraWriteMode(
                        event.target.value === "replace" ? "replace" : "append",
                      )
                    }
                  >
                    <option value="append">Append to target text</option>
                    <option value="replace">Replace target text</option>
                  </select>
                </label>
              ) : null}
            </div>

            {jiraProjectsError ? (
              <p className="notice small">{jiraProjectsError}</p>
            ) : null}
            {jiraProjectsEmpty ? (
              <p className="notice small">{jiraProjectsEmpty}</p>
            ) : null}
            {jiraBoardsError ? (
              <p className="notice small">{jiraBoardsError}</p>
            ) : null}
            {jiraBoardsEmpty ? (
              <p className="notice small">{jiraBoardsEmpty}</p>
            ) : null}
            {jiraBoardIssuesError ? (
              <p className="notice small">{jiraBoardIssuesError}</p>
            ) : null}
            {jiraColumnsEmpty ? (
              <p className="notice small">{jiraColumnsEmpty}</p>
            ) : null}

            <div className="jira-browser-layout">
              <div className="stack">
                <div
                  className="jira-column-tabs"
                  aria-label="Jira board columns"
                >
                  {jiraBrowserColumns.map((column) => (
                    <button
                      key={column.id}
                      type="button"
                      className={
                        column.id === activeJiraColumnId
                          ? "secondary jira-column-tab active"
                          : "secondary jira-column-tab"
                      }
                      aria-pressed={column.id === activeJiraColumnId}
                      title={`Show Jira issues in ${column.name}`}
                      onClick={() => selectJiraColumn(column.id)}
                    >
                      {`${column.name} ${Number(column.count || 0)}`}
                    </button>
                  ))}
                </div>

                <div className="jira-issue-list" aria-live="polite">
                  {jiraColumnsQuery.isLoading || jiraIssuesQuery.isLoading ? (
                    <p className="small">Loading Jira issues...</p>
                  ) : jiraBoardIssuesError ? (
                    <p className="small">Jira issues are unavailable right now.</p>
                  ) : activeJiraIssues.length > 0 ? (
                    activeJiraIssues.map((issue) => (
                      <button
                        key={issue.issueKey}
                        type="button"
                        className={
                          issue.issueKey === selectedJiraIssueKey
                            ? "jira-issue-button active"
                            : "jira-issue-button"
                        }
                        disabled={Boolean(pendingJiraImportIssueKey)}
                        title={`Import Jira issue ${issue.issueKey} into ${jiraTargetText}`}
                        onClick={() => selectJiraIssue(issue.issueKey)}
                      >
                        <strong>{issue.issueKey}</strong>
                        <span>{issue.summary}</span>
                        <span className="small">
                          {[issue.issueType, issue.statusName, issue.assignee]
                            .filter(Boolean)
                            .join(" / ")}
                        </span>
                      </button>
                    ))
                  ) : jiraActiveColumnEmpty ? (
                    <p className="small">{jiraActiveColumnEmpty}</p>
                  ) : (
                    <p className="small">
                      Select a Jira board column to view issues.
                    </p>
                  )}
                </div>
              </div>

              <aside className="jira-issue-action stack">
                {jiraIssueError ? (
                  <p className="notice small">{jiraIssueError}</p>
                ) : selectedJiraIssueKey && jiraIssueDetailQuery.isLoading ? (
                  <p className="small">Adding Jira issue to instructions...</p>
                ) : (
                  <p className="small">
                    Select an issue to append it to {jiraTargetText}.
                  </p>
                )}
              </aside>
            </div>
          </section>
        </div>
      ) : null}

      <form
        id="queue-submit-form"
        className="queue-submit-form"
        onSubmit={handleSubmit}
        aria-disabled={isTemporalFormBlocked}
      >
        <fieldset
          className="stack"
          disabled={isTemporalFormBlocked}
          aria-busy={isTemporalFormBlocked}
        >
        <section
          className="card queue-steps-section stack"
          data-canonical-create-section="Steps"
          aria-label="Steps"
        >
          <div id="queue-steps-list" className="stack queue-steps-list">
            <datalist id={SKILL_OPTIONS_DATALIST_ID}>
              <option value="auto" />
              {(skillsQuery.data || []).map((skillId) => (
                <option key={skillId} value={skillId} />
              ))}
            </datalist>
            <datalist id={MODEL_OPTIONS_DATALIST_ID}>
              {modelOptions.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
            <datalist id={EFFORT_OPTIONS_DATALIST_ID}>
              {effortOptions.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
            <datalist id={REPOSITORY_OPTIONS_DATALIST_ID}>
              {repositoryOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </datalist>
            <datalist id={BRANCH_OPTIONS_DATALIST_ID}>
              {branchOptions.map((item) => (
                <option key={`${item.source}:${item.value}`} value={item.value}>
                  {item.label}
                </option>
              ))}
            </datalist>

            {steps.map((step, index) => {
              const isPrimaryStep = index === 0;
              const showSkillArgsField = showAdvancedStepOptions;
              const visiblePresetInputs = step.presetDetail
                ? (step.presetDetail.inputs || []).filter(
                    (definition) =>
                      isVisiblePresetInput(step.presetDetail?.slug, definition) &&
                      !isFeatureRequestInputKey(definition.name),
                  )
                : [];
              const toolSearchText = toolSearchTextByStep[step.localId] || "";
              const trustedToolDefinitions = trustedToolsQuery.data || [];
              const toolChoiceGroups = groupedToolChoices(
                trustedToolDefinitions,
                toolSearchText,
              );
              const selectedTrustedTool =
                trustedToolDefinitions.find(
                  (tool) => toolDefinitionId(tool) === step.toolId.trim(),
                ) || null;
              const jiraTransitionState =
                jiraTransitionStateByStep[step.localId] || null;
              const showJiraTransitionOptions =
                step.stepType === "tool" &&
                step.toolId.trim() === "jira.transition_issue";
              return (
                <section
                  key={step.localId}
                  className="stack queue-step-section"
                  data-step-index={index}
                >
                  <div className="queue-step-header">
                    <strong>{`Step ${index + 1}`}</strong>
                    <div
                      className="queue-step-controls"
                      role="group"
                      aria-label={`Step ${index + 1} controls`}
                    >
                      <button
                        type="button"
                        className="queue-step-icon-button"
                        data-step-action="up"
                        data-step-index={index}
                        disabled={index === 0}
                        aria-label="Move step up"
                        title="Move step up"
                        onClick={() => moveStep(index, -1)}
                      >
                        <ArrowUpIcon />
                        <span className="sr-only">Move step up</span>
                      </button>
                      <button
                        type="button"
                        className="queue-step-icon-button"
                        data-step-action="down"
                        data-step-index={index}
                        disabled={index === steps.length - 1}
                        aria-label="Move step down"
                        title="Move step down"
                        onClick={() => moveStep(index, 1)}
                      >
                        <ArrowDownIcon />
                        <span className="sr-only">Move step down</span>
                      </button>
                      <button
                        type="button"
                        className="queue-step-icon-button destructive"
                        data-step-action="remove"
                        data-step-index={index}
                        aria-label="Remove step"
                        title="Remove step"
                        onClick={() => removeStep(index)}
                      >
                        <CloseIcon />
                        <span className="sr-only">Remove step</span>
                      </button>
                    </div>
                  </div>

                  <fieldset className="queue-step-type-field">
                    <legend className="sr-only">Step Type</legend>
                    <div className="queue-step-type-options">
                      {STEP_TYPE_OPTIONS.map((option) => {
                        const Icon = option.Icon;
                        return (
                          <label
                            key={option.value}
                            className="queue-step-type-option"
                            title={STEP_TYPE_HELP_TEXT[option.value]}
                          >
                            <input
                              type="radio"
                              name={`queue-step-type-${step.localId}`}
                              value={option.value}
                              checked={step.stepType === option.value}
                              data-step-field="stepType"
                              data-step-index={String(index)}
                              onChange={(event) =>
                                handleStepTypeChange(
                                  step.localId,
                                  event.target.value,
                                )
                              }
                            />
                            <span className="queue-step-type-option-icon">
                              <Icon />
                            </span>
                            <span className="queue-step-type-option-label">
                              {option.label}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  {step.stepTypeMessage ? (
                    <p className="notice small">{step.stepTypeMessage}</p>
                  ) : null}

                  {step.stepType === "tool" ? (
                    <div className="stack queue-step-type-panel">
                      <p className="small">
                        Tool steps run typed governed operations with schema-backed
                        inputs, authorization, capability, retry, binding,
                        validation, and error contracts.
                      </p>
                      <label>
                        Search Tools
                        <input
                          data-step-field="toolSearch"
                          data-step-index={String(index)}
                          placeholder="Search by integration, tool, or purpose"
                          value={toolSearchText}
                          onChange={(event) =>
                            setToolSearchTextByStep((current) => ({
                              ...current,
                              [step.localId]: event.target.value,
                            }))
                          }
                        />
                      </label>
                      {trustedToolsQuery.isLoading || trustedToolsQuery.isFetching ? (
                        <p className="small">Loading trusted Tools...</p>
                      ) : trustedToolsQuery.isError ? (
                        <p className="notice small">
                          Trusted Tool discovery is unavailable. Manual Tool
                          authoring remains available.
                        </p>
                      ) : toolChoiceGroups.length > 0 ? (
                        <div className="queue-tool-choice-groups">
                          {toolChoiceGroups.map((group) => (
                            <div
                              key={`${step.localId}-tool-group-${group.group}`}
                              className="queue-tool-choice-group"
                            >
                              <strong>{group.group}</strong>
                              <div className="queue-tool-choice-list">
                                {group.tools.map((tool) => {
                                  const toolId = toolDefinitionId(tool);
                                  return (
                                    <button
                                      key={`${step.localId}-tool-${toolId}`}
                                      type="button"
                                      className={
                                        step.toolId.trim() === toolId
                                          ? "secondary active"
                                          : "secondary"
                                      }
                                      aria-pressed={step.toolId.trim() === toolId}
                                      title={tool.description || toolId}
                                      onClick={() =>
                                        selectTrustedTool(step.localId, toolId)
                                      }
                                    >
                                      {toolId}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : trustedToolDefinitions.length > 0 ? (
                        <p className="small">No trusted Tools match this search.</p>
                      ) : null}
                      <label>
                        Tool ID
                        <input
                          data-step-field="toolId"
                          data-step-index={String(index)}
                          placeholder="jira.get_issue"
                          value={step.toolId}
                          onChange={(event) =>
                            selectTrustedTool(step.localId, event.target.value)
                          }
                        />
                      </label>
                      <p className="small">
                        {toolContractSummary(selectedTrustedTool)}
                      </p>
                      <label>
                        Tool Version (optional)
                        <input
                          data-step-field="toolVersion"
                          data-step-index={String(index)}
                          placeholder="1.0"
                          value={step.toolVersion}
                          onChange={(event) =>
                            updateStep(step.localId, {
                              toolVersion: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        Tool Inputs (JSON object)
                        <textarea
                          data-step-field="toolInputs"
                          data-step-index={String(index)}
                          placeholder='{"issueKey":"MM-563"}'
                          value={step.toolInputs}
                          onChange={(event) =>
                            updateStep(step.localId, {
                              toolInputs: event.target.value,
                            })
                          }
                        />
                      </label>
                      {showJiraTransitionOptions ? (
                        <div className="stack queue-tool-dynamic-options">
                          <button
                            type="button"
                            className="secondary"
                            aria-busy={Boolean(jiraTransitionState?.isLoading)}
                            disabled={Boolean(jiraTransitionState?.isLoading)}
                            onClick={() => void loadJiraTransitionOptions(step)}
                          >
                            Load Jira target statuses
                          </button>
                          {jiraTransitionState?.error ? (
                            <p className="notice small">
                              {jiraTransitionState.error}
                            </p>
                          ) : null}
                          {jiraTransitionState?.options.length ? (
                            <label>
                              Jira Target Status
                              <select
                                value=""
                                onChange={(event) =>
                                  applyJiraTransitionId(
                                    step.localId,
                                    event.target.value,
                                  )
                                }
                              >
                                <option value="">Select returned status...</option>
                                {jiraTransitionState.options.map((option) => (
                                  <option key={option.id} value={option.id}>
                                    {option.name}
                                  </option>
                                ))}
                              </select>
                            </label>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {step.stepType === "skill" ? (
                    <div className="stack queue-step-type-panel">
                      <label>
                        Skill (optional)
                        <input
                          data-step-field="skillId"
                          data-step-index={String(index)}
                          list={SKILL_OPTIONS_DATALIST_ID}
                          value={step.skillId}
                          placeholder={
                            isPrimaryStep
                              ? "auto (default), moonspec-orchestrate, ..."
                              : "inherit primary step skill"
                          }
                          onChange={(event) =>
                            updateStep(step.localId, {
                              skillId: event.target.value,
                            })
                          }
                        />
                        {isPrimaryStep ? null : (
                          <span className="small">
                            Leave skill blank to inherit primary step defaults.
                          </span>
                        )}
                      </label>

                      {showSkillArgsField ? (
                        <label
                          className="queue-step-skill-args-field"
                          data-skill-args-index={String(index)}
                        >
                          {`Step ${index + 1} Skill Args (optional JSON object)`}
                          <textarea
                            className="queue-step-skill-args"
                            data-step-field="skillArgs"
                            data-step-index={String(index)}
                            placeholder='{"notes":"optional context"}'
                            value={step.skillArgs}
                            onChange={(event) =>
                              updateStep(step.localId, {
                                skillArgs: event.target.value,
                              })
                            }
                          />
                        </label>
                      ) : null}
                      {showAdvancedStepOptions ? (
                        <label>
                          {`Step ${index + 1} Skill Required Capabilities (optional CSV)`}
                          <input
                            data-step-field="skillRequiredCapabilities"
                            data-step-index={String(index)}
                            value={step.skillRequiredCapabilities}
                            placeholder="docker,qdrant,unity"
                            onChange={(event) =>
                              updateStep(step.localId, {
                                skillRequiredCapabilities: event.target.value,
                              })
                            }
                          />
                        </label>
                      ) : null}
                    </div>
                  ) : null}

                  {step.stepType === "preset" ? (
                    <div
                      className="stack queue-step-type-panel"
                      aria-label="Step Preset"
                    >
                      <label>
                        Preset Template
                        <select
                          data-step-field="presetKey"
                          data-step-index={String(index)}
                          value={step.presetKey}
                          disabled={isApplyingPreset}
                          aria-disabled={isApplyingPreset}
                          onChange={(event) => {
                            void handleStepPresetSelectionChange(
                              step.localId,
                              event.target.value,
                            );
                          }}
                        >
                          <option value="">Select preset...</option>
                          {templateItems.map((item) => (
                            <option key={item.key} value={item.key}>
                              {`${item.title} (${scopeLabel(item.scope)})`}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  ) : null}

                  <div className="stack">
                    <div className="queue-field-heading">
                      <label htmlFor={`queue-step-instructions-${step.localId}`}>
                        {usesGenericInstructionsLabel(step.stepType)
                          ? "Instructions"
                          : `Step ${index + 1} Instructions`}
                      </label>
                      <JiraProvenanceChip
                        label={`Step ${index + 1} instructions`}
                        provenance={stepJiraProvenance[step.localId]}
                      />
                      {jiraIntegration?.enabled ? (
                        <button
                          type="button"
                          className="secondary jira-browse-button"
                          aria-label={`Browse Jira issues for Step ${index + 1} instructions`}
                          title={`Browse Jira issues for Step ${index + 1} instructions`}
                          onClick={() =>
                            openJiraBrowser({
                              kind: "step",
                              localId: step.localId,
                            })
                          }
                        >
                          Browse Jira issue
                        </button>
                      ) : null}
                    </div>
                    <textarea
                      id={`queue-step-instructions-${step.localId}`}
                      className="queue-step-instructions"
                      data-step-field="instructions"
                      data-step-index={String(index)}
                      placeholder={
                        isPrimaryStep
                          ? "Describe the task to execute against the repository."
                          : "Step-specific instructions (leave blank to continue from the task objective)."
                      }
                      value={step.instructions}
                      onChange={(event) =>
                        handleStepInstructionsChange(
                          step.localId,
                          event.target.value,
                        )
                      }
                    />
                    {attachmentPolicy.enabled ? (
                      <div className="queue-step-attachments">
                        <div className="queue-step-attachment-control">
                          <span className="queue-step-attachment-label">
                            {`Step ${index + 1} ${attachmentLabel} (optional)`}
                          </span>
                          <button
                            type="button"
                            className="queue-step-attachment-add-button"
                            aria-label={attachmentAddButtonLabel(
                              attachmentPolicy,
                              index + 1,
                            )}
                            title={attachmentAddButtonLabel(
                              attachmentPolicy,
                              index + 1,
                            )}
                            onClick={() =>
                              document
                                .getElementById(
                                  `queue-step-attachments-${step.localId}`,
                                )
                                ?.click()
                            }
                          >
                            +
                          </button>
                          <input
                            id={`queue-step-attachments-${step.localId}`}
                            className="sr-only"
                            type="file"
                            data-step-field="attachments"
                            data-step-index={String(index)}
                            accept={attachmentPolicy.allowedContentTypes.join(",")}
                            multiple
                            aria-label={attachmentFilePickerLabel(
                              attachmentPolicy,
                              index + 1,
                            )}
                            onChange={(event) => {
                              updateStepAttachments(
                                step.localId,
                                Array.from(event.currentTarget.files || []),
                              );
                              event.currentTarget.value = "";
                            }}
                          />
                        </div>
                        {attachmentTargetErrors[
                          attachmentTargetKey(step.localId)
                        ] ? (
                          <p className="notice error">
                            {
                              attachmentTargetErrors[
                                attachmentTargetKey(step.localId)
                              ]
                            }
                          </p>
                        ) : null}
                        {isPrimaryStep && persistedObjectiveAttachments.length > 0 ? (
                          <ul className="list queue-step-attachments-list">
                            {persistedObjectiveAttachments.map((attachment) => (
                              <li key={`objective-${attachment.artifactId}`}>
                                <span>
                                  {`${attachment.filename} (${formatAttachmentBytes(attachment.sizeBytes)})`}
                                </span>
                                <a
                                  className="button secondary"
                                  href={configuredArtifactDownloadUrl(
                                    artifactDownloadEndpoint,
                                    attachment.artifactId,
                                  )}
                                  download={attachment.filename}
                                  title={`Download objective attachment ${attachment.filename}`}
                                >
                                  Download
                                </a>
                                <button
                                  type="button"
                                  className="button secondary"
                                  title={`Remove objective attachment ${attachment.filename}`}
                                  onClick={() =>
                                    removePersistedObjectiveAttachment(
                                      attachment.artifactId,
                                    )
                                  }
                                >
                                  Remove
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {step.inputAttachments.length > 0 ? (
                          <ul className="list queue-step-attachments-list">
                            {step.inputAttachments.map((attachment) => (
                              <li key={`step-${step.localId}-${attachment.artifactId}`}>
                                <span>
                                  {`${attachment.filename} (${formatAttachmentBytes(attachment.sizeBytes)})`}
                                </span>
                                <a
                                  className="button secondary"
                                  href={configuredArtifactDownloadUrl(
                                    artifactDownloadEndpoint,
                                    attachment.artifactId,
                                  )}
                                  download={attachment.filename}
                                  title={`Download Step ${index + 1} attachment ${attachment.filename}`}
                                >
                                  Download
                                </a>
                                <button
                                  type="button"
                                  className="button secondary"
                                  title={`Remove Step ${index + 1} attachment ${attachment.filename}`}
                                  onClick={() =>
                                    removePersistedStepAttachment(
                                      step.localId,
                                      attachment.artifactId,
                                    )
                                  }
                                >
                                  Remove
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {(selectedStepAttachmentFiles[step.localId] || []).length > 0 ? (
                          <ul className="list queue-step-attachments-list">
                            {(selectedStepAttachmentFiles[step.localId] || []).map((file) => (
                              <li key={`${file.name}-${file.size}-${file.lastModified}`}>
                                <span>
                                  <strong>{file.name}</strong>{" "}
                                  <span className="small">
                                    {`${file.type || "application/octet-stream"}, ${formatAttachmentBytes(file.size)}`}
                                  </span>
                                </span>
                                <AttachmentPreview
                                  file={file}
                                  alt={`Preview of Step ${index + 1} attachment ${file.name}`}
                                  onError={() =>
                                    markAttachmentPreviewFailed(
                                      attachmentTargetKey(step.localId),
                                      `Step ${index + 1}`,
                                      file,
                                    )
                                  }
                                />
                                {previewFailureMessages[
                                  `${attachmentTargetKey(step.localId)}:${attachmentFileKey(file)}`
                                ] ? (
                                  <p className="small notice error">
                                    {
                                      previewFailureMessages[
                                        `${attachmentTargetKey(step.localId)}:${attachmentFileKey(file)}`
                                      ]
                                    }
                                  </p>
                                ) : null}
                                {attachmentTargetErrors[
                                  attachmentTargetKey(step.localId)
                                ] ? (
                                  <button
                                    type="button"
                                    className="secondary"
                                    aria-label={`Retry upload for Step ${index + 1} attachment ${file.name}`}
                                    title={`Retry upload for Step ${index + 1} attachment ${file.name}`}
                                    onClick={() =>
                                      clearAttachmentTargetError(
                                        attachmentTargetKey(step.localId),
                                      )
                                    }
                                  >
                                    Retry
                                  </button>
                                ) : null}
                                <button
                                  type="button"
                                  className="secondary"
                                  aria-label={`Remove Step ${index + 1} attachment ${file.name}`}
                                  title={`Remove Step ${index + 1} attachment ${file.name}`}
                                  onClick={() =>
                                    removeStepAttachment(step.localId, file)
                                  }
                                >
                                  Remove
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ) : null}
                  </div>

                  {step.stepType === "preset" ? (
                    <div
                      className="stack queue-step-preset-options"
                      aria-label="Step Preset Options"
                    >
                      {visiblePresetInputs.length > 0 ? (
                        <div className="grid-2">
                          {visiblePresetInputs.map((definition) => {
                              const inputId = `queue-step-${step.localId}-preset-input-${definition.name}`;
                              const value = stepTemplateInputDisplayValue(
                                step,
                                definition,
                              );
                              if (definition.type === "enum") {
                                return (
                                  <label key={definition.name} htmlFor={inputId}>
                                    {definition.label}
                                    <select
                                      id={inputId}
                                      value={value}
                                      disabled={isApplyingPreset}
                                      onChange={(event) =>
                                        updateStepPresetInputValue(
                                          step.localId,
                                          definition,
                                          event.target.value,
                                        )
                                      }
                                    >
                                      {(definition.options || []).map((option) => (
                                        <option key={option} value={option}>
                                          {templateEnumOptionLabel(
                                            definition,
                                            option,
                                          )}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                );
                              }
                              if (definition.type === "boolean") {
                                return (
                                  <label key={definition.name} htmlFor={inputId}>
                                    {definition.label}
                                    <input
                                      id={inputId}
                                      type="checkbox"
                                      checked={value === "true"}
                                      disabled={isApplyingPreset}
                                      onChange={(event) =>
                                        updateStepPresetInputValue(
                                          step.localId,
                                          definition,
                                          event.target.checked,
                                        )
                                      }
                                    />
                                  </label>
                                );
                              }
                              if (
                                definition.type === "textarea" ||
                                definition.type === "markdown"
                              ) {
                                return (
                                  <label key={definition.name} htmlFor={inputId}>
                                    {definition.label}
                                    <textarea
                                      id={inputId}
                                      value={value}
                                      placeholder={definition.placeholder || ""}
                                      disabled={isApplyingPreset}
                                      onChange={(event) =>
                                        updateStepPresetInputValue(
                                          step.localId,
                                          definition,
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                );
                              }
                              return (
                                <label key={definition.name} htmlFor={inputId}>
                                  {definition.label}
                                  <input
                                    id={inputId}
                                    type="text"
                                    value={value}
                                    placeholder={definition.placeholder || ""}
                                    disabled={isApplyingPreset}
                                    onChange={(event) =>
                                      updateStepPresetInputValue(
                                        step.localId,
                                        definition,
                                        event.target.value,
                                      )
                                    }
                                  />
                                </label>
                              );
                            })}
                        </div>
                      ) : null}
                      <button
                        type="button"
                        aria-disabled={
                          isApplyingPreset ||
                          !step.presetKey
                        }
                        aria-busy={isApplyingPreset}
                        title={expandStepPresetTooltip}
                        disabled={
                          isApplyingPreset ||
                          !step.presetKey
                        }
                        onClick={() => handleExpandStepPreset(step.localId)}
                      >
                        Expand
                      </button>
                      {stepPresetStatusText(step) ? (
                        <p className="small">{stepPresetStatusText(step)}</p>
                      ) : null}
                    </div>
                  ) : null}
                </section>
              );
            })}

            <div className="queue-step-extension">
              <button
                type="button"
                className="queue-step-extension-button"
                data-step-action="add"
                title="Add another task step"
                onClick={addStep}
              >
                <span className="queue-step-extension-plus" aria-hidden="true">
                  +
                </span>
                <span>Add Step</span>
              </button>
            </div>

          </div>
        </section>

        {taskTemplateCatalogEnabled ? (
          <section
            className="card stack"
            aria-label="Preset Management"
          >
            <div className="actions">
              <strong>Preset Management</strong>
            </div>
            <label>
              Preset Name
              <input
                id="queue-template-preset-name"
                list="queue-template-preset-name-options"
                value={presetManagementName}
                placeholder="New preset name"
                onChange={(event) =>
                  handlePresetManagementNameChange(event.target.value)
                }
              />
              <datalist id="queue-template-preset-name-options">
                {templateItems.map((item) => (
                  <option key={item.key} value={item.title} />
                ))}
              </datalist>
            </label>
            <div className="actions queue-template-actions">
              {taskTemplateSaveEnabled ? (
                <button
                  type="button"
                  id="queue-template-save-current"
                  className="queue-step-icon-button"
                  aria-label="Save preset"
                  title="Save the current steps using the preset name"
                  aria-disabled={!presetManagementName.trim()}
                  disabled={!presetManagementName.trim()}
                  onClick={handleSaveCurrentStepsAsPreset}
                >
                  <SaveIcon />
                </button>
              ) : null}
              {taskTemplateSaveEnabled ? (
                <button
                  type="button"
                  id="queue-template-delete-current"
                  className="queue-step-icon-button destructive"
                  aria-label="Delete preset"
                  aria-disabled={
                    !selectedPresetDeleteEnabled || isDeletingPreset
                  }
                  aria-busy={isDeletingPreset}
                  title={deletePresetTooltip}
                  disabled={!selectedPresetDeleteEnabled || isDeletingPreset}
                  onClick={handleDeleteSelectedPreset}
                >
                  <TrashIcon />
                </button>
              ) : null}
            </div>
            <p className="small" id="queue-template-message">
              {presetStatusText}
            </p>
          </section>
        ) : null}

        <section
          className="card stack"
          data-canonical-create-section="Dependencies"
          aria-label="Dependencies"
        >
          <div>
            <strong>Dependencies</strong>
            <p className="small">
              Add up to {DEPENDENCY_LIMIT} existing <code>MoonMind.Run</code> prerequisites. The new run stays blocked until each prerequisite finishes in <code>completed</code> state.
            </p>
          </div>
          <div className="grid-2">
            <label>
              Existing run
              <select
                id="queue-dependency-picker"
                value={selectedDependencyWorkflowId}
                onChange={(event) => {
                  setSelectedDependencyWorkflowId(event.target.value);
                  setDependencyMessage(null);
                }}
              >
                <option value="">Select prerequisite...</option>
                {availableDependencyOptions.map((item) => (
                  <option key={item.taskId} value={item.taskId}>
                    {`${item.title} (${item.taskId})`}
                  </option>
                ))}
              </select>
            </label>
            <div className="actions" style={{ alignItems: "flex-end" }}>
              <button
                type="button"
                id="queue-dependency-add"
                title="Add the selected prerequisite run"
                onClick={() => addDependency(selectedDependencyWorkflowId)}
              >
                Add dependency
              </button>
            </div>
          </div>
          <p className="small">
            {dependencyOptionsQuery.isLoading
              ? "Loading recent runs..."
              : dependencyOptionsQuery.isError
                ? "Failed to load recent runs. You can still create the task without dependencies, or try refreshing."
                : availableDependencyOptions.length > 0
                  ? `${availableDependencyOptions.length} recent runs available.`
                  : "No recent prerequisite runs available."}
          </p>
          {selectedDependencies.length > 0 ? (
            <ul className="list" id="queue-dependency-list">
              {selectedDependencies.map((workflowId) => {
                const match = (dependencyOptionsQuery.data || []).find(
                  (item) => item.taskId === workflowId,
                );
                return (
                  <li key={workflowId}>
                    <span>
                      <strong>{match?.title || workflowId}</strong>{" "}
                      <code>{workflowId}</code>
                    </span>
                    <button
                      type="button"
                      className="secondary small"
                      title={`Remove dependency ${workflowId}`}
                      onClick={() => removeDependency(workflowId)}
                    >
                      Remove
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="small">No prerequisites selected.</p>
          )}
          <p className={dependencyMessage ? "notice error" : "small"}>
            {dependencyMessage || "Direct dependencies only. Dependency failures propagate immediately to the dependent run."}
          </p>
        </section>

        <section
          className="stack"
          data-canonical-create-section="Execution context"
          aria-label="Execution context"
        >
        <label>
          Runtime
          <select
            name="runtime"
            value={runtime}
            onChange={(event) => setRuntime(event.target.value)}
          >
            {supportedTaskRuntimes.map((runtimeOption) => (
              <option key={runtimeOption} value={runtimeOption}>
                {runtimeOption}
              </option>
            ))}
          </select>
        </label>

        <div
          id="queue-provider-profile-wrap"
          className={providerOptions.length > 0 ? "" : "hidden"}
          hidden={providerOptions.length === 0}
        >
          <label>
            Provider profile
            <select
              name="providerProfile"
              value={providerProfile}
              onChange={(event) => setProviderProfile(event.target.value)}
            >
              {providerOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.isDefault ? `${option.label} (Default)` : option.label}
                </option>
              ))}
            </select>
          </label>
          <p className="small" id="queue-auth-profile-hint">
            {providerProfilesQuery.isError
              ? "Failed to load provider profiles."
              : ""}
          </p>
        </div>

        <div className="grid-2">
          <label>
            Model
            <input
              name="model"
              list={MODEL_OPTIONS_DATALIST_ID}
              value={model}
              placeholder="runtime default"
              onChange={(event) => {
                const next = event.target.value;
                setModel(next);
                setModelManualOverride(next !== "");
              }}
            />
          </label>
          <label>
            Effort
            <input
              name="effort"
              list={EFFORT_OPTIONS_DATALIST_ID}
              value={effort}
              placeholder="runtime default"
              onChange={(event) => setEffort(event.target.value)}
            />
          </label>
        </div>

        </section>

        <section
          className="stack"
          data-canonical-create-section="Execution controls"
          aria-label="Execution controls"
        >
        <div className="grid-2" data-runtime-visibility="worker">
          <label>
            Priority
            <div className="priority-slider-container">
              <input
                type="range"
                name="priority"
                min="-10"
                max="10"
                value={priority}
                onChange={(event) => setPriority(Number(event.target.value))}
              />
              <output>{priority}</output>
            </div>
          </label>
          <label>
            Max Attempts
            <input
              type="number"
              min="1"
              name="maxAttempts"
              value={maxAttempts}
              onChange={(event) => setMaxAttempts(Number(event.target.value))}
            />
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            name="proposeTasks"
            checked={proposeTasks}
            onChange={(event) => setProposeTasks(event.target.checked)}
          />
          Propose Tasks
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={produceReport}
            aria-label="Produce report artifact"
            onChange={(event) => setProduceReport(event.target.checked)}
          />
          Report
        </label>
        </section>

        {pageMode.mode === "create" ? (
        <details
          className="card"
          id="schedule-panel"
          data-canonical-create-section="Schedule"
          aria-label="Schedule"
        >
          <summary>
            <strong>Schedule (optional)</strong>
          </summary>
          <div className="stack" style={{ marginTop: "0.75rem" }}>
            <label>
              Schedule Mode
              <select
                name="scheduleMode"
                id="schedule-mode-select"
                value={scheduleMode}
                onChange={(event) =>
                  setScheduleMode(event.target.value as ScheduleMode)
                }
              >
                <option value="immediate">Immediate</option>
                <option value="once">
                  Deferred (run once at a specific time)
                </option>
                <option value="deferred_minutes">
                  Deferred (run in N minutes)
                </option>
                <option value="recurring">
                  Recurring (create a cron schedule)
                </option>
              </select>
            </label>

            <div
              id="schedule-once-fields"
              className={scheduleMode === "once" ? "" : "hidden"}
            >
              <label>
                Scheduled For
                <input
                  type="datetime-local"
                  name="scheduledFor"
                  id="schedule-datetime"
                  value={scheduledFor}
                  onChange={(event) => setScheduledFor(event.target.value)}
                />
              </label>
            </div>

            <div
              id="schedule-deferred-minutes-fields"
              className={scheduleMode === "deferred_minutes" ? "" : "hidden"}
            >
              <label>
                Minutes from now
                <input
                  type="number"
                  name="scheduleDeferredMinutes"
                  min="1"
                  max="525600"
                  step="1"
                  placeholder="e.g. 15"
                  value={scheduleDeferredMinutes}
                  onChange={(event) =>
                    setScheduleDeferredMinutes(event.target.value)
                  }
                />
              </label>
            </div>

            <div
              id="schedule-recurring-fields"
              className={scheduleMode === "recurring" ? "stack" : "hidden"}
            >
              <label>
                Cron Expression
                <input
                  name="scheduleCron"
                  placeholder="*/30 * * * *"
                  value={scheduleCron}
                  onChange={(event) => setScheduleCron(event.target.value)}
                />
              </label>
              <label>
                Timezone
                <input
                  name="scheduleTimezone"
                  placeholder="UTC"
                  value={scheduleTimezone}
                  onChange={(event) => setScheduleTimezone(event.target.value)}
                />
              </label>
              <label>
                Schedule Name
                <input
                  name="scheduleName"
                  placeholder="My recurring task"
                  value={scheduleName}
                  onChange={(event) => setScheduleName(event.target.value)}
                />
              </label>
            </div>
          </div>
        </details>
        ) : null}

        <section
          className="stack"
          data-canonical-create-section="Submit"
          aria-label="Submit"
        >
        <label className="checkbox">
          <input
            type="checkbox"
            checked={showAdvancedStepOptions}
            aria-label="Show advanced step options"
            onChange={(event) =>
              setShowAdvancedStepOptions(event.target.checked)
            }
          />
          Advanced mode
          <span className="small">
            Adds skill args and required capabilities to each step. Optional
            worker routing overrides; runtime, publish mode, skills, and
            presets already add the common capabilities automatically.
          </span>
        </label>
        <p
          id="queue-submit-message"
          role={submitMessage ? "alert" : undefined}
          aria-live={submitMessage ? "assertive" : undefined}
          className={
            submitMessage
              ? "queue-submit-message notice error"
              : "queue-submit-message small"
          }
        >
          {submitMessage || ""}
        </p>
        <div
          className="queue-floating-bar queue-floating-bar--liquid-glass queue-step-actions queue-step-submit-actions"
          role="group"
          aria-label="Task submission controls"
        >
          {branchStatusMessage ? (
            <p
              className={
                branchOptionsQuery.isError || selectedBranchIsStale
                  ? "queue-floating-bar-status notice error"
                  : "queue-floating-bar-status small"
              }
            >
              {branchStatusMessage}
            </p>
          ) : null}
          <div className="queue-floating-bar-row">
            <div
              className="queue-inline-selector queue-inline-selector--repo"
              title={repositoryTooltip}
            >
              <RepoIcon />
              <input
                name="repository"
                aria-label="GitHub Repo"
                title={repositoryTooltip}
                list={REPOSITORY_OPTIONS_DATALIST_ID}
                value={repository}
                placeholder="owner/repo"
                onChange={(event) =>
                  handleRepositoryChange(event.target.value)
                }
              />
            </div>
            <div
              className="queue-inline-selector queue-inline-selector--branch"
              title={branchTooltip}
            >
              <BranchIcon />
              <input
                name="branch"
                aria-label="Branch"
                title={branchTooltip}
                list={BRANCH_OPTIONS_DATALIST_ID}
                value={branch}
                placeholder={
                  branchOptionsQuery.isLoading ? "Loading branches..." : "Branch"
                }
                disabled={branchControlDisabled}
                onChange={(event) => {
                  setBranchTouched(true);
                  setBranch(event.target.value);
                }}
              />
            </div>
            <div
              className="queue-inline-selector queue-inline-selector--publish"
              title={publishModeTooltip}
            >
              <PublishIcon />
              <select
                name="publishMode"
                aria-label="Publish Mode"
                title={publishModeTooltip}
                value={publishMode}
                onChange={(event) => setPublishMode(event.target.value)}
                disabled={!mergeAutomationAvailable}
              >
                <option value="none">None</option>
                <option value="branch">Branch</option>
                <option value="pr">PR</option>
                <option
                  value={PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE}
                  disabled={!mergeAutomationAvailable}
                >
                  PR with Merge Automation
                </option>
              </select>
            </div>
            <button
              type="submit"
              ref={submitButtonRef}
              className={
                showPrimaryCtaArrow
                  ? `queue-submit-primary queue-submit-primary--icon${
                      isSubmitArrowExiting
                        ? " queue-submit-primary--arrow-exit"
                        : ""
                    }`
                  : "queue-submit-primary queue-submit-primary--with-arrow"
              }
              disabled={isTemporalFormBlocked}
              aria-disabled={isSubmitting || isTemporalFormBlocked}
              aria-busy={isSubmitting}
              aria-label={primaryCta}
              title={primaryCtaTooltip}
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                if (isTemporalFormBlocked) return;
                const rect =
                  submitButtonRef.current?.getBoundingClientRect() ?? null;
                setSubmitRippleRect(rect);
                setSubmitRippleKey((value) => value + 1);
                setIsSubmitArrowExiting(true);
                scheduleSubmitArrowExitClear();
              }}
            >
              {showPrimaryCtaArrow ? (
                <span
                  aria-hidden="true"
                  className="queue-submit-primary-arrow"
                  data-submit-arrow="right"
                  onAnimationEnd={clearSubmitArrowExit}
                >
                  <ArrowRightIcon />
                </span>
              ) : (
                <span>{primaryCta}</span>
              )}
            </button>
            {showPrimaryCtaArrow &&
            submitRippleKey > 0 &&
            submitRippleRect &&
            typeof document !== "undefined"
              ? createPortal(
                  <span
                    key={submitRippleKey}
                    aria-hidden="true"
                    style={{
                      position: "fixed",
                      top: submitRippleRect.top,
                      left: submitRippleRect.left,
                      width: submitRippleRect.width,
                      height: submitRippleRect.height,
                      pointerEvents: "none",
                      borderRadius: 999,
                      zIndex: 41,
                    }}
                  >
                    <span
                      className="queue-submit-primary-ripple"
                      onAnimationEnd={() => setSubmitRippleKey(0)}
                    />
                  </span>,
                  document.body,
                )
              : null}
          </div>
        </div>
        </section>
        </fieldset>
      </form>
    </div>
  );
}
export default TaskCreatePage;
