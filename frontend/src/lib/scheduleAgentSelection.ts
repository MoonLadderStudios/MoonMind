import { isCustomSelection, type ModelSelection } from './modelSelection';

/** The agent a schedule's future runs use, as its edit form holds it. */
export type ScheduleAgentDraft = {
  /** Empty while a newly chosen Harness still needs one of its own profiles. */
  profileId: string;
  /** A deliberate Harness choice; empty follows the selected profile. */
  harness: string;
  selection: ModelSelection;
};

const PROFILE_REF_KEYS = [
  'providerProfileRef', 'profileId', 'providerProfile', 'executionProfileRef',
  'execution_profile_ref', 'profile_id', 'provider_profile',
];
const AUTHORED_SELECTION_KEYS = ['modelTier', 'model', 'effort', 'tierFallback'] as const;

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function profileRef(source: Record<string, unknown> | null): string {
  for (const key of PROFILE_REF_KEYS) {
    const value = source?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function authoredSelection(source: Record<string, unknown> | null): ModelSelection {
  return Object.fromEntries(
    AUTHORED_SELECTION_KEYS
      .filter((key) => source && Object.prototype.hasOwnProperty.call(source, key))
      .map((key) => [key, source?.[key]]),
  ) as ModelSelection;
}

/**
 * Read the agent selection a saved schedule was authored with. Returns null
 * for a target with no agent to choose: another workflow type, or tool-only
 * work whose runtime is passive metadata.
 */
export function savedScheduleAgent(target: Record<string, unknown> | null | undefined): ScheduleAgentDraft | null {
  const root = record(target);
  if (!root || String(root.workflowType ?? root.workflow_type ?? '') !== 'MoonMind.UserWorkflow') return null;
  const parameters = record(root.initialParameters) ?? record(root.initial_parameters);
  const workflow = record(parameters?.workflow) ?? record(parameters?.task);
  if (!parameters || !workflow) return null;
  const runtime = record(workflow.runtime);
  if (runtime?.authored === false) return null;
  let selection = authoredSelection(runtime);
  if (Object.keys(selection).length === 0) {
    // A target saved before selections nested under the workflow authors a
    // flat pair. A recorded source marks the same fields as a resolution.
    const resolution = record(parameters.modelTierResolution);
    const authoredModel = parameters.modelSource == null || parameters.modelSource === 'task_override';
    const authoredEffort = !resolution || resolution.effortSource === 'task_override';
    selection = {
      ...(typeof parameters.modelTier === 'number' ? { modelTier: parameters.modelTier } : {}),
      ...(authoredModel && typeof parameters.model === 'string' ? { model: parameters.model } : {}),
      ...(authoredEffort && typeof parameters.effort === 'string' ? { effort: parameters.effort } : {}),
    };
  }
  return {
    profileId: profileRef(runtime) || profileRef(workflow) || profileRef(parameters) || profileRef(root),
    harness: '',
    selection,
  };
}

function selectionKey(selection: ModelSelection): string {
  const authored = authoredSelection(selection as Record<string, unknown>);
  return JSON.stringify(AUTHORED_SELECTION_KEYS.map((key) => [key in authored, authored[key] ?? null]));
}

/**
 * The `runtime` a schedule update sends: only what the operator changed. An
 * untouched model selection stays omitted so it keeps following its tier or
 * the profile default instead of being pinned to what the form displayed.
 */
export function scheduleAgentPatch(saved: ScheduleAgentDraft, draft: ScheduleAgentDraft): Record<string, unknown> | null {
  const patch: Record<string, unknown> = {};
  if (draft.profileId && draft.profileId !== saved.profileId) patch.profileId = draft.profileId;
  if (selectionKey(draft.selection) !== selectionKey(saved.selection)) {
    if (isCustomSelection(draft.selection)) {
      patch.model = draft.selection.model ?? null;
      patch.effort = draft.selection.effort ?? null;
    } else if (draft.selection.modelTier !== undefined) {
      patch.modelTier = draft.selection.modelTier;
    }
  }
  return Object.keys(patch).length > 0 ? patch : null;
}
