import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactElement } from "react";
import { createPortal } from "react-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useInRouterContext, useLocation } from "react-router-dom";

import type { BootPayload } from "../boot/parseBootPayload";
import { configQueryDefaults } from "../boot/queryClient";
import { LoadingPlaceholder } from "../components/dashboard/LoadingPlaceholder";
import { SkillCombobox } from "../components/SkillCombobox";
import { DashboardErrorDetails } from "../components/dashboard/DashboardErrorDetails";
import { useLiquidGL } from "../lib/liquidGL/useLiquidGL";
import { navigateTo } from "../lib/navigation";
import {
  readDashboardPreferences,
  updateDashboardPreferences,
} from "../utils/dashboardPreferences";
import {
  buildTemporalArtifactEditUpdatePayload,
  buildRuntimeCommandVersionWarnings,
  buildTemporalSubmissionDraftFromExecution,
  recordTemporalTaskEditingClientEvent,
  resolveTaskSubmitPageMode,
  type TemporalTaskEditingExecutionContract,
  type TemporalTaskInputAttachmentRef,
} from "../lib/temporalTaskEditing";
import {
  readWorkflowListDisplayMode,
} from "../lib/collectionListDisplayMode";
import { WorkflowWorkspaceSidebarPanel } from "../components/workflows/WorkflowWorkspaceSidebar";
import { WORKFLOW_START_ROUTE_CHANGE_REQUEST_EVENT } from "../lib/workflowStartRouteGuard";
import {
  clearRemediationCreateDraft,
  readRemediationCreateDraft,
  type RemediationCreateDraft,
} from "../lib/remediationCreateDraft";

type WorkflowStartDashboardConfig = {
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
    };
  };
};

function readWorkflowStartDashboardConfig(payload: BootPayload): WorkflowStartDashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: WorkflowStartDashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

// This cutoff is enforced on UTF-8 encoded request bytes, not JavaScript string length.
const INLINE_TASK_INPUT_LIMIT_BYTES = 8_000;
export const ARTIFACT_COMPLETE_RETRY_DELAYS_MS = [250, 500, 1000, 2000, 2000];
const ARTIFACT_COMPLETE_RETRY_MESSAGE = "artifact upload is not complete";
const MODEL_OPTIONS_DATALIST_ID = "queue-model-options";
const EFFORT_OPTIONS_DATALIST_ID = "queue-effort-options";
const REPOSITORY_OPTIONS_DATALIST_ID = "queue-repository-options";
const BRANCH_OPTIONS_DATALIST_ID = "queue-branch-options";
const OWNER_REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const PR_RESOLVER_SKILLS = new Set(["pr-resolver"]);
const JIRA_BREAKDOWN_PRESET_SLUG = "jira-breakdown";
const JIRA_BREAKDOWN_ORCHESTRATE_PRESET_SLUG = "jira-breakdown-orchestrate";
const JIRA_BREAKDOWN_IMPLEMENT_PRESET_SLUG = "jira-breakdown-implement";
const JIRA_ORCHESTRATE_PRESET_SLUG = "jira-orchestrate";
const SELF_MANAGED_PUBLISH_SKILLS = new Set([
  ...PR_RESOLVER_SKILLS,
  "fix-comments",
  "fix-ci",
  "fix-merge-conflicts",
]);
const SIDE_EFFECT_PUBLISH_DISABLED_SKILLS = new Set([
  "batch-pr-resolver",
  "batch-dependabot-resolver",
  "batch-workflows",
]);
const PUBLISH_DISABLED_SKILLS = new Set([
  JIRA_BREAKDOWN_PRESET_SLUG,
  JIRA_BREAKDOWN_ORCHESTRATE_PRESET_SLUG,
  JIRA_BREAKDOWN_IMPLEMENT_PRESET_SLUG,
  "jira-verify",
  "jira-pr-verify",
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
const PROPOSE_TASKS_PREFERENCE_KEY = "moonmind.workflow-start.propose-tasks";
const LAST_REPOSITORY_OPTION_PREFERENCE_KEY =
  "moonmind.workflow-start.last-repository-option";
const JIRA_LAST_PROJECT_SESSION_KEY =
  "moonmind.workflow-start.jira.last-project-key";
const JIRA_LAST_BOARD_SESSION_KEY =
  "moonmind.workflow-start.jira.last-board-id";
const JIRA_MANUAL_CONTINUATION_MESSAGE =
  "You can continue creating the workflow manually.";
const DEPENDENCY_LIMIT = 10;
const PENTEST_TOOL_ID = "security.pentest.run";
const PENTEST_SCOPE_ACTIONS = [
  "recon",
  "scan",
  "content_discovery",
  "auth_testing",
  "vuln_validation",
  "exploit_validation",
] as const;
const PENTEST_BASELINE_ACTIONS = ["recon", "scan", "content_discovery"];
const PENTEST_VALIDATE_ACTIONS = [
  "auth_testing",
  "vuln_validation",
  "exploit_validation",
];
const PRESET_REAPPLY_REQUIRED_MESSAGE =
  "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.";
const MAX_EXPLICIT_TITLE_LENGTH = 150;
export const WORKFLOW_START_HEADING_QUOTES = [
  "What's the mission?",
  "Make it so",
  "Go for launch",
  "Free your mind",
  "One small step",
  "Light this candle",
  "All systems go",
];

export function workflowStartFormSnapshot(form: HTMLFormElement | null): string {
  if (!form) {
    return "";
  }
  const values: string[] = [];
  const controls = Array.from(form.elements);
  for (const control of controls) {
    if (
      control instanceof HTMLInputElement ||
      control instanceof HTMLTextAreaElement ||
      control instanceof HTMLSelectElement
    ) {
      if (!control.name && !control.id) {
        continue;
      }
      const key = control.name || control.id;
      if (control instanceof HTMLInputElement) {
        if (control.type === "file") {
          values.push(`${key}=files:${control.files?.length ?? 0}`);
          continue;
        }
        if (control.type === "checkbox" || control.type === "radio") {
          const optionKey = control.value || control.id;
          values.push(`${key}[${optionKey}]=checked:${control.checked}`);
          continue;
        }
      }
      values.push(`${key}=${control.value}`);
    }
  }
  return values.sort().join("\n");
}

export function deriveExplicitWorkflowTitle(sourceValue: string): string | undefined {
  const source = sourceValue.trim();
  if (!source) {
    return undefined;
  }
  const normalized = source
    .slice(0, MAX_EXPLICIT_TITLE_LENGTH * 2)
    .split(/\s+/)
    .join(" ")
    .trim();
  if (!normalized) {
    return undefined;
  }
  // Strip markdown heading prefix (e.g., "# Title" -> "Title")
  const cleaned = normalized.replace(/^#+\s*/, "").trim();
  if (!cleaned) {
    return undefined;
  }
  return cleaned.length > MAX_EXPLICIT_TITLE_LENGTH
    ? `${cleaned.slice(0, MAX_EXPLICIT_TITLE_LENGTH).trimEnd()}…`
    : cleaned;
}

function randomWorkflowStartHeading(except?: string): string {
  if (WORKFLOW_START_HEADING_QUOTES.length === 0) {
    return "Start Workflow";
  }
  const candidates = WORKFLOW_START_HEADING_QUOTES.filter(
    (quote) => quote !== except,
  );
  const choices =
    candidates.length > 0 ? candidates : WORKFLOW_START_HEADING_QUOTES;
  const index = Math.floor(Math.random() * choices.length);
  return choices[index] ?? "Start Workflow";
}

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

function createPentestScopeDraftState(
  overrides: Partial<PentestScopeDraftState> = {},
): PentestScopeDraftState {
  return {
    mode: "generate",
    generatedScopeValues: {},
    validationErrors: {},
    validationWarnings: [],
    uploadStatus: "idle",
    confirmAuthorized: false,
    previewOpen: false,
    ...overrides,
  };
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
      issues?: string;
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
      temporalWorkflowEditing?: boolean;
      temporalTaskEditing?: boolean;
    };
  };
  system?: {
    defaultRepository?: string;
    defaultRuntime?: string;
    defaultAgentRuntime?: string;
    defaultModel?: string;
    defaultTaskModel?: string;
    defaultEffort?: string;
    defaultTaskEffort?: string;
    defaultPublishMode?: string;
    defaultProposeTasks?: boolean;
    defaultModelByRuntime?: Record<string, string>;
    defaultTaskModelByRuntime?: Record<string, string>;
    defaultEffortByRuntime?: Record<string, string>;
    defaultTaskEffortByRuntime?: Record<string, string>;
    supportedAgentRuntimes?: string[];
    supportedRuntimes?: string[];
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
      defaultProfileRef?: string | null;
    };
    omnigentExecutionCatalog?: {
      profiles?: Array<{ ref?: string; displayName?: string; defaultPolicyRef?: string }>;
      policies?: Array<{ ref?: string; hostMode?: string }>;
    };
    presetCatalog?: {
      enabled?: boolean;
      templateSaveEnabled?: boolean;
      list?: string;
      detail?: string;
      expand?: string;
      saveFromWorkflow?: string;
    };
    attachmentPolicy?: {
      enabled?: boolean;
      maxCount?: number;
      maxBytes?: number;
      totalBytes?: number;
      allowedContentTypes?: string[];
    };
    runtimeCommandPreview?: RuntimeCommandPreviewConfig;
    jiraIntegration?: {
      enabled?: boolean;
      defaultProjectKey?: string;
      defaultBoardId?: string;
      rememberLastBoardInSession?: boolean;
    };
  };
}

interface RuntimeCommandCapability {
  slashCommandPassthrough?: boolean;
  renderMode?: string;
  commandHintsRef?: string;
}

interface RuntimeCommandHint {
  label?: string;
  aliases?: string[];
  description?: string;
  argumentPolicy?: Record<string, unknown>;
  bodyPolicy?: Record<string, unknown>;
}

interface RuntimeCommandPreviewConfig {
  hintCatalogVersion?: string;
  runtimes?: Record<string, RuntimeCommandCapability>;
  knownRuntimeCommandHints?: Record<string, RuntimeCommandHint>;
}

interface RuntimeCommandPreviewState {
  sourcePath: string;
  rawInstructions: string;
  rawCommand: string;
  command: string;
  args: string;
  instructionBody: string;
  detectionStatus: "detected" | "escaped" | "malformed";
  hintStatus: "hinted" | "opaque";
  recognitionMode:
    | "hinted_runtime_passthrough"
    | "runtime_passthrough"
    | "escaped_literal"
    | "runtime_does_not_support_slash_commands";
  requiresRuntimeRecognition: boolean;
  messageSeverity: "info" | "warning" | "neutral";
  label: string;
  description: string;
  source: "derived" | "snapshot";
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
  provider_id?: string | null;
  provider_label?: string | null;
  default_model?: string | null;
  default_effort?: string | null;
  model_tiers?: ProviderModelEffortTier[] | null;
  default_model_tier?: number | null;
  is_default?: boolean;
  enabled?: boolean;
  launch_ready?: boolean;
  launchReady?: boolean;
  priority?: number | null;
  tags?: string[] | null;
}

interface OmnigentCatalogGateReason {
  code: string;
  message: string;
  remediationHref: string;
}

interface OmnigentCodexCatalogReadiness {
  schemaVersion: "moonmind.omnigent-codex-readiness.v1";
  runtimeId: "omnigent";
  displayName: string;
  available: boolean;
  defaultExecutionProfileRef: string;
  executionProfiles: Array<{
    ref: string;
    displayName: string;
    available: boolean;
    policyRefs: string[];
    gateReasons: OmnigentCatalogGateReason[];
  }>;
  eligibleProviderProfiles: Array<{
    profileId: string;
    label: string;
    providerId: string;
    busy: boolean;
    queueWhenBusy: boolean;
  }>;
  ineligibleProviderProfiles: Array<{
    profileId: string;
    label: string;
    gateReasons: OmnigentCatalogGateReason[];
  }>;
  gateReasons: OmnigentCatalogGateReason[];
}

interface ProviderModelEffortTier {
  label?: string | null;
  model?: string | null;
  effort?: string | null;
  parameters?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

type TierFallbackMode = "clamp" | "strict";

export interface ModelTierPreview {
  requestedTier: number;
  effectiveTier: number;
  label: string;
  model: string;
  effort: string;
  fallbackReason: "requested_tier_above_configured_range" | null;
  warning: string | null;
}

function modelTiersForProfile(profile: ProviderProfile | undefined): ProviderModelEffortTier[] {
  if (!profile) {
    return [];
  }
  if (Array.isArray(profile.model_tiers) && profile.model_tiers.length > 0) {
    return profile.model_tiers;
  }
  return [
    {
      label: "Default",
      model: profile.default_model ?? null,
      effort: profile.default_effort ?? null,
      parameters: {},
      annotations: {},
    },
  ];
}

export function previewModelTier(
  profile: ProviderProfile | undefined,
  requestedTierValue: string,
): ModelTierPreview | null {
  if (typeof requestedTierValue !== "string") {
    return null;
  }
  const requestedTier = Number.parseInt(requestedTierValue.trim(), 10);
  if (!Number.isInteger(requestedTier) || requestedTier < 1) {
    return null;
  }
  const tiers = modelTiersForProfile(profile);
  if (tiers.length === 0) {
    return {
      requestedTier,
      effectiveTier: requestedTier,
      label: `Tier ${requestedTier}`,
      model: "backend resolved model",
      effort: "backend resolved effort",
      fallbackReason: null,
      warning: null,
    };
  }
  const effectiveTier = Math.min(requestedTier, tiers.length);
  const tier = tiers[effectiveTier - 1] || {};
  const fallbackReason =
    requestedTier > tiers.length ? "requested_tier_above_configured_range" : null;
  return {
    requestedTier,
    effectiveTier,
    label: tier.label || `Tier ${effectiveTier}`,
    model: tier.model || "runtime default model",
    effort: tier.effort || "runtime default effort",
    fallbackReason,
    warning: fallbackReason
      ? `Requested Tier ${requestedTier}, used Tier ${effectiveTier} because the selected profile only defines ${tiers.length} ${tiers.length === 1 ? "tier" : "tiers"}.`
      : null,
  };
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

export function resolveDefaultProviderProfileId(
  profiles: ProviderProfile[],
  configuredDefaultRef?: string | null,
): string {
  const launchableProfiles = profiles.filter(
    (profile) =>
      profile.enabled !== false &&
      profile.launch_ready !== false &&
      profile.launchReady !== false,
  );
  const trimmedRef = configuredDefaultRef?.trim?.() || "";
  if (trimmedRef) {
    const configured = launchableProfiles.find(
      (profile) => profile.profile_id === trimmedRef,
    );
    if (configured) {
      return configured.profile_id;
    }
  }
  const explicitDefault = launchableProfiles.find((profile) => profile.is_default);
  if (explicitDefault) {
    return explicitDefault.profile_id;
  }
  const onlyProfile = launchableProfiles[0];
  if (launchableProfiles.length === 1 && onlyProfile) {
    return onlyProfile.profile_id;
  }
  return (
    [...launchableProfiles].sort((left, right) => {
      const leftPriority = Number(left.priority ?? 100);
      const rightPriority = Number(right.priority ?? 100);
      if (leftPriority !== rightPriority) {
        return rightPriority - leftPriority;
      }
      return left.profile_id.localeCompare(right.profile_id);
    })[0]?.profile_id || ""
  );
}

export function resolveLoadedProviderProfileId({
  profiles,
  providerProfile,
  configuredDefaultRef,
  preserveUnavailableProfile,
}: {
  profiles: ProviderProfile[];
  providerProfile: string;
  configuredDefaultRef?: string | null;
  preserveUnavailableProfile: boolean;
}): string {
  if (profiles.some((profile) => profile.profile_id === providerProfile)) {
    return providerProfile;
  }
  if (preserveUnavailableProfile && providerProfile) {
    return providerProfile;
  }
  return resolveDefaultProviderProfileId(profiles, configuredDefaultRef);
}

interface SkillsResponse {
  items?: {
    worker?: string[];
    [source: string]: string[] | undefined;
  };
  legacyItems?: SkillCapabilityDetail[];
}

interface DependencyPickerExecution {
  taskId?: string;
  workflowId?: string;
  workflowType?: string | null;
  entry?: string | null;
  title: string;
  state?: string | null;
}

interface DependencyPickerListResponse {
  items?: DependencyPickerExecution[];
}

function dependencyWorkflowId(item: DependencyPickerExecution): string {
  return item.workflowId || item.taskId || "";
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

interface PresetInputDefinition {
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

interface PresetSummary {
  slug: string;
  scope: TemplateScope;
  scopeRef?: string | null;
  title: string;
  description: string;
  presetDigest?: string | null;
  inputs?: PresetInputDefinition[];
  inputSchema?: Record<string, unknown>;
  uiSchema?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  requiredCapabilities?: string[];
  capabilities?: string[];
}

interface PresetDetail extends PresetSummary {
  annotations?: Record<string, unknown>;
}

interface SkillCapabilityDetail {
  id: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
  uiSchema?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  contractDigest?: string | null;
  contentDigest?: string | null;
  contentRef?: string | null;
  source?: Record<string, unknown> | null;
  diagnostics?: Array<Record<string, unknown>>;
  requiredCapabilities?: string[];
  publish?: Record<string, unknown> | null;
  sideEffect?: Record<string, unknown> | null;
}

interface SkillCatalogResult {
  ids: string[];
  detailsById: Record<string, SkillCapabilityDetail>;
}

interface PresetListResponse {
  items?: PresetSummary[];
}

interface PresetStepSkill {
  id?: string;
  name?: string;
  type?: string;
  version?: string;
  args?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  inputContractDigest?: string;
  currentInputContractDigest?: string;
  requiredCapabilities?: string[];
}

interface ExpandedStepPayload {
  id?: string;
  title?: string;
  instructions?: string;
  skill?: PresetStepSkill;
  tool?: PresetStepSkill;
  type?: string;
  source?: Record<string, unknown>;
  presetProvenance?: Record<string, unknown>;
  inputAttachments?: StepAttachmentRef[];
  attachments?: StepAttachmentRef[];
  storyOutput?: Record<string, unknown>;
  story_output?: Record<string, unknown>;
  jiraOrchestration?: Record<string, unknown>;
  jira_orchestration?: Record<string, unknown>;
}

interface PresetExpandResponse {
  steps?: ExpandedStepPayload[];
  appliedTemplate?: {
    slug?: string;
    presetDigest?: string;
    inputs?: Record<string, unknown>;
    stepIds?: string[];
    appliedAt?: string;
    composition?: Record<string, unknown>;
    authoredPresets?: Array<Record<string, unknown>>;
  };
  authoredPresets?: Array<Record<string, unknown>>;
  capabilities?: string[];
  warnings?: string[];
}

interface TemplateOption extends PresetSummary {
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
  requiredCapabilities?: string[];
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

type PentestScopeMode = "generate" | "upload" | "existing";

interface PentestScopeDraftState {
  mode: PentestScopeMode;
  generatedScopeValues: Record<string, unknown>;
  uploadedScopeFileName?: string;
  uploadedScopePreview?: Record<string, unknown>;
  attachedArtifactId?: string;
  attachedArtifactRef?: string;
  attachedTarget?: string;
  attachedOperationMode?: string;
  attachedRunnerProfileId?: string;
  validationErrors: Record<string, string>;
  validationWarnings: string[];
  uploadStatus: "idle" | "validating" | "uploading" | "attached" | "failed";
  confirmAuthorized: boolean;
  previewOpen: boolean;
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
  toolInputs: string;
  toolInputValues: Record<string, unknown>;
  toolInputErrors: Record<string, string>;
  toolJsonMode: boolean;
  pentestScopeDraft: PentestScopeDraftState;
  skillId: string;
  skillArgs: string;
  skillInputContractDigest: string;
  skillInputContractNotice: string | null;
  // MM-936: explicit, user-authored required capabilities for this step. The
  // chip selector treats this as the only removable capability source; derived
  // capabilities (skill/tool/preset/runtime/publish) are computed for display.
  explicitRequiredCapabilities: string[];
  runtimeMode: string;
  runtimeModel: string;
  runtimeEffort: string;
  runtimeProviderProfile: string;
  runtimeModelTier: string;
  runtimeTierFallback: TierFallbackMode;
  presetKey: string;
  presetInputValues: Record<string, unknown>;
  presetInputErrors: Record<string, string>;
  presetDetail: PresetDetail | null;
  submitExpansion?: PresetSubmitExpansionState | null;
  presetMessage: string | null;
  templateStepId: string;
  templateInstructions: string;
  inputAttachments: StepAttachmentRef[];
  templateAttachments: StepAttachmentRef[];
  generatedTool?: PresetStepSkill;
  generatedSkill?: PresetStepSkill;
  source?: Record<string, unknown>;
  storyOutput?: Record<string, unknown>;
  jiraOrchestration?: Record<string, unknown>;
  runtimeCommand?: Record<string, unknown>;
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
  expandedSteps: ExpandedStepPayload[];
  inputs: Record<string, unknown>;
  assumptions: string[];
  capabilities: string[];
  warnings: string[];
  appliedTemplate?: PresetExpandResponse["appliedTemplate"];
}

interface AppliedTemplateState {
  slug: string;
  presetDigest?: string;
  inputs: Record<string, unknown>;
  stepIds: string[];
  appliedAt: string;
  capabilities: string[];
  composition?: Record<string, unknown>;
  authoredPresets?: Array<Record<string, unknown>>;
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

function compactSourceFromPresetProvenance(
  provenance: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!provenance) {
    return undefined;
  }
  const source = recordValue(provenance.source);
  const path = Array.isArray(provenance.path)
    ? provenance.path.map((entry) => String(entry).trim()).filter(Boolean)
    : [];
  const presetSlug = String(source.slug || "").trim();
  const presetDigest = String(source.presetDigest || source.digest || "").trim();
  const originalStepId = String(source.originalStepId || "").trim();
  const compact: Record<string, unknown> = { kind: "preset-derived" };
  if (presetSlug) compact.presetSlug = presetSlug;
  if (presetDigest) compact.presetDigest = presetDigest;
  if (path.length > 0) compact.includePath = path;
  if (originalStepId) compact.originalStepId = originalStepId;
  return Object.keys(compact).length > 1 ? compact : undefined;
}

function presetDetailFromCatalogItem(preset: TemplateOption): PresetDetail | null {
  const hasDetailContract =
    Object.prototype.hasOwnProperty.call(preset, "inputs") ||
    Object.prototype.hasOwnProperty.call(preset, "inputSchema") ||
    Object.prototype.hasOwnProperty.call(preset, "uiSchema") ||
    Object.prototype.hasOwnProperty.call(preset, "defaults") ||
    Object.prototype.hasOwnProperty.call(preset, "requiredCapabilities") ||
    Object.prototype.hasOwnProperty.call(preset, "capabilities");
  if (!hasDetailContract) {
    return null;
  }
  return {
    ...preset,
    inputs: Array.isArray(preset.inputs) ? preset.inputs : [],
    inputSchema:
      preset.inputSchema &&
      typeof preset.inputSchema === "object" &&
      !Array.isArray(preset.inputSchema)
        ? preset.inputSchema
        : {},
    uiSchema:
      preset.uiSchema &&
      typeof preset.uiSchema === "object" &&
      !Array.isArray(preset.uiSchema)
        ? preset.uiSchema
        : {},
    defaults:
      preset.defaults &&
      typeof preset.defaults === "object" &&
      !Array.isArray(preset.defaults)
        ? preset.defaults
        : {},
    requiredCapabilities: Array.isArray(preset.requiredCapabilities)
      ? preset.requiredCapabilities
      : [],
    capabilities: Array.isArray(preset.capabilities) ? preset.capabilities : [],
  };
}

function cloneJsonRecord(value: Record<string, unknown>): Record<string, unknown> {
  return structuredClone(value) as Record<string, unknown>;
}

function workflowRecord(source: Record<string, unknown>): Record<string, unknown> {
  return recordValue(source.workflow);
}

function artifactInputWorkflowRecord(
  artifactInput: Record<string, unknown>,
): Record<string, unknown> {
  const snapshotDraft = recordValue(artifactInput.draft);
  const source =
    Object.keys(snapshotDraft).length > 0 ? snapshotDraft : artifactInput;
  return workflowRecord(source);
}

function artifactInputHasStepInstructionGaps(
  artifactInput: Record<string, unknown>,
): boolean {
  const workflow = artifactInputWorkflowRecord(artifactInput);
  const steps = workflow.steps;
  if (!Array.isArray(steps)) {
    return false;
  }
  const appliedStepIds = new Set<string>();
  const appliedTemplates = Array.isArray(workflow.appliedStepTemplates)
    ? workflow.appliedStepTemplates
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
  const sourceWorkflow = artifactInputWorkflowRecord(sourceArtifactInput);
  const sourceSteps = Array.isArray(sourceWorkflow.steps)
    ? sourceWorkflow.steps
    : [];
  if (sourceSteps.length === 0) {
    return artifactInput;
  }

  const merged = cloneJsonRecord(artifactInput);
  const mergedDraft = recordValue(merged.draft);
  const mergedSource =
    Object.keys(mergedDraft).length > 0 ? mergedDraft : merged;
  const mergedWorkflow = workflowRecord(mergedSource);
  const mergedSteps = Array.isArray(mergedWorkflow.steps)
    ? mergedWorkflow.steps
    : [];
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

  const sourceWorkflowInstructions = String(
    sourceWorkflow.instructions || "",
  ).trim();
  if (
    sourceWorkflowInstructions &&
    !String(mergedWorkflow.instructions || "").trim()
  ) {
    mergedWorkflow.instructions = sourceWorkflow.instructions;
    changed = true;
  }

  mergedWorkflow.steps = mergedSteps.map((step, index) => {
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
  mergedSource.workflow = mergedWorkflow;
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

export function buildEditParametersPatch({
  execution,
  artifactInput,
  submittedPayload,
  submittedWorkflow,
}: {
  execution: TemporalTaskEditingExecutionContract;
  artifactInput?: Record<string, unknown> | undefined;
  submittedPayload: Record<string, unknown>;
  submittedWorkflow: Record<string, unknown>;
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
  const artifactBaseWorkflow = workflowRecord(artifactBase.parameters);
  const executionWorkflow = workflowRecord(executionParameters);
  const baseWorkflow = mergeRecordValues(
    artifactBase.fromAuthoritativeDraft
      ? executionWorkflow
      : artifactBaseWorkflow,
    workflowRecord(baseParameters),
  );
  const editWorkflow = { ...submittedWorkflow };

  // This field is not reconstructed into the edit form yet. Preserve the
  // existing value instead of letting the create-form default overwrite it.
  if ("proposeTasks" in editWorkflow) {
    delete editWorkflow.proposeTasks;
  }

  const mergedWorkflow: Record<string, unknown> = {
    ...baseWorkflow,
    ...editWorkflow,
    runtime: recordValue(editWorkflow.runtime),
    git: mergeRecordValues(
      recordValue(baseWorkflow.git),
      recordValue(editWorkflow.git),
    ),
    publish: mergeRecordValues(
      recordValue(baseWorkflow.publish),
      recordValue(editWorkflow.publish),
    ),
  };
  const mergedGit = recordValue(mergedWorkflow.git);
  delete mergedGit.startingBranch;
  delete mergedGit.targetBranch;
  if (Object.keys(mergedGit).length > 0) {
    mergedWorkflow.git = mergedGit;
  } else {
    delete mergedWorkflow.git;
  }

  const parametersPatch: Record<string, unknown> = {
    ...baseParameters,
    ...submittedPayload,
    workflow: mergedWorkflow,
  };
  const submittedRuntime = recordValue(editWorkflow.runtime);
  const submittedRuntimeMode = String(
    submittedRuntime.mode || submittedRuntime.targetRuntime || "",
  ).trim();
  const submittedRuntimeModel = String(submittedRuntime.model || "").trim();
  const submittedRuntimeEffort = String(submittedRuntime.effort || "").trim();
  const submittedRuntimeModelTier = Number.parseInt(
    String(submittedRuntime.modelTier || "").trim(),
    10,
  );
  const submittedRuntimeTierFallback = String(
    submittedRuntime.tierFallback || "",
  ).trim();
  const submittedRuntimeProfile = String(
    submittedRuntime.profileId ||
      submittedRuntime.providerProfile ||
      submittedRuntime.executionProfileRef ||
      "",
  ).trim();
  if (submittedRuntimeMode) {
    parametersPatch.targetRuntime = submittedRuntimeMode;
  }
  parametersPatch.model = submittedRuntimeModel || null;
  parametersPatch.requestedModel = submittedRuntimeModel || null;
  parametersPatch.resolvedModel = submittedRuntimeModel || null;
  parametersPatch.effort = submittedRuntimeEffort || null;
  parametersPatch.modelTier =
    Number.isInteger(submittedRuntimeModelTier) && submittedRuntimeModelTier >= 1
      ? submittedRuntimeModelTier
      : null;
  parametersPatch.tierFallback =
    submittedRuntimeTierFallback === "clamp" ||
    submittedRuntimeTierFallback === "strict"
      ? submittedRuntimeTierFallback
      : null;
  parametersPatch.profileId = submittedRuntimeProfile || null;
  delete parametersPatch.task;
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

const JIRA_ISSUE_KEY_PATTERN = /\b[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*-\d+\b/;

function extractJiraIssueKeyFromText(text: string): string {
  return String(text || "").match(JIRA_ISSUE_KEY_PATTERN)?.[0] || "";
}

function jiraIssueKeyFromValue(value: unknown): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return String(
      record.key ||
        record.issueKey ||
        record.issue_key ||
        record.jiraIssueKey ||
        "",
    ).trim();
  }
  return extractJiraIssueKeyFromText(String(value || ""));
}

function jiraIssuePickerValueFromKey(
  issueKey: string,
  issue?: Pick<JiraIssueDetail, "summary" | "url" | "issueKey"> | null,
): Record<string, unknown> {
  const key = String(issueKey || issue?.issueKey || "").trim();
  return {
    key,
    ...(issue?.summary ? { summary: issue.summary } : {}),
    ...(issue?.url ? { url: issue.url } : {}),
  };
}

function normalizeJiraIssuePickerValue(value: unknown): unknown {
  const issueKey = jiraIssueKeyFromValue(value);
  if (!issueKey) {
    return value;
  }
  return {
    ...(value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}),
    key: issueKey,
  };
}

function presetJiraIssueInputValuesFromIssue(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "inputs"> | null | undefined,
  currentValues: Record<string, unknown>,
  issue: JiraIssueDetail,
): { values: Record<string, unknown>; changedNames: string[] } {
  const issueKey = String(issue.issueKey || "").trim();
  if (!issueKey || !detail) {
    return { values: currentValues, changedNames: [] };
  }
  const values = { ...currentValues };
  const changedNames: string[] = [];
  const issueValue = jiraIssuePickerValueFromKey(issueKey, issue);

  for (const [name, rawSchema] of Object.entries(schemaProperties(detail.inputSchema))) {
    const fieldSchema = recordValue(rawSchema);
    const uiSchema = capabilityFieldUiSchema(detail.uiSchema, name);
    if (capabilityWidgetName(fieldSchema, uiSchema) !== "jira.issue-picker") {
      continue;
    }
    values[name] = {
      ...recordValue(values[name]),
      ...issueValue,
    };
    changedNames.push(name);
  }

  for (const definition of detail.inputs || []) {
    const name = String(definition.name || "").trim();
    const normalized = normalizeTemplateInputKey(name);
    if (!name || (normalized !== "jiraissuekey" && normalized !== "issuekey")) {
      continue;
    }
    values[name] = issueKey;
    changedNames.push(name);
  }

  return { values, changedNames };
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
    toolInputs: "{}",
    toolInputValues: {},
    toolInputErrors: {},
    toolJsonMode: false,
    pentestScopeDraft: createPentestScopeDraftState(),
    skillId: "",
    skillArgs: "",
    skillInputContractDigest: "",
    skillInputContractNotice: null,
    explicitRequiredCapabilities: [],
    runtimeMode: "",
    runtimeModel: "",
    runtimeEffort: "",
    runtimeProviderProfile: "",
    runtimeModelTier: "",
    runtimeTierFallback: "clamp",
    presetKey: "",
    presetInputValues: {},
    presetInputErrors: {},
    presetDetail: null,
    submitExpansion: null,
    presetMessage: null,
    templateStepId: "",
    templateInstructions: "",
    inputAttachments: [],
    templateAttachments: [],
    ...overrides,
  };
}

const RUNTIME_COMMAND_TOKEN_PATTERN =
  /^\/([A-Za-z][A-Za-z0-9_-]*(?:(?::|\.)[A-Za-z0-9_-]+)?)(?:\s+(.*))?$/;

function firstLineAndBody(value: string): { firstLine: string; body: string } {
  const lineEnd = value.indexOf("\n");
  if (lineEnd === -1) {
    return { firstLine: value, body: "" };
  }
  return {
    firstLine: value.slice(0, lineEnd),
    body: value.slice(lineEnd + 1),
  };
}

function looksLikeOrdinarySlashPath(firstLine: string): boolean {
  const token = firstLine.split(/\s+/, 1)[0] || "";
  if (!token.startsWith("/")) {
    return false;
  }
  const withoutSlash = token.slice(1);
  return withoutSlash.includes("/") || withoutSlash.startsWith(".");
}

function runtimeCommandMatchesInstructions(
  stored: Record<string, unknown> | undefined,
  rawInstructions: string,
  runtime: string,
): boolean {
  if (!stored) {
    return false;
  }
  const { firstLine } = firstLineAndBody(rawInstructions);
  const rawCommand = String(stored.rawCommand || "");
  const targetRuntime = String(stored.targetRuntime || runtime || "");
  return Boolean(rawCommand) && rawCommand === firstLine && targetRuntime === runtime;
}

function deriveRuntimeCommandPreview({
  instructions,
  runtime,
  sourcePath,
  config,
  storedRuntimeCommand,
}: {
  instructions: string;
  runtime: string;
  sourcePath: string;
  config: RuntimeCommandPreviewConfig | undefined;
  storedRuntimeCommand: Record<string, unknown> | undefined;
}): RuntimeCommandPreviewState | null {
  if (!instructions || !config) {
    return null;
  }
  if (instructions.startsWith("\\/")) {
    const { firstLine } = firstLineAndBody(instructions);
    return {
      sourcePath,
      rawInstructions: instructions,
      rawCommand: firstLine,
      command: "",
      args: "",
      instructionBody: instructions.slice(1),
      detectionStatus: "escaped",
      hintStatus: "opaque",
      recognitionMode: "escaped_literal",
      requiresRuntimeRecognition: false,
      messageSeverity: "neutral",
      label: `Literal text: ${firstLine.slice(1)}`,
      description: "Escaped leading slash will be submitted as text.",
      source: runtimeCommandMatchesInstructions(
        storedRuntimeCommand,
        instructions,
        runtime,
      )
        ? "snapshot"
        : "derived",
    };
  }
  if (!instructions.startsWith("/")) {
    return null;
  }
  const { firstLine, body } = firstLineAndBody(instructions);
  if (looksLikeOrdinarySlashPath(firstLine)) {
    return {
      sourcePath,
      rawInstructions: instructions,
      rawCommand: firstLine,
      command: "",
      args: "",
      instructionBody: instructions,
      detectionStatus: "malformed",
      hintStatus: "opaque",
      recognitionMode: "escaped_literal",
      requiresRuntimeRecognition: false,
      messageSeverity: "neutral",
      label: "Literal slash text",
      description: "Path-like slash text will be submitted as written.",
      source: "derived",
    };
  }
  const match = RUNTIME_COMMAND_TOKEN_PATTERN.exec(firstLine);
  if (!match) {
    return {
      sourcePath,
      rawInstructions: instructions,
      rawCommand: firstLine,
      command: "",
      args: "",
      instructionBody: instructions,
      detectionStatus: "malformed",
      hintStatus: "opaque",
      recognitionMode: "escaped_literal",
      requiresRuntimeRecognition: false,
      messageSeverity: "neutral",
      label: "Literal slash text",
      description: "Slash text does not match runtime command syntax.",
      source: "derived",
    };
  }
  const command = match[1] || "";
  const args = command.includes(".") ? "" : match[2] || "";
  const hint = config.knownRuntimeCommandHints?.[command];
  const supportsPassthrough = Boolean(
    config.runtimes?.[runtime]?.slashCommandPassthrough,
  );
  const source = runtimeCommandMatchesInstructions(
    storedRuntimeCommand,
    instructions,
    runtime,
  )
    ? "snapshot"
    : "derived";
  const versionWarnings =
    source === "snapshot"
      ? buildRuntimeCommandVersionWarnings(storedRuntimeCommand, {
          hintCatalogVersion: config.hintCatalogVersion,
        })
      : [];
  const warningSuffix =
    versionWarnings.length > 0 ? ` ${versionWarnings.join(" ")}` : "";
  if (!supportsPassthrough) {
    return {
      sourcePath,
      rawInstructions: instructions,
      rawCommand: firstLine,
      command,
      args,
      instructionBody: body,
      detectionStatus: "detected",
      hintStatus: hint ? "hinted" : "opaque",
      recognitionMode: "runtime_does_not_support_slash_commands",
      requiresRuntimeRecognition: false,
      messageSeverity: "warning",
      label: `Unsupported runtime command: /${command}`,
      description:
        "This runtime does not pass through slash commands. Choose a slash-command capable runtime or escape the slash for literal text." +
        warningSuffix,
      source,
    };
  }
  return {
    sourcePath,
    rawInstructions: instructions,
    rawCommand: firstLine,
    command,
    args,
    instructionBody: body,
    detectionStatus: "detected",
    hintStatus: hint ? "hinted" : "opaque",
    recognitionMode: hint ? "hinted_runtime_passthrough" : "runtime_passthrough",
    requiresRuntimeRecognition: true,
    messageSeverity: "info",
    label: `Runtime command: /${command}`,
    description:
      (hint?.description ||
        "Pass-through runtime command. No local hint is available; provider behavior will decide it.") +
      warningSuffix,
    source,
  };
}

function RuntimeCommandPreviewMessage({
  preview,
}: {
  preview: RuntimeCommandPreviewState | null;
}): ReactElement | null {
  if (!preview) {
    return null;
  }
  return (
    <p
      className={`runtime-command-preview runtime-command-preview--${preview.messageSeverity}`}
      data-runtime-command-preview={preview.recognitionMode}
      role={preview.messageSeverity === "warning" ? "alert" : "status"}
      aria-live="polite"
    >
      <span className="runtime-command-preview-label">{preview.label}</span>
      <span className="runtime-command-preview-description">
        {preview.description}
      </span>
    </p>
  );
}

function presetInputValuesFromPayload(
  inputValues: Record<string, unknown> | undefined,
): Record<string, unknown> {
  return Object.entries(inputValues || {}).reduce<Record<string, unknown>>(
    (values, [key, value]) => {
      if (value && typeof value === "object") {
        values[key] = structuredClone(value);
      } else if (typeof value === "boolean") {
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
  inputValues: Record<string, unknown>,
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

    const toolInputs =
      step.stepType === "tool"
        ? (toolPayload.inputs || step.skillArgs || {})
        : {};

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
      skillInputContractDigest:
        step.stepType === "skill" ? step.skillInputContractDigest : "",
      explicitRequiredCapabilities: mergeCapabilities(
        step.skillRequiredCapabilities,
      ),
      runtimeMode: step.runtime?.mode || "",
      runtimeModel: step.runtime?.model || "",
      runtimeEffort: step.runtime?.effort || "",
      runtimeProviderProfile:
        step.runtime?.profileId || step.runtime?.providerProfile || "",
      runtimeModelTier:
        step.runtime?.modelTier != null ? String(step.runtime.modelTier) : "",
      runtimeTierFallback:
        step.runtime?.tierFallback === "strict" ? "strict" : "clamp",
      toolId:
        step.stepType === "tool"
          ? String(toolPayload.id || toolPayload.name || step.skillId || "").trim()
          : "",
      toolInputs:
        step.stepType === "tool"
          ? JSON.stringify(toolInputs, null, 2)
          : "{}",
      toolInputValues:
        step.stepType === "tool" && toolInputs && typeof toolInputs === "object"
          ? structuredClone(toolInputs as Record<string, unknown>)
          : {},
      presetKey,
      presetInputValues:
        step.stepType === "preset"
          ? presetInputValuesFromPayload(presetPayload.inputs)
          : {},
      presetInputErrors: {},
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
      ...(step.runtimeCommand && Object.keys(step.runtimeCommand).length > 0
        ? { runtimeCommand: step.runtimeCommand }
        : {}),
    });
  });
}

function hasAdvancedStepOptionValues(steps: StepState[]): boolean {
  return steps.some(
    (step) =>
      Boolean(step.skillArgs.trim()) ||
      Boolean((step.runtimeMode || "").trim()) ||
      Boolean((step.runtimeModel || "").trim()) ||
      Boolean((step.runtimeEffort || "").trim()) ||
      Boolean((step.runtimeProviderProfile || "").trim()) ||
      Boolean((step.runtimeModelTier || "").trim()) ||
      step.runtimeTierFallback === "strict",
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

function skillPublishMetadataDeclaresAuto(
  publish: Record<string, unknown> | null | undefined,
): boolean {
  if (!publish) {
    return false;
  }
  return (
    String(publish.mode || "").trim().toLowerCase() === "auto" &&
    String(publish.owner || "").trim().toLowerCase() === "agent" &&
    publish.requiresEvidence === true
  );
}

function skillSideEffectMetadataDeclaresAgentOwned(
  sideEffect: Record<string, unknown> | null | undefined,
): boolean {
  if (!sideEffect) {
    return false;
  }
  return (
    String(sideEffect.owner || "").trim().toLowerCase() === "agent" &&
    String(sideEffect.kind || "").trim().length > 0
  );
}

function isSelfManagedPublishSkill(
  skillId: string,
  detail?: SkillCapabilityDetail | null,
): boolean {
  if (detail?.publish) {
    return skillPublishMetadataDeclaresAuto(detail.publish);
  }
  return SELF_MANAGED_PUBLISH_SKILLS.has(skillId.trim().toLowerCase());
}

function isPublishDisabledSkill(skillId: string): boolean {
  return PUBLISH_DISABLED_SKILLS.has(skillId.trim().toLowerCase());
}

function isRepositoryPublishDisabledSkill(
  skillId: string,
  detail?: SkillCapabilityDetail | null,
): boolean {
  if (isPublishDisabledSkill(skillId)) {
    return true;
  }
  if (detail?.sideEffect) {
    return skillSideEffectMetadataDeclaresAgentOwned(detail.sideEffect);
  }
  return SIDE_EFFECT_PUBLISH_DISABLED_SKILLS.has(skillId.trim().toLowerCase());
}

function resolveEffectivePublishSkillId(
  primarySkillId: string,
  appliedTemplates: AppliedTemplateState[],
): string {
  for (const template of [...appliedTemplates].reverse()) {
    const slug = String(template.slug || "").trim();
    if (
      slug &&
      (isSelfManagedPublishSkill(slug) ||
        isRepositoryPublishDisabledSkill(slug))
    ) {
      return slug;
    }
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
      .map((step) => (step.id || "").trim())
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

function templateCapabilitiesForStep(
  appliedTemplates: AppliedTemplateState[],
  step: StepState,
): string[] {
  const stepId = (step.id || "").trim();
  return activeAppliedTemplatesForSteps(appliedTemplates, [step]).flatMap(
    (template) => {
      const stepIds = Array.isArray(template.stepIds)
        ? template.stepIds
            .map((candidate) => String(candidate || "").trim())
            .filter(Boolean)
        : [];
      if (stepIds.length > 0 && (!stepId || !stepIds.includes(stepId))) {
        return [];
      }
      return template.capabilities || [];
    },
  );
}

function authoredPresetsFromAppliedTemplates(
  appliedTemplates: AppliedTemplateState[],
): Array<Record<string, unknown>> {
  return appliedTemplates.flatMap((template) =>
    Array.isArray(template.authoredPresets) ? template.authoredPresets : [],
  );
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

function mergeCapabilities(...groups: Array<string[] | undefined | null>): string[] {
  return Array.from(
    new Set(
      groups
        .flatMap((group) => group || [])
        .map((item) => String(item || "").trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

// MM-936: Capabilities Plus Button.
//
// The step authoring surface presents a single "Add to step" affordance that
// unifies image attachments and required capabilities. `inputAttachments` and
// `requiredCapabilities` remain separate backend concepts; only the authoring
// surface is unified. This frontend capability registry controls how known
// capability tokens are labelled in the menu and chip row. Backend
// normalization of the `requiredCapabilities` contract remains authoritative.
type CapabilityGroup = "Code" | "Integrations" | "Runtime";

interface CapabilityCatalogEntry {
  token: string;
  label: string;
  shortLabel: string;
  description: string;
  icon: string;
  group: CapabilityGroup;
  common?: boolean;
}

export const CAPABILITY_CATALOG: Record<string, CapabilityCatalogEntry> = {
  git: {
    token: "git",
    label: "Git repository",
    shortLabel: "Git",
    description: "Prepare a repository checkout for this step.",
    icon: "🌱",
    group: "Code",
    common: true,
  },
  gh: {
    token: "gh",
    label: "GitHub CLI / PRs",
    shortLabel: "GitHub",
    description:
      "Allow GitHub repository and PR operations through the runtime path.",
    icon: "🐙",
    group: "Integrations",
    common: true,
  },
  jira: {
    token: "jira",
    label: "Jira",
    shortLabel: "Jira",
    description:
      "Allow Jira issue access through the trusted integration path.",
    icon: "📋",
    group: "Integrations",
  },
  docker: {
    token: "docker",
    label: "Docker",
    shortLabel: "Docker",
    description: "Allow container builds and Docker operations for this step.",
    icon: "🐳",
    group: "Runtime",
  },
  codex_cli: {
    token: "codex_cli",
    label: "Codex CLI",
    shortLabel: "Codex",
    description: "Run this step through the Codex CLI runtime.",
    icon: "✨",
    group: "Runtime",
  },
};

// Capability tokens offered, in order, by the "Add to step" menu.
const CAPABILITY_MENU_TOKENS: string[] = [
  "git",
  "gh",
  "jira",
  "docker",
  "codex_cli",
];

function normalizeCapabilityToken(value: string): string {
  return value.trim().toLowerCase();
}

function capabilityCatalogEntry(token: string): CapabilityCatalogEntry {
  const normalized = normalizeCapabilityToken(token);
  const known = CAPABILITY_CATALOG[normalized];
  if (known) {
    return known;
  }
  return {
    token: normalized,
    label: normalized,
    shortLabel: normalized,
    description: "Custom capability required before this step launches.",
    icon: "•",
    group: "Runtime",
  };
}

export type CapabilitySourceKind =
  | "explicit"
  | "preset"
  | "skill"
  | "tool"
  | "runtime"
  | "publish"
  | "template";

const CAPABILITY_SOURCE_LABELS: Record<CapabilitySourceKind, string> = {
  explicit: "added to this step",
  preset: "from preset",
  skill: "from skill",
  tool: "from tool",
  runtime: "from runtime",
  publish: "from publish mode",
  template: "from template",
};

export interface CapabilityContribution {
  token: string;
  sourceKind: CapabilitySourceKind;
  sourceLabel: string;
  removable: boolean;
}

export type CapabilityReadinessState = "requested" | "required_before_launch";

export interface CapabilityReadiness {
  state: CapabilityReadinessState;
  label: string;
  description: string;
  sourceLabels: string[];
  grantsAccess: false;
}

export interface StepCapabilityChip {
  token: string;
  label: string;
  description: string;
  icon: string;
  sources: CapabilityContribution[];
  readiness: CapabilityReadiness;
  removable: boolean;
}

interface BuildCapabilityChipsArgs {
  explicit?: string[] | null | undefined;
  skill?: string[] | null | undefined;
  tool?: string[] | null | undefined;
  generatedSkill?: string[] | null | undefined;
  generatedTool?: string[] | null | undefined;
  preset?: string[] | null | undefined;
  runtime?: string[] | null | undefined;
  publish?: string[] | null | undefined;
  template?: string[] | null | undefined;
}

// Compute the display chips for a step's required capabilities. Sources are
// additive and backend normalization is authoritative, so a capability chip is
// only removable when the sole contribution is the explicit step authoring
// field. Derived contributions (preset, skill, tool, runtime, publish mode,
// template) keep the chip non-removable and carry provenance plus readiness
// semantics for display without implying the UI grants backend access.
export function buildCapabilityChips(
  args: BuildCapabilityChipsArgs,
): StepCapabilityChip[] {
  const contributionGroups: Array<{
    tokens: string[] | null | undefined;
    sourceKind: CapabilitySourceKind;
  }> = [
    { tokens: args.explicit, sourceKind: "explicit" },
    { tokens: args.skill, sourceKind: "skill" },
    { tokens: args.tool, sourceKind: "tool" },
    { tokens: args.generatedSkill, sourceKind: "skill" },
    { tokens: args.generatedTool, sourceKind: "tool" },
    { tokens: args.preset, sourceKind: "preset" },
    { tokens: args.template, sourceKind: "template" },
    { tokens: args.runtime, sourceKind: "runtime" },
    { tokens: args.publish, sourceKind: "publish" },
  ];

  const chipsByToken = new Map<string, StepCapabilityChip>();
  const order: string[] = [];

  for (const group of contributionGroups) {
    for (const rawToken of group.tokens || []) {
      const token = normalizeCapabilityToken(String(rawToken || ""));
      if (!token) {
        continue;
      }
      let chip = chipsByToken.get(token);
      if (!chip) {
        const entry = capabilityCatalogEntry(token);
        chip = {
          token,
          label: entry.label,
          description: entry.description,
          icon: entry.icon,
          sources: [],
          readiness: explicitCapabilityReadiness([]),
          removable: false,
        };
        chipsByToken.set(token, chip);
        order.push(token);
      }
      if (chip.sources.some((source) => source.sourceKind === group.sourceKind)) {
        continue;
      }
      chip.sources.push({
        token,
        sourceKind: group.sourceKind,
        sourceLabel: CAPABILITY_SOURCE_LABELS[group.sourceKind],
        removable: group.sourceKind === "explicit",
      });
    }
  }

  return order.map((token) => {
    const chip = chipsByToken.get(token) as StepCapabilityChip;
    const removable =
      chip.sources.length > 0 &&
      chip.sources.every((source) => source.sourceKind === "explicit");
    const derivedSourceLabels = chip.sources
      .filter((source) => source.sourceKind !== "explicit")
      .map((source) => source.sourceLabel);
    return {
      ...chip,
      readiness:
        derivedSourceLabels.length > 0
          ? derivedCapabilityReadiness(derivedSourceLabels)
          : explicitCapabilityReadiness(
              chip.sources.map((source) => source.sourceLabel),
            ),
      removable,
    };
  });
}

function explicitCapabilityReadiness(sourceLabels: string[]): CapabilityReadiness {
  return {
    state: "requested",
    label: "Requested capability",
    description:
      "This capability token is requested for the step. Runtime policy and credentials still decide whether access is available.",
    sourceLabels,
    grantsAccess: false,
  };
}

function derivedCapabilityReadiness(sourceLabels: string[]): CapabilityReadiness {
  return {
    state: "required_before_launch",
    label: "Required before launch",
    description:
      "This derived capability is required by step configuration before launch. The chip records the requirement; it does not grant access by itself.",
    sourceLabels,
    grantsAccess: false,
  };
}

// The provenance label rendered on a derived (non-removable) chip, e.g.
// "from preset". Returns null for chips whose only source is explicit.
export function capabilityChipProvenanceLabel(
  chip: StepCapabilityChip,
): string | null {
  const derived = chip.sources.find(
    (source) => source.sourceKind !== "explicit",
  );
  return derived ? derived.sourceLabel : null;
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

function isEmptyStepStateEntry(step: StepState | null | undefined): boolean {
  if (!step) {
    return true;
  }
  return (
    !step.id.trim() &&
    !step.instructions.trim() &&
    !step.toolId.trim() &&
    (!step.toolInputs.trim() || step.toolInputs.trim() === "{}") &&
    !step.skillId.trim() &&
    !step.skillArgs.trim() &&
    step.explicitRequiredCapabilities.length === 0 &&
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
): PresetStepSkill | null {
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
): PresetStepSkill | null {
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
  requiredCapabilities: string[] = [],
): PresetStepSkill | null {
  if (step?.stepType !== "tool") {
    return null;
  }
  const toolId = step.toolId.trim();
  if (!toolId) {
    return null;
  }
  const caps = mergeCapabilities(
    requiredCapabilities,
    step.explicitRequiredCapabilities,
  );
  return {
    type: "tool",
    id: toolId,
    inputs,
    ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
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

// Human-friendly display names for agent runtimes. Raw runtime ids such as
// `claude_code` should never surface to operators in the UI.
const RUNTIME_DISPLAY_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex_cli: "Codex CLI",
  codex_cloud: "Codex Cloud",
  omnigent: "Codex via Omnigent",
};

function formatRuntimeLabel(runtimeId: string): string {
  const known = RUNTIME_DISPLAY_LABELS[runtimeId];
  if (known) {
    return known;
  }
  const formatted = runtimeId
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) =>
      word.toLowerCase() === "cli"
        ? "CLI"
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
  return formatted || runtimeId;
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
  stepRuntimeModes: string[];
  publishMode: string;
  repositoryBacked: boolean;
  taskSkillRequiredCapabilities: string[];
  stepSkillRequiredCapabilities: string[];
  toolRequiredCapabilities: string[];
  explicitStepCapabilities: string[];
  templateCapabilities: string[];
}): string[] {
  return Array.from(
    new Set(
      [
        args.runtimeMode,
        ...args.stepRuntimeModes,
        ...(args.repositoryBacked ? ["git"] : []),
        ...(args.publishMode === "pr" ? ["gh"] : []),
        ...args.taskSkillRequiredCapabilities,
        ...args.stepSkillRequiredCapabilities,
        ...args.toolRequiredCapabilities,
        ...args.explicitStepCapabilities,
        ...args.templateCapabilities
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean),
      ].filter(Boolean),
    ),
  );
}

const PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE = "pr_with_merge_automation";

function normalizePublishModeSelection(value: string | null | undefined): string {
  return String(value || "").trim().toLowerCase();
}

function normalizePublishModeForSubmit(value: string | null | undefined): string {
  const normalized = normalizePublishModeSelection(value);
  return normalized === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE
    ? "pr"
    : normalized;
}

function isMergeAutomationPublishMode(value: string | null | undefined): boolean {
  return normalizePublishModeSelection(value) === PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE;
}

function templateEnumOptionLabel(
  definition: PresetInputDefinition,
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
  const source =
    nonEmptyRecordValue(step.source) ||
    compactSourceFromPresetProvenance(nonEmptyRecordValue(step.presetProvenance));
  const normalizedSource = source ? { ...source } : undefined;
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
    toolId: isToolStep ? String(tool.id || tool.name || "").trim() : "",
    toolInputs: isToolStep ? JSON.stringify(inlineInputs, null, 2) : "{}",
    toolInputValues: isToolStep
      ? (inlineInputs as Record<string, unknown>)
      : {},
    skillId: isToolStep ? "" : String(tool.name || tool.id || "").trim(),
    skillArgs: isToolStep ? "" : stringifySkillArgs(inlineInputs),
    // Capabilities declared by an expanded preset's generated tool/skill are
    // derived (non-removable) chips computed from generatedTool/generatedSkill,
    // so the explicit authoring field starts empty for expanded steps.
    explicitRequiredCapabilities: [],
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
  definition: PresetInputDefinition,
): boolean {
  return isVisiblePresetInputName(presetSlug, definition.name);
}

function isVisiblePresetInputName(
  presetSlug: string | undefined,
  rawName: string,
): boolean {
  if (isFeatureRequestInputKey(rawName)) {
    return false;
  }
  const hiddenKeys = presetSlug
    ? HIDDEN_PRESET_INPUT_KEYS[presetSlug]
    : undefined;
  if (hiddenKeys?.has(normalizeTemplateInputKey(rawName))) {
    return false;
  }
  return true;
}

function schemaProperties(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return recordValue(schema?.properties);
}

function schemaRequired(schema: Record<string, unknown> | undefined): Set<string> {
  return new Set(
    (Array.isArray(schema?.required) ? schema.required : [])
      .map((item) => String(item || "").trim())
      .filter(Boolean),
  );
}

function capabilityFieldLabel(name: string, schema: Record<string, unknown>): string {
  return String(schema.title || name)
    .trim()
    .replaceAll("_", " ");
}

function capabilityFieldUiSchema(
  uiSchema: Record<string, unknown> | undefined,
  name: string,
): Record<string, unknown> {
  return recordValue(uiSchema?.[name]);
}

function capabilityFieldVisible(
  uiSchema: Record<string, unknown>,
  values: Record<string, unknown>,
  defaults?: Record<string, unknown>,
): boolean {
  const visibleWhen = recordValue(uiSchema["visibleWhen"]);
  if (Object.keys(visibleWhen).length === 0) {
    return true;
  }
  const field = String(visibleWhen["field"] ?? "").trim();
  if (!field) {
    return true;
  }
  const actual =
    values && values[field] !== undefined
      ? values[field]
      : safeCapabilityDefault(defaults, field);
  if (Object.prototype.hasOwnProperty.call(visibleWhen, "equals")) {
    return actual === visibleWhen["equals"];
  }
  if (Array.isArray(visibleWhen["oneOf"])) {
    return visibleWhen["oneOf"].some((value) => value === actual);
  }
  return true;
}

function capabilityWidgetName(
  schema: Record<string, unknown>,
  uiSchema: Record<string, unknown>,
): string {
  const widget = String(
    uiSchema.widget || schema["x-moonmind-widget"] || schema.format || schema.type || "text",
  )
    .trim()
    .toLowerCase();
  const aliases: Record<string, string> = {
    checkbox: "boolean",
    editor: "textarea",
    "github.repository-picker": "repository-picker",
    repository: "repository-picker",
    repo: "repository-picker",
    "repo-picker": "repository-picker",
    "github.branch-picker": "branch",
  };
  return aliases[widget] || widget;
}

function schemaChoiceOptions(schema: Record<string, unknown>): Array<{
  label: string;
  value: unknown;
}> {
  const choices = Array.isArray(schema.oneOf)
    ? schema.oneOf
    : Array.isArray(schema.anyOf)
      ? schema.anyOf
      : [];
  return choices
    .map((choice) => {
      const choiceSchema = recordValue(choice);
      const value = choiceSchema.const ?? (Array.isArray(choiceSchema.enum) ? choiceSchema.enum[0] : undefined);
      if (value === undefined) {
        return null;
      }
      return {
        label: String(choiceSchema.title || value),
        value,
      };
    })
    .filter((choice): choice is { label: string; value: unknown } => Boolean(choice));
}

function textInputTypeForSchema(schema: Record<string, unknown>): string {
  const format = String(schema.format || "").trim().toLowerCase();
  if (format === "date-time") {
    return "datetime-local";
  }
  if (
    format === "email" ||
    format === "uri" ||
    format === "url" ||
    format === "date"
  ) {
    return format === "uri" ? "url" : format;
  }
  if (schema.type === "number" || schema.type === "integer") {
    return "number";
  }
  return "text";
}

function complexCapabilityTextValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function parseComplexCapabilityValue(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return text;
  }
}

function unsupportedCapabilityWidget(
  widget: string,
  schema: Record<string, unknown>,
): boolean {
  if (Array.isArray(schema.enum) || schemaChoiceOptions(schema).length > 0) {
    return false;
  }
  const supported = new Set([
    "array",
    "boolean",
    "branch",
    "date",
    "date-time",
    "email",
    "integer",
    "json",
    "multi-select",
    "github.issue-picker",
    "jira.issue-picker",
    "markdown",
    "number",
    "object",
    "repository",
    "repository-picker",
    "repo",
    "repo-picker",
    "string",
    "text",
    "textarea",
    "uri",
    "url",
  ]);
  return !supported.has(widget);
}

function containsSecretLikeCapabilityValue(value: unknown): boolean {
  if (value && typeof value === "object") {
    if (Array.isArray(value)) {
      return value.some((item) => containsSecretLikeCapabilityValue(item));
    }
    return Object.entries(value as Record<string, unknown>).some(
      ([key, nested]) =>
        /(authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key)/i.test(
          key,
        ) || containsSecretLikeCapabilityValue(nested),
    );
  }
  return /(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)/i.test(
    String(value || ""),
  );
}

function safeCapabilityDefault(
  defaults: Record<string, unknown> | undefined,
  name: string,
): unknown {
  const value = defaults?.[name];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (containsSecretLikeCapabilityValue(value)) {
    return undefined;
  }
  return structuredClone(value);
}

function capabilityInputValue(
  values: Record<string, unknown>,
  defaults: Record<string, unknown> | undefined,
  name: string,
): unknown {
  return values[name] !== undefined ? values[name] : safeCapabilityDefault(defaults, name);
}

function capabilityInputTextValue(
  values: Record<string, unknown>,
  defaults: Record<string, unknown> | undefined,
  name: string,
): string {
  const value = capabilityInputValue(values, defaults, name);
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    if (record.repository && record.number) {
      return `${String(record.repository)}#${String(record.number)}`;
    }
    return String(record.key || "");
  }
  return value === undefined || value === null ? "" : String(value);
}

function capabilityInputStringArrayValue(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter(Boolean);
}

function normalizeGitHubIssueInput(
  rawValue: string,
  defaultRepository: string | undefined,
): Record<string, unknown> {
  const value = rawValue.trim();
  if (!value) {
    return {};
  }
  const urlMatch = value.match(
    /^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/issues\/(\d+)(?:[/?#].*)?$/i,
  );
  if (urlMatch) {
    return {
      repository: urlMatch[1],
      number: Number(urlMatch[2]),
      url: `https://github.com/${urlMatch[1]}/issues/${urlMatch[2]}`,
    };
  }
  const qualifiedMatch = value.match(
    /^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)$/i,
  );
  if (qualifiedMatch) {
    return { repository: qualifiedMatch[1], number: Number(qualifiedMatch[2]) };
  }
  const shorthandMatch = value.match(/^#(\d+)$/);
  const normalizedRepository = String(defaultRepository || "").trim();
  if (shorthandMatch && OWNER_REPO_PATTERN.test(normalizedRepository)) {
    return { repository: normalizedRepository, number: Number(shorthandMatch[1]) };
  }
  return { repository: normalizedRepository, number: Number.NaN, raw: value };
}

function issuePickerTextValue(
  value: unknown,
  provider: "jira" | "github",
): string {
  const issueValue = recordValue(value);
  if (provider === "github") {
    const repository = String(issueValue.repository || "").trim();
    const number = String(issueValue.number || "").trim();
    if (repository && number) {
      return `${repository}#${number}`;
    }
    return String(issueValue.raw || "");
  }
  return String(issueValue.key || "");
}

function schemaContractHasFields(detail: Pick<PresetDetail, "inputSchema"> | null | undefined): boolean {
  return Object.keys(schemaProperties(detail?.inputSchema)).length > 0;
}

function resolveSchemaCapabilityValues(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "defaults">,
  explicitValues: Record<string, unknown>,
  featureRequestOverride?: string,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  const required = schemaRequired(detail.inputSchema);
  const instructionFeatureRequest = String(featureRequestOverride || "").trim();
  const instructionJiraIssueKey =
    extractJiraIssueKeyFromText(instructionFeatureRequest);
  for (const [name, rawSchema] of Object.entries(schemaProperties(detail.inputSchema))) {
    const fieldSchema = recordValue(rawSchema);
    const uiSchema = capabilityFieldUiSchema(detail.uiSchema, name);
    const widget = capabilityWidgetName(fieldSchema, uiSchema);
    const rawExplicit = explicitValues[name];
    let explicit =
      isFeatureRequestInputKey(name) && instructionFeatureRequest
        ? instructionFeatureRequest
        : rawExplicit;
    if (
      explicit === undefined &&
      widget === "jira.issue-picker" &&
      instructionJiraIssueKey
    ) {
      explicit = jiraIssuePickerValueFromKey(instructionJiraIssueKey);
    } else if (explicit !== undefined && widget === "jira.issue-picker") {
      explicit = normalizeJiraIssuePickerValue(explicit);
    }
    const fallback =
      safeCapabilityDefault(detail.defaults, name) ??
      safeCapabilityDefault(fieldSchema, "default");
    if (explicit !== undefined) {
      if (
        explicit === "" &&
        !required.has(name) &&
        (fieldSchema.type === "number" || fieldSchema.type === "integer")
      ) {
        continue;
      }
      values[name] = explicit;
    } else if (fallback !== undefined) {
      values[name] = fallback;
    }
  }
  return values;
}

function resolvedPresetInputValues(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "defaults">,
  explicitValues: Record<string, unknown>,
  featureRequestOverride?: string,
): Record<string, unknown> {
  return schemaContractHasFields(detail)
    ? resolveSchemaCapabilityValues(detail, explicitValues, featureRequestOverride)
    : explicitValues;
}

function validateSchemaCapabilityValues(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "defaults">,
  values: Record<string, unknown>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  const required = schemaRequired(detail.inputSchema);
  const properties = schemaProperties(detail.inputSchema);
  for (const [name, rawSchema] of Object.entries(properties)) {
    const fieldSchema = recordValue(rawSchema);
    const uiSchema = capabilityFieldUiSchema(detail.uiSchema, name);
    if (!capabilityFieldVisible(uiSchema, values, detail.defaults)) {
      continue;
    }
    const widget = capabilityWidgetName(fieldSchema, uiSchema);
    const value = values[name];
    if (widget === "jira.issue-picker") {
      const issueValue = recordValue(value);
      if (required.has(name) && !String(issueValue.key || "").trim()) {
        errors[name] = "A Jira issue is required.";
      }
      continue;
    }
    if (widget === "github.issue-picker") {
      const issueValue = recordValue(value);
      const repository = String(issueValue.repository || "").trim();
      const number = Number(issueValue.number);
      if (required.has(name) && (!OWNER_REPO_PATTERN.test(repository) || !Number.isInteger(number) || number <= 0)) {
        errors[name] = "A GitHub issue is required.";
      }
      continue;
    }
    if (required.has(name) && (value === undefined || value === null || value === "")) {
      errors[name] = `${capabilityFieldLabel(name, fieldSchema)} is required.`;
      continue;
    }
    if (value === undefined || value === null || value === "") {
      continue;
    }
    if (fieldSchema.type === "array") {
      if (!Array.isArray(value)) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be a JSON array.`;
        continue;
      }
      const itemSchema = recordValue(fieldSchema.items);
      if (Array.isArray(itemSchema.enum)) {
        const allowed = new Set(itemSchema.enum.map((item) => String(item)));
        const invalid = value.some((item) => !allowed.has(String(item)));
        if (invalid) {
          errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must use available options.`;
          continue;
        }
      }
    }
    if (Array.isArray(fieldSchema.enum)) {
      const allowed = new Set(fieldSchema.enum.map((item) => String(item)));
      if (!allowed.has(String(value))) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must use an available option.`;
        continue;
      }
    }
    if (fieldSchema.type === "number" || fieldSchema.type === "integer") {
      const numericValue =
        typeof value === "number" ? value : Number(String(value).trim());
      if (!Number.isFinite(numericValue)) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be a number.`;
        continue;
      }
      if (fieldSchema.type === "integer" && !Number.isInteger(numericValue)) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be a whole number.`;
        continue;
      }
      const minimum =
        fieldSchema.minimum !== undefined && fieldSchema.minimum !== null
          ? Number(fieldSchema.minimum)
          : NaN;
      const maximum =
        fieldSchema.maximum !== undefined && fieldSchema.maximum !== null
          ? Number(fieldSchema.maximum)
          : NaN;
      if (Number.isFinite(minimum) && numericValue < minimum) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be at least ${minimum}.`;
        continue;
      }
      if (Number.isFinite(maximum) && numericValue > maximum) {
        errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be at most ${maximum}.`;
      }
    }
    if (
      fieldSchema.type === "object" &&
      (typeof value !== "object" || Array.isArray(value))
    ) {
      errors[name] = `${capabilityFieldLabel(name, fieldSchema)} must be a JSON object.`;
    }
  }
  return errors;
}

function schemaSkillInputs(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "defaults"> | null | undefined,
  explicitValues: Record<string, unknown>,
): {
  values: Record<string, unknown>;
  errors: Record<string, string>;
} {
  if (!detail || !schemaContractHasFields(detail)) {
    return { values: {}, errors: {} };
  }
  const values = resolveSchemaCapabilityValues(detail, explicitValues);
  return {
    values,
    errors: validateSchemaCapabilityValues(detail, values),
  };
}

function skillInputContractPayload(
  detail: SkillCapabilityDetail | null | undefined,
): Record<string, unknown> {
  if (!detail) {
    return {};
  }
  return {
    ...(detail.inputSchema ? { inputSchema: detail.inputSchema } : {}),
    ...(detail.uiSchema ? { uiSchema: detail.uiSchema } : {}),
    ...(detail.defaults ? { defaults: detail.defaults } : {}),
    ...(detail.contractDigest
      ? { inputContractDigest: detail.contractDigest }
      : {}),
    ...(detail.contentDigest ? { contentDigest: detail.contentDigest } : {}),
    ...(detail.contentRef ? { contentRef: detail.contentRef } : {}),
  };
}

function mergeSkillArgsWithSchemaInputs(
  skillArgs: Record<string, unknown>,
  schemaInputs: Record<string, unknown>,
): Record<string, unknown> {
  return { ...skillArgs, ...schemaInputs };
}

function skillContractDigestNotice(
  savedDigest: string | null | undefined,
  currentDigest: string | null | undefined,
): string | null {
  const saved = String(savedDigest || "").trim();
  const current = String(currentDigest || "").trim();
  if (!saved || !current || saved === current) {
    return null;
  }
  return "This Skill input contract changed since the draft was saved. Entered values were preserved and will be revalidated before submission.";
}

function skillPayloadWithInputs(args: {
  skillId: string;
  inputs: Record<string, unknown>;
  savedInputContractDigest?: string | null | undefined;
  currentInputContractDigest?: string | null | undefined;
  requiredCapabilities?: string[];
  detail?: SkillCapabilityDetail | null;
}): PresetStepSkill {
  const normalizedInputs = Object.fromEntries(
    Object.entries(args.inputs).filter(([, value]) => value !== undefined),
  );
  const detailPayload = skillInputContractPayload(args.detail);
  const savedDigest = String(
    args.savedInputContractDigest || detailPayload.inputContractDigest || "",
  ).trim();
  const currentDigest = String(
    args.currentInputContractDigest || detailPayload.inputContractDigest || "",
  ).trim();
  return {
    id: args.skillId,
    inputs: normalizedInputs,
    ...detailPayload,
    ...(savedDigest ? { inputContractDigest: savedDigest } : {}),
    ...(currentDigest && currentDigest !== savedDigest
      ? { currentInputContractDigest: currentDigest }
      : {}),
    ...(args.requiredCapabilities && args.requiredCapabilities.length > 0
      ? { requiredCapabilities: args.requiredCapabilities }
      : {}),
  };
}

function schemaToolInputs(
  detail: Pick<PresetDetail, "inputSchema" | "uiSchema" | "defaults"> | null | undefined,
  explicitValues: Record<string, unknown>,
): {
  values: Record<string, unknown>;
  errors: Record<string, string>;
} {
  if (!detail || !schemaContractHasFields(detail)) {
    return { values: {}, errors: {} };
  }
  const rawValues = resolveSchemaCapabilityValues(detail, explicitValues);
  const required = schemaRequired(detail.inputSchema);
  const values: Record<string, unknown> = {};
  for (const [name, rawSchema] of Object.entries(schemaProperties(detail.inputSchema))) {
    const fieldSchema = recordValue(rawSchema);
    const value = rawValues[name];
    if (value === undefined || value === null) {
      continue;
    }
    if (typeof value === "string" && !required.has(name) && !value.trim()) {
      continue;
    }
    if (fieldSchema.type === "integer" || fieldSchema.type === "number") {
      if (value === "" && !required.has(name)) {
        continue;
      }
      const numericValue =
        typeof value === "number" ? value : Number(String(value).trim());
      values[name] = numericValue;
      continue;
    }
    if (
      fieldSchema.type === "object" &&
      typeof value === "string" &&
      !value.trim()
    ) {
      continue;
    }
    values[name] = value;
  }
  const sanitized = { ...values };
  delete sanitized.approved_scope;
  return {
    values: sanitized,
    errors: validateSchemaCapabilityValues(detail, sanitized),
  };
}

function detailFromTrustedTool(
  tool: TrustedToolDefinition | null | undefined,
): Pick<
  PresetDetail,
  "inputSchema" | "uiSchema" | "defaults" | "requiredCapabilities"
> | null {
  if (!tool?.inputSchema) {
    return null;
  }
  return {
    inputSchema: tool.inputSchema,
    defaults: {},
    uiSchema: {},
    ...(tool.requiredCapabilities
      ? { requiredCapabilities: tool.requiredCapabilities }
      : {}),
  };
}

function initializeToolInputValues(
  tool: TrustedToolDefinition | null,
  currentValues: Record<string, unknown>,
  toolInputsText: string,
): Record<string, unknown> {
  const detail = detailFromTrustedTool(tool);
  if (!detail || !schemaContractHasFields(detail)) {
    return currentValues;
  }
  const parsed = parseToolInputsText(toolInputsText);
  const merged = parsed.ok
    ? { ...parsed.value, ...currentValues }
    : { ...currentValues };
  return resolveSchemaCapabilityValues(detail, merged);
}

function serializeToolInputValues(values: Record<string, unknown>): string {
  return JSON.stringify(values, null, 2);
}

function slugPart(value: string): string {
  return (
    value
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "target"
  );
}

function targetHostFromValue(value: string): string {
  const target = value.trim();
  if (!target) {
    return "";
  }
  try {
    return new URL(target).hostname;
  } catch {
    return target.replace(/^https?:\/\//i, "").split(/[/:?#]/, 1)[0] || target;
  }
}

function inferPentestTargetClass(target: string): string {
  const host = targetHostFromValue(target).toLowerCase();
  if (
    host === "localhost" ||
    host.endsWith(".local") ||
    host.includes("lab") ||
    host.startsWith("10.") ||
    host.startsWith("192.168.") ||
    /^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(host)
  ) {
    return "lab";
  }
  if (host.endsWith(".internal") || host.endsWith(".test")) {
    return "internal_authorized";
  }
  return "external_authorized";
}

function defaultPentestExpiresAt(): string {
  const expires = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const localExpires = new Date(
    expires.getTime() - expires.getTimezoneOffset() * 60 * 1000,
  );
  return localExpires.toISOString().slice(0, 16);
}

function datetimeLocalToIso(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
  }
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toISOString();
}

function defaultPentestAllowedActions(operationMode: string): string[] {
  if (operationMode === "validate_hypothesis") {
    return [...PENTEST_VALIDATE_ACTIONS];
  }
  if (operationMode === "full_authorized") {
    return [...PENTEST_SCOPE_ACTIONS];
  }
  return [...PENTEST_BASELINE_ACTIONS];
}

function pentestGeneratedScopeValues(
  draft: PentestScopeDraftState,
  toolInputs: Record<string, unknown>,
): Record<string, unknown> {
  const target = String(toolInputs.target || "").trim();
  const host = targetHostFromValue(target);
  const operationMode = String(toolInputs.operation_mode || "recon_only").trim();
  const runnerProfile = String(
    toolInputs.runner_profile_id || "pentestgpt-claude-oauth",
  ).trim();
  const current = draft.generatedScopeValues;
  const inferredClass = inferPentestTargetClass(target);
  return {
    scope_title:
      current.scope_title ||
      (target ? `Pentest scope for ${target}` : "Pentest scope"),
    scope_id:
      current.scope_id ||
      `${slugPart(host || target)}-${new Date().toISOString().slice(0, 10)}`,
    environment: current.environment || "development",
    expires_at: current.expires_at || defaultPentestExpiresAt(),
    approval_ticket: current.approval_ticket || "self-approved-dev-test",
    target_url: current.target_url || target,
    target_host: current.target_host || host,
    target_class: current.target_class || inferredClass,
    allowed_actions: Array.isArray(current.allowed_actions)
      ? current.allowed_actions
      : defaultPentestAllowedActions(operationMode),
    allowed_runner_profiles: Array.isArray(current.allowed_runner_profiles)
      ? current.allowed_runner_profiles
      : [runnerProfile || "pentestgpt-claude-oauth"],
    requires_manual_approval:
      current.requires_manual_approval !== undefined
        ? Boolean(current.requires_manual_approval)
        : inferredClass === "external_authorized",
    approval_recorded:
      current.approval_recorded !== undefined
        ? Boolean(current.approval_recorded)
        : draft.confirmAuthorized,
    application_stack: current.application_stack || "",
    notes: current.notes || "",
  };
}

function buildPentestApprovedScope(
  draft: PentestScopeDraftState,
  toolInputs: Record<string, unknown>,
): Record<string, unknown> {
  const values = pentestGeneratedScopeValues(draft, toolInputs);
  const target = String(values.target_url || toolInputs.target || "").trim();
  const host = String(values.target_host || targetHostFromValue(target)).trim();
  const allowedActions = Array.isArray(values.allowed_actions)
    ? values.allowed_actions.map(String).filter(Boolean)
    : defaultPentestAllowedActions(String(toolInputs.operation_mode || "recon_only"));
  const allowedRunnerProfiles = Array.isArray(values.allowed_runner_profiles)
    ? values.allowed_runner_profiles.map(String).filter(Boolean)
    : [String(toolInputs.runner_profile_id || "pentestgpt-claude-oauth")];
  return {
    scope_id: String(values.scope_id || slugPart(host || target)).trim(),
    title: String(values.scope_title || `Pentest scope for ${target}`).trim(),
    owner_user_id: null,
    created_at: new Date().toISOString(),
    expires_at: datetimeLocalToIso(values.expires_at),
    target_class: String(values.target_class || inferPentestTargetClass(target)),
    targets: [
      ...(target
        ? [
            {
              kind: target.includes("://") ? "url" : "host",
              value: target,
              notes: String(values.notes || "Approved target.").trim(),
            },
          ]
        : []),
      ...(host && host !== target
        ? [
            {
              kind: "fqdn",
              value: host,
              notes: "Host component of approved target.",
            },
          ]
        : []),
    ],
    allowed_actions: allowedActions,
    prohibited_actions: [],
    requires_manual_approval: Boolean(values.requires_manual_approval),
    approval_ticket: String(values.approval_ticket || "").trim(),
    approval_recorded: Boolean(values.approval_recorded || draft.confirmAuthorized),
    allowed_runner_profiles: allowedRunnerProfiles,
    required_network_attachment_type: null,
    metadata: {
      environment: String(values.environment || "development"),
      application_stack: String(values.application_stack || "").trim() || null,
      production: String(values.environment || "").trim() === "production",
      first_pass: true,
      human_restrictions: [
        "No denial-of-service testing on first pass.",
        "No persistence.",
        "No uncontrolled data exfiltration.",
        "No uncontrolled lateral movement.",
      ],
    },
  };
}

function validatePentestScopeDocument(
  scope: Record<string, unknown>,
  target: string,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!String(scope.scope_id || "").trim()) {
    errors.scope_id = "Scope id is required.";
  }
  if (!String(scope.title || "").trim()) {
    errors.title = "Scope title is required.";
  }
  if (!Array.isArray(scope.targets) || scope.targets.length === 0) {
    errors.targets = "At least one target is required.";
  }
  const allowedActions = Array.isArray(scope.allowed_actions)
    ? scope.allowed_actions.map(String)
    : [];
  if (allowedActions.length === 0) {
    errors.allowed_actions = "At least one allowed action is required.";
  } else {
    const unsupported = allowedActions.filter(
      (action) => !PENTEST_SCOPE_ACTIONS.includes(action as typeof PENTEST_SCOPE_ACTIONS[number]),
    );
    if (unsupported.length > 0) {
      errors.allowed_actions = `Unsupported allowed action: ${unsupported[0]}.`;
    }
  }
  if (
    !Array.isArray(scope.allowed_runner_profiles) ||
    scope.allowed_runner_profiles.length === 0
  ) {
    errors.allowed_runner_profiles = "At least one runner profile is required.";
  }
  const expires = new Date(String(scope.expires_at || ""));
  if (Number.isNaN(expires.getTime()) || expires <= new Date()) {
    errors.expires_at = "Expiration must be in the future.";
  }
  const normalizedTarget = target.trim().toLowerCase();
  if (normalizedTarget && Array.isArray(scope.targets)) {
    const normalizedTargetHost = targetHostFromValue(normalizedTarget).toLowerCase();
    const covered = scope.targets.some((entry) => {
      const value =
        entry && typeof entry === "object"
          ? String((entry as Record<string, unknown>).value || "").toLowerCase()
          : "";
      if (!value) {
        return false;
      }
      const scopeHost = targetHostFromValue(value).toLowerCase();
      return (
        value === normalizedTarget ||
        (normalizedTargetHost &&
          scopeHost &&
          (normalizedTargetHost === scopeHost ||
            normalizedTargetHost.endsWith(`.${scopeHost}`)))
      );
    });
    if (!covered) {
      errors.target = "Scope targets do not appear to cover the selected target.";
    }
  }
  return errors;
}

function pentestScopeWarnings(
  draft: PentestScopeDraftState,
  toolInputs: Record<string, unknown>,
): string[] {
  if (!draft.attachedArtifactId) {
    return [];
  }
  const warnings: string[] = [];
  const target = String(toolInputs.target || "").trim();
  const operationMode = String(toolInputs.operation_mode || "").trim();
  const runnerProfileId = String(toolInputs.runner_profile_id || "").trim();
  if (draft.attachedTarget && target && draft.attachedTarget !== target) {
    warnings.push(
      "The target changed after this scope was attached. Regenerate or revalidate the scope before submitting.",
    );
  }
  if (
    draft.attachedOperationMode &&
    operationMode &&
    draft.attachedOperationMode !== operationMode
  ) {
    warnings.push("The operation mode changed after this scope was attached.");
  }
  if (
    draft.attachedRunnerProfileId &&
    runnerProfileId &&
    draft.attachedRunnerProfileId !== runnerProfileId
  ) {
    warnings.push("The runner profile changed after this scope was attached.");
  }
  return warnings;
}

function SchemaCapabilityFields({
  fields,
  detail,
  values,
  errors,
  disabled,
  repositoryOptions,
  branchOptions,
  onChange,
}: {
  fields: Array<[string, unknown]>;
  detail: Pick<PresetDetail, "uiSchema" | "defaults">;
  values: Record<string, unknown>;
  errors: Record<string, string>;
  disabled: boolean;
  repositoryOptions: RepositoryOption[];
  branchOptions: BranchOption[];
  onChange: (name: string, value: unknown) => void;
}): ReactElement | null {
  if (fields.length === 0) {
    return null;
  }
  return (
    <div className="grid-2">
      {fields.map(([name, rawSchema]) => {
        const fieldSchema = recordValue(rawSchema);
        const uiSchema = capabilityFieldUiSchema(detail.uiSchema, name);
        const widget = capabilityWidgetName(fieldSchema, uiSchema);
        const label = capabilityFieldLabel(name, fieldSchema);
        const value = capabilityInputValue(values, detail.defaults, name);
        const inputId = `queue-capability-input-${name}`;
        const error = errors[name];
        const description = String(fieldSchema.description || "").trim();
        const choices = schemaChoiceOptions(fieldSchema);
        const hasTypeSafeRenderer =
          fieldSchema.type === "boolean" ||
          fieldSchema.type === "number" ||
          fieldSchema.type === "integer" ||
          fieldSchema.type === "array" ||
          fieldSchema.type === "object" ||
          Array.isArray(fieldSchema.enum) ||
          choices.length > 0;
        if (unsupportedCapabilityWidget(widget, fieldSchema) && !hasTypeSafeRenderer) {
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="text"
                value={capabilityInputTextValue(values, detail.defaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) => onChange(name, event.target.value)}
              />
              <span className="notice small">
                Widget {widget} is unavailable; using a text field.
              </span>
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        if (widget === "jira.issue-picker" || widget === "github.issue-picker") {
          const issueValue = recordValue(value);
          const provider = widget === "github.issue-picker" ? "github" : "jira";
          const placeholder = String(
            uiSchema.searchPlaceholder ||
              (provider === "github" ? "Search GitHub issues" : "Search Jira issues"),
          );
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="text"
                value={issuePickerTextValue(value, provider)}
                placeholder={placeholder}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) => {
                  if (provider === "github") {
                    onChange(
                      name,
                      normalizeGitHubIssueInput(
                        event.target.value,
                        String(issueValue.repository || readLocalPreference(LAST_REPOSITORY_OPTION_PREFERENCE_KEY) || ""),
                      ),
                    );
                    return;
                  }
                  onChange(name, {
                    ...issueValue,
                    key: event.target.value,
                  });
                }}
              />
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        if (fieldSchema.type === "boolean") {
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <input
                id={inputId}
                aria-label={label}
                type="checkbox"
                checked={Boolean(value)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) => onChange(name, event.target.checked)}
              />
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        const itemSchema = recordValue(fieldSchema.items);
        if (
          fieldSchema.type === "array" &&
          (Array.isArray(itemSchema.enum) || schemaChoiceOptions(itemSchema).length > 0)
        ) {
          const itemChoices = schemaChoiceOptions(itemSchema);
          const itemEnumOptions = Array.isArray(itemSchema.enum)
            ? itemSchema.enum
            : [];
          const itemOptions: Array<{ label: string; value: unknown }> =
            itemChoices.length > 0
              ? itemChoices
              : itemEnumOptions.map((option: unknown) => ({
                  label: String(option),
                  value: option,
                }));
          const selectedValues = capabilityInputStringArrayValue(value);
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <select
                id={inputId}
                aria-label={label}
                multiple
                value={selectedValues}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) => {
                  const selected = Array.from(event.currentTarget.selectedOptions)
                    .map((option) => {
                      const matched = itemOptions.find(
                        (item) => String(item.value) === option.value,
                      );
                      return matched?.value ?? option.value;
                    });
                  onChange(name, selected);
                }}
              >
                {itemOptions.map((option) => (
                  <option key={String(option.value)} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        if (Array.isArray(fieldSchema.enum) || choices.length > 0) {
          const enumOptions = Array.isArray(fieldSchema.enum)
            ? fieldSchema.enum
            : [];
          const options: Array<{ label: string; value: unknown }> =
            choices.length > 0
              ? choices
              : enumOptions.map((option: unknown) => ({
                  label: String(option),
                  value: option,
                }));
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <select
                id={inputId}
                aria-label={label}
                value={capabilityInputTextValue(values, detail.defaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) => {
                  const selected = options.find(
                    (option) => String(option.value) === event.target.value,
                  );
                  onChange(name, selected?.value ?? event.target.value);
                }}
              >
                {options.map((option) => (
                  <option key={String(option.value)} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        if (widget === "textarea" || widget === "markdown" || widget === "json") {
          const structuredValue =
            widget === "json" &&
            (fieldSchema.type === "array" || fieldSchema.type === "object");
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <textarea
                id={inputId}
                aria-label={label}
                value={capabilityInputTextValue(values, detail.defaults, name)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) =>
                  onChange(
                    name,
                    structuredValue
                      ? parseComplexCapabilityValue(event.target.value)
                      : event.target.value,
                  )
                }
              />
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        if (fieldSchema.type === "array" || fieldSchema.type === "object") {
          return (
            <label key={name} htmlFor={inputId}>
              {label}
              <textarea
                id={inputId}
                aria-label={label}
                value={complexCapabilityTextValue(value)}
                disabled={disabled}
                aria-invalid={Boolean(error)}
                onChange={(event) =>
                  onChange(name, parseComplexCapabilityValue(event.target.value))
                }
              />
              {description ? <span className="small">{description}</span> : null}
              {error ? <span className="notice small">{error}</span> : null}
            </label>
          );
        }
        const inputType = textInputTypeForSchema(fieldSchema);
        return (
          <label key={name} htmlFor={inputId}>
            {label}
            <input
              id={inputId}
              aria-label={label}
              type={inputType}
              list={
                widget === "repository" ||
                widget === "repository-picker" ||
                widget === "repo" ||
                widget === "repo-picker" ||
                widget === "branch"
                  ? `${inputId}-options`
                  : undefined
              }
              value={capabilityInputTextValue(values, detail.defaults, name)}
              disabled={disabled}
              aria-invalid={Boolean(error)}
              onChange={(event) =>
                onChange(
                  name,
                  inputType === "number"
                    ? event.target.value === ""
                      ? ""
                      : Number(event.target.value)
                    : event.target.value,
                )
              }
            />
            {widget === "repository" ||
            widget === "repository-picker" ||
            widget === "repo" ||
            widget === "repo-picker" ? (
              <datalist id={`${inputId}-options`}>
                {repositoryOptions.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    label={option.label}
                  />
                ))}
              </datalist>
            ) : null}
            {widget === "branch" ? (
              <datalist id={`${inputId}-options`}>
                {branchOptions.map((option) => (
                  <option
                    key={option.value}
                    value={option.value}
                    label={option.label}
                  />
                ))}
              </datalist>
            ) : null}
            {description ? <span className="small">{description}</span> : null}
            {error ? <span className="notice small">{error}</span> : null}
          </label>
        );
      })}
    </div>
  );
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
        "Workflow instructions could not be loaded from the input artifact.",
      ),
    );
  }
  const rawText = await response.text();
  try {
    return JSON.parse(rawText) as unknown;
  } catch {
    throw new Error("Workflow input artifact did not contain valid JSON.");
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
          label: "Submitted Workflow Input",
          repository: repository || null,
          source: "workflow-console-submit",
          ...(sourceWorkflowId ? { sourceWorkflowId } : {}),
        },
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[WorkflowStart] Network failure during artifact creation.", {
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
      "Workflow input artifact is too large for the current browser submission flow. " +
        "Reduce the submitted instructions or workflow step payload and retry.",
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
        "[WorkflowStart] Network failure during artifact content upload.",
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
        "Failed to upload workflow input artifact content.",
      ),
    );
  }

  await completeArtifactUpload(
    artifactId,
    "Failed to finalize workflow input artifact upload.",
  );
  return { artifactId };
}

async function createJsonArtifact(
  createEndpoint: string,
  body: string,
  metadata: Record<string, unknown>,
  failureLabel: string,
): Promise<{ artifactId: string }> {
  const createResponse = await fetch(createEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({
      content_type: "application/json; charset=utf-8",
      size_bytes: new TextEncoder().encode(body).length,
      retention_class: "pinned",
      metadata,
    }),
  });
  if (!createResponse.ok) {
    throw new Error(
      await responseErrorMessage(createResponse, `Failed to create ${failureLabel}.`),
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
    throw new Error(`${failureLabel} upload details were incomplete.`);
  }
  if (uploadMode === "multipart") {
    throw new Error(`${failureLabel} is too large for browser upload.`);
  }
  const uploadUrl =
    String(created.upload?.upload_url || "").trim() ||
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`;
  const uploadHeaders = new Headers(
    created.upload?.required_headers &&
      typeof created.upload.required_headers === "object"
      ? created.upload.required_headers
      : {},
  );
  if (!uploadHeaders.has("content-type")) {
    uploadHeaders.set("content-type", "application/json; charset=utf-8");
  }
  const uploadResponse = await fetch(uploadUrl, {
    method: "PUT",
    headers: uploadHeaders,
    body,
  });
  if (!uploadResponse.ok) {
    throw new Error(
      await responseErrorMessage(uploadResponse, `Failed to upload ${failureLabel}.`),
    );
  }
  await completeArtifactUpload(
    artifactId,
    `Failed to finalize ${failureLabel}.`,
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
          "[WorkflowStart] Network failure during artifact completion.",
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
            ? "workflow-console-objective-attachment"
            : "workflow-console-step-attachment",
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
  const workflow = payloadRecord.workflow;
  const createTask = payloadRecord.task;
  const workflowInput =
    workflow && typeof workflow === "object" && !Array.isArray(workflow)
      ? workflow
      : createTask && typeof createTask === "object" && !Array.isArray(createTask)
        ? createTask
        : null;
  if (!workflowInput) {
    return;
  }

  const workflowInputRecord = workflowInput as Record<string, unknown>;
  const steps = Array.isArray(workflowInputRecord.steps)
    ? workflowInputRecord.steps
    : [];

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

  if ("instructions" in workflowInputRecord) {
    delete workflowInputRecord.instructions;
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
        label: options.label || "Submitted Workflow Input",
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[WorkflowStart] Network failure during artifact linking.", {
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

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 12.5 10 17.5 19 7.5" />
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

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9.5" strokeWidth="2" />
      <path d="M12 11v6.25" strokeWidth="2.6" strokeLinecap="round" />
      <circle
        cx="12"
        cy="7.75"
        r="1.45"
        fill="currentColor"
        stroke="none"
      />
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

// Defaults applied when the advanced execution controls (Priority / Max
// Attempts) are hidden. Submission uses these so toggling Advanced mode off
// never silently submits stale values nor blocks on a hidden invalid one.
const DEFAULT_PRIORITY = 0;
const DEFAULT_MAX_ATTEMPTS = 3;

interface StepAddMenuProps {
  stepNumber: number;
  attachmentPolicy: AttachmentPolicy;
  presentCapabilityTokens: string[];
  onAddImage: () => void;
  onAddCapability: (token: string) => void;
  onAddCustomCapability: () => void;
}

// MM-943: The single "Add to step" affordance rendered beneath each step's
// instruction box. It unifies image attachments and capabilities without
// merging the underlying backend concepts.
function StepAddMenu({
  stepNumber,
  attachmentPolicy,
  presentCapabilityTokens,
  onAddImage,
  onAddCapability,
  onAddCustomCapability,
}: StepAddMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const focusMenuItem = useCallback((startIndex: number, direction: 1 | -1) => {
    const items = Array.from(
      containerRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]',
      ) || [],
    );
    const itemCount = items.length;
    if (itemCount === 0) {
      return;
    }
    for (let offset = 0; offset < itemCount; offset += 1) {
      const nextIndex =
        (startIndex + offset * direction + itemCount) % itemCount;
      const item = items[nextIndex];
      if (item && !item.disabled) {
        item.focus();
        return;
      }
    }
  }, []);

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) {
      window.setTimeout(() => triggerRef.current?.focus(), 0);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        closeMenu();
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeMenu(true);
        return;
      }
      const items = Array.from(
        containerRef.current?.querySelectorAll<HTMLButtonElement>(
          '[role="menuitem"]',
        ) || [],
      );
      if (event.key === "ArrowDown") {
        event.preventDefault();
        const currentIndex = items.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        focusMenuItem(currentIndex < 0 ? 0 : currentIndex + 1, 1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        const currentIndex = items.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        focusMenuItem(
          currentIndex < 0 ? items.length - 1 : currentIndex - 1,
          -1,
        );
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        focusMenuItem(0, 1);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        focusMenuItem(items.length - 1, -1);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [closeMenu, focusMenuItem, open]);

  const menuLabel = `Add to Step ${stepNumber}`;
  const menuTitle = "Add files or capabilities";
  const menuTitleId = `queue-step-add-menu-title-${stepNumber}`;
  const imageItemLabel = isImageOnlyPolicy(attachmentPolicy)
    ? "Image…"
    : "Attachment…";

  useEffect(() => {
    if (open) {
      window.setTimeout(() => focusMenuItem(0, 1), 0);
    }
  }, [focusMenuItem, open]);

  return (
    <div className="queue-step-context-menu" ref={containerRef}>
      <button
        type="button"
        className="queue-step-attachment-add-button queue-step-context-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={menuLabel}
        title={menuTitle}
        ref={triggerRef}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">+</span>
      </button>
      {open ? (
        <div
          className="queue-step-context-menu-popover"
          role="menu"
          aria-labelledby={menuTitleId}
        >
          <p id={menuTitleId} className="queue-step-context-menu-title">
            Add to step
          </p>
          {attachmentPolicy.enabled ? (
            <>
              <button
                type="button"
                role="menuitem"
                className="queue-step-context-menu-item"
                onClick={() => {
                  closeMenu(true);
                  onAddImage();
                }}
              >
                {imageItemLabel}
              </button>
              <div
                className="queue-step-context-menu-separator"
                role="separator"
              />
            </>
          ) : null}
          <p className="queue-step-context-menu-group-label">Capabilities</p>
          {CAPABILITY_MENU_TOKENS.map((token) => {
            const entry = capabilityCatalogEntry(token);
            const alreadyPresent = presentCapabilityTokens.includes(token);
            return (
              <button
                key={token}
                type="button"
                role="menuitem"
                className="queue-step-context-menu-item queue-step-context-menu-capability"
                disabled={alreadyPresent}
                aria-disabled={alreadyPresent}
                title={entry.description}
                onClick={() => {
                  closeMenu(true);
                  onAddCapability(token);
                }}
              >
                <span
                  className="queue-step-context-menu-capability-icon"
                  aria-hidden="true"
                >
                  {entry.icon}
                </span>
                <span className="queue-step-context-menu-capability-label">
                  {entry.label}
                </span>
                <span className="queue-step-context-menu-capability-token">
                  {entry.token}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            role="menuitem"
            className="queue-step-context-menu-item"
            onClick={() => {
              closeMenu(true);
              onAddCustomCapability();
            }}
          >
            Custom capability…
          </button>
        </div>
      ) : null}
    </div>
  );
}

interface CapabilityChipProps {
  chip: StepCapabilityChip;
  stepNumber: number;
  onRemove: (token: string) => void;
}

function derivedCapabilityExplanation(chip: StepCapabilityChip): string {
  const provenance = capabilityChipProvenanceLabel(chip);
  if (!provenance) {
    return "This capability is required by this step. Change the step configuration to remove it.";
  }
  const source = provenance.replace(/^from /, "");
  return `This capability is required ${provenance}. Change the selected ${source} source or generated step to remove it.`;
}

function CapabilityChip({ chip, stepNumber, onRemove }: CapabilityChipProps) {
  const [showExplanation, setShowExplanation] = useState(false);
  const provenance = capabilityChipProvenanceLabel(chip);
  const explanation = chip.removable ? chip.description : derivedCapabilityExplanation(chip);
  const chipTitle = provenance
    ? `${chip.label}: ${provenance}`
    : chip.description
      ? `${chip.label}: ${chip.description}`
      : chip.label;
  return (
    <li
      className={`queue-step-capability-chip${chip.removable ? "" : " is-derived"}`}
      title={chipTitle}
      onClick={
        chip.removable
          ? undefined
          : () => setShowExplanation((value) => !value)
      }
    >
      <span className="queue-step-capability-chip-icon" aria-hidden="true">
        {chip.icon}
      </span>
      <span className="queue-step-capability-chip-label">{chip.label}</span>
      {chip.removable ? (
        <button
          type="button"
          className="queue-step-icon-button destructive queue-step-capability-chip-remove"
          aria-label={`Remove ${chip.label} capability from Step ${stepNumber}`}
          title={`Remove ${chip.label} capability from Step ${stepNumber}`}
          onClick={() => onRemove(chip.token)}
        >
          <CloseIcon />
          <span className="sr-only">{`Remove ${chip.label} capability from Step ${stepNumber}`}</span>
        </button>
      ) : (
        <button
          type="button"
          className="queue-step-capability-chip-lock"
          aria-label={`${chip.label} capability provenance`}
          title={explanation}
          aria-expanded={showExplanation}
          onFocus={() => setShowExplanation(true)}
          onBlur={() => setShowExplanation(false)}
          onClick={(event) => {
            event.stopPropagation();
            setShowExplanation((value) => !value);
          }}
        >
          <span aria-hidden="true">🔒</span>
          <span className="sr-only">Show capability provenance</span>
        </button>
      )}
      {!chip.removable && showExplanation ? (
        <span className="queue-step-capability-chip-popover" role="tooltip">
          {explanation}
        </span>
      ) : null}
    </li>
  );
}

interface StepContextAttachmentItem {
  key: string;
  filename: string;
  detail: string;
  targetLabel: string;
  href?: string;
  download?: string;
  removeLabel: string;
  retryLabel?: string;
  onRemove: () => void;
  onRetry?: () => void;
}

interface StepContextBarProps {
  stepNumber: number;
  attachments: StepContextAttachmentItem[];
  capabilityChips: StepCapabilityChip[];
  onRemoveCapability: (token: string) => void;
}

function StepContextBar({
  stepNumber,
  attachments,
  capabilityChips,
  onRemoveCapability,
}: StepContextBarProps) {
  if (attachments.length === 0 && capabilityChips.length === 0) {
    return null;
  }
  return (
    <div className="queue-step-context-bar">
      {attachments.length > 0 ? (
        <ul
          className="queue-step-context-chip-list"
          aria-label={`Step ${stepNumber} image attachments`}
        >
          {attachments.map((attachment) => (
            <li key={attachment.key} className="queue-step-attachment-chip">
              <span className="queue-step-attachment-chip-icon" aria-hidden="true">
                🖼
              </span>
              <span className="queue-step-attachment-chip-label">
                {attachment.filename}
              </span>
              <span className="queue-step-attachment-chip-detail">
                {attachment.targetLabel}
              </span>
              <span className="queue-step-attachment-chip-detail">
                {attachment.detail}
              </span>
              {attachment.href ? (
                <a
                  className="queue-step-context-chip-action"
                  href={attachment.href}
                  download={attachment.download}
                  title={`Download ${attachment.targetLabel} attachment ${attachment.filename}`}
                >
                  Download
                </a>
              ) : null}
              {attachment.onRetry && attachment.retryLabel ? (
                <button
                  type="button"
                  className="queue-step-context-chip-action"
                  aria-label={attachment.retryLabel}
                  title={attachment.retryLabel}
                  onClick={attachment.onRetry}
                >
                  Retry
                </button>
              ) : null}
              <button
                type="button"
                className="queue-step-icon-button destructive queue-step-attachment-chip-remove"
                aria-label={attachment.removeLabel}
                title={attachment.removeLabel}
                onClick={attachment.onRemove}
              >
                <CloseIcon />
                <span className="sr-only">{attachment.removeLabel}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {capabilityChips.length > 0 ? (
        <ul
          className="queue-step-capability-chips"
          aria-label={`Step ${stepNumber} capabilities`}
        >
          {capabilityChips.map((chip) => (
            <CapabilityChip
              key={chip.token}
              chip={chip}
              stepNumber={stepNumber}
              onRemove={onRemoveCapability}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function WorkflowStartPageContent({ payload }: { payload: BootPayload }) {
  useLiquidGL({ options: LIQUID_GL_OPTIONS });
  const dashboardConfig = readDashboardConfig(payload);
  const pageMode = useMemo(
    () => resolveTaskSubmitPageMode(window.location.search),
    [],
  );
  const [workflowStartHeadingQuote] = useState(() => randomWorkflowStartHeading());
  const temporalTaskEditingEnabled = Boolean(
    dashboardConfig.features?.temporalDashboard?.temporalWorkflowEditing ??
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
  const omnigentCatalogEndpoint = "/api/omnigent/codex-catalog-readiness";
  const configuredDefaultProviderProfileRef =
    dashboardConfig.system?.providerProfiles?.defaultProfileRef ?? null;
  const presetCatalog = dashboardConfig.system?.presetCatalog;
  const presetCatalogEnabled = Boolean(presetCatalog?.enabled);
  const presetSaveEnabled = Boolean(
    presetCatalog?.templateSaveEnabled,
  );
  const taskTemplateListEndpoint = String(
    presetCatalog?.list || "/api/presets",
  );
  const taskTemplateDetailEndpoint = String(
    presetCatalog?.detail || "/api/presets/{slug}",
  );
  const taskTemplateExpandEndpoint = String(
    presetCatalog?.expand || "/api/presets/{slug}:expand",
  );
  const taskTemplateSaveEndpoint = String(
    presetCatalog?.saveFromWorkflow ||
      "/api/presets/save-from-workflow",
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
    dashboardConfig.system?.defaultRuntime ||
      dashboardConfig.system?.defaultAgentRuntime ||
      "codex_cli",
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
    dashboardConfig.system?.defaultModelByRuntime ||
    dashboardConfig.system?.defaultTaskModelByRuntime ||
    {};
  const defaultTaskEffortByRuntime =
    dashboardConfig.system?.defaultEffortByRuntime ||
    dashboardConfig.system?.defaultTaskEffortByRuntime ||
    {};
  const supportedAgentRuntimes = dashboardConfig.system
    ?.supportedAgentRuntimes ||
    dashboardConfig.system?.supportedRuntimes || ["codex_cli", "claude_code"];
  const runtimeOptions = Array.from(new Set([...supportedAgentRuntimes, "omnigent"]));

  const [steps, setSteps] = useState<StepState[]>([createStepStateEntry(1)]);
  const stepsRef = useRef<StepState[]>(steps);
  const [nextStepNumber, setNextStepNumber] = useState(2);
  // MM-964: the create page remembers the operator's guided vs expert default.
  // The preference seeds the initial value; it is only re-persisted on an
  // explicit toggle of the Advanced mode checkbox, never on the programmatic
  // enabling that happens while reconstructing a preset's advanced step values.
  const [showAdvancedStepOptions, setShowAdvancedStepOptions] = useState(
    () => readDashboardPreferences().createExpertMode,
  );
  const [runtime, setRuntime] = useState(defaultRuntime);
  const omnigentCatalog = dashboardConfig.system?.omnigentExecutionCatalog;
  const omnigentProfiles = omnigentCatalog?.profiles || [];
  const omnigentPolicies = omnigentCatalog?.policies || [];
  const [omnigentExecutionTargetRef, setOmnigentExecutionTargetRef] = useState(
    String(omnigentProfiles[0]?.ref || ""),
  );
  const [omnigentLaunchPolicyRef, setOmnigentLaunchPolicyRef] = useState(
    String(omnigentProfiles[0]?.defaultPolicyRef || omnigentPolicies[0]?.ref || ""),
  );
  const [model, setModel] = useState(
    String(
      defaultTaskModelByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultModel ||
        dashboardConfig.system?.defaultTaskModel ||
        "",
    ),
  );
  const [modelManualOverride, setModelManualOverride] = useState(false);
  const [effort, setEffort] = useState(
    String(
      defaultTaskEffortByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultEffort ||
        dashboardConfig.system?.defaultTaskEffort ||
        "",
    ),
  );
  const [effortManualOverride, setEffortManualOverride] = useState(false);
  const [modelTier, setModelTier] = useState("");
  const [tierFallback, setTierFallback] = useState<TierFallbackMode>("clamp");
  const [repository, setRepository] = useState(initialRepository);
  const [providerProfile, setProviderProfile] = useState("");
  const [branch, setBranch] = useState("");
  const [branchTouched, setBranchTouched] = useState(false);
  const [publishMode, setPublishMode] = useState(
    normalizePublishModeForSubmit(defaultPublishMode),
  );
  const [produceReport, setProduceReport] = useState(false);
  const [priority, setPriority] = useState(DEFAULT_PRIORITY);
  const [maxAttempts, setMaxAttempts] = useState(DEFAULT_MAX_ATTEMPTS);
  const [proposeTasks, setProposeTasks] = useState(() =>
    readProposeTasksPreference(defaultProposeTasks),
  );
  const isInitialMount = useRef(true);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>(() => {
    if (typeof window === "undefined") {
      return "immediate";
    }
    const params = new URLSearchParams(window.location.search);
    const mode = params.get("scheduleMode");
    if (mode === "recurring" || mode === "once") {
      return mode;
    }
    return "immediate";
  });
  const [scheduledFor, setScheduledFor] = useState("");
  const [scheduleDeferredMinutes, setScheduleDeferredMinutes] = useState("");
  const [scheduleCron, setScheduleCron] = useState("");
  const [scheduleTimezone, setScheduleTimezone] = useState("UTC");
  const [scheduleName, setScheduleName] = useState("");
  const [templateFeatureRequest, setTemplateFeatureRequest] = useState("");
  const [selectedDependencyWorkflowId, setSelectedDependencyWorkflowId] = useState("");
  const [selectedDependencies, setSelectedDependencies] = useState<string[]>([]);
  const [remediationDraft, setRemediationDraft] = useState<RemediationCreateDraft | null>(null);
  const remediationDraftIdRef = useRef<string | null>(null);
  const [dependencyMessage, setDependencyMessage] = useState<string | null>(null);
  const [selectedPresetKey, setSelectedPresetKey] = useState("");
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
  const [submitMessageState, setSubmitMessageState] = useState<
    { text: string; tone: "error" | "pending" | "ok" } | null
  >(null);
  const submitMessage = submitMessageState?.text ?? null;
  const submitMessageTone = submitMessageState?.tone ?? "error";
  // Structured technical diagnostics for the most recent submit failure. When
  // set, the friendly submitMessage is rendered through DashboardErrorDetails
  // so endpoint/raw-error detail stays behind a disclosure instead of being
  // dumped into the main error text (MM-959).
  const [submitErrorDetail, setSubmitErrorDetail] = useState<{
    endpoint?: string | null;
    status?: number | string | null;
    requestId?: string | null;
    rawError?: string | null;
  } | null>(null);
  const setSubmitMessage = useCallback(
    (text: string | null, tone: "error" | "pending" | "ok" = "error") => {
      setSubmitErrorDetail(null);
      setSubmitMessageState(text === null ? null : { text, tone });
    },
    [],
  );
  const [isApplyingPreset, setIsApplyingPreset] = useState(false);
  const [isDeletingPreset, setIsDeletingPreset] = useState(false);
  const [isSavingPreset, setIsSavingPreset] = useState(false);
  const [presetDialogMode, setPresetDialogMode] = useState<
    "save" | "delete" | null
  >(null);
  const [presetDialogName, setPresetDialogName] = useState("");
  const [dependencyInfoOpen, setDependencyInfoOpen] = useState(false);
  const [advancedInfoOpen, setAdvancedInfoOpen] = useState(false);
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
  const remediationDraftAppliedRef = useRef<string | null>(null);
  const jiraProjectSelectionInitializedRef = useRef(false);
  const jiraBoardSelectionInitializedRef = useRef(false);
  const routeGuardDirtyRef = useRef(false);
  const approvedNavigationHrefRef = useRef<string | null>(null);

  useEffect(() => {
    const markDraftDirty = (event: Event) => {
      const target = event.target;
      if (target instanceof Element && target.closest("#queue-submit-form")) {
        routeGuardDirtyRef.current = true;
      }
    };
    const handleRouteChangeRequest = (event: Event) => {
      if (!routeGuardDirtyRef.current) {
        return;
      }
      const confirmed = window.confirm(
        "Leave Create? Unsaved workflow draft changes may be lost.",
      );
      if (!confirmed) {
        event.preventDefault();
      } else if (event instanceof CustomEvent && typeof event.detail?.href === "string") {
        approvedNavigationHrefRef.current = new URL(
          event.detail.href,
          window.location.href,
        ).href;
      }
    };
    const confirmDraftNavigation = (): boolean =>
      !routeGuardDirtyRef.current ||
      window.confirm("Leave Create? Unsaved workflow draft changes may be lost.");
    const handleDocumentNavigation = (event: MouseEvent) => {
      if (
        event.defaultPrevented || event.button !== 0 || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey
      ) return;
      const target = event.target;
      const anchor = target instanceof Element ? target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement) || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      if ((anchor.getAttribute("href") || "").trimStart().startsWith("#")) return;
      const destination = new URL(anchor.href, window.location.href);
      const current = new URL(window.location.href);
      if (
        destination.origin !== current.origin ||
        (destination.pathname === current.pathname && destination.search === current.search)
      ) return;
      if (approvedNavigationHrefRef.current !== null) {
        const approved = new URL(approvedNavigationHrefRef.current);
        approvedNavigationHrefRef.current = null;
        if (
          approved.origin === destination.origin &&
          approved.pathname === destination.pathname &&
          approved.search === destination.search
        ) return;
      }
      if (!confirmDraftNavigation()) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!routeGuardDirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener(
      WORKFLOW_START_ROUTE_CHANGE_REQUEST_EVENT,
      handleRouteChangeRequest,
    );
    document.addEventListener("input", markDraftDirty);
    document.addEventListener("change", markDraftDirty);
    document.addEventListener("click", handleDocumentNavigation);
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener(
        WORKFLOW_START_ROUTE_CHANGE_REQUEST_EVENT,
        handleRouteChangeRequest,
      );
      document.removeEventListener("input", markDraftDirty);
      document.removeEventListener("change", markDraftDirty);
      document.removeEventListener("click", handleDocumentNavigation);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, []);

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
      "workflow-start",
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
        if (String(execution.workflowType || "") !== "MoonMind.UserWorkflow") {
          throw new Error(
            "This execution cannot be edited here because only MoonMind.UserWorkflow is supported.",
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
        const inlineTask = workflowRecord(recordValue(execution.inputParameters));
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

  const providerProfileRuntime =
    runtime === "omnigent" ? "codex_cli" : runtime;
  const providerProfilesQuery = useQuery({
    ...configQueryDefaults,
    queryKey: ["workflow-start", "provider-profiles", providerProfileRuntime],
    queryFn: async (): Promise<ProviderProfile[]> => {
      const separator = providerProfilesEndpoint.includes("?") ? "&" : "?";
      const response = await fetch(
        `${providerProfilesEndpoint}${separator}runtime_id=${encodeURIComponent(providerProfileRuntime)}`,
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
    // Omnigent eligibility comes exclusively from the readiness catalog, but
    // the normal profile response remains the authority for model/effort
    // capabilities. Load it for Codex and intersect the two below.
    enabled: Boolean(runtime),
    // Keep the previously loaded profiles visible while a runtime switch
    // refetches. Without this, the query key change empties `data`, which
    // collapses the Runtime/Provider-profile row from `grid-2` to `stack`
    // (full width) until the fetch resolves and it snaps back to half width.
    placeholderData: keepPreviousData,
  });

  const omnigentCatalogQuery = useQuery({
    queryKey: ["workflow-start", "omnigent-codex-catalog-readiness"],
    queryFn: async (): Promise<OmnigentCodexCatalogReadiness> => {
      const response = await fetch(omnigentCatalogEndpoint, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, "Failed to load Omnigent readiness."));
      }
      return (await response.json()) as OmnigentCodexCatalogReadiness;
    },
    staleTime: 0,
    refetchOnWindowFocus: true,
  });

  const activeProviderProfiles: ProviderProfile[] = runtime === "omnigent"
    ? (omnigentCatalogQuery.data?.eligibleProviderProfiles || []).map((profile) => {
        const capabilityProfile = (providerProfilesQuery.data || []).find(
          (candidate) => candidate.profile_id === profile.profileId,
        );
        return {
          ...capabilityProfile,
          profile_id: profile.profileId,
          account_label: profile.label,
          provider_id: profile.providerId,
          enabled: true,
          launch_ready: true,
        };
      })
    : (providerProfilesQuery.data || []);

  useEffect(() => {
    if (runtime !== "omnigent" || !omnigentCatalogQuery.data) return;
    if (!omnigentExecutionTargetRef) {
      setOmnigentExecutionTargetRef(omnigentCatalogQuery.data.defaultExecutionProfileRef);
    }
  }, [runtime, omnigentCatalogQuery.data, omnigentExecutionTargetRef]);

  useEffect(() => {
    const profiles = activeProviderProfiles;
    if (
      (runtime === "omnigent" && omnigentCatalogQuery.isFetching) ||
      (runtime !== "omnigent" && (providerProfilesQuery.isLoading || providerProfilesQuery.isFetching))
    ) {
      return;
    }
    const resolvedProfileId = resolveLoadedProviderProfileId({
      profiles,
      providerProfile,
      configuredDefaultRef: configuredDefaultProviderProfileRef,
      preserveUnavailableProfile:
        pageMode.mode !== "create" && Boolean(temporalDraftAppliedRef.current),
    });
    if (providerProfile !== resolvedProfileId) {
      setProviderProfile(resolvedProfileId);
    }
  }, [
    pageMode.mode,
    providerProfile,
    providerProfilesQuery.data,
    providerProfilesQuery.isFetching,
    providerProfilesQuery.isLoading,
    omnigentCatalogQuery.data,
    omnigentCatalogQuery.isFetching,
    runtime,
    configuredDefaultProviderProfileRef,
  ]);

  useEffect(() => {
    const runtimeChanged = prevRuntimeRef.current !== runtime;
    const profileChanged = prevProviderProfileRef.current !== providerProfile;

    if (runtimeChanged || profileChanged) {
      setModelManualOverride(false);
      setEffortManualOverride(false);
      setModel("");
      setEffort("");
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
          dashboardConfig.system?.defaultEffort ||
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
            dashboardConfig.system?.defaultModel ||
            dashboardConfig.system?.defaultTaskModel ||
            "",
        ),
      );
    }
  }, [
    dashboardConfig.system?.defaultTaskEffort,
    dashboardConfig.system?.defaultEffort,
    dashboardConfig.system?.defaultTaskModel,
    dashboardConfig.system?.defaultModel,
    defaultTaskEffortByRuntime,
    defaultTaskModelByRuntime,
    modelManualOverride,
    pageMode.mode,
    providerProfilesQuery.data,
    providerProfile,
    runtime,
  ]);

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
    if (draft.omnigentExecutionTargetRef) {
      setOmnigentExecutionTargetRef(draft.omnigentExecutionTargetRef);
    }
    if (draft.omnigentLaunchPolicyRef) {
      setOmnigentLaunchPolicyRef(draft.omnigentLaunchPolicyRef);
    }
    if (draft.model) {
      setModel(draft.model);
      setModelManualOverride(true);
    }
    if (draft.effort) {
      setEffort(draft.effort);
      setEffortManualOverride(true);
    }
    if (draft.modelTier != null) {
      setModelTier(String(draft.modelTier));
    }
    if (draft.tierFallback === "strict" || draft.tierFallback === "clamp") {
      setTierFallback(draft.tierFallback);
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

  useEffect(() => {
    if (pageMode.mode !== "create") {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("intent") !== "remediate") {
      setRemediationDraft(null);
      remediationDraftAppliedRef.current = null;
      remediationDraftIdRef.current = null;
      return;
    }
    const draftId = String(params.get("draftId") || "").trim();
    if (!draftId || remediationDraftAppliedRef.current === draftId) {
      return;
    }
    const draft = readRemediationCreateDraft(draftId);
    if (!draft) {
      setRemediationDraft(null);
      remediationDraftAppliedRef.current = null;
      remediationDraftIdRef.current = null;
      setSubmitMessage("The remediation draft is no longer available. Open Remediate from the target workflow again.");
      return;
    }

    remediationDraftAppliedRef.current = draftId;
    remediationDraftIdRef.current = draftId;
    setRemediationDraft(draft);
    setRepository(draft.repository || "MoonLadderStudios/MoonMind");
    if (draft.branch) {
      setBranch(draft.branch);
      setBranchTouched(false);
    }
    if (draft.publishMode) {
      setPublishMode(normalizePublishModeForSubmit(draft.publishMode));
    }
    if (draft.runtime?.mode) {
      prevRuntimeRef.current = draft.runtime.mode;
      setRuntime(draft.runtime.mode);
    }
    if (draft.runtime?.profileId) {
      prevProviderProfileRef.current = draft.runtime.profileId;
      setProviderProfile(draft.runtime.profileId);
    }
    if (draft.runtime?.model) {
      setModel(draft.runtime.model);
      setModelManualOverride(true);
    }
    if (draft.runtime?.effort) {
      setEffort(draft.runtime.effort);
      setEffortManualOverride(true);
    }
    if (draft.runtime?.modelTier != null) {
      setModelTier(String(draft.runtime.modelTier));
    }
    if (draft.runtime?.tierFallback === "strict" || draft.runtime?.tierFallback === "clamp") {
      setTierFallback(draft.runtime.tierFallback);
    }
    if (draft.instructions) {
      setSteps([
        createStepStateEntry(1, {
          title: "Remediate target workflow",
          instructions: draft.instructions,
        }),
      ]);
      setNextStepNumber(2);
    }
  }, [pageMode.mode, setSubmitMessage]);

  const remediationTargetFreshnessQuery = useQuery({
    queryKey: [
      "workflow-start",
      "remediation-target-freshness",
      remediationDraft?.target.workflowId || "",
      remediationDraft?.target.runId || "",
      temporalDetailEndpoint,
    ],
    enabled: Boolean(remediationDraft?.target.workflowId),
    queryFn: async (): Promise<TemporalTaskEditingExecutionContract> => {
      const workflowId = String(remediationDraft?.target.workflowId || "");
      const response = await fetch(
        configuredTemporalDetailUrl(temporalDetailEndpoint, workflowId),
        { headers: { Accept: "application/json" } },
      );
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to check remediation target freshness.",
          ),
        );
      }
      return (await response.json()) as TemporalTaskEditingExecutionContract;
    },
  });

  const remediationTargetFreshnessWarning = useMemo(() => {
    if (!remediationDraft) {
      return "";
    }
    if (remediationTargetFreshnessQuery.isError) {
      return "Target freshness could not be checked. Review the pinned run before submitting.";
    }
    const currentRunId = String(
      remediationTargetFreshnessQuery.data?.runId ||
        remediationTargetFreshnessQuery.data?.temporalRunId ||
        "",
    ).trim();
    if (currentRunId && currentRunId !== remediationDraft.target.runId) {
      return "Target workflow changed after this remediation draft was created. Open Remediate again before submitting.";
    }
    return "";
  }, [
    remediationDraft,
    remediationTargetFreshnessQuery.data?.runId,
    remediationTargetFreshnessQuery.data?.temporalRunId,
    remediationTargetFreshnessQuery.isError,
  ]);

  const dependencyOptionsQuery = useQuery({
    queryKey: ["workflow-start", "dependency-options", temporalListEndpoint],
    queryFn: async (): Promise<DependencyPickerExecution[]> => {
      const response = await fetch(
        withQueryParams(temporalListEndpoint, {
          source: "temporal",
          pageSize: "50",
          workflowType: "MoonMind.UserWorkflow",
          entry: "user_workflow",
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
          String(item.workflowType || "MoonMind.UserWorkflow") === "MoonMind.UserWorkflow" &&
          String(item.entry || "user_workflow") === "user_workflow",
      );
    },
  });

  const skillsQuery = useQuery({
    ...configQueryDefaults,
    queryKey: ["workflow-start", "skills"],
    queryFn: async (): Promise<SkillCatalogResult> => {
      const response = await fetch("/api/workflows/skills", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load skills."),
        );
      }
      const data = (await response.json()) as SkillsResponse;
      const detailsById: Record<string, SkillCapabilityDetail> = {};
      for (const item of data.legacyItems || []) {
        const id = String(item.id || "").trim();
        if (!id) {
          continue;
        }
        detailsById[id] = {
          id,
          description: String(item.description || "").trim(),
          ...(item.inputSchema ? { inputSchema: item.inputSchema } : {}),
          ...(item.uiSchema ? { uiSchema: item.uiSchema } : {}),
          ...(item.defaults ? { defaults: item.defaults } : {}),
          ...(item.contractDigest ? { contractDigest: item.contractDigest } : {}),
          ...(item.contentDigest ? { contentDigest: item.contentDigest } : {}),
          ...(item.contentRef ? { contentRef: item.contentRef } : {}),
          ...(item.source ? { source: item.source } : {}),
          ...(Array.isArray(item.diagnostics)
            ? { diagnostics: item.diagnostics }
            : {}),
          ...(Array.isArray(item.requiredCapabilities)
            ? {
                requiredCapabilities: item.requiredCapabilities
                  .map((capability) => String(capability || "").trim().toLowerCase())
                  .filter(Boolean),
              }
            : {}),
          ...(item.publish &&
          typeof item.publish === "object" &&
          !Array.isArray(item.publish)
            ? { publish: item.publish }
            : {}),
          ...(item.sideEffect &&
          typeof item.sideEffect === "object" &&
          !Array.isArray(item.sideEffect)
            ? { sideEffect: item.sideEffect }
            : {}),
        };
      }
      const ids = Array.from(
        new Set(
          Object.values(data.items || {})
            .flatMap((items) => items || [])
            .map((id) => String(id || "").trim())
            .filter(Boolean),
        ),
      );
      return {
        ids: ids.length > 0 ? ids : Object.keys(detailsById),
        detailsById,
      };
    },
  });

  useEffect(() => {
    const primarySkill = String(steps[0]?.skillId || "")
      .trim()
      .toLowerCase();
    const primarySkillDetail =
      skillsQuery.data?.detailsById[primarySkill] || null;
    if (isSelfManagedPublishSkill(primarySkill, primarySkillDetail)) {
      setPublishMode("auto");
    } else if (isRepositoryPublishDisabledSkill(primarySkill, primarySkillDetail)) {
      setPublishMode("none");
    }
  }, [skillsQuery.data?.detailsById, steps[0]?.skillId]);

  const hasToolStep = steps.some((step) => step.stepType === "tool");
  const trustedToolsQuery = useQuery({
    ...configQueryDefaults,
    queryKey: ["workflow-start", "trusted-tools"],
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
            ...(Array.isArray(tool.requiredCapabilities)
              ? {
                  requiredCapabilities: mergeCapabilities(
                    tool.requiredCapabilities,
                  ),
                }
              : {}),
          };
        })
        .filter((tool) => Boolean(tool.name));
    },
  });

  const templateOptionsQuery = useQuery({
    ...configQueryDefaults,
    queryKey: [
      "workflow-start",
      "task-template-catalog",
      taskTemplateListEndpoint,
    ],
    enabled: presetCatalogEnabled,
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
            const data = (await response.json()) as PresetListResponse;
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
    ...configQueryDefaults,
    queryKey: ["workflow-start", "jira", "projects", jiraIntegration?.endpoints.projects],
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
    ...configQueryDefaults,
    queryKey: [
      "workflow-start",
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
    ...configQueryDefaults,
    queryKey: [
      "workflow-start",
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
    ...configQueryDefaults,
    queryKey: [
      "workflow-start",
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
    ...configQueryDefaults,
    queryKey: [
      "workflow-start",
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
    if (!presetCatalogEnabled || !selectedPresetKey) {
      return;
    }
    const selectedStillExists = templateItems.some(
      (item) => item.key === selectedPresetKey,
    );
    if (selectedStillExists) {
      return;
    }
    setSelectedPresetKey("");
  }, [selectedPresetKey, presetCatalogEnabled, templateItems]);

  const selectedPreset =
    templateItems.find((item) => item.key === selectedPresetKey) || null;
  const effectiveSkillId = useMemo(
    () =>
      resolveEffectivePublishSkillId(
        String(steps[0]?.skillId || "").trim() || "auto",
        activeAppliedTemplatesForSteps(appliedTemplates, steps),
      ),
    [appliedTemplates, steps],
  );
  const effectiveSkillDetail =
    skillsQuery.data?.detailsById[effectiveSkillId.trim()] || null;

  useEffect(() => {
    if (
      pageMode.mode === "create" &&
      selectedPreset &&
      isSelfManagedPublishSkill(selectedPreset.slug)
    ) {
      setPublishMode("auto");
    } else if (
      pageMode.mode === "create" &&
      selectedPreset &&
      isRepositoryPublishDisabledSkill(selectedPreset.slug)
    ) {
      setPublishMode("none");
    }
  }, [pageMode.mode, selectedPreset?.slug]);

  const mergeAutomationAvailable =
    !isSelfManagedPublishSkill(effectiveSkillId, effectiveSkillDetail) &&
    !isRepositoryPublishDisabledSkill(effectiveSkillId, effectiveSkillDetail);
  const autoPublishAvailable = isSelfManagedPublishSkill(
    effectiveSkillId,
    effectiveSkillDetail,
  );

  useEffect(() => {
    if (autoPublishAvailable) {
      if (publishMode !== "auto" && publishMode !== "none") {
        setPublishMode("auto");
      }
    } else if (!mergeAutomationAvailable) {
      if (publishMode !== "none") {
        setPublishMode("none");
      }
    } else if (publishMode === "auto" && !autoPublishAvailable) {
      setPublishMode("pr");
    }
  }, [
    autoPublishAvailable,
    effectiveSkillId,
    mergeAutomationAvailable,
    publishMode,
  ]);

  const availableDependencyOptions = useMemo(
    () =>
      (dependencyOptionsQuery.data || []).filter(
        (item) => !selectedDependencies.includes(dependencyWorkflowId(item)),
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
    const nextInstructions = writeJiraImportedText(
      targetStep.instructions,
      selectedJiraImportText,
      jiraWriteMode,
    );
    const presetInputUpdate =
      targetStep.stepType === "preset"
        ? presetJiraIssueInputValuesFromIssue(
            targetStep.presetDetail,
            targetStep.presetInputValues,
            issue,
          )
        : { values: targetStep.presetInputValues, changedNames: [] };
    const nextPresetInputErrors =
      presetInputUpdate.changedNames.length > 0
        ? Object.fromEntries(
            Object.entries(targetStep.presetInputErrors).filter(
              ([name]) => !presetInputUpdate.changedNames.includes(name),
            ),
          ) as Record<string, string>
        : targetStep.presetInputErrors;
    updateStep(importTarget.localId, {
      instructions: nextInstructions,
      ...(presetInputUpdate.changedNames.length > 0
        ? {
            presetInputValues: presetInputUpdate.values,
            presetInputErrors: nextPresetInputErrors,
          }
        : {}),
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
      setSelectedDependencyWorkflowId("");
      setDependencyMessage("That prerequisite is already selected.");
      return;
    }
    if (selectedDependencies.length >= DEPENDENCY_LIMIT) {
      setSelectedDependencyWorkflowId("");
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
    setAttachmentTargetErrors((current) => {
      const next = { ...current };
      delete next[targetKey];
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

  const providerOptions = [...activeProviderProfiles]
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
      // Default profiles read better as the provider name (e.g. "Anthropic",
      // "OpenAI", "Google") than as their auto-seeded account label. The
      // " (Default)" suffix is appended at render time.
      label:
        profile.is_default && profile.provider_label
          ? profile.provider_label
          : profile.account_label || profile.profile_id,
      isDefault: Boolean(profile.is_default),
    }));
  const selectedOmnigentReadiness = (omnigentCatalogQuery.data?.executionProfiles || []).find(
    (profile) => profile.ref === omnigentExecutionTargetRef,
  );
  const selectableOmnigentProfiles = omnigentProfiles.filter((profile) =>
    (omnigentCatalogQuery.data?.executionProfiles || []).some(
      (readiness) => readiness.ref === profile.ref && readiness.available,
    ),
  );
  const selectableOmnigentPolicies = omnigentPolicies.filter((policy) =>
    selectedOmnigentReadiness?.policyRefs.includes(String(policy.ref || "")),
  );
  const selectedEligibleOmnigentProfile = (omnigentCatalogQuery.data?.eligibleProviderProfiles || []).find(
    (profile) => profile.profileId === providerProfile,
  );
  const historicalOmnigentProviderProfile = (omnigentCatalogQuery.data?.ineligibleProviderProfiles || []).find(
    (profile) => profile.profileId === providerProfile,
  );
  const selectedOmnigentPolicyAvailable = selectableOmnigentPolicies.some(
    (policy) => policy.ref === omnigentLaunchPolicyRef,
  );
  const omnigentSelectionGateReason =
    selectedOmnigentReadiness?.gateReasons?.[0]?.message ||
    historicalOmnigentProviderProfile?.gateReasons?.[0]?.message ||
    omnigentCatalogQuery.data?.gateReasons?.[0]?.message ||
    (!selectedEligibleOmnigentProfile
      ? "Choose an eligible Codex OAuth Provider Profile."
      : selectedEligibleOmnigentProfile.busy && !selectedEligibleOmnigentProfile.queueWhenBusy
          ? "The selected Provider Profile is busy and does not support queued waiting."
          : !selectedOmnigentPolicyAvailable
            ? "Choose a compatible Omnigent host policy."
            : null);
  const omnigentSelectionEligible =
    runtime !== "omnigent" ||
    (omnigentCatalogQuery.data?.available === true &&
      selectedOmnigentReadiness?.available === true &&
      Boolean(selectedEligibleOmnigentProfile) &&
      selectedOmnigentPolicyAvailable &&
      !(selectedEligibleOmnigentProfile?.busy && !selectedEligibleOmnigentProfile.queueWhenBusy));

  const selectedProviderProfileForPreview = providerProfilesQuery.isPlaceholderData
    ? undefined
    : activeProviderProfiles.find(
        (profile) => profile.profile_id === providerProfile,
      );
  const selectedProfileSupportsModelControls =
    runtime !== "omnigent" ||
    Boolean(
      selectedProviderProfileForPreview &&
        ((selectedProviderProfileForPreview.model_tiers?.length || 0) > 0 ||
          selectedProviderProfileForPreview.default_model ||
          selectedProviderProfileForPreview.default_effort),
    );
  const workflowTierPreview = previewModelTier(
    selectedProviderProfileForPreview,
    modelTier,
  );

  // MM-936: the primary step always carries the publish-mode capability (gh)
  // when publishing as a PR, surfaced as a non-removable derived chip.
  const stepPublishModeRequiresGh =
    normalizePublishModeForSubmit(publishMode) === "pr";
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
            String(dashboardConfig.system?.defaultModel || ""),
            String(dashboardConfig.system?.defaultTaskModel || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultModel,
      dashboardConfig.system?.defaultTaskModel,
      defaultTaskModelByRuntime,
      runtime,
    ],
  );

  const skillComboboxOptions = useMemo(() => {
    const ids = skillsQuery.data?.ids || [];
    return Array.from(new Set(["auto", ...ids]));
  }, [skillsQuery.data?.ids]);

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
            String(dashboardConfig.system?.defaultEffort || ""),
            String(dashboardConfig.system?.defaultTaskEffort || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultEffort,
      dashboardConfig.system?.defaultTaskEffort,
      defaultTaskEffortByRuntime,
      runtime,
    ],
  );

  const branchLookupEndpoint = normalizeMoonMindApiPath(
    dashboardConfig.sources?.github?.branches,
  );
  const submittedRepository = repository.trim();
  const selectedRepositoryForBranchLookup =
    submittedRepository || defaultRepository;
  const branchLookupRepository = canLookupRepositoryBranches(
    selectedRepositoryForBranchLookup,
  )
    ? selectedRepositoryForBranchLookup.trim()
    : "";
  const branchOptionsQuery = useQuery({
    ...configQueryDefaults,
    queryKey: ["workflow-start", "github-branches", branchLookupRepository],
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
    (!branchTouched && pageMode.mode === "create" && submittedRepository
      ? defaultBranch
      : "");
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
    !branchLookupRepository;
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
    presetDetail: PresetDetail | null = null,
  ) {
    updateStep(localId, {
      presetKey,
      presetInputValues: {},
      presetInputErrors: {},
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
    expectedInputValues?: Record<string, unknown>,
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
    const catalogDetail = preset ? presetDetailFromCatalogItem(preset) : null;
    updateStepPreset(localId, presetKey, catalogDetail);
    if (!preset) {
      return;
    }
    if (
      pageMode.mode === "create" &&
      isSelfManagedPublishSkill(preset.slug)
    ) {
      setPublishMode("auto");
    } else if (
      pageMode.mode === "create" &&
      isRepositoryPublishDisabledSkill(preset.slug)
    ) {
      setPublishMode("none");
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
          updates.skillId !== step.skillId
        ) {
          nextStep.presetInputValues = {};
          nextStep.presetInputErrors = {};
          if (!showAdvancedStepOptions) {
            nextStep.skillArgs = "";
          }
        }
        if (Object.prototype.hasOwnProperty.call(updates, "toolInputValues")) {
          nextStep.toolInputs = serializeToolInputValues(nextStep.toolInputValues);
          nextStep.toolInputErrors = {};
        }
        return nextStep;
      }),
    );
  }

  // MM-936: Append one or more explicit capability tokens to a step. Tokens are
  // normalized and de-duplicated; existing tokens (including derived ones) are
  // preserved. The chip selector only ever mutates the explicit field.
  function addStepCapabilities(localId: string, tokens: string[]) {
    const normalized = mergeCapabilities(tokens);
    if (normalized.length === 0) {
      return;
    }
    setSteps((current) =>
      current.map((step) =>
        step.localId === localId
          ? {
              ...step,
              explicitRequiredCapabilities: mergeCapabilities(
                step.explicitRequiredCapabilities,
                normalized,
              ),
            }
          : step,
      ),
    );
  }

  function removeStepCapability(localId: string, token: string) {
    const normalized = normalizeCapabilityToken(token);
    setSteps((current) =>
      current.map((step) =>
        step.localId === localId
          ? {
              ...step,
              explicitRequiredCapabilities:
                step.explicitRequiredCapabilities.filter(
                  (existing) =>
                    normalizeCapabilityToken(existing) !== normalized,
                ),
            }
          : step,
      ),
    );
  }

  function promptForCustomStepCapability(localId: string) {
    const value = window.prompt(
      "Add a custom capability token (for example: unity, qdrant). Separate multiple tokens with commas.",
    );
    if (value) {
      addStepCapabilities(localId, parseCapabilitiesCsv(value));
    }
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
    const tool =
      (trustedToolsQuery.data || []).find(
        (candidate) => toolDefinitionId(candidate) === toolId,
      ) || null;
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId) {
          return step;
        }
        const nextValues = initializeToolInputValues(
          tool,
          step.toolInputValues,
          step.toolInputs,
        );
        return {
          ...step,
          stepType: "tool",
          toolId,
          toolInputValues: nextValues,
          toolInputs: serializeToolInputValues(nextValues),
          toolInputErrors: {},
          toolJsonMode: false,
        };
      }),
    );
    setStepJiraProvenance((current) => {
      if (!current[localId]) {
        return current;
      }
      const { [localId]: _removed, ...rest } = current;
      return rest;
    });
    setJiraTransitionStateByStep((current) => {
      if (!current[localId]) {
        return current;
      }
      const { [localId]: _removed, ...rest } = current;
      return rest;
    });
  }

  function updateToolInputValue(localId: string, name: string, value: unknown) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    const nextValues = { ...(step?.toolInputValues || {}), [name]: value };
    updateStep(localId, { toolInputValues: nextValues });
  }

  function updatePentestScopeDraft(
    localId: string,
    updates: Partial<PentestScopeDraftState>,
  ) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    updateStep(localId, {
      pentestScopeDraft: {
        ...(step?.pentestScopeDraft || createPentestScopeDraftState()),
        ...updates,
      },
    });
  }

  function updateGeneratedPentestScopeValue(
    localId: string,
    name: string,
    value: unknown,
  ) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    const draft = step?.pentestScopeDraft || createPentestScopeDraftState();
    updatePentestScopeDraft(localId, {
      generatedScopeValues: {
        ...draft.generatedScopeValues,
        [name]: value,
      },
      validationErrors: {},
    });
  }

  async function attachPentestScopeArtifact(
    localId: string,
    scope: Record<string, unknown>,
  ) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    if (!step) {
      return;
    }
    const target = String(step.toolInputValues.target || "").trim();
    const errors = validatePentestScopeDocument(scope, target);
    if (!step.pentestScopeDraft.confirmAuthorized) {
      errors.authorization = "Confirm authorization before attaching scope.";
    }
    if (Object.keys(errors).length > 0) {
      updatePentestScopeDraft(localId, {
        validationErrors: errors,
        uploadStatus: "failed",
      });
      return;
    }
    updatePentestScopeDraft(localId, {
      uploadStatus: "uploading",
      validationErrors: {},
    });
    try {
      const body = JSON.stringify(scope, null, 2);
      const artifact = await createJsonArtifact(
        artifactCreateEndpoint,
        body,
        {
          label: `Approved Pentest Scope - ${target || "target"}`,
          artifact_type: "approved_pentest_scope",
          target: target || null,
          source: "workflow-start-pentest-scope",
          tool: PENTEST_TOOL_ID,
          scope_id: String(scope.scope_id || "").trim(),
          environment: String(recordValue(scope.metadata).environment || "development"),
        },
        "approved Pentest scope artifact",
      );
      const nextValues = {
        ...step.toolInputValues,
        scope_artifact_ref: artifact.artifactId,
      };
      updateStep(localId, {
        toolInputValues: nextValues,
        pentestScopeDraft: {
          ...step.pentestScopeDraft,
          attachedArtifactId: artifact.artifactId,
          attachedArtifactRef: artifact.artifactId,
          attachedTarget: target,
          attachedOperationMode: String(step.toolInputValues.operation_mode || "").trim(),
          attachedRunnerProfileId: String(step.toolInputValues.runner_profile_id || "").trim(),
          validationErrors: {},
          validationWarnings: [],
          uploadStatus: "attached",
        },
      });
    } catch (error) {
      const failure =
        error instanceof Error
          ? error
          : new Error("Failed to attach approved Pentest scope.");
      updatePentestScopeDraft(localId, {
        uploadStatus: "failed",
        validationErrors: { upload: failure.message },
      });
    }
  }

  async function attachGeneratedPentestScope(localId: string) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    if (!step) {
      return;
    }
    await attachPentestScopeArtifact(
      localId,
      buildPentestApprovedScope(step.pentestScopeDraft, step.toolInputValues),
    );
  }

  async function handlePentestScopeFile(
    localId: string,
    file: File | undefined,
  ) {
    if (!file) {
      return;
    }
    try {
      updatePentestScopeDraft(localId, {
        uploadStatus: "validating",
        uploadedScopeFileName: file.name,
        validationErrors: {},
      });
      const parsed = JSON.parse(await file.text()) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Uploaded scope must be a JSON object.");
      }
      updatePentestScopeDraft(localId, {
        uploadedScopePreview: parsed as Record<string, unknown>,
        uploadStatus: "idle",
      });
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Invalid uploaded scope JSON.");
      const step = stepsRef.current.find((item) => item.localId === localId);
      updateStep(localId, {
        pentestScopeDraft: {
          ...(step?.pentestScopeDraft || createPentestScopeDraftState()),
          uploadStatus: "failed",
          validationErrors: { upload: failure.message },
        },
      });
    }
  }

  async function attachUploadedPentestScope(localId: string) {
    const step = stepsRef.current.find((item) => item.localId === localId);
    const scope = step?.pentestScopeDraft.uploadedScopePreview;
    if (!step || !scope) {
      updatePentestScopeDraft(localId, {
        validationErrors: { upload: "Choose a valid JSON scope file first." },
      });
      return;
    }
    await attachPentestScopeArtifact(localId, scope);
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
      toolInputValues: {
        ...step.toolInputValues,
        transitionId,
        targetStatus: undefined,
      },
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

        const nextStep: StepState = {
          ...step,
          stepType: nextType,
        };

        // MM-936: explicitRequiredCapabilities is a step-level field that is
        // merged for every step type, so it must be preserved across type
        // changes. Only the skill-specific id/args are cleared here.
        if (
          step.stepType === "skill" &&
          (step.skillId.trim() || step.skillArgs.trim())
        ) {
          nextStep.skillId = "";
          nextStep.skillArgs = "";
        }

        if (
          step.stepType === "tool" &&
          (step.toolId.trim() ||
            (step.toolInputs.trim() && step.toolInputs.trim() !== "{}"))
        ) {
          nextStep.toolId = "";
          nextStep.toolInputs = "{}";
          nextStep.toolInputValues = {};
          nextStep.toolInputErrors = {};
          nextStep.toolJsonMode = false;
          nextStep.pentestScopeDraft = createPentestScopeDraftState();
        }

        if (
          step.stepType === "preset" &&
          (step.presetKey ||
            Object.keys(step.presetInputValues).length > 0)
        ) {
          nextStep.presetKey = "";
          nextStep.presetInputValues = {};
          nextStep.presetInputErrors = {};
          nextStep.presetDetail = null;
          nextStep.presetMessage = null;
        }

        return nextStep;
      }),
    );
  }

  function stepRuntimePayload(step: StepState): Record<string, string | number> | null {
    const mode = (step.runtimeMode || "").trim();
    const modelValue = (step.runtimeModel || "").trim();
    const effortValue = (step.runtimeEffort || "").trim();
    const profileId = (step.runtimeProviderProfile || "").trim();
    const modelTierValue = (step.runtimeModelTier || "").trim();
    const modelTier = Number.parseInt(modelTierValue, 10);
    const hasModelTier = Number.isInteger(modelTier) && modelTier >= 1;
    const tierFallback = step.runtimeTierFallback === "strict" ? "strict" : "clamp";
    if (!mode && !modelValue && !effortValue && !profileId && !hasModelTier && tierFallback !== "strict") {
      return null;
    }
    return {
      ...(mode ? { mode } : {}),
      ...(modelValue ? { model: modelValue } : {}),
      ...(effortValue ? { effort: effortValue } : {}),
      ...(profileId ? { profileId } : {}),
      ...(hasModelTier ? { modelTier } : {}),
      ...(hasModelTier || tierFallback === "strict" ? { tierFallback } : {}),
    };
  }

  function updateStepPresetInputValue(
    localId: string,
    definition: Pick<PresetInputDefinition, "name">,
    value: unknown,
  ) {
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId) {
          return step;
        }
        const { [definition.name]: _removed, ...remainingErrors } =
          step.presetInputErrors;
        return {
          ...step,
          presetInputValues: {
            ...step.presetInputValues,
            [definition.name]: value,
          },
          presetInputErrors: remainingErrors,
        };
      }),
    );
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
    inputs: PresetInputDefinition[],
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
    definition: PresetInputDefinition,
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
  ): Promise<PresetDetail> {
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
    return (await response.json()) as PresetDetail;
  }

  async function expandPresetForDraft({
    preset,
    detail,
    inputValues,
    featureRequestOverride,
    submitIntent,
  }: {
    preset: TemplateOption;
    detail: PresetDetail;
    inputValues: Record<string, unknown>;
    featureRequestOverride?: string;
    submitIntent?: string;
  }): Promise<PresetExpansionState> {
    const scopeParams = {
      scope: preset.scope,
      scopeRef: preset.scopeRef || undefined,
    };
    const schemaDriven = schemaContractHasFields(detail);
    const { values: inputs, assumptions } = schemaDriven
      ? {
          values: resolveSchemaCapabilityValues(
            detail,
            inputValues,
            featureRequestOverride,
          ),
          assumptions: [] as string[],
        }
      : resolveTemplateInputs(
          detail.inputs || [],
          inputValues,
          featureRequestOverride,
        );
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
    const expanded = (await expandResponse.json()) as PresetExpandResponse;
    const expandedSteps = expanded.steps || [];
    return {
      presetKey: preset.key,
      presetTitle: preset.title,
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
    detail: PresetDetail,
    expansion: PresetExpansionState,
    expandedSteps: StepState[],
  ): AppliedTemplateState | null {
    if (expandedSteps.length === 0) {
      return null;
    }
    const appliedTemplate = expansion.appliedTemplate || {};
    return {
      slug: String(appliedTemplate.slug || preset.slug),
      ...(String(appliedTemplate.presetDigest || detail.presetDigest || preset.presetDigest || "").trim()
        ? {
            presetDigest: String(
              appliedTemplate.presetDigest || detail.presetDigest || preset.presetDigest,
            ).trim(),
          }
        : {}),
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
      ...(appliedTemplate.composition &&
      typeof appliedTemplate.composition === "object"
        ? { composition: appliedTemplate.composition }
        : {}),
      ...(Array.isArray(appliedTemplate.authoredPresets)
        ? { authoredPresets: appliedTemplate.authoredPresets }
        : {}),
    };
  }

  function recordAppliedPreset(
    preset: TemplateOption,
    detail: PresetDetail,
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
    detail: PresetDetail;
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
      ? ` ${preset.title} manages its own PR/publish flow, so Publish Mode is Auto and merge automation is unavailable.`
      : isRepositoryPublishDisabledSkill(
            String(expansion.appliedTemplate?.slug || preset.slug),
          )
        ? ` ${preset.title} performs non-repository side effects, so Publish Mode is None.`
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
      if (schemaContractHasFields(detail)) {
        const validationErrors = validateSchemaCapabilityValues(
          detail,
          resolveSchemaCapabilityValues(
            detail,
            step.presetInputValues,
            step.instructions,
          ),
        );
        if (Object.keys(validationErrors).length > 0) {
          updateStepPresetIfCurrent(localId, preset.key, {
            presetInputErrors: validationErrors,
            presetMessage: Object.values(validationErrors)[0] || "Preset input is required.",
          });
          return;
        }
      }
      const expansion = await expandPresetForDraft({
        preset,
        detail,
        inputValues: resolvedPresetInputValues(
          detail,
          step.presetInputValues,
          step.instructions,
        ),
        featureRequestOverride: step.instructions,
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

  async function handleSaveCurrentStepsAsPreset(nameOverride: string): Promise<boolean> {
    if (!presetSaveEnabled || isSavingPreset) {
      return false;
    }
    const title = nameOverride.trim();
    if (!title) {
      setTemplateMessage("Enter a preset name before saving.");
      return false;
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
      // MM-936: explicit capabilities are authored through the always-visible
      // chip selector, so they persist into presets regardless of Advanced mode.
      const caps = step.explicitRequiredCapabilities;
      const skillArgsRaw = showAdvancedStepOptions ? step.skillArgs.trim() : "";
      const skillDetail = skillId
        ? skillsQuery.data?.detailsById[skillId] || null
        : null;
      const structuredSkillInputs = schemaSkillInputs(
        skillDetail,
        step.presetInputValues,
      );
      if (Object.keys(structuredSkillInputs.errors).length > 0) {
        updateStep(step.localId, {
          presetInputErrors: structuredSkillInputs.errors,
        });
        setTemplateMessage(
          Object.values(structuredSkillInputs.errors)[0] ||
            `Complete required Skill input fields before saving Step ${index + 1}.`,
        );
        return false;
      }
      if (
        skillId ||
        skillArgsRaw ||
        Object.keys(structuredSkillInputs.values).length > 0 ||
        caps.length > 0
      ) {
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
            return false;
          }
        }
        skillArgs = mergeSkillArgsWithSchemaInputs(
          skillArgs,
          structuredSkillInputs.values,
        );
        const normalizedTool = {
          type: "skill",
          name: skillId || "auto",
          inputs: skillArgs,
          ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
        };
        blueprint.tool = normalizedTool;
        const selectedSkillDetail =
          skillsQuery.data?.detailsById[normalizedTool.name] || null;
        blueprint.skill = skillPayloadWithInputs({
          skillId: normalizedTool.name,
          inputs: skillArgs,
          savedInputContractDigest: selectedSkillDetail?.contractDigest,
          currentInputContractDigest: selectedSkillDetail?.contractDigest,
          requiredCapabilities: caps,
          detail: selectedSkillDetail,
        });
      }
      presetSteps.push(blueprint);
    }

    if (presetSteps.length === 0) {
      setTemplateMessage(
        "Add at least one step with instructions before saving.",
      );
      return false;
    }

    setIsSavingPreset(true);
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
      return true;
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to save preset.");
      setTemplateMessage(`Failed to save preset: ${failure.message}`);
      return false;
    } finally {
      setIsSavingPreset(false);
    }
  }

  async function handleDeleteSelectedPreset(nameOverride: string): Promise<boolean> {
    if (!presetSaveEnabled || isDeletingPreset) {
      return false;
    }
    const normalized = nameOverride.trim().toLowerCase();
    if (!normalized) {
      setTemplateMessage("Enter a preset name to delete.");
      return false;
    }
    const personalItems = templateItems.filter(
      (item) => item.scope === "personal",
    );
    const matchesName = (item: (typeof templateItems)[number]) =>
      item.title.trim().toLowerCase() === normalized ||
      item.slug.trim().toLowerCase() === normalized;
    const target = personalItems.find(matchesName);
    if (!target) {
      if (templateItems.some(matchesName)) {
        setTemplateMessage("Only personal presets can be deleted.");
      } else {
        setTemplateMessage(`No preset named '${nameOverride.trim()}' found.`);
      }
      return false;
    }

    setIsDeletingPreset(true);
    setTemplateMessage("Deleting preset...");
    try {
      const response = await fetch(
        withQueryParams(
          interpolatePath(taskTemplateDetailEndpoint, {
            slug: target.slug,
          }),
          {
            scope: target.scope,
            scopeRef: target.scopeRef || undefined,
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
      setPresetReapplyNeeded(false);
      setTemplateMessage(`Deleted preset '${target.title}'.`);
      await templateOptionsQuery.refetch();
      return true;
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to delete preset.");
      setTemplateMessage(`Failed to delete preset: ${failure.message}`);
      return false;
    } finally {
      setIsDeletingPreset(false);
    }
  }

  function openPresetSaveDialog() {
    setPresetDialogMode("save");
    setPresetDialogName("");
    setTemplateMessage(null);
  }

  function openPresetDeleteDialog() {
    setPresetDialogMode("delete");
    setPresetDialogName("");
    setTemplateMessage(null);
  }

  function closePresetDialog() {
    setPresetDialogMode(null);
    setPresetDialogName("");
  }

  async function confirmPresetDialog() {
    const mode = presetDialogMode;
    const name = presetDialogName.trim();
    if (!name) {
      return;
    }
    let succeeded = false;
    if (mode === "save") {
      succeeded = await handleSaveCurrentStepsAsPreset(name);
    } else if (mode === "delete") {
      succeeded = await handleDeleteSelectedPreset(name);
    }
    if (succeeded) {
      closePresetDialog();
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
    parametersPatch?: Record<string, unknown> | null;
  }): Promise<void> {
    const updatePayload = buildTemporalArtifactEditUpdatePayload({
      updateName,
      inputArtifactRef,
      parametersPatch: parametersPatch ?? null,
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
          isRerun
            ? "Failed to request workflow rerun."
            : "Failed to save workflow changes.",
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
      if (isRerun) {
        setSubmitMessage(statusText, "ok");
        return;
      }
      try {
        window.sessionStorage.setItem(
          "moonmind.temporalTaskEditing.notice",
          statusText,
        );
      } catch {
        // Success handling should not depend on session storage availability.
      }
      const redirectWorkflowId =
        String(result.execution?.workflowId || "").trim() || workflowId;
      navigateTo(
        `/workflows/${encodeURIComponent(redirectWorkflowId)}?source=temporal`,
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
    updates: Partial<
      Pick<StepState, "presetInputErrors" | "presetMessage" | "submitExpansion">
    >,
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
      setSubmitMessage("Expanding preset...", "pending");

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
        if (schemaContractHasFields(detail)) {
          const validationErrors = validateSchemaCapabilityValues(
            detail,
            resolveSchemaCapabilityValues(
              detail,
              step.presetInputValues,
              step.instructions,
            ),
          );
          if (Object.keys(validationErrors).length > 0) {
            const message =
              Object.values(validationErrors)[0] || "Preset input is required.";
            updatePresetSubmitExpansion(step.localId, {
              presetInputErrors: validationErrors,
              presetMessage: message,
              submitExpansion: {
                status: "failed",
                requestId,
                errorMessage: message,
              },
            });
            throw new Error(message);
          }
        }
        const expansion = await expandPresetForDraft({
          preset,
          detail,
          inputValues: resolvedPresetInputValues(
            detail,
            step.presetInputValues,
            step.instructions,
          ),
          featureRequestOverride: step.instructions,
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
    const normalizedRepository = repository.trim();
    if (normalizedRepository && !isValidRepositoryInput(normalizedRepository)) {
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
    const supportedAgentRuntimeIds = runtimeOptions.map((item) =>
      item.trim().toLowerCase(),
    );
    if (!supportedAgentRuntimeIds.includes(normalizedRuntime)) {
      setSubmitMessage(
        `Runtime must be one of: ${runtimeOptions.join(", ")}.`,
      );
      clearSubmitBusy();
      return;
    }
    if (normalizedRuntime === "omnigent") {
      const refreshed = await omnigentCatalogQuery.refetch();
      if (refreshed.isError) {
        setSubmitMessage("Codex via Omnigent readiness could not be verified.");
        clearSubmitBusy();
        return;
      }
      const catalog = refreshed.data;
      const executionProfile = (catalog?.executionProfiles || []).find(
        (profile) => profile.ref === omnigentExecutionTargetRef,
      );
      const eligibleProfile = catalog?.eligibleProviderProfiles.find(
        (profile) => profile.profileId === providerProfile,
      );
      if (!catalog?.available || !executionProfile?.available) {
        setSubmitMessage(
          executionProfile?.gateReasons[0]?.message ||
            catalog?.gateReasons[0]?.message ||
            "Codex via Omnigent readiness could not be verified.",
        );
        clearSubmitBusy();
        return;
      }
      if (!eligibleProfile) {
        setSubmitMessage(
          "The selected Codex OAuth Provider Profile is no longer eligible. Choose an eligible profile explicitly.",
        );
        clearSubmitBusy();
        return;
      }
      if (!executionProfile.policyRefs.includes(omnigentLaunchPolicyRef)) {
        setSubmitMessage(
          "The selected Omnigent host policy is no longer compatible. Choose an available policy explicitly.",
        );
        clearSubmitBusy();
        return;
      }
      if (eligibleProfile.busy && !eligibleProfile.queueWhenBusy) {
        setSubmitMessage("The selected Provider Profile is busy and does not support queued waiting.");
        clearSubmitBusy();
        return;
      }
    }
    const submittedModelTierValue = selectedProfileSupportsModelControls ? modelTier.trim() : "";
    const submittedModelTier = Number.parseInt(submittedModelTierValue, 10);
    const hasSubmittedModelTier = submittedModelTierValue !== "";
    if (
      hasSubmittedModelTier &&
      (!Number.isInteger(submittedModelTier) || submittedModelTier < 1)
    ) {
      setSubmitMessage("Model tier must be a positive number.");
      clearSubmitBusy();
      return;
    }
    const invalidStepRuntime = submissionSteps.find((step) => {
      const stepRuntime = (step.runtimeMode || "").trim().toLowerCase();
      return (
        stepRuntime === "omnigent" ||
        (stepRuntime && !supportedAgentRuntimeIds.includes(stepRuntime))
      );
    });
    if (invalidStepRuntime) {
      const stepIndex = submissionSteps.indexOf(invalidStepRuntime) + 1;
      setSubmitMessage(
        (invalidStepRuntime.runtimeMode || "").trim().toLowerCase() === "omnigent"
          ? `Step ${stepIndex} cannot use Codex via Omnigent; select it as the workflow runtime instead.`
          : `Step ${stepIndex} runtime must be one of: ${supportedAgentRuntimes.join(", ")}.`,
      );
      clearSubmitBusy();
      return;
    }
    const invalidStepModelTier = submissionSteps.find((step) => {
      const value = (step.runtimeModelTier || "").trim();
      if (!value) {
        return false;
      }
      const parsed = Number.parseInt(value, 10);
      return !Number.isInteger(parsed) || parsed < 1;
    });
    if (invalidStepModelTier) {
      const stepIndex = submissionSteps.indexOf(invalidStepModelTier) + 1;
      setSubmitMessage(`Step ${stepIndex} model tier must be a positive number.`);
      clearSubmitBusy();
      return;
    }

    const normalizedPublishMode = normalizePublishModeForSubmit(publishMode);
    if (!["auto", "none", "branch", "pr"].includes(normalizedPublishMode)) {
      setSubmitMessage("Publish mode must be one of: auto, none, branch, pr.");
      clearSubmitBusy();
      return;
    }

    // Priority and Max Attempts live behind the Advanced mode toggle. When that
    // toggle is off the inputs are unmounted, so fall back to defaults instead
    // of validating or submitting whatever value the hidden state still holds.
    const effectivePriority = showAdvancedStepOptions ? priority : DEFAULT_PRIORITY;
    const effectiveMaxAttempts = showAdvancedStepOptions
      ? maxAttempts
      : DEFAULT_MAX_ATTEMPTS;

    if (!Number.isInteger(effectivePriority)) {
      setSubmitMessage("Priority must be an integer.");
      clearSubmitBusy();
      return;
    }
    if (!Number.isInteger(effectiveMaxAttempts) || effectiveMaxAttempts < 1) {
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
          setSubmitMessage(expanded.warnings.join(" "), "pending");
        } else {
          setSubmitMessage("Starting workflow...", "pending");
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
    const primaryStepIsTool = primaryStep?.stepType === "tool";
    const primarySkillId = primaryStepIsSkill
      ? primaryValidation.value.skillId.trim() || "auto"
      : "auto";
    const activeSubmissionAppliedTemplates = activeAppliedTemplatesForSteps(
      submissionAppliedTemplates,
      submissionSteps,
    );
    submissionAppliedTemplates = activeSubmissionAppliedTemplates;
    const effectiveSubmissionSkillId = primarySkillId;
    const effectivePublishSkillId = resolveEffectivePublishSkillId(
      primarySkillId,
      activeSubmissionAppliedTemplates,
    );
    const effectivePublishSkillDetailForSubmit =
      skillsQuery.data?.detailsById[effectivePublishSkillId.trim()] || null;
    if (
      normalizedPublishMode === "auto" &&
      !isSelfManagedPublishSkill(
        effectivePublishSkillId,
        effectivePublishSkillDetailForSubmit,
      )
    ) {
      setSubmitMessage(
        "Publish mode Auto requires an auto-publish-capable skill.",
      );
      clearSubmitBusy();
      return;
    }
    const effectivePublishMode =
      isSelfManagedPublishSkill(
        effectivePublishSkillId,
        effectivePublishSkillDetailForSubmit,
      )
        ? "auto"
        : isRepositoryPublishDisabledSkill(
            effectivePublishSkillId,
            effectivePublishSkillDetailForSubmit,
          )
          ? "none"
          : normalizedPublishMode;
    if (effectivePublishMode === "branch" && !effectiveBranch) {
      setSubmitMessage(
        "Choose a branch before saving or rerunning this publish-mode workflow.",
      );
      clearSubmitBusy();
      return;
    }
    const primarySkillArgsRaw = primaryStepIsSkill && showAdvancedStepOptions
      ? String(primaryStep?.skillArgs || "").trim()
      : "";
    const primarySkillDetail = primaryStepIsSkill
      ? skillsQuery.data?.detailsById[primaryValidation.value.skillId.trim()] || null
      : null;
    const taskSkillRequiredCapabilities = primaryStepIsSkill
      ? mergeCapabilities(
          primarySkillDetail?.requiredCapabilities,
          primaryStep?.explicitRequiredCapabilities,
        )
      : [];
    const primarySchemaInputs = primaryStep
      ? schemaSkillInputs(primarySkillDetail, primaryStep.presetInputValues)
      : { values: {}, errors: {} };
    if (primaryStep && Object.keys(primarySchemaInputs.errors).length > 0) {
      updateStep(primaryStep.localId, {
        presetInputErrors: primarySchemaInputs.errors,
      });
      setSubmitMessage(
        Object.values(primarySchemaInputs.errors)[0] ||
          "Complete required Skill input fields before submitting.",
      );
      clearSubmitBusy();
      return;
    }

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
    primarySkillArgs = mergeSkillArgsWithSchemaInputs(
      primarySkillArgs,
      primarySchemaInputs.values,
    );
    const primarySavedInputContractDigest =
      primaryStep?.skillInputContractDigest ||
      primarySkillDetail?.contractDigest ||
      "";
    const primaryCurrentInputContractDigest =
      primarySkillDetail?.contractDigest || "";
    const primaryToolDefinition =
      primaryStepIsTool
        ? (trustedToolsQuery.data || []).find(
            (tool) => toolDefinitionId(tool) === primaryStep.toolId.trim(),
          ) || null
        : null;
    const primaryToolDetail = detailFromTrustedTool(primaryToolDefinition);
    const primaryToolRequiredCapabilities =
      primaryStepIsTool && !executableGeneratedToolPayload(primaryStep)
        ? mergeCapabilities(primaryToolDetail?.requiredCapabilities)
        : [];
    let primaryToolInputs: Record<string, unknown> = {};
    if (primaryStepIsTool && !executableGeneratedToolPayload(primaryStep)) {
      if (!primaryStep.toolId.trim()) {
        setSubmitMessage("Select a Tool before submitting a Tool step.");
        clearSubmitBusy();
        return;
      }
      if (primaryToolDetail && schemaContractHasFields(primaryToolDetail)) {
        const structuredInputs = schemaToolInputs(
          primaryToolDetail,
          primaryStep.toolInputValues,
        );
        if (Object.keys(structuredInputs.errors).length > 0) {
          updateStep(primaryStep.localId, {
            toolInputErrors: structuredInputs.errors,
          });
          setSubmitMessage(
            Object.values(structuredInputs.errors)[0] ||
              "Complete required Tool input fields before submitting.",
          );
          clearSubmitBusy();
          return;
        }
        primaryToolInputs = structuredInputs.values;
      } else {
        const parsedToolInputs = parseToolInputsText(primaryStep.toolInputs);
        if (!parsedToolInputs.ok) {
          setSubmitMessage("Step 1 Tool Inputs must be valid JSON object text.");
          clearSubmitBusy();
          return;
        }
        primaryToolInputs = parsedToolInputs.value;
      }
    }

    const primaryStepTool = {
      type: "skill",
      name: primarySkillId,
      ...(Object.keys(primarySkillArgs).length > 0
        ? { inputs: primarySkillArgs }
        : {}),
      ...skillInputContractPayload(primarySkillDetail),
      ...(taskSkillRequiredCapabilities.length > 0
        ? { requiredCapabilities: taskSkillRequiredCapabilities }
        : {}),
    };
    const primaryStepSkill = skillPayloadWithInputs({
      skillId: primarySkillId,
      inputs: primarySkillArgs,
      savedInputContractDigest: primarySavedInputContractDigest,
      currentInputContractDigest: primaryCurrentInputContractDigest,
      requiredCapabilities: taskSkillRequiredCapabilities,
      detail: primarySkillDetail,
    });
    const primaryStepHasSkillOverride =
      hasExplicitSkillSelection(primarySkillId) ||
      Object.keys(primarySkillArgs).length > 0 ||
      Boolean(primarySavedInputContractDigest) ||
      taskSkillRequiredCapabilities.length > 0;

    const parsedAdditionalStepInputs: Array<{
      sourceIndex: number;
      step: StepState;
      skillId: string;
      skillDetail: SkillCapabilityDetail | null;
      skillArgsRaw: string;
      skillArgs: Record<string, unknown>;
      skillInputContractDigest: string;
      currentSkillInputContractDigest: string;
      skillCaps: string[];
      toolCaps: string[];
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
      const stepSkillDetail = stepIsSkill
        ? skillsQuery.data?.detailsById[stepSkillId] || null
        : null;
      const stepSkillCaps = stepIsSkill
        ? mergeCapabilities(
            stepSkillDetail?.requiredCapabilities,
            step.explicitRequiredCapabilities,
          )
        : [];
      const stepToolDefinition =
        stepIsTool
          ? (trustedToolsQuery.data || []).find(
              (tool) => toolDefinitionId(tool) === step.toolId.trim(),
            ) || null
          : null;
      const stepToolDetail = detailFromTrustedTool(stepToolDefinition);
      const stepToolCaps =
        stepIsTool && !generatedToolPayload
          ? mergeCapabilities(stepToolDetail?.requiredCapabilities)
          : [];
      const stepSchemaInputs = schemaSkillInputs(
        stepSkillDetail,
        step.presetInputValues,
      );
      if (Object.keys(stepSchemaInputs.errors).length > 0) {
        updateStep(step.localId, {
          presetInputErrors: stepSchemaInputs.errors,
        });
        setSubmitMessage(
          Object.values(stepSchemaInputs.errors)[0] ||
            `Complete required Skill input fields before submitting Step ${index + 1}.`,
        );
        clearSubmitBusy();
        return;
      }
      const hasAuthoredToolInputs =
        stepIsTool &&
        Boolean(step.toolInputs.trim()) &&
        step.toolInputs.trim() !== "{}";
      const hasRuntimePayload = Boolean(stepRuntimePayload(step));
      const stepAttachmentFiles = selectedStepAttachmentFiles[step.localId] || [];
      const hasStepContent =
        Boolean(step.instructions) ||
        stepAttachmentFiles.length > 0 ||
        step.inputAttachments.length > 0 ||
        (stepIsTool && Boolean(step.toolId.trim())) ||
        hasAuthoredToolInputs ||
        (stepIsTool && Object.keys(step.toolInputValues).length > 0) ||
        Boolean(stepSkillId) ||
        Boolean(stepSkillArgsRaw) ||
        Object.keys(stepSchemaInputs.values).length > 0 ||
        stepSkillCaps.length > 0 ||
        stepToolCaps.length > 0 ||
        Boolean(generatedToolPayload) ||
        Boolean(generatedSkillPayload) ||
        hasRuntimePayload;
      let stepSkillArgs: Record<string, unknown> = {};
      let stepToolInputs: Record<string, unknown> = {};
      if (stepIsTool && !generatedToolPayload) {
        if (hasStepContent && !step.toolId.trim()) {
          setSubmitMessage(`Select a Tool before submitting Step ${index + 1}.`);
          clearSubmitBusy();
          return;
        }
        if (stepToolDetail && schemaContractHasFields(stepToolDetail)) {
          const structuredInputs = schemaToolInputs(
            stepToolDetail,
            step.toolInputValues,
          );
          if (Object.keys(structuredInputs.errors).length > 0) {
            updateStep(step.localId, {
              toolInputErrors: structuredInputs.errors,
            });
            setSubmitMessage(
              Object.values(structuredInputs.errors)[0] ||
                `Complete required Tool input fields before submitting Step ${index + 1}.`,
            );
            clearSubmitBusy();
            return;
          }
          stepToolInputs = structuredInputs.values;
        } else {
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
      stepSkillArgs = mergeSkillArgsWithSchemaInputs(
        stepSkillArgs,
        stepSchemaInputs.values,
      );
      const stepSavedInputContractDigest =
        step.skillInputContractDigest ||
        stepSkillDetail?.contractDigest ||
        "";
      const stepCurrentInputContractDigest =
        stepSkillDetail?.contractDigest || "";
      parsedAdditionalStepInputs.push({
        sourceIndex: index,
        step,
        skillId: stepSkillId,
        skillDetail: stepSkillDetail,
        skillArgsRaw: stepSkillArgsRaw,
        skillArgs: stepSkillArgs,
        skillInputContractDigest: stepSavedInputContractDigest,
        currentSkillInputContractDigest: stepCurrentInputContractDigest,
        skillCaps: stepSkillCaps,
        toolCaps: stepToolCaps,
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
      skillDetail: stepSkillDetail,
      skillArgsRaw: stepSkillArgsRaw,
      skillArgs: stepSkillArgs,
      skillInputContractDigest: stepSkillInputContractDigest,
      currentSkillInputContractDigest: stepCurrentSkillInputContractDigest,
      skillCaps: stepSkillCaps,
      toolCaps: stepToolCaps,
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
      const runtimePayload = stepRuntimePayload(step);
      if (runtimePayload) {
        stepPayload.runtime = runtimePayload;
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
        const toolPayload = manualToolPayload(step, stepToolInputs, stepToolCaps);
        if (toolPayload) {
          stepPayload.tool = toolPayload;
        }
      } else if (stepSkillId || stepSkillArgsRaw || stepSkillCaps.length > 0) {
        const effectiveStepSkillId = stepSkillId || "auto";
        const effectiveStepSkillDetail = stepSkillId ? stepSkillDetail : null;
        stepPayload.tool = {
          type: "skill",
          name: effectiveStepSkillId,
          inputs: stepSkillArgs,
          ...skillInputContractPayload(effectiveStepSkillDetail),
          ...(stepSkillCaps.length > 0
            ? { requiredCapabilities: stepSkillCaps }
            : {}),
        };
        stepPayload.skill = skillPayloadWithInputs({
          skillId: effectiveStepSkillId,
          inputs: stepSkillArgs,
          savedInputContractDigest: stepSkillInputContractDigest,
          currentInputContractDigest: stepCurrentSkillInputContractDigest,
          requiredCapabilities: stepSkillCaps,
          detail: effectiveStepSkillDetail,
        });
        stepSkillRequiredCapabilities.push(...stepSkillCaps);
      }
      additionalSteps.push({ sourceIndex, payload: stepPayload });
    }

    const includePrimaryStepForObjectiveOverride =
      Boolean(primaryInstructionsForSubmit) &&
      objectiveInstructionsForSubmit !== primaryInstructionsForSubmit;
    const primaryRuntimePayload = primaryStep
      ? stepRuntimePayload(primaryStep)
      : null;
    const hasTemplateBoundStep = submissionSteps.some((step) =>
      Boolean((step.id || "").trim()),
    );
    const primaryGeneratedToolPayload =
      executableGeneratedToolPayload(primaryStep) ||
      manualToolPayload(
        primaryStep,
        primaryToolInputs,
        primaryToolRequiredCapabilities,
      );
    const includeExplicitSteps =
      additionalSteps.length > 0 ||
      includePrimaryStepForObjectiveOverride ||
      hasTemplateBoundStep ||
      Boolean(primaryGeneratedToolPayload) ||
      Boolean(primaryRuntimePayload) ||
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
              ...(primaryRuntimePayload
                ? { runtime: primaryRuntimePayload }
                : {}),
            },
          },
          ...additionalSteps,
        ].map((entry) => {
          const sourceStep = submissionSteps[entry.sourceIndex];
          if (!sourceStep) {
            return entry.payload;
          }
          const submittedPayload = entry.payload;
          const payloadAttachments = Array.isArray(
            submittedPayload.inputAttachments,
          )
            ? (submittedPayload.inputAttachments as StepAttachmentRef[])
            : [];
          const effectivePayloadAttachments =
            Array.isArray(submittedPayload.inputAttachments) ||
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
          const sourceInstructionsChanged =
            Boolean(sourceStep.templateStepId) &&
            String(entry.payload.instructions || sourceStep.instructions) !==
              sourceStep.templateInstructions;
          const submittedSource =
            sourceStep.source && Object.keys(sourceStep.source).length > 0
              ? {
                  ...sourceStep.source,
                  ...(sourceInstructionsChanged
                    ? { kind: "detached" }
                    : {}),
                }
              : undefined;
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
            Boolean((sourceStep.id || "").trim()) ||
            Boolean(sourceStep.title.trim()) ||
            Boolean(sourceStep.storyOutput) ||
            Boolean(
              submittedSource && Object.keys(submittedSource).length > 0,
            ) ||
            Boolean(
              sourceStep.jiraOrchestration &&
                Object.keys(sourceStep.jiraOrchestration).length > 0,
            );
          const shouldSubmitStepType =
            submittedStepType === "tool" ||
            Boolean(submittedPayload?.tool) ||
            Boolean(submittedPayload?.skill);
          const submittedStep = {
            ...(shouldPreserveStepId && (sourceStep.id || "").trim()
              ? { id: (sourceStep.id || "").trim() }
              : {}),
            ...(sourceStep.title.trim()
              ? { title: sourceStep.title.trim() }
              : {}),
            ...(hasSubmittedStepShape && shouldSubmitStepType
              ? { type: submittedStepType }
              : {}),
            ...(sourceStep.storyOutput
              ? { storyOutput: sourceStep.storyOutput }
              : {}),
            ...(submittedSource && Object.keys(submittedSource).length > 0
              ? { source: submittedSource }
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
    // MM-936: Explicit step capabilities authored via the chip selector bubble
    // into the task's normalized requiredCapabilities for every step type, not
    // only skill steps. Skill steps additionally carry them on the step payload.
    const explicitStepCapabilities = mergeCapabilities(
      ...submissionSteps.map((step) =>
        step ? step.explicitRequiredCapabilities : [],
      ),
    );
    const toolRequiredCapabilities = mergeCapabilities(
      primaryToolRequiredCapabilities,
      ...parsedAdditionalStepInputs.map((entry) => entry.toolCaps),
    );
    const mergedCapabilities = deriveRequiredCapabilities({
      runtimeMode: normalizedRuntime,
      stepRuntimeModes: normalizedSteps
        .map((step) =>
          String(
            (step as { runtime?: { mode?: unknown } }).runtime?.mode || "",
          ).trim(),
        )
        .filter(Boolean),
      publishMode: effectivePublishMode,
      taskSkillRequiredCapabilities,
      stepSkillRequiredCapabilities,
      toolRequiredCapabilities,
      explicitStepCapabilities,
      templateCapabilities,
      repositoryBacked: Boolean(normalizedRepository),
    });

    const normalizedTaskTool = primaryStepTool;

    // Derive title from feature request / resolved objective when a preset is applied
    // Address: Gemini r3034477058 (trim before split), Copilot r3034495920
    // (derive from objectiveInstructions), Codex r3034482711 / Copilot r3034495938
    // (clamp to backend max of 150).
    const explicitTitle = ((): string | undefined => {
      // Prefer the resolved objective text (which already falls back through
      // feature request, primary instructions, and template inputs) so that
      // preset-driven tasks derive titles from the same source the backend
      // would fall back to.
      return deriveExplicitWorkflowTitle(objectiveInstructions);
    })();

    // Only include task-level agent skill selectors for real instruction bundles.
    const taskSkillSelectors = hasExplicitSkillSelection(
      effectiveSubmissionSkillId,
    )
      ? { include: [{ name: effectiveSubmissionSkillId }] }
      : undefined;
    const submissionAuthoredPresets =
      authoredPresetsFromAppliedTemplates(submissionAppliedTemplates);

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
      !isSelfManagedPublishSkill(
        effectivePublishSkillId,
        effectivePublishSkillDetailForSubmit,
      ) &&
      !isRepositoryPublishDisabledSkill(
        effectivePublishSkillId,
        effectivePublishSkillDetailForSubmit,
      );
    // Never resolve a provider profile from placeholder data: during a runtime
    // switch refetch `data` still holds the previous runtime's profiles, which
    // must not be submitted under the newly selected runtime.
    const selectedProviderProfile = runtime !== "omnigent" && providerProfilesQuery.isPlaceholderData
      ? undefined
      : activeProviderProfiles.find(
          (profile) => profile.profile_id === providerProfile,
        );
    const selectedProviderId = selectedProviderProfile?.provider_id?.trim?.() || "";
    const submittedModel = selectedProfileSupportsModelControls && modelManualOverride ? model.trim() : "";
    const submittedEffort = selectedProfileSupportsModelControls && effortManualOverride ? effort.trim() : "";

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
        ...(hasSubmittedModelTier ? { modelTier: submittedModelTier } : {}),
        ...(selectedProfileSupportsModelControls &&
        (hasSubmittedModelTier || tierFallback === "strict")
          ? { tierFallback }
          : {}),
        ...(submittedModel ? { model: submittedModel } : {}),
        ...(submittedEffort ? { effort: submittedEffort } : {}),
        ...(providerProfile ? { profileId: providerProfile } : {}),
        ...(selectedProviderId
          ? { profileSelector: { providerId: selectedProviderId } }
          : {}),
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
      ...(normalizedRepository && effectiveBranch
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
      ...(submissionAuthoredPresets.length > 0
        ? { authoredPresets: submissionAuthoredPresets }
        : {}),
      ...(selectedDependencies.length > 0
        ? { dependsOn: selectedDependencies }
        : {}),
    };
    if (remediationDraft) {
      taskPayload.remediation = remediationDraft.remediation;
    }

    const requestBody: Record<string, unknown> = {
      type: "task",
      priority: effectivePriority,
      maxAttempts: effectiveMaxAttempts,
      payload: {
        ...(normalizedRepository ? { repository: normalizedRepository } : {}),
        ...(mergedCapabilities.length > 0
          ? { requiredCapabilities: mergedCapabilities }
          : {}),
        targetRuntime: normalizedRuntime,
        ...(normalizedRuntime === "omnigent" && omnigentExecutionTargetRef
          ? {
              omnigent: {
                executionTargetRef: omnigentExecutionTargetRef,
                ...(omnigentLaunchPolicyRef
                  ? { launchPolicyRef: omnigentLaunchPolicyRef }
                  : {}),
              },
            }
          : {}),
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
        temporalDraftData && pageMode.intent !== "comparison"
      ? buildEditParametersPatch({
          execution: temporalDraftData.execution,
          artifactInput: temporalDraftData.artifactInput,
          submittedPayload,
          submittedWorkflow: taskPayload,
        })
      : null;
      const artifactPayload = editParametersPatch ?? submittedPayload;
      const isExactRerunRequest =
        pageMode.mode === "rerun" && pageMode.intent === "rerun";
      if (
        pageMode.intent === "edit-for-rerun" &&
        pageMode.executionId &&
        temporalDraftData?.execution
      ) {
        const sourceWorkflowId = String(pageMode.executionId).trim();
        const sourceRunId = String(
          temporalDraftData.execution.runId ||
            temporalDraftData.execution.temporalRunId ||
            "",
        ).trim();
        if (!sourceWorkflowId || !sourceRunId) {
          throw new Error(
            "Cannot request edited retry because the source execution identity is missing.",
          );
        }
        const editedWorkflowPayload: Record<string, unknown> = {
          ...mergeRecordValues(taskPayload, workflowRecord(artifactPayload)),
          recovery: {
            kind: "edited_full_retry",
            sourceWorkflowId,
            sourceRunId,
          },
        };
        delete editedWorkflowPayload.resume;
        artifactPayload.workflow = editedWorkflowPayload;
        delete artifactPayload.task;
      }
      if (
        pageMode.intent === "comparison" &&
        pageMode.executionId &&
        temporalDraftData?.execution
      ) {
        const sourceWorkflowId = String(pageMode.executionId).trim();
        const sourceRunId = String(
          temporalDraftData.execution.runId ||
            temporalDraftData.execution.temporalRunId ||
            "",
        ).trim();
        if (!sourceWorkflowId || !sourceRunId) {
          throw new Error(
            "Cannot start comparison because the source execution identity is missing.",
          );
        }
        const comparisonTaskPayload: Record<string, unknown> = {
          ...recordValue(artifactPayload.task ?? taskPayload),
          comparison: {
            kind: "model_runtime_comparison",
            sourceWorkflowId,
            sourceRunId,
          },
        };
        delete comparisonTaskPayload.recovery;
        delete comparisonTaskPayload.resume;
        artifactPayload.task = comparisonTaskPayload;
        requestBody.payload = artifactPayload;
      }
      const rerunDraft = temporalDraftData?.draft;
      const currentPublishModeSelection = normalizePublishModeSelection(publishMode);
      const rerunDraftPublishModeSelection = normalizePublishModeSelection(
        rerunDraft?.publishMode,
      );
      const rerunDraftEffectivePublishMode = normalizePublishModeForSubmit(
        rerunDraftPublishModeSelection,
      );
      const rerunFormChanged = isExactRerunRequest
        ? !rerunDraft ||
          selectedAttachmentFiles.length > 0 ||
          normalizedRepository !== String(rerunDraft.repository || "").trim() ||
          normalizedRuntime !== String(rerunDraft.runtime || "").trim() ||
          omnigentExecutionTargetRef.trim() !==
            String(rerunDraft.omnigentExecutionTargetRef || "").trim() ||
          omnigentLaunchPolicyRef.trim() !==
            String(rerunDraft.omnigentLaunchPolicyRef || "").trim() ||
          model.trim() !== String(rerunDraft.model || "").trim() ||
          effort.trim() !== String(rerunDraft.effort || "").trim() ||
          effectivePublishMode !== rerunDraftEffectivePublishMode ||
          currentPublishModeSelection !== rerunDraftPublishModeSelection ||
          produceReport !== Boolean(rerunDraft.reportOutputEnabled) ||
          objectiveInstructionsForSubmit !==
            String(rerunDraft.taskInstructions || "").trim() ||
          JSON.stringify(
            submissionSteps.map((step) => ({
              id: step.id.trim(),
              title: step.title.trim(),
              instructions: step.instructions.trim(),
              skillId: step.skillId.trim(),
              inputAttachments: step.inputAttachments,
            })),
          ) !==
            JSON.stringify(
              rerunDraft.steps.map((step) => ({
                id: String(step.id || "").trim(),
                title: String(step.title || "").trim(),
                instructions: String(step.instructions || "").trim(),
                skillId: String(step.skillId || "").trim(),
                inputAttachments: step.inputAttachments,
              })),
            ) ||
          JSON.stringify(taskLevelAttachmentRefs) !==
            JSON.stringify(rerunDraft.inputAttachments)
        : false;
      const isExactRerun = isExactRerunRequest && !rerunFormChanged;
      const artifactWorkflowPayload = mergeRecordValues(
        taskPayload,
        workflowRecord(artifactPayload),
      );
      const taskInputArtifactBody = JSON.stringify({
        repository: artifactPayload.repository ?? normalizedRepository,
        workflow: artifactWorkflowPayload,
      });
      const taskInputArtifactBytes = utf8ByteLength(taskInputArtifactBody);
      const existingInputArtifactRef = String(
        temporalDraftQuery.data?.execution.inputArtifactRef || "",
      ).trim();
      const shouldCreateInputArtifact =
        !isExactRerun &&
        (taskInputArtifactBytes > INLINE_TASK_INPUT_LIMIT_BYTES ||
          (pageMode.mode !== "create" && Boolean(existingInputArtifactRef)));
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

      if (
        pageMode.mode === "edit" ||
        (pageMode.mode === "rerun" && pageMode.intent !== "comparison")
      ) {
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
          inputArtifactRef: isExactRerun ? null : inputArtifactRef,
          parametersPatch: isExactRerun ? null : artifactPayload,
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
        const detail = await responseErrorDetail(
          response,
          "Failed to start workflow.",
        );
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
      if (remediationDraftIdRef.current) {
        clearRemediationCreateDraft(remediationDraftIdRef.current);
        remediationDraftIdRef.current = null;
      }
      const redirectPath =
        String(created.redirectPath || "").trim() ||
        (created.definitionId
          ? `/schedules/${encodeURIComponent(created.definitionId)}`
          : created.workflowId
            ? `/workflows/${encodeURIComponent(created.workflowId)}?source=temporal`
            : "");
      if (!redirectPath) {
        throw new Error(
          "Workflow was started but no redirect path was returned.",
        );
      }
      navigateTo(redirectPath);
      didNavigateAfterCreate = true;
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to start workflow.");
      // Detect network-level fetch failures (TypeError: "Failed to fetch")
      // and log additional diagnostics to help troubleshoot.
      if (
        failure instanceof TypeError &&
        failure.message === "Failed to fetch"
      ) {
        console.error(
          "[WorkflowStart] Network-level fetch failure during task creation.",
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
          "Couldn't reach the workflow start service. This usually means the " +
            "API is unreachable, a network/connectivity issue, or a CORS policy " +
            "is blocking the request. Check your connection and try again.",
        );
        setSubmitErrorDetail({
          endpoint: temporalCreateEndpoint,
          rawError: `${failure.name}: ${failure.message}`,
        });
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
    pageMode.intent === "comparison"
      ? "Compare Workflow"
      : pageMode.intent === "edit" || pageMode.intent === "edit-for-rerun"
        ? "Edit Workflow"
        : pageMode.mode === "rerun"
          ? "Start New Run"
          : "Start Workflow";
  const visiblePageTitle =
    pageMode.mode === "create" ? workflowStartHeadingQuote : pageTitle;
  const primaryCta =
    pageMode.intent === "comparison"
      ? "Start Comparison Run"
      : pageMode.intent === "edit"
      ? "Save Changes"
      : pageMode.intent === "edit-for-rerun"
        ? "Run edited workflow"
      : pageMode.mode === "rerun"
        ? "Start New Run"
        : "Start Workflow";
  const showPrimaryCtaArrow = true;
  const primaryCtaTooltip =
    pageMode.intent === "comparison"
      ? "Start a new comparison run from this workflow draft"
      : pageMode.intent === "edit"
      ? "Save changes to this workflow draft"
      : pageMode.intent === "edit-for-rerun"
        ? "Start a new run from this edited workflow draft"
      : pageMode.mode === "rerun"
        ? "Start a new run from this workflow draft"
        : "Start this workflow";
  const repositoryTooltip = "Select the GitHub repository for this workflow";
  const branchTooltip = branchOptionsQuery.isLoading
    ? "Loading branches for the selected repository..."
    : branchControlDisabled
      ? branchStatusMessage ||
        "Choose a valid GitHub repository before selecting a branch"
      : "Select the branch to check out before the workflow starts";
  const publishModeTooltip = autoPublishAvailable
    ? "Auto is available because the selected skill owns publishing"
    : !mergeAutomationAvailable
      ? "Repository publishing is unavailable for the selected preset or skill"
      : "Select how MoonMind publishes workflow changes";
  const expandStepPresetTooltip =
    "Expand the selected preset into editable steps at this position";
  const modeLoadError =
    pageMode.mode !== "create" && !temporalTaskEditingEnabled
      ? "Temporal task editing is not enabled."
      : temporalDraftQuery.isError
        ? temporalDraftQuery.error instanceof Error
          ? temporalDraftQuery.error.message
          : "Failed to reconstruct the workflow draft."
        : null;
  const isTemporalFormBlocked =
    pageMode.mode !== "create" &&
    (temporalDraftQuery.isLoading || Boolean(modeLoadError));
  const isSubmitBlocked =
    isTemporalFormBlocked || (runtime === "omnigent" && !omnigentSelectionEligible);

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
    <div className="stack workflow-start-page dashboard-surface dashboard-surface--page">
      <section
        className="workflow-start-heading"
        data-canonical-create-section="Header"
        aria-label="Header"
      >
        <h2 className="page-title">{visiblePageTitle}</h2>
      </section>

      {pageMode.mode !== "create" && temporalDraftQuery.isLoading ? (
        <LoadingPlaceholder
          surface="workflow-start"
          region="editable draft"
          variant="form-controls"
          density="normal"
          preserveContext
          className="notice"
        />
      ) : null}

      {modeLoadError ? (
        <p className="notice error" role="alert">
          {modeLoadError}
        </p>
      ) : null}

      {pageMode.intent === "edit-for-rerun" && !modeLoadError ? (
        <p className="notice" role="status">
          You are editing a previous workflow. Your changes will create a new run.
          The original run will remain unchanged.
        </p>
      ) : null}

      {pageMode.intent === "comparison" && !modeLoadError ? (
        <p className="notice" role="status">
          You are starting a comparison run. Choose a different runtime or model
          to compare results with the source run.
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
        {remediationDraft ? (
          <section
            className="card queue-remediation-draft-summary stack"
            aria-label="Remediation draft"
            data-jira-issue="MM-1119"
          >
            <div className="queue-section-heading">
              <div>
                <h2>Remediation Draft</h2>
                <p className="small">
                  Target {remediationDraft.target.title || remediationDraft.target.workflowId}
                </p>
              </div>
            </div>
            <div className="grid-2">
              <label>
                Target workflow
                <input value={remediationDraft.target.workflowId} readOnly />
              </label>
              <label>
                Pinned run
                <input value={remediationDraft.target.runId} readOnly />
              </label>
              <label>
                Remediation mode
                <input value={remediationDraft.remediation.mode} readOnly />
              </label>
              <label>
                Authority
                <input value={remediationDraft.remediation.authorityMode} readOnly />
              </label>
              <label>
                Action policy
                <input value={remediationDraft.remediation.actionPolicyRef || ""} readOnly />
              </label>
              <label>
                Checkpoint refs
                <input
                  value={String(remediationDraft.target.stepSelectors?.length || 0)}
                  readOnly
                />
              </label>
            </div>
            <p className="small">
              Evidence preview: recovery, incident, step ledger, checkpoint branch, adapter, diagnostics, and linked artifact refs.
            </p>
            {remediationTargetFreshnessWarning ? (
              <p className="notice small" role="alert">
                {remediationTargetFreshnessWarning}
              </p>
            ) : null}
          </section>
        ) : null}
        <section
          className="card queue-steps-section stack"
          data-canonical-create-section="Steps"
          aria-label="Steps"
        >
          <div id="queue-steps-list" className="stack queue-steps-list">
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
              const visiblePresetSchemaFields = schemaContractHasFields(
                step.presetDetail,
              )
                ? Object.entries(
                    schemaProperties(step.presetDetail?.inputSchema),
                  ).filter(([name]) => {
                    if (!isVisiblePresetInputName(step.presetDetail?.slug, name)) {
                      return false;
                    }
                    const uiSchema = capabilityFieldUiSchema(
                      step.presetDetail?.uiSchema,
                      name,
                    );
                    return capabilityFieldVisible(
                      uiSchema,
                      step.presetInputValues,
                      step.presetDetail?.defaults,
                    );
                  })
                : [];
              const selectedSkillDetail =
                skillsQuery.data?.detailsById[step.skillId.trim()] || null;
              const skillContractNotice =
                skillContractDigestNotice(
                  step.skillInputContractDigest,
                  selectedSkillDetail?.contractDigest,
                ) || step.skillInputContractNotice;
              const visibleSkillSchemaFields = schemaContractHasFields(
                selectedSkillDetail,
              )
                ? Object.entries(schemaProperties(selectedSkillDetail?.inputSchema))
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
              const selectedToolDetail = detailFromTrustedTool(selectedTrustedTool);
              const selectedToolRequired = schemaRequired(
                selectedToolDetail?.inputSchema,
              );
              const visibleToolSchemaFields =
                selectedToolDetail && schemaContractHasFields(selectedToolDetail)
                  ? Object.entries(schemaProperties(selectedToolDetail.inputSchema))
                  : [];
              const requiredToolSchemaFields = visibleToolSchemaFields.filter(
                ([name]) => selectedToolRequired.has(name),
              );
              const optionalToolSchemaFields = visibleToolSchemaFields.filter(
                ([name]) => !selectedToolRequired.has(name),
              );
              const selectedPresetDetail =
                step.stepType === "preset" ? step.presetDetail : null;
              const selectedPresetCapabilities = selectedPresetDetail
                ? mergeCapabilities(
                    selectedPresetDetail.requiredCapabilities,
                    selectedPresetDetail.capabilities,
                  )
                : [];
              const stepTemplateCapabilities = templateCapabilitiesForStep(
                appliedTemplates,
                step,
              );
              // MM-936: derive the capability chip row for this step. Explicit
              // chips are removable; preset/skill/tool/runtime/publish/template
              // derived chips are non-removable and carry provenance.
              const stepCapabilityChips = buildCapabilityChips({
                explicit: step.explicitRequiredCapabilities,
                skill: selectedSkillDetail?.requiredCapabilities,
                generatedSkill: step.generatedSkill?.requiredCapabilities,
                tool: selectedToolDetail?.requiredCapabilities,
                generatedTool: step.generatedTool?.requiredCapabilities,
                preset: selectedPresetCapabilities,
                template: stepTemplateCapabilities,
                runtime: step.runtimeMode ? [step.runtimeMode] : [],
                publish:
                  isPrimaryStep && stepPublishModeRequiresGh ? ["gh"] : [],
              });
              const stepCapabilityTokens = stepCapabilityChips.map(
                (chip) => chip.token,
              );
              const stepPreviewProfileId =
                step.runtimeProviderProfile.trim() || providerProfile;
              const stepPreviewProfile = providerProfilesQuery.isPlaceholderData
                ? undefined
                : (providerProfilesQuery.data || []).find(
                    (profile) => profile.profile_id === stepPreviewProfileId,
                  );
              const stepTierPreview = previewModelTier(
                stepPreviewProfile,
                step.runtimeModelTier,
              );
              const isPentestTool = step.toolId.trim() === PENTEST_TOOL_ID;
              const pentestScopeValues = isPentestTool
                ? pentestGeneratedScopeValues(
                    step.pentestScopeDraft,
                    step.toolInputValues,
                  )
                : {};
              const pentestWarnings = isPentestTool
                ? [
                    ...step.pentestScopeDraft.validationWarnings,
                    ...pentestScopeWarnings(
                      step.pentestScopeDraft,
                      step.toolInputValues,
                    ),
                  ]
                : [];
              const jiraTransitionState =
                jiraTransitionStateByStep[step.localId] || null;
              const showJiraTransitionOptions =
                step.stepType === "tool" &&
                step.toolId.trim() === "jira.transition_issue";
              const instructionPreview = deriveRuntimeCommandPreview({
                instructions: step.instructions,
                runtime,
                sourcePath: index === 0
                  ? "objective.instructions"
                  : `steps[${index - 1}].instructions`,
                config: dashboardConfig.system?.runtimeCommandPreview,
                storedRuntimeCommand: step.runtimeCommand,
              });
              const stepAttachmentTargetError =
                attachmentTargetErrors[attachmentTargetKey(step.localId)];
              const stepContextAttachments: StepContextAttachmentItem[] = [
                ...(isPrimaryStep
                  ? persistedObjectiveAttachments.map((attachment) => ({
                      key: `objective-${attachment.artifactId}`,
                      filename: attachment.filename,
                      detail: formatAttachmentBytes(attachment.sizeBytes),
                      targetLabel: "Objective",
                      href: configuredArtifactDownloadUrl(
                        artifactDownloadEndpoint,
                        attachment.artifactId,
                      ),
                      download: attachment.filename,
                      removeLabel: `Remove objective attachment ${attachment.filename}`,
                      onRemove: () =>
                        removePersistedObjectiveAttachment(
                          attachment.artifactId,
                        ),
                    }))
                  : []),
                ...step.inputAttachments.map((attachment) => ({
                  key: `step-${step.localId}-${attachment.artifactId}`,
                  filename: attachment.filename,
                  detail: formatAttachmentBytes(attachment.sizeBytes),
                  targetLabel: `Step ${index + 1}`,
                  href: configuredArtifactDownloadUrl(
                    artifactDownloadEndpoint,
                    attachment.artifactId,
                  ),
                  download: attachment.filename,
                  removeLabel: `Remove Step ${index + 1} attachment ${attachment.filename}`,
                  onRemove: () =>
                    removePersistedStepAttachment(
                      step.localId,
                      attachment.artifactId,
                    ),
                })),
                ...(selectedStepAttachmentFiles[step.localId] || []).map(
                  (file) => ({
                    key: `pending-${step.localId}-${file.name}-${file.size}-${file.lastModified}`,
                    filename: file.name || "attachment",
                    detail: `${file.type || "application/octet-stream"}, ${formatAttachmentBytes(file.size)}`,
                    targetLabel: `Step ${index + 1}`,
                    removeLabel: `Remove Step ${index + 1} attachment ${file.name}`,
                    onRemove: () => removeStepAttachment(step.localId, file),
                    ...(stepAttachmentTargetError
                      ? {
                          retryLabel: `Retry upload for Step ${index + 1} attachment ${file.name}`,
                          onRetry: () =>
                            clearAttachmentTargetError(
                              attachmentTargetKey(step.localId),
                            ),
                        }
                      : {}),
                  }),
                ),
              ];
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

                  <fieldset className="segmented-control-field">
                    <legend className="sr-only">Step Type</legend>
                    <div
                      className="segmented-control"
                      data-intensity="loud"
                      style={{
                        "--segmented-control-count": STEP_TYPE_OPTIONS.length,
                      } as CSSProperties}
                    >
                      {STEP_TYPE_OPTIONS.map((option) => {
                        const Icon = option.Icon;
                        return (
                          <label
                            key={option.value}
                            className="segmented-control-item"
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
                            <span className="segmented-control-item-icon">
                              <Icon />
                            </span>
                            <span className="segmented-control-item-label">
                              {option.label}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  {step.stepType === "tool" ? (
                    <div className="stack segmented-control-panel">
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
                      {isPentestTool ? (
                        <div className="notice small">
                          Runs require an approved scope artifact. Inline scope is
                          not accepted from workflow submission. External targets
                          are disabled unless explicitly enabled by deployment
                          policy. PentestGPT runs through the Claude OAuth
                          runner profile.
                        </div>
                      ) : null}
                      {selectedToolDetail && visibleToolSchemaFields.length > 0 ? (
                        <div className="stack">
                          <SchemaCapabilityFields
                            fields={requiredToolSchemaFields.filter(
                              ([name]) =>
                                !(isPentestTool && name === "scope_artifact_ref"),
                            )}
                            detail={selectedToolDetail}
                            values={step.toolInputValues}
                            errors={step.toolInputErrors}
                            disabled={false}
                            repositoryOptions={repositoryOptions}
                            branchOptions={branchOptions}
                            onChange={(name, value) =>
                              updateToolInputValue(step.localId, name, value)
                            }
                          />
                          {isPentestTool ? (
                            <div className="stack queue-tool-dynamic-options">
                              <strong>Approved Scope</strong>
                              <div
                                className="segmented-control"
                                data-intensity="loud"
                                role="radiogroup"
                                aria-label="Approved Scope mode"
                                style={{
                                  "--segmented-control-count": 3,
                                } as CSSProperties}
                              >
                                {[
                                  ["generate", "Generate from fields"],
                                  ["upload", "Upload JSON"],
                                  ["existing", "Use existing artifact ref"],
                                ].map(([mode, label]) => (
                                  <label
                                    key={mode}
                                    className="segmented-control-item"
                                  >
                                    <input
                                      type="radio"
                                      name={`pentest-scope-mode-${step.localId}`}
                                      value={mode}
                                      checked={step.pentestScopeDraft.mode === mode}
                                      onChange={(event) =>
                                        updatePentestScopeDraft(step.localId, {
                                          mode: event.target.value as PentestScopeMode,
                                          validationErrors: {},
                                        })
                                      }
                                    />
                                    <span className="segmented-control-item-label">
                                      {label}
                                    </span>
                                  </label>
                                ))}
                              </div>
                              {step.pentestScopeDraft.mode === "generate" ? (
                                <div className="grid-2">
                                  <label>
                                    Scope title
                                    <input
                                      value={String(pentestScopeValues.scope_title || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "scope_title",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    Environment
                                    <select
                                      value={String(pentestScopeValues.environment || "development")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "environment",
                                          event.target.value,
                                        )
                                      }
                                    >
                                      {["development", "staging", "internal", "lab", "production"].map((item) => (
                                        <option key={item} value={item}>
                                          {item}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    Target URL
                                    <input
                                      value={String(pentestScopeValues.target_url || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "target_url",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    Target host/FQDN
                                    <input
                                      value={String(pentestScopeValues.target_host || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "target_host",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    Target class
                                    <select
                                      value={String(pentestScopeValues.target_class || "lab")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "target_class",
                                          event.target.value,
                                        )
                                      }
                                    >
                                      {["lab", "internal_authorized", "external_authorized"].map((item) => (
                                        <option key={item} value={item}>
                                          {item}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    Expires at
                                    <input
                                      type="datetime-local"
                                      value={String(pentestScopeValues.expires_at || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "expires_at",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    Approval ticket / reason
                                    <input
                                      value={String(pentestScopeValues.approval_ticket || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "approval_ticket",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    Allowed actions
                                    <select
                                      value={String(
                                        Array.isArray(pentestScopeValues.allowed_actions)
                                          ? pentestScopeValues.allowed_actions.join(",")
                                          : PENTEST_BASELINE_ACTIONS.join(","),
                                      )}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "allowed_actions",
                                          event.target.value.split(",").filter(Boolean),
                                        )
                                      }
                                    >
                                      <option value={PENTEST_BASELINE_ACTIONS.join(",")}>
                                        First-pass baseline
                                      </option>
                                      <option value={PENTEST_VALIDATE_ACTIONS.join(",")}>
                                        Validate hypothesis
                                      </option>
                                      {String(step.toolInputValues.operation_mode || "") ===
                                      "full_authorized" ? (
                                        <option value={PENTEST_SCOPE_ACTIONS.join(",")}>
                                          Full authorized
                                        </option>
                                      ) : null}
                                    </select>
                                  </label>
                                  <label>
                                    Application stack
                                    <input
                                      value={String(pentestScopeValues.application_stack || "")}
                                      onChange={(event) =>
                                        updateGeneratedPentestScopeValue(
                                          step.localId,
                                          "application_stack",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                </div>
                              ) : null}
                              {step.pentestScopeDraft.mode === "upload" ? (
                                <div className="stack">
                                  <label>
                                    Upload approved scope JSON
                                    <input
                                      type="file"
                                      accept="application/json,.json"
                                      onChange={(event) => {
                                        void handlePentestScopeFile(
                                          step.localId,
                                          event.currentTarget.files?.[0],
                                        );
                                        event.currentTarget.value = "";
                                      }}
                                    />
                                  </label>
                                  {step.pentestScopeDraft.uploadedScopeFileName ? (
                                    <p className="small">
                                      {`Selected: ${step.pentestScopeDraft.uploadedScopeFileName}`}
                                    </p>
                                  ) : null}
                                </div>
                              ) : null}
                              {step.pentestScopeDraft.mode === "existing" ? (
                                <label>
                                  Approved scope artifact
                                  <input
                                    value={String(step.toolInputValues.scope_artifact_ref || "")}
                                    placeholder="art_..."
                                    aria-invalid={Boolean(step.toolInputErrors.scope_artifact_ref)}
                                    onChange={(event) =>
                                      updateToolInputValue(
                                        step.localId,
                                        "scope_artifact_ref",
                                        event.target.value,
                                      )
                                    }
                                  />
                                  <span className="small">
                                    ArtifactRef for the approved pentest scope document.
                                  </span>
                                </label>
                              ) : null}
                              <label>
                                <input
                                  type="checkbox"
                                  checked={step.pentestScopeDraft.confirmAuthorized}
                                  onChange={(event) =>
                                    updatePentestScopeDraft(step.localId, {
                                      confirmAuthorized: event.target.checked,
                                      validationErrors: {},
                                    })
                                  }
                                />
                                I confirm I am authorized to test this target within the selected scope.
                              </label>
                              {step.pentestScopeDraft.mode === "generate" ? (
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={step.pentestScopeDraft.uploadStatus === "uploading"}
                                  onClick={() => void attachGeneratedPentestScope(step.localId)}
                                >
                                  Generate and attach scope
                                </button>
                              ) : null}
                              {step.pentestScopeDraft.mode === "upload" ? (
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={step.pentestScopeDraft.uploadStatus === "uploading"}
                                  onClick={() => void attachUploadedPentestScope(step.localId)}
                                >
                                  Upload and attach scope
                                </button>
                              ) : null}
                              {step.pentestScopeDraft.attachedArtifactId ? (
                                <p className="notice small">
                                  {`Approved scope attached: ${step.pentestScopeDraft.attachedArtifactId}`}
                                </p>
                              ) : null}
                              {Object.values(step.pentestScopeDraft.validationErrors).map((error) => (
                                <p key={error} className="notice small">
                                  {error}
                                </p>
                              ))}
                              {pentestWarnings.map((warning) => (
                                <p key={warning} className="notice small">
                                  {warning}
                                </p>
                              ))}
                            </div>
                          ) : null}
                          {optionalToolSchemaFields.length > 0 ? (
                            <details>
                              <summary>Optional inputs</summary>
                              <SchemaCapabilityFields
                                fields={optionalToolSchemaFields.filter(
                                  ([name]) =>
                                    !(isPentestTool && name === "approved_scope"),
                                )}
                                detail={selectedToolDetail}
                                values={step.toolInputValues}
                                errors={step.toolInputErrors}
                                disabled={false}
                                repositoryOptions={repositoryOptions}
                                branchOptions={branchOptions}
                                onChange={(name, value) =>
                                  updateToolInputValue(step.localId, name, value)
                                }
                              />
                            </details>
                          ) : null}
                          <details open={step.toolJsonMode}>
                            <summary
                              onClick={(event) => {
                                event.preventDefault();
                                updateStep(step.localId, {
                                  toolJsonMode: !step.toolJsonMode,
                                });
                              }}
                            >
                              Edit JSON
                            </summary>
                            {step.toolJsonMode ? (
                              <label>
                                Tool Inputs (JSON object)
                                <textarea
                                  data-step-field="toolInputs"
                                  data-step-index={String(index)}
                                  placeholder='{"issueKey":"MM-563"}'
                                  value={step.toolInputs}
                                  onChange={(event) => {
                                    const text = event.target.value;
                                    const parsed = parseToolInputsText(text);
                                    updateStep(step.localId, {
                                      toolInputs: text,
                                      ...(parsed.ok
                                        ? {
                                            toolInputValues: parsed.value,
                                            toolInputErrors: {},
                                          }
                                        : {}),
                                    });
                                  }}
                                />
                              </label>
                            ) : null}
                          </details>
                        </div>
                      ) : (
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
                      )}
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
                    <div className="stack segmented-control-panel">
                      <div className="field">
                        <label htmlFor={`queue-step-${step.localId}-skill-id`}>
                          Skill (optional)
                        </label>
                        <SkillCombobox
                          inputId={`queue-step-${step.localId}-skill-id`}
                          value={step.skillId}
                          options={skillComboboxOptions}
                          dataStepIndex={String(index)}
                          ariaLabel={`Step ${index + 1} skill`}
                          placeholder={
                            isPrimaryStep
                              ? "auto (default), moonspec-orchestrate, ..."
                              : "optional Skill name"
                          }
                          onChange={(nextValue) =>
                            updateStep(step.localId, {
                              skillId: nextValue,
                              skillInputContractDigest:
                                skillsQuery.data?.detailsById[nextValue]
                                  ?.contractDigest || "",
                              skillInputContractNotice: null,
                            })
                          }
                        />
                        {isPrimaryStep ? null : (
                          <span className="small">
                            Leave skill blank to run this step without a selected Skill.
                          </span>
                        )}
                      </div>
                      {selectedSkillDetail && visibleSkillSchemaFields.length === 0 ? (
                        <div
                          className="notice small"
                          data-testid={`skill-schema-fallback-${index}`}
                        >
                          <strong>{selectedSkillDetail.id}</strong>
                          {selectedSkillDetail.description ? (
                            <span>{`: ${selectedSkillDetail.description}`}</span>
                          ) : null}
                          <span>
                            {" "}
                            This Skill does not publish structured input fields.
                          </span>
                        </div>
                      ) : null}

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
                      {selectedSkillDetail ? (
                        <>
                          {skillContractNotice ? (
                            <p
                              className="queue-submit-message notice pending"
                              role="status"
                              aria-live="polite"
                            >
                              {skillContractNotice}
                            </p>
                          ) : null}
                        <SchemaCapabilityFields
                          fields={visibleSkillSchemaFields}
                          detail={selectedSkillDetail}
                          values={step.presetInputValues}
                          errors={step.presetInputErrors}
                          disabled={false}
                          repositoryOptions={repositoryOptions}
                          branchOptions={branchOptions}
                          onChange={(name, value) =>
                            updateStepPresetInputValue(
                              step.localId,
                              { name },
                              value,
                            )
                          }
                        />
                        </>
                      ) : null}
                    </div>
                  ) : null}

                  {step.stepType === "preset" ? (
                    <div
                      className="stack segmented-control-panel"
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

                  {showAdvancedStepOptions ? (
                    <div
                      className="grid-2"
                      aria-label={`Step ${index + 1} runtime selection`}
                    >
                      <label>
                        {`Step ${index + 1} Runtime`}
                        <select
                          data-step-field="runtimeMode"
                          data-step-index={String(index)}
                          value={step.runtimeMode}
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeMode: event.target.value,
                            })
                          }
                        >
                          <option value="">Inherit agent runtime</option>
                          {supportedAgentRuntimes.map((runtimeOption) => (
                            <option key={runtimeOption} value={runtimeOption}>
                              {formatRuntimeLabel(runtimeOption)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {`Step ${index + 1} Provider profile`}
                        <input
                          data-step-field="runtimeProviderProfile"
                          data-step-index={String(index)}
                          value={step.runtimeProviderProfile}
                          placeholder="inherit workflow profile"
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeProviderProfile: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        {`Step ${index + 1} Model tier intent`}
                        <input
                          data-step-field="runtimeModelTier"
                          data-step-index={String(index)}
                          type="number"
                          min="1"
                          value={step.runtimeModelTier}
                          placeholder={isPrimaryStep ? "inherit workflow tier" : "inherit"}
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeModelTier: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        {`Step ${index + 1} Tier fallback`}
                        <select
                          data-step-field="runtimeTierFallback"
                          data-step-index={String(index)}
                          value={step.runtimeTierFallback}
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeTierFallback:
                                event.target.value === "strict" ? "strict" : "clamp",
                            })
                          }
                        >
                          <option value="clamp">Clamp to configured tiers</option>
                          <option value="strict">Reject if unavailable</option>
                        </select>
                      </label>
                      {stepTierPreview ? (
                        <div
                          className={`runtime-command-preview${stepTierPreview.warning ? " runtime-command-preview--warning" : ""}`}
                          aria-label={`Step ${index + 1} model tier preview`}
                        >
                          <span className="runtime-command-preview-label">
                            {`Tier ${stepTierPreview.requestedTier} · ${stepTierPreview.label} · ${stepTierPreview.model} · ${stepTierPreview.effort}`}
                          </span>
                          {stepTierPreview.warning ? (
                            <span className="runtime-command-preview-description">
                              {stepTierPreview.warning}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                      <label>
                        {`Step ${index + 1} Hard override model`}
                        <input
                          data-step-field="runtimeModel"
                          data-step-index={String(index)}
                          list={MODEL_OPTIONS_DATALIST_ID}
                          value={step.runtimeModel}
                          placeholder="runtime default"
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeModel: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        {`Step ${index + 1} Hard override effort`}
                        <input
                          data-step-field="runtimeEffort"
                          data-step-index={String(index)}
                          list={EFFORT_OPTIONS_DATALIST_ID}
                          value={step.runtimeEffort}
                          placeholder="runtime default"
                          onChange={(event) =>
                            updateStep(step.localId, {
                              runtimeEffort: event.target.value,
                            })
                          }
                        />
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
                          ? "Describe the workflow to execute against the repository."
                          : "Step-specific instructions (leave blank to continue from the workflow objective)."
                      }
                      value={step.instructions}
                      onChange={(event) =>
                        handleStepInstructionsChange(
                          step.localId,
                          event.target.value,
                        )
                      }
                    />
                    <RuntimeCommandPreviewMessage preview={instructionPreview} />
                    <div
                      className="queue-step-context"
                      aria-label={`Add to Step ${index + 1}`}
                    >
                      <StepAddMenu
                        stepNumber={index + 1}
                        attachmentPolicy={attachmentPolicy}
                        presentCapabilityTokens={stepCapabilityTokens}
                        onAddImage={() =>
                          document
                            .getElementById(
                              `queue-step-attachments-${step.localId}`,
                            )
                            ?.click()
                        }
                        onAddCapability={(token) =>
                          addStepCapabilities(step.localId, [token])
                        }
                        onAddCustomCapability={() =>
                          promptForCustomStepCapability(step.localId)
                        }
                      />
                      <StepContextBar
                        stepNumber={index + 1}
                        attachments={stepContextAttachments}
                        capabilityChips={stepCapabilityChips}
                        onRemoveCapability={(token) =>
                          removeStepCapability(step.localId, token)
                        }
                      />
                    </div>
                    {attachmentPolicy.enabled ? (
                      <div className="queue-step-attachments">
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
                        {stepAttachmentTargetError ? (
                          <p className="notice error">
                            {stepAttachmentTargetError}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>

                  {step.stepType === "preset" ? (
                    <div
                      className="stack queue-step-preset-options"
                      aria-label="Step Preset Options"
                    >
                      {visiblePresetSchemaFields.length > 0 && step.presetDetail ? (
                        <SchemaCapabilityFields
                          fields={visiblePresetSchemaFields}
                          detail={step.presetDetail}
                          values={step.presetInputValues}
                          errors={step.presetInputErrors}
                          disabled={isApplyingPreset}
                          repositoryOptions={repositoryOptions}
                          branchOptions={branchOptions}
                          onChange={(name, value) =>
                            updateStepPresetInputValue(
                              step.localId,
                              { name },
                              value,
                            )
                          }
                        />
                      ) : visiblePresetInputs.length > 0 ? (
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
                title="Add another workflow step"
                onClick={addStep}
              >
                <span className="queue-step-extension-plus" aria-hidden="true">
                  +
                </span>
                <span>Add Step</span>
              </button>
            </div>

            {presetCatalogEnabled && presetSaveEnabled ? (
              <div className="stack queue-preset-management-block">
                <div
                  className="actions queue-template-actions queue-preset-management-inline"
                  aria-label="Preset Management"
                >
                  <strong>Preset Management</strong>
                  <button
                    type="button"
                    id="queue-template-save-current"
                    className="queue-step-icon-button"
                    aria-label="Save preset"
                    title="Save the current steps as a preset"
                    aria-busy={isSavingPreset}
                    disabled={isSavingPreset}
                    onClick={openPresetSaveDialog}
                  >
                    <SaveIcon />
                  </button>
                  <button
                    type="button"
                    id="queue-template-delete-current"
                    className="queue-step-icon-button destructive"
                    aria-label="Delete preset"
                    aria-busy={isDeletingPreset}
                    title="Delete a personal preset by name"
                    disabled={isDeletingPreset}
                    onClick={openPresetDeleteDialog}
                  >
                    <TrashIcon />
                  </button>
                </div>
                {presetReapplyNeeded ? (
                  <p className="small notice" id="queue-template-message">
                    {PRESET_REAPPLY_REQUIRED_MESSAGE}
                  </p>
                ) : templateMessage ? (
                  <p className="small" id="queue-template-message">
                    {templateMessage}
                  </p>
                ) : null}
              </div>
            ) : null}

          </div>
        </section>

        <section
          className="stack"
          data-canonical-create-section="Execution context"
          aria-label="Execution context"
        >
        <div className={providerOptions.length > 0 ? "grid-2" : "stack"}>
          <label>
            Runtime
            <select
              name="runtime"
              value={runtime}
              onChange={(event) => setRuntime(event.target.value)}
            >
              {runtimeOptions.map((runtimeOption) => (
                <option
                  key={runtimeOption}
                  value={runtimeOption}
                  disabled={runtimeOption === "omnigent" && omnigentCatalogQuery.data?.available !== true}
                >
                  {formatRuntimeLabel(runtimeOption)}
                </option>
              ))}
            </select>
            {omnigentCatalogQuery.data?.available === false ? (
              <span className="small" role="status">
                Codex via Omnigent is unavailable: {omnigentCatalogQuery.data.gateReasons[0]?.message || "readiness checks failed."}{" "}
                <button type="button" onClick={() => void omnigentCatalogQuery.refetch()}>
                  Refresh readiness
                </button>
              </span>
            ) : null}
          </label>

          {providerOptions.length > 0 ? (
            <div id="queue-provider-profile-wrap">
              <label>
                Provider profile
                <select
                  name="providerProfile"
                  value={providerProfile}
                  onChange={(event) => setProviderProfile(event.target.value)}
                  // While a runtime switch refetch is in flight, `keepPreviousData`
                  // keeps the previous runtime's profiles in `data` only so the row
                  // layout stays stable. Those profiles do not belong to the newly
                  // selected runtime, so the control is disabled and the stale
                  // options are withheld to prevent selecting/submitting them.
                  disabled={runtime === "omnigent" ? omnigentCatalogQuery.isFetching : providerProfilesQuery.isPlaceholderData}
                >
                  {runtime !== "omnigent" && providerProfilesQuery.isPlaceholderData ? (
                    <option value="">Loading profiles…</option>
                  ) : (
                    <>
                      {runtime === "omnigent" && providerProfile && !providerOptions.some((option) => option.id === providerProfile) ? (
                        <option value={providerProfile} disabled>
                          {historicalOmnigentProviderProfile?.label || providerProfile} (Unavailable — replacement required)
                        </option>
                      ) : null}
                      {providerOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.isDefault
                            ? `${option.label} (Default)`
                            : option.label}
                        </option>
                      ))}
                    </>
                  )}
                </select>
              </label>
              {providerProfilesQuery.isError ? (
                <p className="small" id="queue-auth-profile-hint">
                  Failed to load provider profiles.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>

        {runtime.trim().toLowerCase() === "omnigent" && omnigentProfiles.length > 0 ? (
          <div className="grid-2" aria-label="Omnigent execution target">
            <label>
              Execution target
              <select
                name="omnigentExecutionTargetRef"
                value={omnigentExecutionTargetRef}
                onChange={(event) => {
                  const ref = event.target.value;
                  setOmnigentExecutionTargetRef(ref);
                  const profile = omnigentProfiles.find((item) => item.ref === ref);
                  if (profile?.defaultPolicyRef) {
                    setOmnigentLaunchPolicyRef(profile.defaultPolicyRef);
                  }
                }}
              >
                {!selectableOmnigentProfiles.some((profile) => profile.ref === omnigentExecutionTargetRef) && omnigentExecutionTargetRef ? (
                  <option value={omnigentExecutionTargetRef} disabled>
                    {omnigentExecutionTargetRef} (Unavailable — replacement required)
                  </option>
                ) : null}
                {selectableOmnigentProfiles.map((profile) => (
                  <option key={profile.ref} value={profile.ref}>
                    {profile.displayName || profile.ref}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Host policy
              <select
                name="omnigentLaunchPolicyRef"
                value={omnigentLaunchPolicyRef}
                onChange={(event) => setOmnigentLaunchPolicyRef(event.target.value)}
              >
                {!selectableOmnigentPolicies.some((policy) => policy.ref === omnigentLaunchPolicyRef) && omnigentLaunchPolicyRef ? (
                  <option value={omnigentLaunchPolicyRef} disabled>
                    {omnigentLaunchPolicyRef} (Unavailable — replacement required)
                  </option>
                ) : null}
                {selectableOmnigentPolicies.map((policy) => (
                  <option key={policy.ref} value={policy.ref}>
                    {policy.hostMode === "on_demand_docker" ? "On-demand Docker" : "Static Compose"}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}

        {runtime.trim().toLowerCase() === "omnigent" ? (
          <>
            {!omnigentSelectionEligible && omnigentSelectionGateReason ? (
              <div className="notice error small" role="alert">
                Codex via Omnigent cannot be submitted: {omnigentSelectionGateReason}
              </div>
            ) : null}
            <div className="notice small" aria-label="Effective Omnigent selection">
              <div>Runtime: Codex via Omnigent</div>
              <div>Provider Profile: {providerOptions.find((option) => option.id === providerProfile)?.label || historicalOmnigentProviderProfile?.label || providerProfile || "Not selected"}</div>
              <div>Host mode: {omnigentPolicies.find((policy) => policy.ref === omnigentLaunchPolicyRef)?.hostMode === "on_demand_docker" ? "On-demand Docker" : "Static Compose"}</div>
              <div>Policy: {omnigentLaunchPolicyRef || "Not selected"}</div>
              <div>Repository: {repository.trim() || "Not selected"}</div>
            </div>
          </>
        ) : null}

        {selectedProfileSupportsModelControls ? (
        <><div className="grid-2" aria-label="Workflow model tier intent">
          <label>
            Model tier intent
            <input
              name="modelTier"
              type="number"
              min="1"
              value={modelTier}
              onChange={(event) => setModelTier(event.target.value)}
            />
          </label>
          <label>
            Tier fallback
            <select
              name="tierFallback"
              value={tierFallback}
              onChange={(event) =>
                setTierFallback(event.target.value === "strict" ? "strict" : "clamp")
              }
            >
              <option value="clamp">Clamp to configured tiers</option>
              <option value="strict">Reject if unavailable</option>
            </select>
          </label>
        </div>
        {workflowTierPreview ? (
          <div
            className={`runtime-command-preview${workflowTierPreview.warning ? " runtime-command-preview--warning" : ""}`}
            aria-label="Workflow model tier preview"
          >
            <span className="runtime-command-preview-label">
              {`Tier ${workflowTierPreview.requestedTier} · ${workflowTierPreview.label} · ${workflowTierPreview.model} · ${workflowTierPreview.effort}`}
            </span>
            {workflowTierPreview.warning ? (
              <span className="runtime-command-preview-description">
                {workflowTierPreview.warning}
              </span>
            ) : null}
          </div>
        ) : null}</>
        ) : null}

        {selectedProfileSupportsModelControls ? (<div className="grid-2">
          <label>
            Hard override model
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
            Hard override effort
            <input
              name="effort"
              list={EFFORT_OPTIONS_DATALIST_ID}
              value={effort}
              placeholder="runtime default"
              onChange={(event) => {
                const next = event.target.value;
                setEffort(next);
                setEffortManualOverride(next !== "");
              }}
            />
          </label>
        </div>) : null}

        </section>

        <section
          className="stack"
          data-canonical-create-section="Execution controls"
          aria-label="Execution controls"
        >
        {showAdvancedStepOptions ? (
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
        ) : null}

        <label className="checkbox">
          <input
            type="checkbox"
            name="proposeTasks"
            checked={proposeTasks}
            onChange={(event) => setProposeTasks(event.target.checked)}
          />
          Propose follow-up work
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
        <div className="queue-advanced-row">
          <label className="checkbox queue-advanced-checkbox">
            <input
              type="checkbox"
              checked={showAdvancedStepOptions}
              onChange={(event) => {
                setShowAdvancedStepOptions(event.target.checked);
                updateDashboardPreferences({
                  createExpertMode: event.target.checked,
                });
              }}
            />
            Advanced mode
          </label>
          <button
            type="button"
            className="queue-step-icon-button queue-info-toggle queue-advanced-info-toggle"
            aria-label="Advanced mode info"
            aria-expanded={advancedInfoOpen}
            aria-controls="queue-advanced-info-panel"
            title="About advanced mode"
            onClick={() => setAdvancedInfoOpen((open) => !open)}
          >
            <InfoIcon />
          </button>
        </div>
        {advancedInfoOpen ? (
          <div
            id="queue-advanced-info-panel"
            className="notice queue-advanced-info-panel"
            role="note"
          >
            <p className="small">
              Adds skill args and required capabilities to each step. Optional
              worker routing overrides; runtime, publish mode, skills, and
              presets already add the common capabilities automatically.
            </p>
          </div>
        ) : null}
        </section>

        {pageMode.mode === "create" ? (
        <section
          className="stack"
          id="schedule-panel"
          data-canonical-create-section="Schedule"
          aria-label="Schedule"
        >
          <strong>Schedule</strong>
          <div className="stack">
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
                  placeholder="My recurring workflow"
                  value={scheduleName}
                  onChange={(event) => setScheduleName(event.target.value)}
                />
              </label>
            </div>
          </div>
        </section>
        ) : null}

        <section
          className="stack"
          data-canonical-create-section="Dependencies"
          aria-label="Dependencies"
        >
          <div className="queue-dependencies-heading">
            <strong>Dependencies</strong>
            <button
              type="button"
              className="queue-step-icon-button queue-info-toggle queue-dependencies-info-toggle"
              aria-label="Dependencies info"
              aria-expanded={dependencyInfoOpen}
              aria-controls="queue-dependencies-info-panel"
              title="About dependencies"
              onClick={() => setDependencyInfoOpen((open) => !open)}
            >
              <InfoIcon />
            </button>
          </div>
          {dependencyInfoOpen ? (
            <div
              id="queue-dependencies-info-panel"
              className="notice queue-dependencies-info-panel"
              role="note"
            >
              <p className="small">
                Add up to {DEPENDENCY_LIMIT} existing <code>MoonMind.UserWorkflow</code>{" "}
                prerequisites. The new run stays blocked until each
                prerequisite finishes in <code>completed</code> state.
              </p>
              <p className="small">
                Direct dependencies only. The new run stays blocked while a
                prerequisite is running, failed, canceled, terminated, timed
                out, or unresolvable, and unblocks once the prerequisite
                completes successfully. Cancel this run or bypass the
                dependency to proceed without that prerequisite.
              </p>
              <p className="small">
                {dependencyOptionsQuery.isLoading
                  ? "Loading recent runs..."
                  : dependencyOptionsQuery.isError
                    ? "Failed to load recent runs. You can still start the workflow without dependencies, or try refreshing."
                    : `${availableDependencyOptions.length} recent runs available.`}
              </p>
            </div>
          ) : null}
          <label htmlFor="queue-dependency-picker">
            Prerequisite
            <select
              id="queue-dependency-picker"
              value={selectedDependencyWorkflowId}
              onChange={(event) => {
                const workflowId = event.target.value;
                if (workflowId) {
                  addDependency(workflowId);
                } else {
                  setSelectedDependencyWorkflowId("");
                  setDependencyMessage(null);
                }
              }}
            >
              <option value="">Select prerequisite to add...</option>
              {availableDependencyOptions.map((item) => (
                <option key={dependencyWorkflowId(item)} value={dependencyWorkflowId(item)}>
                  {`${item.title} (${dependencyWorkflowId(item)})`}
                </option>
              ))}
            </select>
          </label>
          {selectedDependencies.length > 0 ? (
            <ul className="list" id="queue-dependency-list">
              {selectedDependencies.map((workflowId) => {
                const match = (dependencyOptionsQuery.data || []).find(
                  (item) => dependencyWorkflowId(item) === workflowId,
                );
                return (
                  <li key={workflowId}>
                    <span>
                      <strong>{match?.title || workflowId}</strong>{" "}
                      <code>{workflowId}</code>
                    </span>
                    <button
                      type="button"
                      className="queue-step-icon-button destructive"
                      aria-label={`Remove prerequisite ${workflowId}`}
                      title={`Remove prerequisite ${workflowId}`}
                      onClick={() => removeDependency(workflowId)}
                    >
                      <CloseIcon />
                      <span className="sr-only">{`Remove prerequisite ${workflowId}`}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {dependencyMessage ? (
            <p className="notice error">{dependencyMessage}</p>
          ) : null}
        </section>

        <section
          className="stack"
          data-canonical-create-section="Submit"
          aria-label="Submit"
        >
        {submitMessage && submitErrorDetail ? (
          <DashboardErrorDetails
            className="queue-submit-message"
            message={submitMessage}
            endpoint={submitErrorDetail.endpoint}
            status={submitErrorDetail.status}
            requestId={submitErrorDetail.requestId}
            rawError={submitErrorDetail.rawError}
          />
        ) : submitMessage ? (
          <p
            id="queue-submit-message"
            role={submitMessageTone === "error" ? "alert" : "status"}
            aria-live={submitMessageTone === "error" ? "assertive" : "polite"}
            className={
              submitMessageTone === "pending"
                ? "queue-submit-message notice pending"
                : submitMessageTone === "ok"
                  ? "queue-submit-message notice ok"
                  : "queue-submit-message notice error"
            }
          >
            {submitMessage}
          </p>
        ) : null}
        <div
          className="queue-floating-bar queue-floating-bar--liquid-glass queue-step-actions queue-step-submit-actions"
          role="group"
          aria-label="Workflow submission controls"
        >
          {branchStatusMessage ? (
            <p
              className={
                branchOptionsQuery.isError || selectedBranchIsStale
                  ? "queue-authoring-controls-status notice error"
                  : "queue-authoring-controls-status small"
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
                  branchOptionsQuery.isLoading
                    ? "Loading branches..."
                    : "Branch"
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
              >
                <option
                  value="auto"
                  disabled={!autoPublishAvailable}
                  title="Auto — selected skill decides"
                >
                  Auto
                </option>
                <option value="none">None</option>
                <option value="branch" disabled={!mergeAutomationAvailable}>
                  Branch
                </option>
                <option value="pr" disabled={!mergeAutomationAvailable}>
                  PR
                </option>
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
              disabled={isSubmitBlocked}
              aria-disabled={isSubmitting || isSubmitBlocked}
              aria-busy={isSubmitting}
              aria-label={primaryCta}
              title={primaryCtaTooltip}
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                if (isSubmitBlocked) return;
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
                  <span
                    className="queue-submit-primary-arrow-glyph"
                    data-submit-icon="arrow"
                  >
                    <ArrowRightIcon />
                  </span>
                  <span
                    className="queue-submit-primary-arrow-glyph queue-submit-primary-arrow-glyph--check"
                    data-submit-icon="check"
                  >
                    <CheckIcon />
                  </span>
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
      {presetDialogMode ? (
        <div className="jira-browser-backdrop">
          <section
            className="jira-browser-panel stack queue-preset-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="queue-preset-dialog-title"
          >
            <div className="queue-step-header">
              <h3 id="queue-preset-dialog-title">
                {presetDialogMode === "save"
                  ? "Save preset"
                  : "Delete preset"}
              </h3>
              <button
                type="button"
                className="queue-step-icon-button"
                aria-label="Close preset dialog"
                title="Close"
                onClick={closePresetDialog}
              >
                <CloseIcon />
              </button>
            </div>
            <label>
              Preset Name
              <input
                autoFocus
                list="queue-template-preset-name-options"
                value={presetDialogName}
                placeholder={
                  presetDialogMode === "save"
                    ? "New preset name"
                    : "Existing preset name"
                }
                onChange={(event) => setPresetDialogName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void confirmPresetDialog();
                  }
                }}
              />
              <datalist id="queue-template-preset-name-options">
                {templateItems.map((item) => (
                  <option key={item.key} value={item.title} />
                ))}
              </datalist>
            </label>
            <div className="actions queue-template-actions">
              <button
                type="button"
                className="secondary"
                onClick={closePresetDialog}
              >
                Cancel
              </button>
              <button
                type="button"
                className={
                  presetDialogMode === "delete"
                    ? "queue-step-icon-button destructive"
                    : "queue-step-icon-button"
                }
                aria-label={
                  presetDialogMode === "save"
                    ? "Confirm save preset"
                    : "Confirm delete preset"
                }
                title={
                  presetDialogMode === "save"
                    ? "Save preset"
                    : "Delete preset"
                }
                disabled={!presetDialogName.trim()}
                onClick={() => {
                  void confirmPresetDialog();
                }}
              >
                {presetDialogMode === "save" ? <SaveIcon /> : <TrashIcon />}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export function WorkflowStartPage({ payload }: { payload: BootPayload }) {
  const inRouterContext = useInRouterContext();
  if (inRouterContext) {
    return <WorkflowStartPageWithRouterLocation payload={payload} />;
  }
  return (
    <WorkflowStartPageWithSearch
      payload={payload}
      searchString={typeof window !== "undefined" ? window.location.search : ""}
    />
  );
}

function WorkflowStartPageWithRouterLocation({ payload }: { payload: BootPayload }) {
  const { search: searchString } = useLocation();
  return <WorkflowStartPageWithSearch payload={payload} searchString={searchString} />;
}

function WorkflowStartPageWithSearch({
  payload,
  searchString,
}: {
  payload: BootPayload;
  searchString: string;
}) {
  const displayMode = readWorkflowListDisplayMode(payload);
  const search = useMemo(
    () => new URLSearchParams(searchString),
    [searchString],
  );
  if (displayMode === "table") {
    return <WorkflowStartPageContent payload={payload} />;
  }
  const cfg = readWorkflowStartDashboardConfig(payload);
  const sidebarVisible = displayMode === "sidebar"
    && cfg?.features?.temporalDashboard?.listEnabled !== false;

  return (
    <div
      className="workflow-start-workspace workflow-workspace-shell"
      data-sidebar-collapsed={sidebarVisible ? "false" : "true"}
      data-workflow-list-display-mode={displayMode}
    >
      {sidebarVisible ? (
        <WorkflowWorkspaceSidebarPanel payload={payload} search={search} defaultSource="temporal" />
      ) : (
        <div className="workflow-workspace-sidebar-slot" hidden aria-hidden="true" />
      )}
      <main className="workflow-start-primary" aria-label="Create workflow">
        <WorkflowStartPageContent payload={payload} />
      </main>
    </div>
  );
}

export default WorkflowStartPage;
