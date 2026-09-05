import { useEffect, useMemo, useRef, useState } from 'react';
import { QueryClient, useMutation, useQuery } from '@tanstack/react-query';

import type { components } from '../../generated/openapi';
import { formatRuntimeLabel, formatStatusLabel } from '../../utils/formatters';
import {
  buildProviderProfileTierPayload,
  computeTierRenumberingImpact,
  duplicateTierDraft,
  normalizeProviderProfileTiers,
  runtimeDefaultTierDraft,
  tierDisplayEffort,
  tierDisplayModel,
  type ProviderProfileTierDraft,
} from '../../utils/providerProfileTiers';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

type ProviderProfileCreationPreset =
  components['schemas']['ProviderProfileCreationPresetResponse'];

export interface ProviderProfile {
  execution_configuration?: { profileId: string; version: number; digest: string } | null;
  profile_id: string;
  runtime_id: string;
  provider_id: string;
  provider_label?: string | null;
  default_model?: string | null;
  default_effort?: string | null;
  model_overrides?: Record<string, string> | null;
  model_tiers?: ProviderModelEffortTier[] | null;
  default_model_tier?: number | null;
  credential_source: string;
  runtime_materialization_mode: string;
  volume_ref?: string | null;
  volume_mount_path?: string | null;
  secret_refs: Record<string, string>;
  max_parallel_runs: number;
  cooldown_after_429_seconds: number;
  rate_limit_policy: string;
  enabled: boolean;
  is_default?: boolean;
  auth_state?: string | null;
  disabled_reason?: string | null;
  first_authenticated_at?: string | null;
  last_validated_at?: string | null;
  last_auth_method?: string | null;
  command_behavior?: Record<string, unknown> | null;
  tags?: string[] | null;
  priority?: number | null;
  clear_env_keys?: string[] | null;
  account_label?: string | null;
  readiness?: ProviderProfileReadiness | null;
  authentication_method?: AuthenticationMethod | null;
  creation_capabilities?: ProviderProfileCreationCapabilities | null;
  launch_isolation?: ProviderProfileLaunchIsolation | null;
}

export interface ProviderProfileLaunchIsolation {
  effective_keys: string[];
  source: string;
  derived: boolean;
  editable: boolean;
  lock_reason: string;
  strategy_id: string;
  classification: string;
  explanations: Record<string, string>;
  audit_reason_present: boolean;
}

// The backend owns every creation-preset and creation-capability shape. These
// aliases are the single frontend binding to the generated contract; a second
// hand-maintained copy is rejected by
// providerProfileRedesignConformance.test.tsx (#3822).
type AuthenticationMethod = components['schemas']['ProviderProfileAuthenticationMethod'];

type AuthenticationMethodCapability =
  components['schemas']['ProviderProfileAuthenticationMethodCapability'];

type ProviderProfileCreationCapabilities =
  components['schemas']['ProviderProfileCreationCapabilitiesResponse'];

const AUTHENTICATION_METHODS: readonly AuthenticationMethod[] = [
  'oauth',
  'api_key',
  'none',
];

/**
 * Narrow a server-declared authentication method id to the canonical enum.
 * The generated capability contract types `id` as a plain string, so an
 * unrecognized value selects nothing instead of being coerced into a
 * credential contract the browser cannot honor.
 */
function asAuthenticationMethod(value: string | null | undefined): AuthenticationMethod | '' {
  return AUTHENTICATION_METHODS.includes(value as AuthenticationMethod)
    ? (value as AuthenticationMethod)
    : '';
}

interface ProviderProfileTierCapabilities {
  version: string;
  profile_id: string | null;
  runtime_id: string;
  provider_id: string;
  evidence: {
    source: string;
    credential_generation: number | null;
    image_ref: string | null;
    observed_at: string | null;
    stale: boolean;
  };
  tier_constraints: { min_count: number; max_count: number | null };
  model: {
    runtime_default: string | null;
    allow_custom: boolean;
    options: Array<{ value: string; label: string; description: string | null; status: string; recommended?: boolean }>;
  };
  effort: {
    supported: boolean;
    runtime_default: string | null;
    allow_custom: boolean;
    application: string;
    options: Array<{ value: string; label: string; description: string | null; status: string; compatible_models: string[] | null }>;
  };
  diagnostics: Array<{ code: string; level: string; message: string }>;
}

export interface ProviderModelEffortTier {
  label?: string | null;
  model?: string | null;
  effort?: string | null;
  parameters?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

interface ProviderReadinessCheck {
  id: string;
  label: string;
  status: 'pass' | 'warning' | 'error';
  message: string;
}

interface ProviderProfileReadiness {
  status: 'ready' | 'warning' | 'blocked';
  launch_ready: boolean;
  summary: string;
  checks: ProviderReadinessCheck[];
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

interface ProviderProfilesManagerProps {
  /**
   * Rows to display. Settings owns the runtime filter, so this collection is
   * already scoped to `selectedRuntimeId` when a single runtime is active.
   */
  profiles: ProviderProfile[];
  secretSlugs: string[];
  onNotice: (notice: Notice | null) => void;
  queryClient: QueryClient;
  /** Map of canonical runtime_id → default model from the boot config. */
  defaultTaskModelByRuntime?: Record<string, string>;
  canWriteProviderProfiles?: boolean;
  /**
   * Canonical runtime_id of the active administrative filter, or undefined for
   * the All-runtimes view. It seeds the runtime of a newly created profile and
   * never overwrites the immutable runtime of an existing one.
   */
  selectedRuntimeId?: string | undefined;
  /** Canonical runtime IDs offered beside the All-runtimes option. */
  runtimeFilterOptions?: string[];
  /** Called with a canonical runtime_id, or undefined for All runtimes. */
  onSelectRuntimeId?: ((runtimeId: string | undefined) => void) | undefined;
}

interface ProviderProfileFormState {
  executionConfiguration?: string;
  profileId: string;
  runtimeId: string;
  providerId: string;
  providerLabel: string;
  defaultModel: string;
  defaultEffort: string;
  authenticationMethod: AuthenticationMethod | '';
  credentialSource: string;
  runtimeMaterializationMode: string;
  secretRefsText: string;
  volumeRef: string;
  volumeMountPath: string;
  maxParallelRuns: string;
  cooldownAfter429Seconds: string;
  rateLimitPolicy: string;
  enabled: boolean;
  isDefault: boolean;
  commandBehavior: string;
  tagsText: string;
  priority: string;
  clearEnvKeysText: string;
  accountLabel: string;
}

interface ProviderProfileSavePayload {
  execution_configuration?: { profileId: string; version: number; digest: string } | null;
  profile_id: string;
  runtime_id?: string;
  provider_id?: string;
  provider_label?: string | null;
  model_tiers?: ProviderModelEffortTier[];
  default_model_tier?: number;
  authentication_method?: AuthenticationMethod;
  preset_version?: string;
  credential_source?: string;
  runtime_materialization_mode?: string;
  secret_refs?: Record<string, string>;
  volume_ref?: string | null;
  volume_mount_path?: string | null;
  max_parallel_runs?: number;
  cooldown_after_429_seconds?: number;
  rate_limit_policy?: string;
  enabled?: boolean;
  is_default?: boolean;
  command_behavior?: Record<string, unknown> | null;
  tags?: string[] | null;
  priority?: number;
  clear_env_keys?: string[] | null;
  account_label?: string | null;
  import_existing_credential_volume?: boolean;
}

type OAuthSessionStatus =
  | 'pending'
  | 'starting'
  | 'bridge_ready'
  | 'awaiting_user'
  | 'verifying'
  | 'registering_profile'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired';

interface OAuthSessionResponse {
  session_id: string;
  runtime_id: string;
  profile_id: string;
  status: OAuthSessionStatus;
  terminal_session_id?: string | null;
  terminal_bridge_id?: string | null;
  session_transport?: string | null;
  failure_reason?: string | null;
}

interface OAuthSessionState {
  sessionId: string;
  profileId: string;
  status: OAuthSessionStatus;
  sessionTransport?: string | null | undefined;
  terminalSessionId?: string | null | undefined;
  terminalBridgeId?: string | null | undefined;
  failureReason?: string | null | undefined;
}

export const PROVIDER_PROFILE_QUERY_KEY = ['provider-profiles'] as const;
/** Sentinel value for the All-runtimes administrative view in Settings. */
export const ALL_RUNTIMES_FILTER_VALUE = 'all';
const RUNTIME_FILTER_CONTROL_ID = 'provider-profile-runtime-filter';
const PROVIDER_PROFILE_REFRESH_STORAGE_KEY = 'moonmind:provider-profile-updated';

function providerProfileModelTiers(profile: ProviderProfile): ProviderModelEffortTier[] {
  if (Array.isArray(profile.model_tiers) && profile.model_tiers.length > 0) {
    return profile.model_tiers;
  }
  return [];
}

function providerProfileTierCount(profile: ProviderProfile): number | null {
  if (Array.isArray(profile.model_tiers)) {
    return profile.model_tiers.length;
  }
  return null;
}

function cloneTierDrafts(
  tiers: ProviderProfileTierDraft[],
): ProviderProfileTierDraft[] {
  return tiers.map((tier) => ({
    ...tier,
    parameters: { ...tier.parameters },
    annotations: { ...tier.annotations },
  }));
}

function oauthSessionStateFromResponse(
  session: OAuthSessionResponse,
  fallbackProfileId?: string,
): OAuthSessionState {
  return {
    sessionId: session.session_id,
    profileId: session.profile_id || fallbackProfileId || '',
    status: session.status,
    sessionTransport: session.session_transport,
    terminalSessionId: session.terminal_session_id,
    terminalBridgeId: session.terminal_bridge_id,
    failureReason: session.failure_reason,
  };
}

function oauthSessionStatesEqual(left: OAuthSessionState, right: OAuthSessionState): boolean {
  return (
    left.sessionId === right.sessionId &&
    left.profileId === right.profileId &&
    left.status === right.status &&
    left.sessionTransport === right.sessionTransport &&
    left.terminalSessionId === right.terminalSessionId &&
    left.terminalBridgeId === right.terminalBridgeId &&
    left.failureReason === right.failureReason
  );
}

export function defaultFormState(runtimeId = ''): ProviderProfileFormState {
  return {
    profileId: '',
    runtimeId,
    providerId: '',
    providerLabel: '',
    defaultModel: '',
    defaultEffort: '',
    authenticationMethod: '',
    credentialSource: '',
    runtimeMaterializationMode: '',
    secretRefsText: '{}',
    volumeRef: '',
    volumeMountPath: '',
    maxParallelRuns: '',
    cooldownAfter429Seconds: '',
    rateLimitPolicy: '',
    enabled: false,
    isDefault: false,
    commandBehavior: '{}',
    tagsText: '',
    priority: '',
    clearEnvKeysText: '',
    accountLabel: '',
  };
}

export function toFormState(profile: ProviderProfile): ProviderProfileFormState {
  return {
    executionConfiguration: JSON.stringify(profile.execution_configuration || null),
    profileId: profile.profile_id,
    runtimeId: profile.runtime_id,
    providerId: profile.provider_id,
    providerLabel: profile.provider_label ?? '',
    defaultModel: profile.default_model ?? '',
    defaultEffort: profile.default_effort ?? '',
    authenticationMethod: profile.authentication_method ?? '',
    credentialSource: profile.credential_source,
    runtimeMaterializationMode: profile.runtime_materialization_mode,
    secretRefsText: JSON.stringify(profile.secret_refs ?? {}, null, 2),
    volumeRef: profile.volume_ref ?? '',
    volumeMountPath: profile.volume_mount_path ?? '',
    maxParallelRuns: String(profile.max_parallel_runs ?? 1),
    cooldownAfter429Seconds: String(profile.cooldown_after_429_seconds ?? 300),
    rateLimitPolicy: profile.rate_limit_policy ?? 'backoff',
    enabled: Boolean(profile.enabled),
    isDefault: Boolean(profile.is_default),
    commandBehavior: profile.command_behavior ? JSON.stringify(profile.command_behavior, null, 2) : '{}',
    tagsText: (profile.tags ?? []).join(', '),
    priority: profile.priority != null ? String(profile.priority) : '',
    clearEnvKeysText: (profile.clear_env_keys ?? []).join('\n'),
    accountLabel: profile.account_label ?? '',
  };
}

function parseSecretRefs(text: string): Record<string, string> {
  if (text.trim() === '') {
    return {};
  }
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Secret refs must be a JSON object.');
  }
  const secretRefs: Record<string, string> = {};
  for (const [key, value] of Object.entries(parsed)) {
    if (typeof value !== 'string') {
      throw new Error('Secret ref values must be strings.');
    }
    secretRefs[key] = value;
  }
  return secretRefs;
}

export function parseCommandBehavior(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (trimmed === '' || trimmed === '{}') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error('Command behavior must be valid JSON.');
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Command behavior must be a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

export function parseTags(text: string): string[] | null {
  const tags = text.split(',').map(t => t.trim()).filter(Boolean);
  return tags.length > 0 ? tags : null;
}

export function parsePriority(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const num = Number(trimmed);
  if (isNaN(num) || !Number.isFinite(num)) {
    throw new Error('Priority must be a valid number.');
  }
  return num;
}

export function parseClearEnvKeys(text: string): string[] | null {
  const keys = text.split('\n').map(k => k.trim()).filter(Boolean);
  return keys.length > 0 ? keys : null;
}

function readinessLabel(status: ProviderProfileReadiness['status']): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function readinessClass(status: ProviderProfileReadiness['status']): string {
  if (status === 'ready') {
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400';
  }
  if (status === 'warning') {
    return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300';
  }
  return 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300';
}

function visibleReadinessChecks(readiness?: ProviderProfileReadiness | null): ProviderReadinessCheck[] {
  return (readiness?.checks ?? []).filter((check) => check.status !== 'pass');
}

type ProviderAuthActionLabel =
  | 'OAuth'
  | 'Use Anthropic API key'
  | 'Use OpenCode API key'
  | 'Validate OAuth'
  | 'Disconnect OAuth';

interface ClaudeAuthAction {
  id: string;
  label: ProviderAuthActionLabel;
}

type OpencodeAuthAction = ClaudeAuthAction;

type ClaudeEnrollmentStep =
  | 'not_connected'
  | 'awaiting_external_step'
  | 'awaiting_token_paste'
  | 'validating_token'
  | 'saving_secret'
  | 'updating_profile'
  | 'ready'
  | 'failed';

interface ClaudeReadinessMetadata {
  connected?: boolean;
  lastValidatedAt?: string;
  failureReason?: string;
  backingSecretExists?: boolean;
  launchReady?: boolean;
}

type ProviderAuthModel =
  | { kind: 'codex_oauth' }
  | {
      kind: 'claude_credentials';
      statusLabel: string | null;
      actions: ClaudeAuthAction[];
      readiness: ClaudeReadinessMetadata | null;
    }
  | {
      kind: 'opencode_credentials';
      statusLabel: string | null;
      actions: OpencodeAuthAction[];
      readiness: ClaudeReadinessMetadata | null;
    }
  | { kind: 'none' };

interface ClaudeEnrollmentState {
  profile: ProviderProfile;
  step: ClaudeEnrollmentStep;
  token: string;
  failureReason: string | null;
  statusLabel: string | null;
  readiness: ClaudeReadinessMetadata | null;
}

interface ClaudeManualAuthResult {
  status?: string;
  status_label?: string;
  statusLabel?: string;
  readiness?: Record<string, unknown> | null;
  failure_reason?: string | null;
  failureReason?: string | null;
}

interface ApiKeyEnrollmentCopy {
  providerName: string;
  credentialLabel: string;
  description: string;
  readyLabel: string;
}

const CLAUDE_AUTH_ACTION_LABELS: Record<string, ProviderAuthActionLabel> = {
  connect_oauth: 'OAuth',
  use_api_key: 'Use Anthropic API key',
  validate_oauth: 'Validate OAuth',
  disconnect_oauth: 'Disconnect OAuth',
};

const OPENCODE_AUTH_ACTION_LABELS: Record<string, ProviderAuthActionLabel> = {
  use_api_key: 'Use OpenCode API key',
};

const CLAUDE_ENROLLMENT_STEPS: ClaudeEnrollmentStep[] = [
  'not_connected',
  'awaiting_external_step',
  'awaiting_token_paste',
  'validating_token',
  'saving_secret',
  'updating_profile',
  'ready',
  'failed',
];

const CLAUDE_ENROLLMENT_PROGRESS_DELAY_MS = 350;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function commandBehaviorValue(profile: ProviderProfile, key: string): unknown {
  const commandBehavior = profile.command_behavior;
  if (!commandBehavior || typeof commandBehavior !== 'object' || Array.isArray(commandBehavior)) {
    return undefined;
  }
  return commandBehavior[key];
}

function commandBehaviorString(profile: ProviderProfile, key: string): string | null {
  const value = commandBehaviorValue(profile, key);
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null;
}

function commandBehaviorStringArray(profile: ProviderProfile, key: string): string[] | null {
  const value = commandBehaviorValue(profile, key);
  if (!Array.isArray(value)) return null;
  return value.filter((item): item is string => typeof item === 'string' && item.trim() !== '');
}

function normalizeBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function normalizeReadinessMetadata(value: unknown): ClaudeReadinessMetadata | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const readiness: ClaudeReadinessMetadata = {};
  const connected = normalizeBoolean(record.connected);
  const lastValidatedAt =
    typeof record.last_validated_at === 'string'
      ? record.last_validated_at
      : typeof record.lastValidatedAt === 'string'
        ? record.lastValidatedAt
        : undefined;
  const failureReason =
    typeof record.failure_reason === 'string'
      ? record.failure_reason
      : typeof record.failureReason === 'string'
        ? record.failureReason
        : undefined;
  const backingSecretExists =
    normalizeBoolean(record.backing_secret_exists) ?? normalizeBoolean(record.backingSecretExists);
  const launchReady = normalizeBoolean(record.launch_ready) ?? normalizeBoolean(record.launchReady);

  if (connected !== undefined) readiness.connected = connected;
  if (lastValidatedAt !== undefined) readiness.lastValidatedAt = lastValidatedAt;
  if (failureReason !== undefined) readiness.failureReason = failureReason;
  if (backingSecretExists !== undefined) readiness.backingSecretExists = backingSecretExists;
  if (launchReady !== undefined) readiness.launchReady = launchReady;

  return Object.values(readiness).some((field) => field !== undefined) ? readiness : null;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function redactClaudeSecretText(value: string | null | undefined, submittedToken?: string): string | null {
  if (!value) return null;
  let redacted = value;
  const trimmedToken = submittedToken?.trim();
  if (trimmedToken) {
    redacted = redacted.replace(new RegExp(escapeRegExp(trimmedToken), 'g'), '[REDACTED]');
  }
  redacted = redacted.replace(/sk-ant-[A-Za-z0-9._-]+/g, '[REDACTED]');
  redacted = redacted.replace(/(token|api[_-]?key|password)=\S+/gi, '$1=[REDACTED]');
  return redacted;
}

function extractErrorMessage(
  payload: unknown,
  fallback = 'Provider credential request failed.',
): string {
  if (typeof payload === 'string') return payload;
  if (!payload || typeof payload !== 'object') return fallback;
  const record = payload as Record<string, unknown>;
  if (typeof record.message === 'string') return record.message;
  if (typeof record.detail === 'string') return record.detail;
  if (record.detail && typeof record.detail === 'object') {
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.message === 'string') return detail.message;
    if (typeof detail.error === 'string') return detail.error;
  }
  if (typeof record.error === 'string') return record.error;
  return fallback;
}

function extractErrorCode(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  if (typeof record.code === 'string') return record.code;
  if (record.detail && typeof record.detail === 'object') {
    const detail = record.detail as Record<string, unknown>;
    return typeof detail.code === 'string' ? detail.code : null;
  }
  return null;
}

/**
 * FastAPI request validation answers with `detail: [{ loc: ['body', <field>, ...] }]`,
 * so read the first body-scoped location instead of treating the array as an
 * object that carries a `field` key.
 */
function extractValidationDetailField(detail: readonly unknown[]): string | null {
  for (const entry of detail) {
    if (!entry || typeof entry !== 'object') continue;
    const loc = (entry as Record<string, unknown>).loc;
    if (!Array.isArray(loc)) continue;
    const bodyIndex = loc.indexOf('body');
    if (bodyIndex < 0) continue;
    const field = loc[bodyIndex + 1];
    if (typeof field === 'string') return field;
  }
  return null;
}

/** Canonical Provider Profile field a server validation failure targets. */
function extractErrorField(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  if (typeof record.field === 'string') return record.field;
  if (Array.isArray(record.detail)) {
    return extractValidationDetailField(record.detail);
  }
  if (record.detail && typeof record.detail === 'object') {
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.field === 'string') return detail.field;
    const requiredFields = detail.required_fields;
    if (Array.isArray(requiredFields) && typeof requiredFields[0] === 'string') {
      return requiredFields[0];
    }
  }
  return null;
}

class ProviderProfileRequestError extends Error {
  constructor(
    message: string,
    readonly code: string | null,
    readonly field: string | null = null,
  ) {
    super(message);
    this.name = 'ProviderProfileRequestError';
  }
}

/**
 * Provider Profile fields that only exist inside the `Show advanced options`
 * region. Server validation aimed at one of these must reveal the region and
 * move focus to the offending control (ProviderProfileCreation.md 10.2-10.3).
 */
const ADVANCED_DISCLOSURE_FIELDS: ReadonlySet<string> = new Set([
  'provider_label',
  'credential_source',
  'runtime_materialization_mode',
  'secret_refs',
  'volume_ref',
  'volume_mount_path',
  'cooldown_after_429_seconds',
  'rate_limit_policy',
  'priority',
  'tags',
  'command_behavior',
  'clear_env_keys',
]);

const ADVANCED_REGION_ID = 'provider-profile-advanced-region';

const FOCUSABLE_ADVANCED_CONTROL =
  'input:not([disabled]):not([readonly]), select:not([disabled]), textarea:not([disabled]):not([readonly]), button:not([disabled])';

function isFirstPartyOAuthProfile(profile: ProviderProfile): boolean {
  const authActions = commandBehaviorStringArray(profile, 'auth_actions') ?? [];
  return (
    profile.runtime_id === 'codex_cli' &&
    profile.provider_id === 'openai' &&
    (authActions.includes('connect_oauth') ||
      profile.credential_source === 'oauth_volume' ||
      profile.runtime_materialization_mode === 'oauth_home' ||
      Boolean(profile.volume_ref || profile.volume_mount_path))
  );
}

function isCanonicalClaudeAnthropicProfile(profile: ProviderProfile): boolean {
  return (
    profile.runtime_id === 'claude_code' &&
    profile.provider_id === 'anthropic'
  );
}

function isClaudeCredentialMethodProfile(profile: ProviderProfile): boolean {
  return (
    profile.runtime_id === 'claude_code' &&
    profile.provider_id === 'anthropic'
  );
}

function defaultClaudeCredentialActions(profile: ProviderProfile): string[] {
  if (!isCanonicalClaudeAnthropicProfile(profile)) {
    return [];
  }
  const actions = ['use_api_key'];
  if (
    profile.credential_source === 'oauth_volume' ||
    profile.runtime_materialization_mode === 'oauth_home' ||
    Boolean(profile.volume_ref || profile.volume_mount_path)
  ) {
    actions.unshift('connect_oauth');
  }
  return actions;
}

function claudeCredentialActions(profile: ProviderProfile): ClaudeAuthAction[] {
  const actionIds = commandBehaviorStringArray(profile, 'auth_actions');
  const resolvedActionIds = actionIds ?? defaultClaudeCredentialActions(profile);
  return resolvedActionIds
    .map((actionId) => {
      const label = CLAUDE_AUTH_ACTION_LABELS[actionId];
      return label ? { id: actionId, label } : null;
    })
    .filter((action): action is ClaudeAuthAction => action !== null);
}

function isOpencodeGoProfile(profile: ProviderProfile): boolean {
  return profile.runtime_id === 'opencode' && profile.provider_id === 'opencode-go';
}

function isOpencodeCredentialMethodProfile(profile: ProviderProfile): boolean {
  return profile.runtime_id === 'opencode' && (profile.provider_id === 'opencode-go' || profile.provider_id === 'opencode');
}

function defaultOpencodeCredentialActions(profile: ProviderProfile): string[] {
  if (!isOpencodeGoProfile(profile)) {
    return [];
  }
  return ['use_api_key'];
}

function opencodeCredentialActions(profile: ProviderProfile): OpencodeAuthAction[] {
  const actionIds = commandBehaviorStringArray(profile, 'auth_actions');
  const resolvedActionIds = actionIds ?? defaultOpencodeCredentialActions(profile);
  return resolvedActionIds
    .map((actionId) => {
      const label = OPENCODE_AUTH_ACTION_LABELS[actionId];
      return label ? { id: actionId, label } : null;
    })
    .filter((action): action is OpencodeAuthAction => action !== null);
}

function providerAuthModel(profile: ProviderProfile): ProviderAuthModel {
  if (isFirstPartyOAuthProfile(profile)) {
    return { kind: 'codex_oauth' };
  }

  if (isOpencodeCredentialMethodProfile(profile)) {
    return {
      kind: 'opencode_credentials',
      statusLabel: formatStatusLabel(commandBehaviorString(profile, 'auth_status_label'), ''),
      actions: opencodeCredentialActions(profile),
      readiness: normalizeReadinessMetadata(commandBehaviorValue(profile, 'auth_readiness')),
    };
  }

  if (!isClaudeCredentialMethodProfile(profile)) {
    return { kind: 'none' };
  }

  return {
    kind: 'claude_credentials',
    statusLabel: formatStatusLabel(commandBehaviorString(profile, 'auth_status_label'), ''),
    actions: claudeCredentialActions(profile),
    readiness: normalizeReadinessMetadata(commandBehaviorValue(profile, 'auth_readiness')),
  };
}

function apiKeyEnrollmentCopy(profile: ProviderProfile): ApiKeyEnrollmentCopy {
  if (profile.runtime_id === 'codex_cli' && profile.provider_id === 'openai') {
    return {
      providerName: 'OpenAI',
      credentialLabel: 'OpenAI API key',
      description:
        'Use an OpenAI API key for Codex launches. Paste the key here, then validate and save it as a managed provider credential.',
      readyLabel: 'OpenAI API key ready',
    };
  }
  return {
    providerName: 'OpenCode',
    credentialLabel: 'OpenCode API key',
    description:
      'Use an OpenCode Go API key for OpenCode launches. Paste the key here, then validate and save it as a managed provider credential.',
    readyLabel: 'OpenCode API key ready',
  };
}

function activationStatusLabel(profile: ProviderProfile): string | null {
  if (!profile.auth_state && !profile.disabled_reason) return null;
  if (profile.auth_state === 'not_configured' || profile.disabled_reason === 'missing_credentials') {
    return 'Setup required';
  }
  if (profile.disabled_reason === 'user_disabled') return 'Manually disabled';
  if (profile.disabled_reason === 'policy_disabled') return 'Policy blocked';
  if (profile.auth_state === 'validation_failed' || profile.disabled_reason === 'auth_invalid') {
    return 'Validation failed';
  }
  if (profile.auth_state === 'disconnected' || profile.disabled_reason === 'disconnected') {
    return 'Disconnected';
  }
  if (profile.auth_state === 'connected') return 'Connected';
  return formatStatusLabel(profile.auth_state ?? profile.disabled_reason ?? null, '');
}

function mayEnableFromSettings(profile: ProviderProfile): boolean {
  if (profile.enabled) return true;
  return profile.auth_state === 'connected' && profile.disabled_reason !== 'policy_disabled';
}

function oauthStatusLabel(status: OAuthSessionStatus): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function isActiveOAuthStatus(status: OAuthSessionStatus): boolean {
  return ['pending', 'starting', 'bridge_ready', 'awaiting_user', 'verifying', 'registering_profile'].includes(status);
}

function canFinalizeOAuthStatus(status: OAuthSessionStatus): boolean {
  return status === 'awaiting_user' || status === 'verifying';
}

function canRetryOAuthStatus(status: OAuthSessionStatus): boolean {
  return status === 'failed' || status === 'cancelled' || status === 'expired';
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function applyCreationPresetToForm(
  form: ProviderProfileFormState,
  preset: ProviderProfileCreationPreset,
): ProviderProfileFormState {
  const value = (fieldName: string): unknown => preset.fields[fieldName]?.value;
  const stringValue = (fieldName: string): string => {
    const fieldValue = value(fieldName);
    return fieldValue == null ? '' : String(fieldValue);
  };
  return {
    ...form,
    credentialSource: stringValue('credential_source'),
    runtimeMaterializationMode: stringValue('runtime_materialization_mode'),
    secretRefsText: JSON.stringify(value('secret_refs') ?? {}, null, 2),
    volumeRef: stringValue('volume_ref'),
    volumeMountPath: stringValue('volume_mount_path'),
    maxParallelRuns: stringValue('max_parallel_runs'),
    cooldownAfter429Seconds: stringValue('cooldown_after_429_seconds'),
    rateLimitPolicy: stringValue('rate_limit_policy'),
    enabled: value('enabled') === true,
    isDefault: value('is_default') === true,
    commandBehavior: JSON.stringify(value('command_behavior') ?? {}, null, 2),
    tagsText: Array.isArray(value('user_tags'))
      ? (value('user_tags') as string[]).join(', ')
      : '',
    priority: stringValue('priority'),
    clearEnvKeysText: Array.isArray(value('clear_env_keys'))
      ? (value('clear_env_keys') as string[]).join('\n')
      : '',
  };
}

function buildSavePayload(
  form: ProviderProfileFormState,
  options: {
    isEditing: boolean;
    formBaseline: ProviderProfileFormState;
    creationPreset: ProviderProfileCreationPreset | null;
    importExistingCredentialVolume: boolean;
    tierDrafts: ProviderProfileTierDraft[];
    defaultTierClientId: string | null;
    tierBaseline: ProviderProfileTierDraft[] | null;
    tierBaselineDefaultId: string | null;
  },
): ProviderProfileSavePayload {
  const isCodexOAuth =
    form.runtimeId.trim() === 'codex_cli' &&
    ((options.isEditing &&
      form.credentialSource === 'oauth_volume' &&
      form.runtimeMaterializationMode === 'oauth_home') ||
      (!options.isEditing && form.authenticationMethod === 'oauth'));
  const payload: ProviderProfileSavePayload = { profile_id: form.profileId.trim() };

  if (options.isEditing) {
    const baseline = options.formBaseline;
    const setChanged = <Key extends keyof ProviderProfileSavePayload>(
      key: Key,
      current: ProviderProfileSavePayload[Key],
      previous: ProviderProfileSavePayload[Key],
    ) => {
      if (!valuesEqual(current, previous)) payload[key] = current;
    };
    setChanged('provider_id', form.providerId.trim(), baseline.providerId.trim());
    setChanged(
      'provider_label',
      form.providerLabel.trim() || null,
      baseline.providerLabel.trim() || null,
    );
    {
      const currentTierPayload = buildProviderProfileTierPayload(options.tierDrafts, options.defaultTierClientId);
      const baselineTierPayload = options.tierBaseline
        ? buildProviderProfileTierPayload(options.tierBaseline, options.tierBaselineDefaultId)
        : null;
      const tierChanged = !baselineTierPayload || !valuesEqual(currentTierPayload.model_tiers, baselineTierPayload.model_tiers) || currentTierPayload.default_model_tier !== baselineTierPayload.default_model_tier;
      if (tierChanged) {
        payload.model_tiers = currentTierPayload.model_tiers;
        payload.default_model_tier = currentTierPayload.default_model_tier;
      }
    }
    setChanged(
      'account_label',
      form.accountLabel.trim() || null,
      baseline.accountLabel.trim() || null,
    );
    setChanged(
      'secret_refs',
      parseSecretRefs(form.secretRefsText),
      parseSecretRefs(baseline.secretRefsText),
    );
    setChanged(
      'volume_ref',
      form.volumeRef.trim() || null,
      baseline.volumeRef.trim() || null,
    );
    setChanged(
      'volume_mount_path',
      form.volumeMountPath.trim() || null,
      baseline.volumeMountPath.trim() || null,
    );
    setChanged(
      'max_parallel_runs',
      isCodexOAuth ? 1 : Number(form.maxParallelRuns),
      isCodexOAuth ? 1 : Number(baseline.maxParallelRuns),
    );
    setChanged(
      'cooldown_after_429_seconds',
      Number(form.cooldownAfter429Seconds),
      Number(baseline.cooldownAfter429Seconds),
    );
    setChanged('rate_limit_policy', form.rateLimitPolicy, baseline.rateLimitPolicy);
    setChanged('execution_configuration', JSON.parse(form.executionConfiguration || 'null'), JSON.parse(baseline.executionConfiguration || 'null'));
    setChanged('enabled', form.enabled, baseline.enabled);
    setChanged('is_default', form.isDefault, baseline.isDefault);
    setChanged(
      'command_behavior',
      parseCommandBehavior(form.commandBehavior),
      parseCommandBehavior(baseline.commandBehavior),
    );
    setChanged('tags', parseTags(form.tagsText), parseTags(baseline.tagsText));
    setChanged(
      'clear_env_keys',
      parseClearEnvKeys(form.clearEnvKeysText),
      parseClearEnvKeys(baseline.clearEnvKeysText),
    );
    const priority = parsePriority(form.priority);
    const baselinePriority = parsePriority(baseline.priority);
    if (priority !== null && priority !== baselinePriority) payload.priority = priority;
    if (options.importExistingCredentialVolume) {
      payload.import_existing_credential_volume = true;
      payload.volume_ref = form.volumeRef.trim() || null;
      payload.volume_mount_path = form.volumeMountPath.trim() || null;
    }
    return payload;
  }

  if (form.executionConfiguration) payload.execution_configuration = JSON.parse(form.executionConfiguration);
  payload.runtime_id = form.runtimeId.trim();
  payload.provider_id = form.providerId.trim();
  payload.provider_label = form.providerLabel.trim() || null;
  {
    const tierPayload = buildProviderProfileTierPayload(options.tierDrafts, options.defaultTierClientId);
    payload.model_tiers = tierPayload.model_tiers;
    payload.default_model_tier = tierPayload.default_model_tier;
  }
  payload.account_label = form.accountLabel.trim() || null;

  if (!payload.profile_id) {
    throw new Error('Profile ID is required.');
  }
  if (!payload.runtime_id) {
    throw new Error('Runtime ID is required.');
  }
  if (!payload.provider_id) {
    throw new Error('Provider ID is required.');
  }

  const preset = options.creationPreset;
  const authenticationMethod = form.authenticationMethod;
  if (!authenticationMethod) {
    throw new Error('Choose a supported authentication method.');
  }
  const usesManualCreation =
    preset?.supported === false && preset.manual_creation_allowed;

  if (form.credentialSource) payload.credential_source = form.credentialSource;
  if (form.runtimeMaterializationMode) {
    payload.runtime_materialization_mode = form.runtimeMaterializationMode;
  }
  payload.secret_refs = parseSecretRefs(form.secretRefsText);
  payload.volume_ref = form.volumeRef.trim() || null;
  payload.volume_mount_path = form.volumeMountPath.trim() || null;
  if (form.maxParallelRuns.trim()) {
    payload.max_parallel_runs = isCodexOAuth ? 1 : Number(form.maxParallelRuns);
  }
  if (form.cooldownAfter429Seconds.trim()) {
    payload.cooldown_after_429_seconds = Number(form.cooldownAfter429Seconds);
  }
  if (form.rateLimitPolicy) payload.rate_limit_policy = form.rateLimitPolicy;
  payload.is_default = form.isDefault;
  payload.command_behavior = parseCommandBehavior(form.commandBehavior);
  payload.tags = parseTags(form.tagsText);
  payload.clear_env_keys = parseClearEnvKeys(form.clearEnvKeysText);
  const priority = parsePriority(form.priority);
  if (priority !== null) payload.priority = priority;

  if (usesManualCreation) {
    if (!payload.credential_source || !payload.runtime_materialization_mode) {
      throw new Error(
        'Manual creation requires credential source and materialization mode.',
      );
    }
    return payload;
  }
  if (!preset) {
    throw new Error('Wait for the backend creation preset before creating the profile.');
  }
  if (!preset.supported) {
    throw new Error(
      preset.diagnostics[0]?.message ??
        'This runtime, provider, and authentication combination is unsupported.',
    );
  }
  payload.authentication_method = authenticationMethod;
  payload.preset_version = preset.version;

  const omitWhenRecommended = (
    payloadKey: keyof ProviderProfileSavePayload,
    fieldName: string,
  ) => {
    if (valuesEqual(payload[payloadKey], preset.fields[fieldName]?.value)) {
      delete payload[payloadKey];
    }
  };
  omitWhenRecommended('credential_source', 'credential_source');
  omitWhenRecommended('runtime_materialization_mode', 'runtime_materialization_mode');
  omitWhenRecommended('secret_refs', 'secret_refs');
  omitWhenRecommended('volume_ref', 'volume_ref');
  omitWhenRecommended('volume_mount_path', 'volume_mount_path');
  omitWhenRecommended('max_parallel_runs', 'max_parallel_runs');
  omitWhenRecommended('cooldown_after_429_seconds', 'cooldown_after_429_seconds');
  omitWhenRecommended('rate_limit_policy', 'rate_limit_policy');
  omitWhenRecommended('is_default', 'is_default');
  omitWhenRecommended('command_behavior', 'command_behavior');
  if (valuesEqual(payload.tags ?? [], preset.fields.user_tags?.value ?? [])) {
    delete payload.tags;
  }
  omitWhenRecommended('priority', 'priority');
  // #3821: standard guided creation never authors clear_env_keys; the
  // backend isolation authority owns the value.
  delete payload.clear_env_keys;

  if (options.importExistingCredentialVolume) {
    payload.import_existing_credential_volume = true;
    payload.volume_ref = form.volumeRef.trim() || null;
    payload.volume_mount_path = form.volumeMountPath.trim() || null;
  }
  return payload;
}

export function ProviderProfilesManager({
  profiles,
  secretSlugs,
  onNotice,
  queryClient,
  defaultTaskModelByRuntime = {},
  canWriteProviderProfiles = true,
  selectedRuntimeId,
  runtimeFilterOptions = [],
  onSelectRuntimeId,
}: ProviderProfilesManagerProps) {
  const createFormRuntimeSeed = selectedRuntimeId ?? '';
  const [form, setForm] = useState<ProviderProfileFormState>(() =>
    defaultFormState(createFormRuntimeSeed),
  );
  const [formBaseline, setFormBaseline] = useState<ProviderProfileFormState>(() =>
    defaultFormState(createFormRuntimeSeed),
  );
  const [creationPreset, setCreationPreset] =
    useState<ProviderProfileCreationPreset | null>(null);
  const [creationPresetLoading, setCreationPresetLoading] = useState(false);
  const [creationPresetError, setCreationPresetError] = useState<string | null>(null);
  const [creationPresetRefreshKey, setCreationPresetRefreshKey] = useState(0);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const createFormRuntimeSeedRef = useRef(createFormRuntimeSeed);
  const [oauthSessions, setOauthSessions] = useState<Record<string, OAuthSessionState>>({});
  const [tmateOAuthSession, setTmateOAuthSession] = useState<OAuthSessionState | null>(null);
  const [claudeEnrollment, setClaudeEnrollment] = useState<ClaudeEnrollmentState | null>(null);
  const claudeEnrollmentDrawerRef = useRef<HTMLDivElement | null>(null);
  const claudeEnrollmentProfileIdRef = useRef<string | null>(null);
  const [creationCapabilities, setCreationCapabilities] =
    useState<ProviderProfileCreationCapabilities | null>(null);
  const [creationCapabilitiesError, setCreationCapabilitiesError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const executionConfigurations = useQuery({
    queryKey: ['settings', 'profile-execution-configurations'],
    enabled: showAdvanced,
    queryFn: async (): Promise<Array<{profileId: string; displayName: string; activeVersion: number; versions: Array<{version: number; digest: string}>}>> => {
      const response = await fetch('/api/omnigent/agent-profiles', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Execution configurations could not be loaded.');
      const value = await response.json();
      return Array.isArray(value) ? value : [];
    },
  });
  const [advancedFocusRequest, setAdvancedFocusRequest] = useState<
    { field: string; nonce: number } | null
  >(null);
  const [showImportedVolume, setShowImportedVolume] = useState(false);
  const [importedVolumeRef, setImportedVolumeRef] = useState('');
  const [importedVolumeValidated, setImportedVolumeValidated] = useState(false);
  const startOAuthFromCreationRef = useRef<(profile: ProviderProfile) => void>(() => undefined);
  // Create-time "use as runtime default" intent cannot be honored at creation:
  // guided creation stores the profile disabled with is_default=false until
  // credential validation, so the intent is persisted here and applied once
  // the readiness-completing operation (OAuth finalize or API-key enrollment)
  // succeeds for that profile.
  const pendingDefaultIntentRef = useRef<Set<string>>(new Set());
  const applyPendingDefaultIntent = async (profileId: string) => {
    if (!pendingDefaultIntentRef.current.has(profileId)) return;
    pendingDefaultIntentRef.current.delete(profileId);
    try {
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ is_default: true }),
        },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(
          extractErrorMessage(
            errorPayload,
            `Failed to set "${profileId}" as the runtime default.`,
          ),
        );
      }
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({ level: 'ok', text: `Profile "${profileId}" is now the runtime default.` });
    } catch (error) {
      onNotice({
        level: 'error',
        text: error instanceof Error
          ? error.message
          : `Failed to set "${profileId}" as the runtime default.`,
      });
    }
  };
  const [tierDrafts, setTierDrafts] = useState<ProviderProfileTierDraft[]>(() => [runtimeDefaultTierDraft()]);
  const [defaultTierClientId, setDefaultTierClientId] = useState<string | null>(() => tierDrafts[0]?.clientId ?? null);
  const [tierBaseline, setTierBaseline] = useState<ProviderProfileTierDraft[] | null>(
    () => cloneTierDrafts(tierDrafts),
  );
  const [tierBaselineDefaultId, setTierBaselineDefaultId] = useState<string | null>(
    () => tierDrafts[0]?.clientId ?? null,
  );
  const [invalidSavedDefaultIndex, setInvalidSavedDefaultIndex] = useState<number | null>(null);
  const [isTierRepair, setIsTierRepair] = useState(false);
  const [tierFieldErrors, setTierFieldErrors] = useState<Record<string, string>>({});
  const [tierLiveMessage, setTierLiveMessage] = useState<string>('');
  const [tierUndo, setTierUndo] = useState<{ tier: ProviderProfileTierDraft; index: number; wasDefault: boolean } | null>(null);
  const [tierRemoveDialog, setTierRemoveDialog] = useState<{ index: number; tier: ProviderProfileTierDraft; isDefault: boolean; isMiddle: boolean } | null>(null);
  const [tierRemoveReplacementId, setTierRemoveReplacementId] = useState<string | null>(null);
  const [tierCapabilities, setTierCapabilities] = useState<ProviderProfileTierCapabilities | null>(null);
  const [tierCapabilitiesError, setTierCapabilitiesError] = useState<string | null>(null);
  const [tierCapabilitiesLoading, setTierCapabilitiesLoading] = useState(false);
  const [customModelEntryTiers, setCustomModelEntryTiers] = useState<Set<string>>(new Set());
  const tierSectionRef = useRef<HTMLElement | null>(null);
  const tierLiveRef = useRef<HTMLDivElement | null>(null);
  const focusedTierClientIdRef = useRef<string | null>(null);
  const [showResetPreview, setShowResetPreview] = useState(false);

  const activeRuntimeFilterValue = selectedRuntimeId ?? ALL_RUNTIMES_FILTER_VALUE;
  // Canonical runtime IDs are the option values; the shared runtime formatter
  // supplies the display text so Settings never invents a second label source.
  // The active filter is always offered so the control stays consistent even if
  // its last profile is deleted.
  const runtimeFilterChoices = useMemo(() => {
    const runtimeIds: string[] = [];
    for (const runtimeId of [...runtimeFilterOptions, selectedRuntimeId ?? '']) {
      const canonical = runtimeId.trim();
      if (canonical && !runtimeIds.includes(canonical)) {
        runtimeIds.push(canonical);
      }
    }
    return [
      { value: ALL_RUNTIMES_FILTER_VALUE, label: 'All runtimes' },
      ...runtimeIds.map((runtimeId) => ({
        value: runtimeId,
        label: formatRuntimeLabel(runtimeId),
      })),
    ] as ReadonlyArray<{ value: string; label: string }>;
  }, [runtimeFilterOptions, selectedRuntimeId]);

  const isEditing = editingProfileId !== null;
  const editingProfile = useMemo(
    () => profiles.find((profile) => profile.profile_id === editingProfileId) ?? null,
    [editingProfileId, profiles],
  );
  const selectedAuthenticationCapability = useMemo<AuthenticationMethodCapability | null>(
    () =>
      creationCapabilities?.authentication_methods.find(
        (method) => method.id === form.authenticationMethod,
      ) ?? null,
    [creationCapabilities, form.authenticationMethod],
  );
  const hasUnknownExistingAuthenticationMethod = Boolean(
    isEditing &&
      (!editingProfile?.authentication_method ||
        !creationCapabilities?.authentication_methods.some(
          (method) => method.id === editingProfile.authentication_method,
        )),
  );
  const isCodexOAuthForm =
    form.runtimeId.trim() === 'codex_cli' &&
    (form.authenticationMethod === 'oauth' ||
      (form.credentialSource === 'oauth_volume' &&
        form.runtimeMaterializationMode === 'oauth_home'));
  const manualCreationAllowed =
    !isEditing &&
    creationPreset?.supported === false &&
    creationPreset.manual_creation_allowed;
  const defaultFormValues = defaultFormState();

  useEffect(() => {
    const runtimeId = form.runtimeId.trim();
    const providerId = form.providerId.trim();
    setCreationCapabilitiesError(null);
    setImportedVolumeValidated(false);
    if (!runtimeId || !providerId) {
      setCreationCapabilities(null);
      return;
    }
    if (editingProfileId !== null) {
      const embedded = editingProfile?.creation_capabilities;
      if (
        embedded &&
        embedded.runtime_id === runtimeId &&
        embedded.provider_id === providerId
      ) {
        setCreationCapabilities(embedded);
      } else {
        // Current API responses carry this metadata.  Keeping an older row
        // inspectable is safer than guessing capabilities in the browser.
        setCreationCapabilities(null);
        setCreationCapabilitiesError(
          'Creation capabilities are unavailable; the existing credential contract will be preserved.',
        );
      }
      return;
    }

    const controller = new AbortController();
    void fetch(
      `/api/v1/provider-profiles/creation-capabilities?runtime_id=${encodeURIComponent(runtimeId)}&provider_id=${encodeURIComponent(providerId)}`,
      { headers: { Accept: 'application/json' }, signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(extractErrorMessage(payload));
        }
        return response.json() as Promise<ProviderProfileCreationCapabilities>;
      })
      .then((capabilities) => {
        setCreationCapabilities(capabilities);
        setForm((current) => {
          const methodStillSupported = capabilities.authentication_methods.some(
            (method) => method.id === current.authenticationMethod,
          );
          return methodStillSupported
            ? current
            : {
                ...current,
                authenticationMethod: asAuthenticationMethod(
                  capabilities.authentication_methods[0]?.id,
                ),
              };
        });
        if (!capabilities.supported) {
          setCreationCapabilitiesError(
            capabilities.diagnostics[0] ??
              'No validated creation preset exists for this runtime and provider.',
          );
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setCreationCapabilities(null);
        setCreationCapabilitiesError(
          error instanceof Error
            ? error.message
            : 'Failed to load Provider Profile creation capabilities.',
        );
      });
    return () => controller.abort();
  }, [editingProfile, editingProfileId, form.providerId, form.runtimeId]);

  // Tier capabilities: profile-scoped for edit, draft-scoped for create — MoonLadderStudios/MoonMind#3815
  useEffect(() => {
    if (isEditing && editingProfileId) {
      setTierCapabilitiesLoading(true);
      setTierCapabilitiesError(null);
      const controller = new AbortController();
      void fetch(`/api/v1/provider-profiles/${encodeURIComponent(editingProfileId)}/capabilities`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(extractErrorMessage(payload));
          }
          return response.json() as Promise<ProviderProfileTierCapabilities>;
        })
        .then((caps) => {
          setTierCapabilities(caps);
          if (caps.evidence?.stale) {
            setTierCapabilitiesError('Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.');
          }
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setTierCapabilities(null);
          setTierCapabilitiesError(
            error instanceof Error ? error.message : 'Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.',
          );
        })
        .finally(() => setTierCapabilitiesLoading(false));
      return () => controller.abort();
    }
    const runtimeId = form.runtimeId.trim();
    const providerId = form.providerId.trim();
    if (!runtimeId || !providerId) {
      setTierCapabilities(null);
      setTierCapabilitiesError(null);
      return;
    }
    if (isEditing) return;
    setTierCapabilitiesLoading(true);
    setTierCapabilitiesError(null);
    const controller = new AbortController();
    void fetch(
      `/api/v1/provider-profiles/capabilities?runtime_id=${encodeURIComponent(runtimeId)}&provider_id=${encodeURIComponent(providerId)}`,
      { headers: { Accept: 'application/json' }, signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(extractErrorMessage(payload));
        }
        return response.json() as Promise<ProviderProfileTierCapabilities>;
      })
      .then((caps) => {
        setTierCapabilities(caps);
        if (caps.evidence?.stale) {
          setTierCapabilitiesError('Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.');
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setTierCapabilities(null);
        setTierCapabilitiesError(
          error instanceof Error ? error.message : 'Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.',
        );
      })
      .finally(() => setTierCapabilitiesLoading(false));
    return () => controller.abort();
  }, [isEditing, editingProfileId, form.runtimeId, form.providerId]);

  useEffect(() => {
    const isEditingProfile = editingProfileId !== null;
    const runtimeId = form.runtimeId.trim();
    const providerId = form.providerId.trim();
    const authenticationMethod = form.authenticationMethod;
    if (!runtimeId || !providerId || !authenticationMethod) {
      setCreationPreset(null);
      setCreationPresetLoading(false);
      setCreationPresetError(null);
      return;
    }

    const controller = new AbortController();
    const params = new URLSearchParams({
      runtime_id: runtimeId,
      provider_id: providerId,
      authentication_method: authenticationMethod,
    });
    setCreationPreset(null);
    setCreationPresetLoading(true);
    setCreationPresetError(null);

    void fetch(`/api/v1/provider-profiles/creation-preset?${params.toString()}`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload: unknown = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(
            extractErrorMessage(
              payload,
              'Failed to load the Provider Profile creation preset.',
            ),
          );
        }
        return payload as ProviderProfileCreationPreset;
      })
      .then((preset) => {
        if (
          !preset ||
          typeof preset.version !== 'string' ||
          !preset.fields ||
          typeof preset.fields !== 'object'
        ) {
          throw new Error('Failed to load the Provider Profile creation preset.');
        }
        setCreationPreset(preset);
        if (isEditingProfile) {
          // Editing never auto-applies the preset to the draft; it is only
          // the source for an explicit "reset to recommended" action.
          return;
        }
        if (preset.supported) {
          setForm((current) =>
            current.runtimeId.trim() === preset.runtime_id &&
            current.providerId.trim() === preset.provider_id &&
            current.authenticationMethod === preset.authentication_method
              ? applyCreationPresetToForm(current, preset)
              : current,
          );
        } else if (preset.manual_creation_allowed) {
          setShowAdvanced(true);
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setCreationPresetError(
          error instanceof Error
            ? error.message
            : 'Failed to load the Provider Profile creation preset.',
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setCreationPresetLoading(false);
      });

    return () => controller.abort();
  }, [
    editingProfileId,
    form.runtimeId,
    form.providerId,
    form.authenticationMethod,
    creationPresetRefreshKey,
  ]);
  // #1205: OAuth may populate account_label from validated provider identity.
  // Only populate the draft that still represents the completed profile:
  // after a successful creation the form resets, so an unrelated later
  // success (for example profile A completing while draft B is open) must
  // not copy A's identity label into B's draft.
  useEffect(() => {
    if (isEditing) return;
    const draftProfileId = form.profileId.trim();
    if (!draftProfileId) return;
    const succeeded = Object.values(oauthSessions).find(
      (s) => s.status === 'succeeded' && s.profileId === draftProfileId,
    );
    if (!succeeded) return;
    const profileId = succeeded.profileId;
    if (!profileId) return;
    const linked = profiles.find((p) => p.profile_id === profileId);
    const validatedLabel = linked?.account_label?.trim();
    if (validatedLabel && !form.accountLabel.trim()) {
      setForm((cur) => (cur.accountLabel.trim() ? cur : { ...cur, accountLabel: validatedLabel }));
    }
  }, [oauthSessions, profiles, isEditing, form.accountLabel, form.profileId]);

  const updateClaudeEnrollmentForProfile = (
    profileId: string,
    updater: (current: ClaudeEnrollmentState) => ClaudeEnrollmentState,
  ) => {
    setClaudeEnrollment((current) => {
      if (!current || current.profile.profile_id !== profileId) {
        return current;
      }
      return updater(current);
    });
  };

  const applyOAuthSessionResponse = (session: OAuthSessionResponse, fallbackProfileId?: string) => {
    const sessionState = oauthSessionStateFromResponse(session, fallbackProfileId);
    setOauthSessions((current) => ({
      ...current,
      [sessionState.profileId]: sessionState,
    }));
    if (sessionState.sessionTransport === 'tmate') {
      setTmateOAuthSession(sessionState);
    } else {
      window.open(
        `/oauth-terminal?session_id=${encodeURIComponent(sessionState.sessionId)}`,
        '_blank',
        'noopener,noreferrer',
      );
    }
  };

  useEffect(() => {
    claudeEnrollmentProfileIdRef.current = claudeEnrollment?.profile.profile_id ?? null;
  }, [claudeEnrollment?.profile.profile_id]);

  // The runtime filter is the administrative equivalent of "Add Provider
  // Profile for this runtime": it seeds the create form so a new profile lands
  // in the runtime the operator is looking at. An explicitly typed runtime is
  // never overwritten, and an existing profile's immutable runtime is never
  // touched.
  useEffect(() => {
    const previousSeed = createFormRuntimeSeedRef.current;
    if (previousSeed === createFormRuntimeSeed) {
      return;
    }
    createFormRuntimeSeedRef.current = createFormRuntimeSeed;
    if (editingProfileId !== null) {
      return;
    }
    setForm((current) =>
      current.runtimeId === '' || current.runtimeId === previousSeed
        ? { ...current, runtimeId: createFormRuntimeSeed }
        : current,
    );
    setFormBaseline((current) =>
      current.runtimeId === '' || current.runtimeId === previousSeed
        ? { ...current, runtimeId: createFormRuntimeSeed }
        : current,
    );
  }, [createFormRuntimeSeed, editingProfileId]);

  // A single-runtime view cannot show the row being edited when that row
  // belongs to another runtime, so close the editor instead of leaving it
  // pointing at an invisible profile.
  useEffect(() => {
    if (editingProfileId === null || !selectedRuntimeId) {
      return;
    }
    if (form.runtimeId === selectedRuntimeId) {
      return;
    }
    setEditingProfileId(null);
    const nextForm = defaultFormState(selectedRuntimeId);
    setForm(nextForm);
    setFormBaseline(nextForm);
  }, [editingProfileId, form.runtimeId, selectedRuntimeId]);

  useEffect(() => {
    const handleProviderProfileRefresh = (event: StorageEvent) => {
      if (event.key !== PROVIDER_PROFILE_REFRESH_STORAGE_KEY || !event.newValue) {
        return;
      }
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
    };
    window.addEventListener('storage', handleProviderProfileRefresh);
    return () => {
      window.removeEventListener('storage', handleProviderProfileRefresh);
    };
  }, [queryClient]);

  const resetForm = () => {
    setEditingProfileId(null);
    const nextForm = defaultFormState(createFormRuntimeSeed);
    setForm(nextForm);
    setFormBaseline(nextForm);
    const initialTier = runtimeDefaultTierDraft();
    setTierDrafts([initialTier]);
    setDefaultTierClientId(initialTier.clientId);
    setTierBaseline(cloneTierDrafts([initialTier]));
    setTierBaselineDefaultId(initialTier.clientId);
    setInvalidSavedDefaultIndex(null);
    setIsTierRepair(false);
    setTierFieldErrors({});
    setTierUndo(null);
    setTierRemoveDialog(null);
    setCreationPreset(null);
    setCreationPresetError(null);
    setCreationCapabilities(null);
    setCreationCapabilitiesError(null);
    setShowAdvanced(false);
    setShowImportedVolume(false);
    setImportedVolumeRef('');
    setImportedVolumeValidated(false);
    onNotice(null);
  };

  const beginEditingProfile = (profile: ProviderProfile) => {
    const capabilities = profile.creation_capabilities ?? null;
    const selectedMethod = capabilities?.authentication_methods.find(
      (method) => method.id === profile.authentication_method,
    );
    const knownRoles = new Set(
      (selectedMethod?.secret_roles ?? []).map((role) => role.role),
    );
    const hasUnknownRole = Object.keys(profile.secret_refs ?? {}).some(
      (role) => !knownRoles.has(role),
    );
    const hasUnknownMethod = !profile.authentication_method || !selectedMethod;
    const nextForm = toFormState(profile);
    setEditingProfileId(profile.profile_id);
    setForm(nextForm);
    setFormBaseline(nextForm);
    const normalized = normalizeProviderProfileTiers(profile.model_tiers, profile.default_model_tier);
    if (normalized.isRepair) {
      const repairTier = runtimeDefaultTierDraft();
      setTierDrafts([repairTier]);
      setDefaultTierClientId(repairTier.clientId);
      setIsTierRepair(false);
      setInvalidSavedDefaultIndex(normalized.invalidSavedDefaultIndex);
      setTierBaseline(null);
      setTierBaselineDefaultId(null);
    } else {
      setTierDrafts(normalized.tiers);
      setDefaultTierClientId(normalized.defaultTierClientId);
      setIsTierRepair(false);
      setInvalidSavedDefaultIndex(normalized.invalidSavedDefaultIndex);
      setTierBaseline(cloneTierDrafts(normalized.tiers));
      setTierBaselineDefaultId(normalized.defaultTierClientId);
    }
    setTierFieldErrors({});
    setTierUndo(null);
    setTierRemoveDialog(null);
    setCreationCapabilities(capabilities);
    setShowAdvanced(hasUnknownMethod || hasUnknownRole);
    setShowImportedVolume(false);
    setImportedVolumeRef(profile.volume_ref ?? '');
    setImportedVolumeValidated(false);
    onNotice(null);
  };

  const handleEditTiers = (profile: ProviderProfile) => {
    beginEditingProfile(profile);
    window.setTimeout(() => {
      tierSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const first = tierSectionRef.current?.querySelector<HTMLElement>('input, select, button');
      first?.focus();
    }, 0);
  };

  const tierDraftsChanged = useMemo(() => {
    if (!tierBaseline) return tierDrafts.length > 0;
    if (tierBaseline.length !== tierDrafts.length) return true;
    if (tierBaselineDefaultId !== defaultTierClientId) return true;
    for (let idx = 0; idx < tierDrafts.length; idx += 1) {
      const a = tierDrafts[idx]!;
      const b = tierBaseline[idx]!;
      if (a.label !== b.label || a.model !== b.model || a.effort !== b.effort || JSON.stringify(a.parameters) !== JSON.stringify(b.parameters) || JSON.stringify(a.annotations) !== JSON.stringify(b.annotations)) {
        return true;
      }
    }
    return false;
  }, [tierBaseline, tierBaselineDefaultId, tierDrafts, defaultTierClientId]);

  const hasTierDraft = tierDrafts.length > 0 || isTierRepair || invalidSavedDefaultIndex !== null;

  // Server validation aimed at a collapsed advanced field reveals the region
  // and hands focus to that control. The region is keyed by the canonical
  // backend field name, so no per-error branch is needed.
  useEffect(() => {
    if (!advancedFocusRequest || !showAdvanced) return;
    const region = document.getElementById(ADVANCED_REGION_ID);
    const container = region?.querySelector<HTMLElement>(
      `[data-advanced-field="${advancedFocusRequest.field}"]`,
    );
    const control =
      container?.querySelector<HTMLElement>(FOCUSABLE_ADVANCED_CONTROL) ?? container ?? null;
    control?.focus();
    setAdvancedFocusRequest(null);
  }, [advancedFocusRequest, showAdvanced]);

  useSettingsDraftRegistration(
    'provider-profile',
    canWriteProviderProfiles && (JSON.stringify(form) !== JSON.stringify(formBaseline) || tierDraftsChanged || hasTierDraft && !tierBaseline),
    resetForm,
  );

  const handleAddTier = () => {
    const newTier = runtimeDefaultTierDraft();
    setTierDrafts((current) => [...current, newTier]);
    if (!defaultTierClientId && tierDrafts.length === 0) {
      setDefaultTierClientId(newTier.clientId);
    }
    setTierUndo(null);
    setIsTierRepair(false);
    setTierLiveMessage(`Tier ${tierDrafts.length + 1} added`);
    focusedTierClientIdRef.current = newTier.clientId;
    window.setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-tier-client-id="${newTier.clientId}"] input, [data-tier-client-id="${newTier.clientId}"] select`);
      el?.focus();
    }, 0);
  };

  const handleCreateRepairTier = () => {
    const newTier = runtimeDefaultTierDraft();
    setTierDrafts([newTier]);
    setDefaultTierClientId(newTier.clientId);
    setIsTierRepair(false);
    setInvalidSavedDefaultIndex(null);
    setTierBaseline(null);
    setTierBaselineDefaultId(null);
    setTierLiveMessage('Tier 1 created');
  };

  const handleDuplicateTier = (source: ProviderProfileTierDraft) => {
    const copy = duplicateTierDraft(source);
    setTierDrafts((current) => [...current, copy]);
    setTierLiveMessage(`Tier ${tierDrafts.length + 1} duplicated`);
  };

  const handleTierLabelChange = (clientId: string, label: string) => {
    setTierDrafts((current) => current.map((t) => (t.clientId === clientId ? { ...t, label } : t)));
  };

  const handleTierModelChange = (clientId: string, model: string | null) => {
    setTierDrafts((current) => current.map((t) => (t.clientId === clientId ? { ...t, model } : t)));
  };

  const handleTierEffortChange = (clientId: string, effort: string | null) => {
    setTierDrafts((current) => current.map((t) => (t.clientId === clientId ? { ...t, effort } : t)));
  };

  const handleTierParametersChange = (clientId: string, text: string) => {
    try {
      const parsed = text.trim() === '' ? {} : JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Must be object');
      setTierDrafts((current) => current.map((t) => (t.clientId === clientId ? { ...t, parameters: parsed as Record<string, unknown> } : t)));
      setTierFieldErrors((prev) => {
        const next = { ...prev };
        delete next[`${clientId}.parameters`];
        return next;
      });
    } catch (e) {
      setTierFieldErrors((prev) => ({ ...prev, [`${clientId}.parameters`]: e instanceof Error ? e.message : 'Invalid JSON' }));
    }
  };

  const handleTierAnnotationsChange = (clientId: string, text: string) => {
    try {
      const parsed = text.trim() === '' ? {} : JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Must be object');
      setTierDrafts((current) => current.map((t) => (t.clientId === clientId ? { ...t, annotations: parsed as Record<string, unknown> } : t)));
      setTierFieldErrors((prev) => {
        const next = { ...prev };
        delete next[`${clientId}.annotations`];
        return next;
      });
    } catch (e) {
      setTierFieldErrors((prev) => ({ ...prev, [`${clientId}.annotations`]: e instanceof Error ? e.message : 'Invalid JSON' }));
    }
  };

  const requestRemoveTier = (index: number) => {
    const tier = tierDrafts[index];
    if (!tier) return;
    if (tierDrafts.length === 1) return;
    const isDefault = tier.clientId === defaultTierClientId;
    const isMiddle = index < tierDrafts.length - 1;
    if (!isDefault && !isMiddle) {
      const wasDefault = false;
      const removed = tier;
      setTierDrafts((current) => current.filter((_, i) => i !== index));
      setTierUndo({ tier: removed, index, wasDefault });
      setTierLiveMessage(`Tier ${index + 1} removed`);
      return;
    }
    setTierRemoveDialog({ index, tier, isDefault, isMiddle });
    if (isDefault) {
      const survivors = tierDrafts.filter((_, i) => i !== index);
      const fallback = survivors[index] ?? survivors[index - 1] ?? survivors[0];
      setTierRemoveReplacementId(fallback ? fallback.clientId : null);
    } else {
      setTierRemoveReplacementId(null);
    }
  };

  const confirmRemoveTier = () => {
    if (!tierRemoveDialog) return;
    const { index, isDefault } = tierRemoveDialog;
    const removed = tierDrafts[index];
    if (!removed) return;
    const nextDrafts = tierDrafts.filter((_, i) => i !== index);
    let nextDefault = defaultTierClientId;
    if (isDefault) {
      if (!tierRemoveReplacementId || !nextDrafts.some((t) => t.clientId === tierRemoveReplacementId)) {
        onNotice({ level: 'error', text: 'Choose a replacement default tier.' });
        return;
      }
      nextDefault = tierRemoveReplacementId;
    }
    setTierDrafts(nextDrafts);
    setDefaultTierClientId(nextDefault);
    setTierRemoveDialog(null);
    setTierRemoveReplacementId(null);
    setTierLiveMessage(`Tier ${index + 1} removed`);
  };

  const handleUndoLastRemove = () => {
    if (!tierUndo) return;
    setTierDrafts((current) => {
      const next = [...current];
      next.splice(tierUndo.index, 0, tierUndo.tier);
      return next;
    });
    if (tierUndo.wasDefault) {
      setDefaultTierClientId(tierUndo.tier.clientId);
    }
    setTierUndo(null);
    setTierLiveMessage(`Tier ${tierUndo.index + 1} restored`);
  };

  const closeClaudeEnrollment = () => {
    claudeEnrollmentProfileIdRef.current = null;
    setClaudeEnrollment(null);
  };

  const openClaudeEnrollment = (profile: ProviderProfile) => {
    const authModel = providerAuthModel(profile);
    claudeEnrollmentProfileIdRef.current = profile.profile_id;
    setClaudeEnrollment({
      profile,
      step: 'not_connected',
      token: '',
      failureReason: null,
      statusLabel: authModel.kind === 'claude_credentials' ? authModel.statusLabel : null,
      readiness: authModel.kind === 'claude_credentials' ? authModel.readiness : null,
    });
    onNotice(null);
  };

  const updateClaudeEnrollmentToken = (token: string) => {
    setClaudeEnrollment((current) => (current ? { ...current, token } : current));
  };

  const continueClaudeEnrollment = () => {
    setClaudeEnrollment((current) =>
      current ? { ...current, step: 'awaiting_token_paste', failureReason: null } : current,
    );
  };

  const claudeEnrollmentMutation = useMutation({
    mutationFn: async ({
      profileId,
      submittedToken,
    }: {
      profileId: string;
      submittedToken: string;
    }) => {
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}/manual-auth/commit`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: submittedToken }),
        },
      );
      const payload: unknown = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(redactClaudeSecretText(extractErrorMessage(payload), submittedToken) ?? 'Anthropic API key validation failed.');
      }

      return payload as ClaudeManualAuthResult;
    },
    onMutate: ({ profileId }) => {
      updateClaudeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'validating_token',
        token: '',
        failureReason: null,
      }));
    },
    onSuccess: async (result, { profileId }) => {
      updateClaudeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'saving_secret',
        token: '',
      }));
      await delay(CLAUDE_ENROLLMENT_PROGRESS_DELAY_MS);
      updateClaudeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'updating_profile',
        token: '',
      }));
      await delay(CLAUDE_ENROLLMENT_PROGRESS_DELAY_MS);
      if (claudeEnrollmentProfileIdRef.current !== profileId) {
        return;
      }
      updateClaudeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'ready',
        token: '',
        failureReason: null,
        statusLabel: formatStatusLabel(result.status_label ?? result.statusLabel ?? current.statusLabel, ''),
        readiness: normalizeReadinessMetadata(result.readiness) ?? current.readiness,
      }));
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({
        level: 'ok',
        text: `Anthropic API key enrollment completed for "${profileId}".`,
      });
      void applyPendingDefaultIntent(profileId);
    },
    onError: (error, { profileId, submittedToken }) => {
      if (claudeEnrollmentProfileIdRef.current !== profileId) {
        return;
      }
      const failureReason =
        error instanceof Error
          ? redactClaudeSecretText(error.message, submittedToken)
          : 'Anthropic API key validation failed.';
      updateClaudeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'failed',
        token: '',
        failureReason: failureReason ?? 'Anthropic API key validation failed.',
      }));
    },
  });

  const submitClaudeEnrollment = () => {
    if (!claudeEnrollment) return;
    const profileId = claudeEnrollment.profile.profile_id;
    const submittedToken = claudeEnrollment.token.trim();
    if (!submittedToken) {
      onNotice({ level: 'error', text: 'Anthropic API key is required.' });
      return;
    }

    claudeEnrollmentMutation.mutate({ profileId, submittedToken });
  };

  useEffect(() => {
    if (!claudeEnrollment) return;
    claudeEnrollmentDrawerRef.current?.focus();
  }, [claudeEnrollment?.profile.profile_id]);

  useEffect(() => {
    if (!claudeEnrollment) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeClaudeEnrollment();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [claudeEnrollment]);

  // ── OpenCode API-key enrollment (mirrors Claude flow but uses /credentials/api-key) ──
  const [opencodeEnrollment, setOpencodeEnrollment] = useState<ClaudeEnrollmentState | null>(null);
  const opencodeEnrollmentDrawerRef = useRef<HTMLDivElement | null>(null);
  const opencodeEnrollmentProfileIdRef = useRef<string | null>(null);

  const updateOpencodeEnrollmentForProfile = (
    profileId: string,
    updater: (current: ClaudeEnrollmentState) => ClaudeEnrollmentState,
  ) => {
    setOpencodeEnrollment((current) => {
      if (!current || current.profile.profile_id !== profileId) {
        return current;
      }
      return updater(current);
    });
  };

  const closeOpencodeEnrollment = () => {
    opencodeEnrollmentProfileIdRef.current = null;
    setOpencodeEnrollment(null);
  };

  const openOpencodeEnrollment = (profile: ProviderProfile) => {
    const authModel = providerAuthModel(profile);
    opencodeEnrollmentProfileIdRef.current = profile.profile_id;
    setOpencodeEnrollment({
      profile,
      step: 'not_connected',
      token: '',
      failureReason: null,
      statusLabel: authModel.kind === 'opencode_credentials' ? authModel.statusLabel : null,
      readiness: authModel.kind === 'opencode_credentials' ? authModel.readiness : null,
    });
    onNotice(null);
  };

  const updateOpencodeEnrollmentToken = (token: string) => {
    setOpencodeEnrollment((current) => (current ? { ...current, token } : current));
  };

  const continueOpencodeEnrollment = () => {
    setOpencodeEnrollment((current) =>
      current ? { ...current, step: 'awaiting_token_paste', failureReason: null } : current,
    );
  };

  const opencodeEnrollmentMutation = useMutation({
    mutationFn: async ({
      profileId,
      submittedToken,
      profile,
    }: {
      profileId: string;
      submittedToken: string;
      profile: ProviderProfile;
    }) => {
      const copy = apiKeyEnrollmentCopy(profile);
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}/credentials/api-key`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: submittedToken }),
        },
      );
      const payload: unknown = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          redactClaudeSecretText(extractErrorMessage(payload), submittedToken) ??
            `${copy.credentialLabel} validation failed.`,
        );
      }

      return payload as ClaudeManualAuthResult;
    },
    onMutate: ({ profileId }) => {
      updateOpencodeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'validating_token',
        token: '',
        failureReason: null,
      }));
    },
    onSuccess: async (result, { profileId, profile }) => {
      const copy = apiKeyEnrollmentCopy(profile);
      updateOpencodeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'saving_secret',
        token: '',
      }));
      await delay(CLAUDE_ENROLLMENT_PROGRESS_DELAY_MS);
      updateOpencodeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'updating_profile',
        token: '',
      }));
      await delay(CLAUDE_ENROLLMENT_PROGRESS_DELAY_MS);
      if (opencodeEnrollmentProfileIdRef.current !== profileId) {
        return;
      }
      updateOpencodeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'ready',
        token: '',
        failureReason: null,
        statusLabel: formatStatusLabel(result.status_label ?? result.statusLabel ?? current.statusLabel, ''),
        readiness: normalizeReadinessMetadata(result.readiness) ?? current.readiness,
      }));
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({
        level: 'ok',
        text: `${copy.credentialLabel} enrollment completed for "${profileId}".`,
      });
      void applyPendingDefaultIntent(profileId);
    },
    onError: (error, { profileId, submittedToken, profile }) => {
      const copy = apiKeyEnrollmentCopy(profile);
      if (opencodeEnrollmentProfileIdRef.current !== profileId) {
        return;
      }
      const failureReason =
        error instanceof Error
          ? redactClaudeSecretText(error.message, submittedToken)
          : `${copy.credentialLabel} validation failed.`;
      updateOpencodeEnrollmentForProfile(profileId, (current) => ({
        ...current,
        step: 'failed',
        token: '',
        failureReason: failureReason ?? `${copy.credentialLabel} validation failed.`,
      }));
    },
  });

  const submitOpencodeEnrollment = () => {
    if (!opencodeEnrollment) return;
    const profile = opencodeEnrollment.profile;
    const copy = apiKeyEnrollmentCopy(profile);
    const profileId = opencodeEnrollment.profile.profile_id;
    const submittedToken = opencodeEnrollment.token.trim();
    if (!submittedToken) {
      onNotice({ level: 'error', text: `${copy.credentialLabel} is required.` });
      return;
    }

    opencodeEnrollmentMutation.mutate({ profileId, submittedToken, profile });
  };

  useEffect(() => {
    if (!opencodeEnrollment) return;
    opencodeEnrollmentDrawerRef.current?.focus();
  }, [opencodeEnrollment?.profile.profile_id]);

  useEffect(() => {
    opencodeEnrollmentProfileIdRef.current = opencodeEnrollment?.profile.profile_id ?? null;
  }, [opencodeEnrollment?.profile.profile_id]);

  useEffect(() => {
    if (!opencodeEnrollment) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeOpencodeEnrollment();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [opencodeEnrollment]);

  const importedVolumeMutation = useMutation({
    mutationFn: async () => {
      const volumeRef = importedVolumeRef.trim();
      if (!volumeRef) {
        throw new Error('Existing credential volume is required.');
      }
      const response = await fetch('/api/v1/provider-profiles/credential-volume/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          runtime_id: form.runtimeId.trim(),
          provider_id: form.providerId.trim(),
          volume_ref: volumeRef,
        }),
      });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(extractErrorMessage(payload));
      }
      return payload as {
        volume_ref: string;
        volume_mount_path: string;
      };
    },
    onSuccess: (result) => {
      setForm((current) => ({
        ...current,
        volumeRef: result.volume_ref,
        volumeMountPath: result.volume_mount_path,
      }));
      setImportedVolumeValidated(true);
      onNotice({ level: 'ok', text: 'Imported credential volume validated.' });
    },
    onError: (error: Error) => {
      setImportedVolumeValidated(false);
      onNotice({ level: 'error', text: error.message });
    },
  });

  const saveMutation = useMutation({
    mutationFn: async (formState: ProviderProfileFormState) => {
      if (tierDrafts.length === 0) {
        throw new Error('At least one tier is required.');
      }
      if (!defaultTierClientId) {
        throw new Error('Select a default tier.');
      }
      const payload = buildSavePayload(formState, {
        isEditing,
        formBaseline,
        creationPreset,
        importExistingCredentialVolume: importedVolumeValidated,
        tierDrafts,
        defaultTierClientId,
        tierBaseline,
        tierBaselineDefaultId,
      });
      const endpoint = isEditing
        ? `/api/v1/provider-profiles/${encodeURIComponent(payload.profile_id)}`
        : '/api/v1/provider-profiles';
      const response = await fetch(endpoint, {
        method: isEditing ? 'PATCH' : 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail = extractErrorMessage(
          errorPayload,
          `Failed to ${isEditing ? 'update' : 'create'} provider profile.`,
        );
        throw new ProviderProfileRequestError(
          detail,
          extractErrorCode(errorPayload),
          extractErrorField(errorPayload),
        );
      }
      return response.json() as Promise<ProviderProfile>;
    },
    onSuccess: (savedProfile, submittedForm) => {
      const createdProfile = !isEditing;
      if (createdProfile && submittedForm.isDefault && !savedProfile.is_default) {
        // Creation stores the profile non-default until credential validation;
        // carry the checked intent forward to the readiness-completing step.
        pendingDefaultIntentRef.current.add(savedProfile.profile_id);
      }
      onNotice({
        level: 'ok',
        text: isEditing
          ? `Profile "${editingProfileId}" updated.`
          : `Profile "${submittedForm.profileId.trim()}" created.`,
      });
      setEditingProfileId(null);
      const nextForm = defaultFormState(createFormRuntimeSeed);
      setForm(nextForm);
      setFormBaseline(nextForm);
      {
        const normalized = normalizeProviderProfileTiers(savedProfile.model_tiers, savedProfile.default_model_tier);
        if (normalized.isRepair) {
          const repair = runtimeDefaultTierDraft();
          setTierDrafts([repair]);
          setDefaultTierClientId(repair.clientId);
          setIsTierRepair(false);
          setInvalidSavedDefaultIndex(null);
        } else {
          setTierDrafts(normalized.tiers);
          setDefaultTierClientId(normalized.defaultTierClientId);
          setIsTierRepair(false);
          setInvalidSavedDefaultIndex(normalized.invalidSavedDefaultIndex);
        }
        setTierBaseline(null);
        setTierBaselineDefaultId(null);
        setTierFieldErrors({});
        setTierUndo(null);
        setTierRemoveDialog(null);
      }
      setShowAdvanced(false);
      queryClient.setQueryData<ProviderProfile[]>(
        PROVIDER_PROFILE_QUERY_KEY,
        (currentProfiles = []) => {
          const nextProfiles = currentProfiles.some(
            (profile) => profile.profile_id === savedProfile.profile_id,
          )
            ? currentProfiles.map((profile) =>
                profile.profile_id === savedProfile.profile_id ? savedProfile : profile,
              )
            : [...currentProfiles, savedProfile];

          if (!savedProfile.is_default) {
            return nextProfiles;
          }

          return nextProfiles.map((profile) =>
            profile.runtime_id === savedProfile.runtime_id &&
            profile.profile_id !== savedProfile.profile_id &&
            profile.is_default
              ? { ...profile, is_default: false }
              : profile,
          );
        },
      );
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      const submittedMethod =
        savedProfile.creation_capabilities?.authentication_methods.find(
          (method) => method.id === submittedForm.authenticationMethod,
        ) ??
        creationCapabilities?.authentication_methods.find(
          (method) => method.id === submittedForm.authenticationMethod,
        );
      const guidedApiKeySetupPending =
        submittedMethod?.setup_action === 'api_key' &&
        submittedMethod.launch_ready_after_setup &&
        savedProfile.auth_state !== 'connected';
      if (
        createdProfile &&
        submittedForm.authenticationMethod === 'api_key' &&
        guidedApiKeySetupPending
      ) {
        if (
          savedProfile.runtime_id === 'claude_code' &&
          savedProfile.provider_id === 'anthropic'
        ) {
          openClaudeEnrollment(savedProfile);
        } else {
          openOpencodeEnrollment(savedProfile);
        }
      } else if (
        createdProfile &&
        submittedForm.authenticationMethod === 'oauth' &&
        !importedVolumeValidated
      ) {
        startOAuthFromCreationRef.current(savedProfile);
      }
    },
    onError: (error: Error) => {
      setShowAdvanced(true);
      const targetField =
        error instanceof ProviderProfileRequestError ? error.field : null;
      if (targetField && ADVANCED_DISCLOSURE_FIELDS.has(targetField)) {
        // The control is inside the region we just revealed, so the focus move
        // has to wait for that render.
        setAdvancedFocusRequest((current) => ({
          field: targetField,
          nonce: (current?.nonce ?? 0) + 1,
        }));
      }
      if (
        !isEditing &&
        error instanceof ProviderProfileRequestError &&
        error.code === 'provider_profile_creation_preset_version_mismatch'
      ) {
        setCreationPresetRefreshKey((current) => current + 1);
        onNotice({
          level: 'error',
          text: 'The creation policy changed. Reloading the current preset for review.',
        });
        return;
      }
      const message = error.message ?? '';
      const tierMatch = message.match(/model_tiers\.(\d+)\.(model|effort|label|parameters|annotations)/);
      if (tierMatch) {
        const tierIndex = parseInt(tierMatch[1]!, 10);
        const field = tierMatch[2]!;
        const tier = tierDrafts[tierIndex];
        if (tier) {
          setTierFieldErrors((prev) => ({ ...prev, [`${tier.clientId}.${field}`]: message }));
          tierSectionRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
      } else if (message.includes('default_model_tier')) {
        setTierFieldErrors((prev) => ({ ...prev, default_model_tier: message }));
      }
      onNotice({ level: 'error', text: error.message });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (profileId: string) => {
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to delete provider profile.';
        throw new Error(detail);
      }
    },
    onSuccess: (_data, profileId) => {
      onNotice({ level: 'ok', text: `Profile "${profileId}" deleted.` });
      if (editingProfileId === profileId) {
        setEditingProfileId(null);
        const nextForm = defaultFormState(createFormRuntimeSeed);
        setForm(nextForm);
        setFormBaseline(nextForm);
      }
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async ({
      profileId,
      enabled,
    }: {
      profileId: string;
      enabled: boolean;
    }) => {
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: JSON.stringify({ enabled }),
        },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to update provider profile state.';
        throw new Error(detail);
      }
    },
    onSuccess: (_data, variables) => {
      onNotice({
        level: 'ok',
        text: `Profile "${variables.profileId}" ${
          variables.enabled ? 'enabled' : 'disabled'
        }.`,
      });
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  const startOAuthMutation = useMutation({
    mutationFn: async (profile: ProviderProfile) => {
      const response = await fetch('/api/v1/oauth-sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          runtime_id: profile.runtime_id,
          profile_id: profile.profile_id,
          volume_ref: profile.volume_ref ?? undefined,
          volume_mount_path: profile.volume_mount_path ?? undefined,
          provider_id: profile.provider_id,
          provider_label: profile.provider_label ?? undefined,
          account_label: profile.account_label ?? profile.profile_id,
          max_parallel_runs: profile.max_parallel_runs,
          cooldown_after_429_seconds: profile.cooldown_after_429_seconds,
          rate_limit_policy: profile.rate_limit_policy,
        }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to start OAuth session.';
        throw new Error(detail);
      }
      return response.json() as Promise<OAuthSessionResponse>;
    },
    onSuccess: (session) => {
      applyOAuthSessionResponse(session);
      onNotice({
        level: 'ok',
        text: `OAuth session "${session.session_id}" started for "${session.profile_id}".`,
      });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });
  startOAuthFromCreationRef.current = (profile) => startOAuthMutation.mutate(profile);

  const cancelOAuthMutation = useMutation({
    mutationFn: async ({ profileId, sessionId }: { profileId: string; sessionId: string }) => {
      const response = await fetch(
        `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}/cancel`,
        { method: 'POST' },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to cancel OAuth session.';
        throw new Error(detail);
      }
      return { profileId, sessionId };
    },
    onSuccess: ({ profileId }) => {
      setOauthSessions((current) => ({
        ...current,
        [profileId]: {
          ...current[profileId],
          sessionId: current[profileId]?.sessionId ?? '',
          profileId,
          status: 'cancelled',
        },
      }));
      onNotice({ level: 'ok', text: `OAuth session for "${profileId}" cancelled.` });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  const finalizeOAuthMutation = useMutation({
    mutationFn: async ({ profileId, sessionId }: { profileId: string; sessionId: string }) => {
      const response = await fetch(
        `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}/finalize`,
        { method: 'POST' },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to finalize OAuth session.';
        throw new Error(detail);
      }
      return { profileId, sessionId };
    },
    onSuccess: ({ profileId, sessionId }) => {
      setOauthSessions((current) => ({
        ...current,
        [profileId]: { ...current[profileId], sessionId, profileId, status: 'succeeded' },
      }));
      setTmateOAuthSession((current) =>
        current?.sessionId === sessionId ? { ...current, status: 'succeeded' } : current,
      );
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({ level: 'ok', text: `OAuth session for "${profileId}" finalized.` });
      void applyPendingDefaultIntent(profileId);
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  const retryOAuthMutation = useMutation({
    mutationFn: async ({ profileId, sessionId }: { profileId: string; sessionId: string }) => {
      const response = await fetch(
        `/api/v1/oauth-sessions/${encodeURIComponent(sessionId)}/reconnect`,
        { method: 'POST' },
      );
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : 'Failed to retry OAuth session.';
        throw new Error(detail);
      }
      const session = (await response.json()) as OAuthSessionResponse;
      return { profileId, session };
    },
    onSuccess: ({ profileId, session }) => {
      applyOAuthSessionResponse(session, profileId);
      onNotice({ level: 'ok', text: `OAuth session for "${profileId}" retried.` });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  const claudeOAuthLifecycleMutation = useMutation({
    mutationFn: async ({
      profileId,
      actionId,
    }: {
      profileId: string;
      actionId: 'validate_oauth' | 'disconnect_oauth';
    }) => {
      const endpointAction = actionId === 'validate_oauth' ? 'validate' : 'disconnect';
      const response = await fetch(
        `/api/v1/provider-profiles/${encodeURIComponent(profileId)}/oauth/${endpointAction}`,
        { method: 'POST' },
      );
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(redactClaudeSecretText(extractErrorMessage(payload)) ?? 'Claude OAuth action failed.');
      }
      return { profileId, actionId };
    },
    onSuccess: ({ profileId, actionId }) => {
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({
        level: 'ok',
        text:
          actionId === 'validate_oauth'
            ? `Claude OAuth validated for "${profileId}".`
            : `Claude OAuth disconnected for "${profileId}".`,
      });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

  useEffect(() => {
    const activeSessions = Object.entries(oauthSessions).filter(([, session]) =>
      isActiveOAuthStatus(session.status),
    );
    if (activeSessions.length === 0) {
      return undefined;
    }

    const pollSessionStatuses = async () => {
      const sessionUpdates = await Promise.all(
        activeSessions.map(async ([profileId, session]) => {
          const response = await fetch(
            `/api/v1/oauth-sessions/${encodeURIComponent(session.sessionId)}`,
            { headers: { Accept: 'application/json' } },
          );
          if (!response.ok) {
            return null;
          }
          const updatedSession = (await response.json()) as OAuthSessionResponse;
          return { profileId, session: updatedSession };
        }),
      );

      const appliedUpdates = sessionUpdates.filter(
        (update): update is { profileId: string; session: OAuthSessionResponse } => update !== null,
      );
      if (appliedUpdates.length === 0) {
        return;
      }

      setOauthSessions((current) => {
        const next = { ...current };
        let hasChanges = false;
        for (const { profileId, session } of appliedUpdates) {
          const existing = current[profileId];
          const sessionState = oauthSessionStateFromResponse(session, profileId);
          if (existing && oauthSessionStatesEqual(existing, sessionState)) {
            continue;
          }
          next[profileId] = sessionState;
          hasChanges = true;
        }
        return hasChanges ? next : current;
      });

      setTmateOAuthSession((current) => {
        if (!current) {
          return current;
        }
        const matchingUpdate = appliedUpdates.find(
          ({ session }) => session.session_id === current.sessionId,
        );
        if (!matchingUpdate) {
          return current;
        }
        const sessionState = oauthSessionStateFromResponse(
          matchingUpdate.session,
          matchingUpdate.profileId,
        );
        return oauthSessionStatesEqual(current, sessionState) ? current : sessionState;
      });

      if (appliedUpdates.some(({ session }) => session.status === 'succeeded')) {
        queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      }
    };

    const intervalId = window.setInterval(() => {
      void pollSessionStatuses().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [oauthSessions, queryClient]);

  const formSecretRefs = useMemo(() => {
    try {
      return parseSecretRefs(form.secretRefsText);
    } catch {
      return {};
    }
  }, [form.secretRefsText]);
  const selectedSecretRoleNames = new Set(
    (selectedAuthenticationCapability?.secret_roles ?? []).map((role) => role.role),
  );
  const unknownSecretRoleBindings = Object.entries(formSecretRefs).filter(
    ([role]) => !selectedSecretRoleNames.has(role),
  );
  const selectedCredentialSource =
    selectedAuthenticationCapability?.fields.credential_source?.value ?? form.credentialSource;
  const selectedMaterializationMode =
    selectedAuthenticationCapability?.fields.runtime_materialization_mode?.value ??
    form.runtimeMaterializationMode;
  const volumeReferenceLabel =
    form.volumeRef ||
    (selectedAuthenticationCapability?.id === 'oauth' ? 'Owned by enrollment' : 'Not used');
  const volumeMountPathLabel =
    form.volumeMountPath || selectedAuthenticationCapability?.imported_volume.mount_path || 'Not used';
  const volumeMetadataSource = importedVolumeValidated
    ? 'validated_import'
    : form.volumeRef || form.volumeMountPath
      ? 'existing_profile'
      : selectedAuthenticationCapability?.imported_volume.source ?? 'existing_profile';
  const currentConnectionLabel = editingProfile
    ? activationStatusLabel(editingProfile) ?? 'Unknown'
    : form.authenticationMethod === 'none'
      ? 'Ready without credentials'
      : 'Setup required';
  const currentLaunchReadinessLabel = editingProfile?.readiness?.launch_ready
    ? 'Ready'
    : form.authenticationMethod === 'none'
      ? 'Ready after profile creation.'
      : form.authenticationMethod === 'oauth'
        ? 'Blocked until OAuth setup succeeds.'
        : form.authenticationMethod === 'api_key'
          ? 'Blocked until API-key setup succeeds.'
          : 'Blocked until a supported authentication method is selected.';

  const updateSecretRoleBinding = (role: string, secretRef: string) => {
    setForm((current) => {
      const next = parseSecretRefs(current.secretRefsText);
      if (secretRef) {
        next[role] = secretRef;
      } else {
        delete next[role];
      }
      return { ...current, secretRefsText: JSON.stringify(next, null, 2) };
    });
  };

  return (
    <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-200 dark:border-slate-800 pb-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Profiles</h3>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Manage your configured provider profiles. Select a profile below to edit, or scroll down to create a new one.
          </p>
        </div>
      </div>

      {onSelectRuntimeId ? (
        <div className="mt-4 flex flex-col gap-1.5 text-sm md:max-w-xs">
          <label
            className="font-medium text-slate-700 dark:text-slate-300"
            htmlFor={RUNTIME_FILTER_CONTROL_ID}
          >
            Profile runtime filter
          </label>
          <select
            id={RUNTIME_FILTER_CONTROL_ID}
            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
            value={activeRuntimeFilterValue}
            onChange={(event) =>
              onSelectRuntimeId(
                event.target.value === ALL_RUNTIMES_FILTER_VALUE
                  ? undefined
                  : event.target.value,
              )
            }
          >
            {runtimeFilterChoices.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="provider-profiles-table-wrap mt-6 overflow-x-auto">
        <table
          className="provider-profiles-table min-w-full md:divide-y divide-slate-200 dark:divide-slate-800 text-left text-sm"
          role="table"
        >
          <thead className="bg-slate-50 dark:bg-slate-800/50" role="rowgroup">
            <tr role="row">
              <th
                id="provider-profile-header-profile"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Profile
              </th>
              <th
                id="provider-profile-header-runtime"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Runtime
              </th>
              <th
                id="provider-profile-header-provider"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Provider
              </th>
              <th
                id="provider-profile-header-credential"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Credential
              </th>
              <th
                id="provider-profile-header-secret-refs"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Secret refs
              </th>
              <th
                id="provider-profile-header-status"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Status
              </th>
              <th
                id="provider-profile-header-actions"
                scope="col"
                role="columnheader"
                className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400"
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-mm-border/80 bg-transparent" role="rowgroup">
            {profiles.length === 0 ? (
              <tr role="row">
                <td className="px-3 py-6 text-slate-500 dark:text-slate-400" colSpan={7} role="cell">
                  {selectedRuntimeId
                    ? `No provider profiles are configured for ${formatRuntimeLabel(selectedRuntimeId)}.`
                    : 'No provider profiles configured yet.'}
                </td>
              </tr>
            ) : (
              profiles.map((profile) => {
                const oauthSession = oauthSessions[profile.profile_id];
                const authModel = providerAuthModel(profile);
                const canStartOAuth = authModel.kind === 'codex_oauth';
                const canUseGenericApiKey = Boolean(
                  ((profile.runtime_id === 'codex_cli' && profile.provider_id === 'openai') ||
                    profile.creation_capabilities?.authentication_methods.some(
                      (method) => method.id === 'api_key',
                    )) &&
                    !isClaudeCredentialMethodProfile(profile) &&
                    !isOpencodeCredentialMethodProfile(profile),
                );
                const activationLabel = activationStatusLabel(profile);
                const modelTiers = providerProfileModelTiers(profile);
                const enableAllowed = mayEnableFromSettings(profile);
                void defaultTaskModelByRuntime;
                const claudeReadiness =
                  authModel.kind === 'claude_credentials' ? authModel.readiness : undefined;
                const opencodeReadiness =
                  authModel.kind === 'opencode_credentials' ? authModel.readiness : undefined;
                const hasStatusDetails = Boolean(
                  (activationLabel && activationLabel !== 'Connected') ||
                    profile.disabled_reason ||
                    profile.last_validated_at ||
                    profile.readiness ||
                    claudeReadiness?.connected !== undefined ||
                    claudeReadiness?.lastValidatedAt ||
                    claudeReadiness?.backingSecretExists !== undefined ||
                    claudeReadiness?.launchReady !== undefined ||
                    claudeReadiness?.failureReason ||
                    opencodeReadiness?.connected !== undefined ||
                    opencodeReadiness?.lastValidatedAt ||
                    opencodeReadiness?.backingSecretExists !== undefined ||
                    opencodeReadiness?.launchReady !== undefined ||
                    opencodeReadiness?.failureReason,
                );
                return (
                <tr key={profile.profile_id} role="row">
                  <td
                    className="px-3 py-4"
                    data-label="Profile"
                    headers="provider-profile-header-profile"
                    role="cell"
                  >
                    <div className="font-medium text-slate-900 dark:text-white">{profile.profile_id}</div>
                    {profile.is_default ? (
                      <div className="text-xs font-medium text-emerald-700 dark:text-emerald-400">
                        Runtime default
                      </div>
                    ) : null}
                    {profile.model_overrides && Object.keys(profile.model_overrides).length > 0 ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        Overrides: {Object.keys(profile.model_overrides).join(', ')}
                      </div>
                    ) : null}
                    {(() => {
                      const count = providerProfileTierCount(profile);
                      const tiers = modelTiers;
                      const isRepair = count === 0 || count === null;
                      if (isRepair) {
                        return (
                          <div className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-300" aria-label={`${profile.profile_id} tier policy unavailable`}>
                            Tier policy unavailable · needs repair
                          </div>
                        );
                      }
                      const validDefault = profile.default_model_tier && profile.default_model_tier >= 1 && profile.default_model_tier <= tiers.length ? profile.default_model_tier : 1;
                      const hasInvalidDefault = profile.default_model_tier != null && (profile.default_model_tier < 1 || profile.default_model_tier > tiers.length);
                      return (
                        <div className="mt-2 space-y-1" aria-label={`${profile.profile_id} model tier mapping`}>
                          <div className="text-xs font-medium text-slate-700 dark:text-slate-300">
                            {tiers.length} tiers · Default: Tier {validDefault}
                            {hasInvalidDefault ? <span className="ml-1 text-amber-600 dark:text-amber-400">· Invalid saved default: Tier {profile.default_model_tier}</span> : null}
                          </div>
                          <div className="space-y-1">
                            {tiers.slice(0, 2).map((tier, tierIndex) => {
                              const tierNumber = tierIndex + 1;
                              return (
                                <div key={`${profile.profile_id}-tier-${tierNumber}`} className="text-xs text-slate-600 dark:text-slate-300">
                                  <span className="font-medium">Tier {tierNumber}{tierNumber === validDefault ? ' default' : ''}</span>
                                  {' · '}
                                  <span className="font-mono">{tier.label || `Tier ${tierNumber}`}</span>
                                  {' · '}
                                  <span className="font-mono">{tierDisplayModel(tier.model)}</span>
                                  {' · '}
                                  <span className="font-mono">{tierDisplayEffort(tier.effort)}</span>
                                  {tierNumber === validDefault ? <span className="ml-1 inline-flex rounded bg-violet-100 dark:bg-violet-900/30 px-1 text-[10px] font-semibold text-violet-700 dark:text-violet-300">Default</span> : null}
                                </div>
                              );
                            })}
                            {tiers.length > 2 ? <div className="text-xs text-slate-500 dark:text-slate-400">+{tiers.length - 2} more</div> : null}
                          </div>
                          {tiers.length > 2 || hasInvalidDefault ? (
                            <details className="group">
                              <summary className="cursor-pointer text-xs font-medium text-slate-500 dark:text-slate-400">Show tier mapping</summary>
                              <div className="mt-1 space-y-1">
                                {tiers.map((tier, tierIndex) => {
                                  const tierNumber = tierIndex + 1;
                                  return (
                                    <div key={`${profile.profile_id}-tier-full-${tierNumber}`} className="text-xs text-slate-600 dark:text-slate-300">
                                      <span className="font-medium">Tier {tierNumber}{tierNumber === validDefault ? ' default' : ''}</span>
                                      {' · '}
                                      <span className="font-mono">{tier.label || `Tier ${tierNumber}`}</span>
                                      {' · '}
                                      <span className="font-mono">{tierDisplayModel(tier.model)}</span>
                                      {' · '}
                                      <span className="font-mono">{tierDisplayEffort(tier.effort)}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </details>
                          ) : null}
                        </div>
                      );
                    })()}
                  </td>
                  <td
                    className="px-3 py-4 text-slate-700 dark:text-slate-300"
                    data-label="Runtime"
                    headers="provider-profile-header-runtime"
                    role="cell"
                  >
                    {profile.runtime_id}
                  </td>
                  <td
                    className="px-3 py-4"
                    data-label="Provider"
                    headers="provider-profile-header-provider"
                    role="cell"
                  >
                    <div className="text-slate-700 dark:text-slate-300">{profile.provider_id}</div>
                    {profile.provider_label ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">{profile.provider_label}</div>
                    ) : null}
                    {profile.tags && profile.tags.length > 0 ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        Tags: {profile.tags.join(', ')}
                      </div>
                    ) : null}
                    {profile.priority != null ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        Priority: {profile.priority}
                      </div>
                    ) : null}
                  </td>
                  <td
                    className="px-3 py-4 text-slate-700 dark:text-slate-300"
                    data-label="Credential"
                    headers="provider-profile-header-credential"
                    role="cell"
                  >
                    <div>{profile.credential_source}</div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      {profile.runtime_materialization_mode}
                    </div>
                    {profile.volume_ref ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        OAuth volume: {profile.volume_ref}
                      </div>
                    ) : null}
                    {profile.volume_mount_path ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        Mount: {profile.volume_mount_path}
                      </div>
                    ) : null}
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      Concurrency: {profile.max_parallel_runs}
                    </div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      Cooldown: {profile.cooldown_after_429_seconds}s
                    </div>
                  </td>
                  <td
                    className="px-3 py-4 text-xs text-slate-600 dark:text-slate-400"
                    data-label="Secret refs"
                    headers="provider-profile-header-secret-refs"
                    role="cell"
                  >
                    <div className="font-medium text-slate-700 dark:text-slate-300">
                      Role-aware SecretRefs
                    </div>
                    {Object.entries(profile.secret_refs ?? {}).length === 0 ? (
                      <div>No secret refs</div>
                    ) : (
                      <dl className="space-y-1">
                        {Object.entries(profile.secret_refs ?? {}).map(([role, ref]) => (
                          <div key={role}>
                            <dt className="font-mono font-semibold">{role}</dt>
                            <dd className="font-mono">{ref}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </td>
                  <td
                    className="px-3 py-4"
                    data-label="Status"
                    headers="provider-profile-header-status"
                    role="cell"
                  >
                    <div className="provider-profile-status flex flex-col gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
                            profile.enabled
                              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                              : 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
                          }`}
                        >
                          {profile.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        {profile.readiness ? (
                          <span
                            className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${readinessClass(profile.readiness.status)}`}
                          >
                            Readiness: {readinessLabel(profile.readiness.status)}
                          </span>
                        ) : null}
                      </div>
                      {authModel.kind === 'claude_credentials' && authModel.statusLabel ? (
                        <div className="text-xs font-medium text-slate-600 dark:text-slate-400">
                          {authModel.statusLabel}
                        </div>
                      ) : null}
                      {authModel.kind === 'opencode_credentials' && authModel.statusLabel ? (
                        <div className="text-xs font-medium text-slate-600 dark:text-slate-400">
                          {authModel.statusLabel}
                        </div>
                      ) : null}
                      {oauthSession ? (
                        <div className="text-xs font-medium text-slate-600 dark:text-slate-400">
                          OAuth: {oauthStatusLabel(oauthSession.status)}
                        </div>
                      ) : null}
                      {oauthSession?.failureReason ? (
                        <div className="text-xs text-rose-600 dark:text-rose-400">
                          {oauthSession.failureReason}
                        </div>
                      ) : null}
                      {hasStatusDetails ? (
                        <details className="provider-profile-status-details">
                          <summary className="cursor-pointer text-xs font-medium text-slate-500 dark:text-slate-400">
                            Diagnostics
                          </summary>
                          <div className="mt-2 space-y-1">
                            {activationLabel ? (
                              <div className="text-xs font-medium text-slate-600 dark:text-slate-400">
                                {activationLabel}
                              </div>
                            ) : null}
                            {profile.disabled_reason ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Reason: {formatStatusLabel(profile.disabled_reason, profile.disabled_reason)}
                              </div>
                            ) : null}
                            {profile.last_validated_at ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Last validated: {profile.last_validated_at}
                              </div>
                            ) : null}
                            {profile.readiness ? (
                              <div className="space-y-1">
                                <div className="text-xs text-slate-500 dark:text-slate-400">
                                  {profile.readiness.summary}
                                </div>
                                {visibleReadinessChecks(profile.readiness).map((check) => (
                                  <div
                                    key={check.id}
                                    className={
                                      check.status === 'error'
                                        ? 'text-xs text-rose-600 dark:text-rose-400'
                                        : 'text-xs text-amber-700 dark:text-amber-300'
                                    }
                                  >
                                    {check.label}: {redactClaudeSecretText(check.message)}
                                  </div>
                                ))}
                              </div>
                            ) : null}
                            {authModel.kind === 'claude_credentials' && authModel.readiness?.connected !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Claude connection: {authModel.readiness.connected ? 'Connected' : 'Not connected'}
                              </div>
                            ) : null}
                            {authModel.kind === 'claude_credentials' && authModel.readiness?.lastValidatedAt ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Last validated: {authModel.readiness.lastValidatedAt}
                              </div>
                            ) : null}
                            {authModel.kind === 'claude_credentials' && authModel.readiness?.backingSecretExists !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Backing secret: {authModel.readiness.backingSecretExists ? 'Present' : 'Missing'}
                              </div>
                            ) : null}
                            {authModel.kind === 'claude_credentials' && authModel.readiness?.launchReady !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Launch readiness: {authModel.readiness.launchReady ? 'Ready' : 'Not ready'}
                              </div>
                            ) : null}
                            {authModel.kind === 'claude_credentials' && authModel.readiness?.failureReason ? (
                              <div className="text-xs text-rose-600 dark:text-rose-400">
                                Failure: {redactClaudeSecretText(authModel.readiness.failureReason)}
                              </div>
                            ) : null}
                            {authModel.kind === 'opencode_credentials' && authModel.readiness?.connected !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                OpenCode connection: {authModel.readiness.connected ? 'Connected' : 'Not connected'}
                              </div>
                            ) : null}
                            {authModel.kind === 'opencode_credentials' && authModel.readiness?.lastValidatedAt ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Last validated: {authModel.readiness.lastValidatedAt}
                              </div>
                            ) : null}
                            {authModel.kind === 'opencode_credentials' && authModel.readiness?.backingSecretExists !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Backing secret: {authModel.readiness.backingSecretExists ? 'Present' : 'Missing'}
                              </div>
                            ) : null}
                            {authModel.kind === 'opencode_credentials' && authModel.readiness?.launchReady !== undefined ? (
                              <div className="text-xs text-slate-500 dark:text-slate-400">
                                Launch readiness: {authModel.readiness.launchReady ? 'Ready' : 'Not ready'}
                              </div>
                            ) : null}
                            {authModel.kind === 'opencode_credentials' && authModel.readiness?.failureReason ? (
                              <div className="text-xs text-rose-600 dark:text-rose-400">
                                Failure: {redactClaudeSecretText(authModel.readiness.failureReason)}
                              </div>
                            ) : null}
                          </div>
                        </details>
                      ) : null}
                    </div>
                  </td>
                  <td
                    className="px-3 py-4"
                    data-label="Actions"
                    headers="provider-profile-header-actions"
                    role="cell"
                  >
                    <div className="flex flex-wrap gap-2">
                      {canWriteProviderProfiles && canStartOAuth ? (
                        <button
                          type="button"
                          className="rounded-full border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition hover:border-emerald-500 dark:hover:border-emerald-500"
                          onClick={() => startOAuthMutation.mutate(profile)}
                          disabled={startOAuthMutation.isPending}
                          aria-label={`OAuth ${profile.profile_id}`}
                        >
                          OAuth
                        </button>
                      ) : null}
                      {canWriteProviderProfiles && authModel.kind === 'claude_credentials'
                        ? authModel.actions.map((action) => (
                            <button
                              key={action.id}
                              type="button"
                              className="rounded-full border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition hover:border-emerald-500 dark:hover:border-emerald-500"
                              onClick={() => {
                                if (action.id === 'connect_oauth') {
                                  startOAuthMutation.mutate(profile);
                                  return;
                                }
                                if (action.id === 'use_api_key') {
                                  openClaudeEnrollment(profile);
                                  return;
                                }
                                if (action.id === 'validate_oauth' || action.id === 'disconnect_oauth') {
                                  claudeOAuthLifecycleMutation.mutate({
                                    profileId: profile.profile_id,
                                    actionId: action.id,
                                  });
                                }
                              }}
                              disabled={claudeOAuthLifecycleMutation.isPending}
                              aria-label={`${action.label} ${profile.profile_id}`}
                            >
                              {action.label}
                            </button>
                          ))
                        : null}
                      {canWriteProviderProfiles && authModel.kind === 'opencode_credentials'
                        ? authModel.actions.map((action) => (
                            <button
                              key={action.id}
                              type="button"
                              className="rounded-full border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition hover:border-emerald-500 dark:hover:border-emerald-500"
                              onClick={() => {
                                if (action.id === 'use_api_key') {
                                  openOpencodeEnrollment(profile);
                                  return;
                                }
                              }}
                              disabled={opencodeEnrollmentMutation.isPending}
                              aria-label={`${action.label} ${profile.profile_id}`}
                            >
                              {action.label}
                            </button>
                          ))
                        : null}
                      {canWriteProviderProfiles && canUseGenericApiKey ? (
                        <button
                          type="button"
                          className="rounded-full border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition hover:border-emerald-500 dark:hover:border-emerald-500"
                          onClick={() => openOpencodeEnrollment(profile)}
                          disabled={opencodeEnrollmentMutation.isPending}
                          aria-label={`Use ${apiKeyEnrollmentCopy(profile).credentialLabel} ${profile.profile_id}`}
                        >
                          Use {apiKeyEnrollmentCopy(profile).credentialLabel}
                        </button>
                      ) : null}
                      {canWriteProviderProfiles && oauthSession && isActiveOAuthStatus(oauthSession.status) ? (
                        <button
                          type="button"
                          className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                          onClick={() =>
                            cancelOAuthMutation.mutate({
                              profileId: profile.profile_id,
                              sessionId: oauthSession.sessionId,
                            })
                          }
                          disabled={cancelOAuthMutation.isPending}
                          aria-label={`Cancel OAuth ${profile.profile_id}`}
                        >
                          Cancel OAuth
                        </button>
                      ) : null}
                      {canWriteProviderProfiles && oauthSession && canFinalizeOAuthStatus(oauthSession.status) ? (
                        <button
                          type="button"
                          className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                          onClick={() =>
                            finalizeOAuthMutation.mutate({
                              profileId: profile.profile_id,
                              sessionId: oauthSession.sessionId,
                            })
                          }
                          disabled={finalizeOAuthMutation.isPending}
                          aria-label={`Finalize ${profile.profile_id}`}
                        >
                          Finalize
                        </button>
                      ) : null}
                      {canWriteProviderProfiles && oauthSession && canRetryOAuthStatus(oauthSession.status) ? (
                        <button
                          type="button"
                          className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                          onClick={() =>
                            retryOAuthMutation.mutate({
                              profileId: profile.profile_id,
                              sessionId: oauthSession.sessionId,
                            })
                          }
                          disabled={retryOAuthMutation.isPending}
                          aria-label={`Retry ${profile.profile_id}`}
                        >
                          Retry
                        </button>
                      ) : null}
                      {canWriteProviderProfiles ? (
                        <>
                          <button
                            type="button"
                            className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                            onClick={() => {
                              beginEditingProfile(profile);
                            }}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                            onClick={() => handleEditTiers(profile)}
                            aria-label={`Edit tiers ${profile.profile_id}`}
                          >
                            Edit tiers
                          </button>
                          <button
                            type="button"
                            className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                            onClick={() =>
                              toggleMutation.mutate({
                                profileId: profile.profile_id,
                                enabled: !profile.enabled,
                              })
                            }
                            disabled={!profile.enabled && !enableAllowed}
                            title={
                              !profile.enabled && !enableAllowed
                                ? 'Complete credential setup before enabling this profile.'
                                : undefined
                            }
                          >
                            {profile.enabled ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            type="button"
                            className="queue-action queue-action-danger px-3 py-1.5 text-xs font-medium transition"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete provider profile "${profile.profile_id}"?`,
                                )
                              ) {
                                deleteMutation.mutate(profile.profile_id);
                              }
                            }}
                          >
                            Delete
                          </button>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {tmateOAuthSession ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setTmateOAuthSession(null);
            }
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="tmate-oauth-session-title"
            className="w-full max-w-xl rounded-lg border border-slate-200 bg-white p-5 shadow-2xl outline-none dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h4
                  id="tmate-oauth-session-title"
                  className="text-base font-semibold text-slate-900 dark:text-white"
                >
                  Tmate OAuth session
                </h4>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Complete provider login in the Tmate terminal, then finalize this OAuth session.
                </p>
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-400 dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-500"
                onClick={() => setTmateOAuthSession(null)}
              >
                Close
              </button>
            </div>
            <dl className="mt-4 space-y-3 text-sm">
              <div>
                <dt className="font-medium text-slate-700 dark:text-slate-300">Session</dt>
                <dd className="mt-1 font-mono text-xs text-slate-600 dark:text-slate-400">
                  {tmateOAuthSession.sessionId}
                </dd>
              </div>
              {tmateOAuthSession.terminalSessionId ? (
                <div>
                  <dt className="font-medium text-slate-700 dark:text-slate-300">Web terminal</dt>
                  <dd className="mt-1 break-all font-mono text-xs text-slate-600 dark:text-slate-400">
                    {tmateOAuthSession.terminalSessionId}
                  </dd>
                </div>
              ) : null}
              {tmateOAuthSession.terminalBridgeId ? (
                <div>
                  <dt className="font-medium text-slate-700 dark:text-slate-300">SSH terminal</dt>
                  <dd className="mt-1 break-all font-mono text-xs text-slate-600 dark:text-slate-400">
                    {tmateOAuthSession.terminalBridgeId}
                  </dd>
                </div>
              ) : null}
            </dl>
            <div className="mt-5 flex flex-wrap gap-2">
              {tmateOAuthSession.terminalSessionId ? (
                <a
                  className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
                  href={tmateOAuthSession.terminalSessionId}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open Tmate
                </a>
              ) : null}
              {canFinalizeOAuthStatus(tmateOAuthSession.status) ? (
                <button
                  type="button"
                  className="inline-flex items-center justify-center rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-400 dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-500"
                  onClick={() => {
                    finalizeOAuthMutation.mutate({
                      profileId: tmateOAuthSession.profileId,
                      sessionId: tmateOAuthSession.sessionId,
                    });
                  }}
                  disabled={finalizeOAuthMutation.isPending}
                >
                  Finalize
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {claudeEnrollment ? (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-slate-950/40"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeClaudeEnrollment();
            }
          }}
        >
          <div
            ref={claudeEnrollmentDrawerRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="claude-enrollment-title"
            tabIndex={-1}
            className="h-full w-full max-w-2xl overflow-y-auto border-l border-emerald-200 dark:border-emerald-900/60 bg-white dark:bg-slate-900 p-5 shadow-2xl outline-none"
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <h4
                id="claude-enrollment-title"
                className="text-base font-semibold text-slate-900 dark:text-white"
              >
                Anthropic API key enrollment for {claudeEnrollment.profile.profile_id}
              </h4>
              <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
                Use an Anthropic API key for Claude Code launches. Paste the key here, then validate and save it as a managed provider credential.
              </p>
            </div>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-lg border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500"
              onClick={closeClaudeEnrollment}
            >
              Cancel API key enrollment
            </button>
          </div>

          <div className="mt-4 flex flex-wrap gap-2" aria-label="Claude enrollment lifecycle states">
            {CLAUDE_ENROLLMENT_STEPS.map((step) => (
              <span
                key={step}
                className={`rounded-full px-2.5 py-1 font-mono text-xs ${
                  step === claudeEnrollment.step
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
                    : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                }`}
              >
                {formatStatusLabel(step)}
              </span>
            ))}
          </div>

          {claudeEnrollment.step === 'not_connected' ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-4 text-sm text-slate-700 dark:text-slate-300">
                Continue when you are ready to paste the Anthropic API key. This path stores the key in Managed Secrets and does not create an OAuth terminal session.
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                onClick={continueClaudeEnrollment}
              >
                Continue to API key paste
              </button>
            </div>
          ) : null}

          {claudeEnrollment.step === 'awaiting_token_paste' ? (
            <div className="mt-5 space-y-4">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Anthropic API key</span>
                <input
                  type="password"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={claudeEnrollment.token}
                  onChange={(event) => updateClaudeEnrollmentToken(event.target.value)}
                  autoComplete="off"
                />
              </label>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                onClick={() => void submitClaudeEnrollment()}
              >
                Validate and save Anthropic API key
              </button>
            </div>
          ) : null}

          {['validating_token', 'saving_secret', 'updating_profile'].includes(claudeEnrollment.step) ? (
            <div className="mt-5 rounded-xl border border-sky-200 dark:border-sky-900/60 bg-sky-50 dark:bg-sky-950/30 p-4 text-sm font-medium text-sky-800 dark:text-sky-300">
              Processing Anthropic API key enrollment: {formatStatusLabel(claudeEnrollment.step)}
            </div>
          ) : null}

          {claudeEnrollment.step === 'ready' ? (
            <div className="mt-5 rounded-xl border border-emerald-200 dark:border-emerald-900/60 bg-emerald-50 dark:bg-emerald-950/30 p-4 text-sm font-medium text-emerald-800 dark:text-emerald-300">
              {formatStatusLabel(claudeEnrollment.statusLabel ?? 'Anthropic API key ready')}
            </div>
          ) : null}

          {claudeEnrollment.step === 'failed' ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-xl border border-rose-200 dark:border-rose-900/60 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm font-medium text-rose-700 dark:text-rose-300">
                {claudeEnrollment.failureReason ?? 'Anthropic API key validation failed.'}
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500"
                onClick={continueClaudeEnrollment}
              >
                Return to API key paste
              </button>
            </div>
          ) : null}
          </div>
        </div>
      ) : null}

      {opencodeEnrollment ? (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-slate-950/40"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeOpencodeEnrollment();
            }
          }}
        >
          <div
            ref={opencodeEnrollmentDrawerRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="opencode-enrollment-title"
            tabIndex={-1}
            className="h-full w-full max-w-2xl overflow-y-auto border-l border-emerald-200 dark:border-emerald-900/60 bg-white dark:bg-slate-900 p-5 shadow-2xl outline-none"
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <h4
                id="opencode-enrollment-title"
                className="text-base font-semibold text-slate-900 dark:text-white"
              >
                {apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel} enrollment for {opencodeEnrollment.profile.profile_id}
              </h4>
              <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
                {apiKeyEnrollmentCopy(opencodeEnrollment.profile).description}
              </p>
            </div>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-lg border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500"
              onClick={closeOpencodeEnrollment}
            >
              Cancel API key enrollment
            </button>
          </div>

          <div className="mt-4 flex flex-wrap gap-2" aria-label={`${apiKeyEnrollmentCopy(opencodeEnrollment.profile).providerName} enrollment lifecycle states`}>
            {CLAUDE_ENROLLMENT_STEPS.map((step) => (
              <span
                key={step}
                className={`rounded-full px-2.5 py-1 font-mono text-xs ${
                  step === opencodeEnrollment.step
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
                    : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                }`}
              >
                {formatStatusLabel(step)}
              </span>
            ))}
          </div>

          {opencodeEnrollment.step === 'not_connected' ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-xl border border-slate-200 dark:border-slate-800 p-4 text-sm text-slate-700 dark:text-slate-300">
                Continue when you are ready to paste the {apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel}. This path stores the key in Managed Secrets and does not create an OAuth terminal session.
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                onClick={continueOpencodeEnrollment}
              >
                Continue to API key paste
              </button>
            </div>
          ) : null}

          {opencodeEnrollment.step === 'awaiting_token_paste' ? (
            <div className="mt-5 space-y-4">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>{apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel}</span>
                <input
                  type="password"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={opencodeEnrollment.token}
                  onChange={(event) => updateOpencodeEnrollmentToken(event.target.value)}
                  autoComplete="off"
                />
              </label>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                onClick={() => void submitOpencodeEnrollment()}
              >
                Validate and save {apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel}
              </button>
            </div>
          ) : null}

          {['validating_token', 'saving_secret', 'updating_profile'].includes(opencodeEnrollment.step) ? (
            <div className="mt-5 rounded-xl border border-sky-200 dark:border-sky-900/60 bg-sky-50 dark:bg-sky-950/30 p-4 text-sm font-medium text-sky-800 dark:text-sky-300">
              Processing {apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel} enrollment: {formatStatusLabel(opencodeEnrollment.step)}
            </div>
          ) : null}

          {opencodeEnrollment.step === 'ready' ? (
            <div className="mt-5 rounded-xl border border-emerald-200 dark:border-emerald-900/60 bg-emerald-50 dark:bg-emerald-950/30 p-4 text-sm font-medium text-emerald-800 dark:text-emerald-300">
              {formatStatusLabel(opencodeEnrollment.statusLabel ?? apiKeyEnrollmentCopy(opencodeEnrollment.profile).readyLabel)}
            </div>
          ) : null}

          {opencodeEnrollment.step === 'failed' ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-xl border border-rose-200 dark:border-rose-900/60 bg-rose-50 dark:bg-rose-950/30 p-4 text-sm font-medium text-rose-700 dark:text-rose-300">
                {opencodeEnrollment.failureReason ?? `${apiKeyEnrollmentCopy(opencodeEnrollment.profile).credentialLabel} validation failed.`}
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500"
                onClick={continueOpencodeEnrollment}
              >
                Return to API key paste
              </button>
            </div>
          ) : null}
          </div>
        </div>
      ) : null}

      {/* ── Form Section ── */}
      {canWriteProviderProfiles ? (
      <div className="mt-8 border-t border-slate-200 dark:border-slate-700 pt-8">
        <div className="flex flex-col gap-1 mb-6 md:flex-row md:items-end md:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
              {isEditing ? `Edit Profile: ${editingProfileId}` : 'Create Profile'}
            </h3>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Fields marked <span className="text-amber-600 dark:text-amber-400 font-semibold">*</span> are
              required. Others have sensible defaults and can usually be left as-is.
            </p>
          </div>
        </div>

        <form
          className="space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate(form);
          }}
        >
          {/* ── 1. Identity (Profile ID, Runtime, Provider, Account label) ── */}
          <fieldset className="rounded-2xl border border-amber-200/60 dark:border-amber-800/40 bg-amber-50/30 dark:bg-amber-900/10 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-amber-700 dark:text-amber-400">
              Identity <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; required</span>
            </legend>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Profile ID <span className="text-amber-600 dark:text-amber-400">*</span></span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.profileId}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, profileId: event.target.value }))
                  }
                  disabled={isEditing}
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Runtime ID <span className="text-amber-600 dark:text-amber-400">*</span></span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.runtimeId}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, runtimeId: event.target.value }))
                  }
                  placeholder="codex_cli"
                  disabled={isEditing}
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Provider ID <span className="text-amber-600 dark:text-amber-400">*</span></span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.providerId}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, providerId: event.target.value }))
                  }
                  placeholder="openai"
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Account label</span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.accountLabel}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, accountLabel: event.target.value }))
                  }
                  placeholder="Team account"
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">Optional friendly account identity — auto-populated from OAuth identity when available</p>
              </label>
            </div>
          </fieldset>

          <fieldset className="rounded-2xl border border-emerald-200/70 dark:border-emerald-900/60 bg-emerald-50/30 dark:bg-emerald-950/20 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-emerald-800 dark:text-emerald-300">
              Authentication and readiness
            </legend>
            {creationCapabilities?.authentication_methods.length ? (
              <div className="grid gap-3 sm:grid-cols-3">
                {creationCapabilities.authentication_methods.map((method) => (
                  <label
                    key={method.id}
                    className="flex items-center gap-3 rounded-xl border border-emerald-200 dark:border-emerald-900 bg-white dark:bg-slate-900 px-4 py-3 text-sm font-medium text-slate-800 dark:text-slate-200"
                  >
                    <input
                      type="radio"
                      name="provider-profile-authentication-method"
                      value={method.id}
                      checked={form.authenticationMethod === method.id}
                      disabled={isEditing}
                      onChange={() => {
                        setForm((current) => ({
                          ...current,
                          authenticationMethod: asAuthenticationMethod(method.id),
                        }));
                        setShowImportedVolume(false);
                        setImportedVolumeValidated(false);
                      }}
                    />
                    {method.label}
                  </label>
                ))}
              </div>
            ) : isEditing ? (
              <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                Unknown existing method: {form.credentialSource} → {form.runtimeMaterializationMode}. The stored contract is preserved for inspection.
              </div>
            ) : (
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Choose a runtime and provider to load supported authentication methods.
              </p>
            )}
            {hasUnknownExistingAuthenticationMethod && creationCapabilities?.authentication_methods.length ? (
              <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                Unknown existing method: {form.credentialSource} → {form.runtimeMaterializationMode}. The stored contract remains inspectable and is not replaced by a supported method automatically.
              </div>
            ) : null}
            {creationCapabilitiesError ? (
              <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                {creationCapabilitiesError}
              </div>
            ) : null}
            {!isEditing ? (
              <div
                className={`rounded-xl border p-3 text-sm ${
                  creationPresetError || creationPreset?.supported === false
                    ? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300'
                    : 'border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-300'
                }`}
                aria-live="polite"
              >
                {creationPresetLoading
                  ? 'Loading backend creation recommendations…'
                  : creationPresetError
                    ? creationPresetError
                    : creationPreset
                      ? creationPreset.supported
                        ? `Backend preset ${creationPreset.version} loaded. Untouched advanced values will be normalized by the server.`
                        : creationPreset.diagnostics[0]?.message ??
                          'This combination does not have a safe standard creation preset.'
                      : 'Choose an authentication method to load backend creation recommendations.'}
              </div>
            ) : null}
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-3 text-sm text-slate-700 dark:text-slate-300">
                Connection: {currentConnectionLabel}
              </div>
              <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-3 text-sm text-slate-700 dark:text-slate-300">
                Launch readiness: {currentLaunchReadinessLabel}
              </div>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              OAuth and API-key setup use dedicated backend flows. No credential plaintext, volume name, mount path, or SecretRef JSON is collected here.
            </p>
          </fieldset>


          {/* ── Model & effort tiers ── */}
          <fieldset ref={tierSectionRef as unknown as React.RefObject<HTMLFieldSetElement>} className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4" aria-labelledby="tier-section-title">
            <legend id="tier-section-title" className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Model &amp; effort tiers</legend>
            <p className="text-sm text-slate-600 dark:text-slate-400">Map workflow tier requests to a model and effort for this profile. Future launches use the saved policy. Historical runs keep their record.</p>
            {canWriteProviderProfiles ? (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-300">{tierDrafts.length} tiers{defaultTierClientId ? ` · Default: Tier ${tierDrafts.findIndex((t) => t.clientId === defaultTierClientId) + 1}` : ''}</span>
                <button type="button" className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900" onClick={handleAddTier}>Add tier</button>
                {invalidSavedDefaultIndex !== null ? <span className="rounded bg-amber-100 dark:bg-amber-900/30 px-2 py-1 text-xs font-semibold text-amber-700 dark:text-amber-300">Invalid saved default: Tier {invalidSavedDefaultIndex}</span> : null}
              </div>
            ) : (
              <div className="text-sm font-medium text-slate-700 dark:text-slate-300">{tierDrafts.length} tiers{defaultTierClientId ? ` · Default: Tier ${tierDrafts.findIndex((t) => t.clientId === defaultTierClientId) + 1}` : ''}</div>
            )}
            {isTierRepair ? (
              <div className="rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 text-sm text-amber-800 dark:text-amber-300">
                <p>This profile has no model tiers and cannot be saved in this state.</p>
                {canWriteProviderProfiles ? <button type="button" className="mt-3 inline-flex items-center justify-center rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white" onClick={handleCreateRepairTier}>Create runtime-default Tier 1</button> : <p className="mt-2 text-xs">Read-only: complete policy not available.</p>}
              </div>
            ) : null}
            {tierUndo ? (
              <div className="flex items-center gap-3 rounded-xl border border-sky-200 dark:border-sky-900 bg-sky-50 dark:bg-sky-950/30 p-3 text-sm text-sky-800 dark:text-sky-300">
                <span>Tier removed. </span>
                <button type="button" className="underline" onClick={handleUndoLastRemove}>Undo</button>
              </div>
            ) : null}
            {tierCapabilitiesLoading ? (
              <p className="text-xs text-slate-500 dark:text-slate-400">Loading model choices from backend capabilities…</p>
            ) : null}
            {tierCapabilitiesError ? (
              <div className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                {tierCapabilitiesError}
              </div>
            ) : null}
            {tierCapabilities?.evidence?.stale ? (
              <div className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-xs text-amber-800 dark:text-amber-300">
                Model discovery is refreshing. Cached choices remain available; the runtime verifies your selected model before execution.
              </div>
            ) : null}
            {tierCapabilities?.diagnostics?.length ? (
              <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-300">
                {tierCapabilities.diagnostics.map((d, idx) => (
                  <li key={`${d.code}-${idx}`}>{d.message}</li>
                ))}
              </ul>
            ) : null}
            <div className="sr-only" aria-live="polite" ref={tierLiveRef}>{tierLiveMessage}</div>
            <ol className="space-y-4" aria-label="Model and effort tiers">
              {tierDrafts.map((tier, index) => {
                const tierNumber = index + 1;
                const isDefault = tier.clientId === defaultTierClientId;
                const isOnlyTier = tierDrafts.length === 1;
                return (
                  <li key={tier.clientId} data-tier-client-id={tier.clientId} className={`rounded-2xl border p-4 shadow-sm ${isDefault ? 'border-violet-300 dark:border-violet-700 bg-violet-50/40 dark:bg-violet-950/20' : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800'}`}>
                    <fieldset className="space-y-3">
                      <legend className="flex w-full items-center justify-between">
                        <span className="text-sm font-semibold text-slate-900 dark:text-white">Tier {tierNumber}{tier.label ? ` · ${tier.label}` : ''}{isDefault ? <span className="ml-2 inline-flex rounded bg-violet-100 dark:bg-violet-900/30 px-2 py-0.5 text-xs font-semibold text-violet-700 dark:text-violet-300">Default</span> : null}</span>
                        {canWriteProviderProfiles ? (
                          <span className="flex items-center gap-2">
                            <button type="button" className="text-xs font-medium text-slate-600 dark:text-slate-400 hover:underline" onClick={() => handleDuplicateTier(tier)} aria-label={`Duplicate Tier ${tierNumber} as new last tier`}>Duplicate as new last tier</button>
                            <button type="button" className={`text-xs font-medium ${isOnlyTier ? 'text-slate-400 cursor-not-allowed' : 'text-rose-600 dark:text-rose-400 hover:underline'}`} disabled={isOnlyTier} onClick={() => requestRemoveTier(index)} aria-label={`Remove Tier ${tierNumber}`}>Remove tier</button>
                          </span>
                        ) : null}
                      </legend>
                      {canWriteProviderProfiles ? (
                        <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                          <input type="radio" name="default-tier-group" value={tier.clientId} checked={isDefault} onChange={() => setDefaultTierClientId(tier.clientId)} aria-label={isDefault ? 'Default tier' : `Use Tier ${tierNumber} as default`} />
                          {isDefault ? 'Default tier' : `Use Tier ${tierNumber} as default`}
                        </label>
                      ) : (
                        <div className="text-sm font-medium text-slate-700 dark:text-slate-300">{isDefault ? 'Default tier' : `Tier ${tierNumber}`}</div>
                      )}
                      {canWriteProviderProfiles ? (
                        <>
                          <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                            <span>Label</span>
                            <input className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={tier.label} onChange={(e) => handleTierLabelChange(tier.clientId, e.target.value)} placeholder="Optional tier label" aria-label={`Tier ${tierNumber} label`} />
                          </label>
                          <div className="grid gap-4 md:grid-cols-2">
                            <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                              <span>Model</span>
                              {(() => {
                                const catalogOptions = tierCapabilities?.model?.options ?? [];
                                const allowCustomModel = tierCapabilities?.model?.allow_custom === true;
                                const knownValues = new Set([
                                  '__runtime_default__',
                                  ...catalogOptions.map((o) => o.value),
                                  ...(tierCapabilities === null ? ['gpt-5.5', 'gpt-4o'] : []),
                                ]);
                                const currentValue = tier.model ?? '__runtime_default__';
                                const isCustomValue = tier.model != null && tier.model !== '' && !knownValues.has(tier.model);
                                const isCustomDraft = customModelEntryTiers.has(tier.clientId);
                                // Catalog freshness controls observed options. The backend's
                                // explicit allow_custom policy independently authorizes manual
                                // entry, which the write/launch boundaries still validate.
                                const selectValue = isCustomValue || isCustomDraft ? '__custom__' : currentValue;
                                const showCustomInput = allowCustomModel && (isCustomValue || isCustomDraft);
                                return (
                                  <>
                                    <select className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={selectValue} onChange={(e) => {
                                      const next = e.target.value;
                                      if (next === '__custom__') {
                                        setCustomModelEntryTiers((prev) => new Set(prev).add(tier.clientId));
                                        return;
                                      }
                                      setCustomModelEntryTiers((prev) => {
                                        if (!prev.has(tier.clientId)) return prev;
                                        const nextSet = new Set(prev);
                                        nextSet.delete(tier.clientId);
                                        return nextSet;
                                      });
                                      handleTierModelChange(tier.clientId, next === '__runtime_default__' ? null : next);
                                    }} aria-label={`Tier ${tierNumber} model`}>
                                      <option value="__runtime_default__">Runtime default{tierCapabilities?.model?.runtime_default ? ` — ${tierCapabilities.model.runtime_default}` : ''}</option>
                                      {catalogOptions.map((opt) => (
                                        <option key={opt.value} value={opt.value}>
                                          {opt.label}
                                          {opt.status === 'deprecated' ? ' (Deprecated)' : ''}
                                          {opt.recommended ? ' · Recommended' : ''}
                                        </option>
                                      ))}
                                      {tierCapabilities === null ? (
                                        <>
                                          <option value="gpt-5.5">GPT-5.5</option>
                                          <option value="gpt-4o">GPT-4o</option>
                                        </>
                                      ) : null}
                                      {tier.model && !knownValues.has(tier.model) && tier.model !== '' ? (
                                        <option value={tier.model}>{tier.model} (existing — custom or unavailable)</option>
                                      ) : null}
                                      {allowCustomModel ? (
                                        <option value="__custom__">Custom value…</option>
                                      ) : null}
                                    </select>
                                    {allowCustomModel && showCustomInput ? (
                                      <input
                                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm font-normal"
                                        value={tier.model ?? ''}
                                        onChange={(e) => {
                                          const text = e.target.value.trim();
                                          handleTierModelChange(tier.clientId, text === '' ? null : text);
                                        }}
                                        placeholder="Custom model id"
                                        aria-label={`Tier ${tierNumber} custom model`}
                                      />
                                    ) : null}
                                  </>
                                );
                              })()}
                              {tierFieldErrors[`${tier.clientId}.model`] ? <span className="text-xs text-rose-600">{tierFieldErrors[`${tier.clientId}.model`]}</span> : null}
                            </label>
                            <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                              <span>Effort level</span>
                              <select className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={tier.effort ?? '__runtime_default__'} onChange={(e) => handleTierEffortChange(tier.clientId, e.target.value === '__runtime_default__' ? null : e.target.value)} aria-label={`Tier ${tierNumber} effort`}>
                                <option value="__runtime_default__">Runtime default{tierCapabilities?.effort?.runtime_default ? ` — ${tierCapabilities.effort.runtime_default}` : ''}</option>
                                {(tierCapabilities?.effort?.options ?? []).map((opt) => (
                                  <option key={opt.value} value={opt.value}>
                                    {opt.label}
                                    {opt.status !== 'available' ? ` (${opt.status})` : ''}
                                  </option>
                                ))}
                                {tierCapabilities === null ? (
                                  <>
                                    <option value="low">low</option>
                                    <option value="medium">medium</option>
                                    <option value="high">high</option>
                                    <option value="xhigh">xhigh</option>
                                  </>
                                ) : null}
                                {tier.effort && !tierCapabilities?.effort?.options.some((o) => o.value === tier.effort) && tier.effort !== '__runtime_default__' ? (
                                  <option value={tier.effort}>{tier.effort} (existing — custom or unavailable)</option>
                                ) : null}
                              </select>
                              {tierFieldErrors[`${tier.clientId}.effort`] ? <span className="text-xs text-rose-600">{tierFieldErrors[`${tier.clientId}.effort`]}</span> : null}
                            </label>
                          </div>
                          <div className="text-xs text-slate-500 dark:text-slate-400">Resolves to {tierDisplayModel(tier.model)} · {tierDisplayEffort(tier.effort)}</div>
                          <details className="rounded-xl border border-slate-200 dark:border-slate-800 p-3">
                            <summary className="cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300">Advanced tier options</summary>
                            <div className="mt-3 space-y-3">
                              <label className="flex flex-col gap-1.5 text-sm">
                                <span>Parameters (JSON)</span>
                                <textarea rows={3} className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-xs" defaultValue={JSON.stringify(tier.parameters, null, 2)} onBlur={(e) => handleTierParametersChange(tier.clientId, e.target.value)} aria-label={`Tier ${tierNumber} parameters`} />
                                {tierFieldErrors[`${tier.clientId}.parameters`] ? <span className="text-xs text-rose-600">{tierFieldErrors[`${tier.clientId}.parameters`]}</span> : null}
                              </label>
                              <label className="flex flex-col gap-1.5 text-sm">
                                <span>Annotations (JSON)</span>
                                <textarea rows={3} className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-xs" defaultValue={JSON.stringify(tier.annotations, null, 2)} onBlur={(e) => handleTierAnnotationsChange(tier.clientId, e.target.value)} aria-label={`Tier ${tierNumber} annotations`} />
                                {tierFieldErrors[`${tier.clientId}.annotations`] ? <span className="text-xs text-rose-600">{tierFieldErrors[`${tier.clientId}.annotations`]}</span> : null}
                              </label>
                            </div>
                          </details>
                        </>
                      ) : (
                        <div className="space-y-2 text-sm text-slate-700 dark:text-slate-300">
                          <div>Label: {tier.label || `Tier ${tierNumber}`}</div>
                          <div>Model: <span className="font-mono">{tierDisplayModel(tier.model)}</span></div>
                          <div>Effort: <span className="font-mono">{tierDisplayEffort(tier.effort)}</span></div>
                          {Object.keys(tier.parameters).length > 0 ? <div>Parameters: <span className="font-mono text-xs">{JSON.stringify(tier.parameters)}</span></div> : null}
                          {Object.keys(tier.annotations).length > 0 ? <div>Annotations: <span className="font-mono text-xs">{JSON.stringify(tier.annotations)}</span></div> : null}
                        </div>
                      )}
                    </fieldset>
                  </li>
                );
              })}
            </ol>
            {!isTierRepair ? <button type="button" className="w-full rounded-xl border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300" onClick={handleAddTier}>Add tier</button> : null}
            <p className="text-xs text-slate-500 dark:text-slate-400">Future launches use the saved policy. Historical runs keep their record.</p>
            {tierRemoveDialog ? (
              <div role="dialog" aria-modal="true" aria-labelledby="tier-remove-title" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) setTierRemoveDialog(null); }}>
                <div className="w-full max-w-lg rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-2xl">
                  <h4 id="tier-remove-title" className="text-sm font-semibold text-slate-900 dark:text-white">Remove Tier {tierRemoveDialog.index + 1}?</h4>
                  {tierRemoveDialog.isMiddle ? (
                    <div className="mt-3 text-sm text-slate-600 dark:text-slate-400">
                      <p>This changes future tier-number resolution for this profile:</p>
                      <ul className="mt-2 list-disc pl-5">
                        {computeTierRenumberingImpact(tierDrafts, tierRemoveDialog.index).map((item) => (
                          <li key={`${item.from}-${item.to}`}>Tier {item.from}: {item.label} → becomes Tier {item.to}</li>
                        ))}
                      </ul>
                      <p className="mt-2 text-xs">Existing historical runs do not change.</p>
                    </div>
                  ) : null}
                  {tierRemoveDialog.isDefault ? (
                    <div className="mt-3 space-y-2">
                      <p className="text-sm text-slate-600 dark:text-slate-400">Choose a replacement default tier:</p>
                      <div className="space-y-2">
                        {tierDrafts.filter((_, i) => i !== tierRemoveDialog.index).map((t) => {
                          const originalIndex = tierDrafts.findIndex((x) => x.clientId === t.clientId);
                          return (
                            <label key={t.clientId} className="flex items-center gap-2 text-sm">
                              <input type="radio" name="replacement-default-tier" value={t.clientId} checked={tierRemoveReplacementId === t.clientId} onChange={() => setTierRemoveReplacementId(t.clientId)} />
                              Tier {originalIndex + 1}{originalIndex >= tierRemoveDialog.index ? ` → Tier ${originalIndex}` : ''}: {t.label || `Tier ${originalIndex + 1}`}
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                  <div className="mt-5 flex justify-end gap-3">
                    <button type="button" className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm" onClick={() => setTierRemoveDialog(null)}>Cancel</button>
                    <button type="button" className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={confirmRemoveTier}>Remove and renumber</button>
                  </div>
                </div>
              </div>
            ) : null}
          </fieldset>



          {/* ── 4. Capacity ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Capacity</legend>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Max parallel runs</span>
                <input
                  type="number"
                  min="1"
                  disabled={isCodexOAuthForm}
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={isCodexOAuthForm ? '1' : form.maxParallelRuns}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, maxParallelRuns: event.target.value }))
                  }
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  {isCodexOAuthForm
                    ? 'Fixed at 1 because the Codex OAuth home is an exclusive mutable identity.'
                    : `Default: ${defaultFormValues.maxParallelRuns}`}
                </p>
              </label>
              <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-4 text-sm text-slate-600 dark:text-slate-400">
                Capacity is the one Runtime Limits value most users can reason about directly. The backend creation preset owns the recommended value.
              </div>
            </div>
          </fieldset>



          {/* ── 5. Use as runtime default (readiness-gated) ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-3">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Use as runtime default</legend>
            {(() => {
              const readiness = editingProfile?.readiness ?? null;
              const launchReady = isEditing ? Boolean(readiness?.launch_ready) : Boolean(selectedAuthenticationCapability?.launch_ready_after_setup || selectedAuthenticationCapability?.fields.may_become_runtime_default?.value);
              // The readiness gate prevents assigning an unready profile as
              // default, but an already-default profile may still be demoted.
              const assignBlocked = isEditing && !launchReady && !form.isDefault;
              const demoteAllowed = isEditing && !launchReady && form.isDefault;
              return (
                <>
                  <label className={`flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm font-medium ${assignBlocked ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300' : 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-300'}`}>
                    <input
                      type="checkbox"
                      checked={form.isDefault}
                      disabled={assignBlocked}
                      aria-label="Runtime default"
                      onChange={(event) =>
                        setForm((current) => ({ ...current, isDefault: event.target.checked }))
                      }
                    />
                    Use as runtime default
                  </label>
                  {assignBlocked ? (
                    <p className="text-xs text-amber-700 dark:text-amber-300">
                      Default assignment is disabled until launch readiness succeeds. Complete credential setup and resolve readiness blockers first.
                    </p>
                  ) : demoteAllowed ? (
                    <p className="text-xs text-amber-700 dark:text-amber-300">
                      This profile is currently the runtime default but is not launch-ready. You may clear this checkbox to demote it; re-assigning default requires launch readiness.
                    </p>
                  ) : (
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      When enabled, this profile becomes the preferred launch target for its runtime after readiness succeeds.
                    </p>
                  )}
                </>
              );
            })()}
          </fieldset>


          <div className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-5 space-y-3">
            <label className="flex items-center gap-3 text-sm font-semibold text-slate-800 dark:text-slate-200">
              <input
                type="checkbox"
                checked={showAdvanced}
                aria-controls={ADVANCED_REGION_ID}
                onChange={(event) => setShowAdvanced(event.target.checked)}
              />
              Show advanced options
            </label>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Credential bindings, volumes, rate limits, routing, and launch shaping
            </p>
            {!showAdvanced ? (
              <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                {selectedAuthenticationCapability
                  ? `Using recommended ${selectedAuthenticationCapability.label} launch settings`
                  : 'Preserving the existing launch contract'}
              </p>
            ) : null}
          </div>

          {showAdvanced ? (
            <div id={ADVANCED_REGION_ID} className="space-y-6">
              <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
                <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Credential launch contract
                </legend>
                <div className="grid gap-4 md:grid-cols-2">
                  {manualCreationAllowed ? (
                    <label data-advanced-field="credential_source" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Credential source</span>
                      <select
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                        value={form.credentialSource}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            credentialSource: event.target.value,
                          }))
                        }
                      >
                        <option value="">Choose source…</option>
                        <option value="secret_ref">secret_ref</option>
                        <option value="oauth_volume">oauth_volume</option>
                        <option value="none">none</option>
                      </select>
                    </label>
                  ) : (
                    <div data-advanced-field="credential_source" tabIndex={-1} className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-4 text-sm">
                    <div className="font-semibold text-slate-800 dark:text-slate-200">
                      Credential source: {String(selectedCredentialSource)}
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      Source: {selectedAuthenticationCapability?.fields.credential_source?.source ?? 'existing_profile'}
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      Locked: {selectedAuthenticationCapability?.fields.credential_source?.lock_reason ?? 'Unknown existing value is preserved.'}
                    </div>
                  </div>
                  )}
                  {manualCreationAllowed ? (
                    <label data-advanced-field="runtime_materialization_mode" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Materialization mode</span>
                      <select
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                        value={form.runtimeMaterializationMode}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            runtimeMaterializationMode: event.target.value,
                          }))
                        }
                      >
                        <option value="">Choose materialization…</option>
                        <option value="api_key_env">api_key_env</option>
                        <option value="env_bundle">env_bundle</option>
                        <option value="config_bundle">config_bundle</option>
                        <option value="composite">composite</option>
                        <option value="oauth_home">oauth_home</option>
                      </select>
                    </label>
                  ) : (
                    <div data-advanced-field="runtime_materialization_mode" tabIndex={-1} className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 p-4 text-sm">
                    <div className="font-semibold text-slate-800 dark:text-slate-200">
                      Materialization mode: {String(selectedMaterializationMode)}
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      Materialization source: {selectedAuthenticationCapability?.fields.runtime_materialization_mode?.source ?? 'existing_profile'}
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      Locked: {selectedAuthenticationCapability?.fields.runtime_materialization_mode?.lock_reason ?? 'Unknown existing value is preserved.'}
                    </div>
                  </div>
                  )}
                </div>
              </fieldset>

              <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
                <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Role-aware SecretRef bindings
                </legend>
                <div data-advanced-field="secret_refs" className="space-y-4">
                {(selectedAuthenticationCapability?.secret_roles ?? []).map((role) => {
                  const controlId = `provider-profile-secret-role-${role.role}`;
                  return (
                  <div key={role.role} className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <label htmlFor={controlId}>{role.label} ({role.required ? 'required' : 'optional'})</label>
                    <select
                      id={controlId}
                      className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                      value={formSecretRefs[role.role] ?? ''}
                      onChange={(event) => updateSecretRoleBinding(role.role, event.target.value)}
                    >
                      <option value="">Not selected</option>
                      {formSecretRefs[role.role] && !secretSlugs.some((slug) => `db://${slug}` === formSecretRefs[role.role]) ? (
                        <option value={formSecretRefs[role.role]}>{formSecretRefs[role.role]} (existing)</option>
                      ) : null}
                      {secretSlugs.map((slug) => (
                        <option key={slug} value={`db://${slug}`}>db://{slug}</option>
                      ))}
                    </select>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Compatible references: {role.compatible_schemes.map((scheme) => `${scheme}://`).join(', ')}
                    </p>
                    {role.required && !formSecretRefs[role.role] ? (
                      <p className="text-xs text-rose-600 dark:text-rose-400">Required role is missing; this profile remains disabled and blocked.</p>
                    ) : (
                      <p className="text-xs text-emerald-700 dark:text-emerald-400">Selected reference is ready for backend validation.</p>
                    )}
                  </div>
                  );
                })}
                {unknownSecretRoleBindings.map(([role, ref]) => (
                  <label key={role} className="flex flex-col gap-1.5 text-sm font-medium text-amber-800 dark:text-amber-300">
                    <span>Unknown existing role: {role}</span>
                    <input
                      readOnly
                      className="w-full rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 font-mono text-sm"
                      value={ref}
                    />
                    <p className="text-xs">Preserved for inspection and round-trip; the current preset does not replace it.</p>
                  </label>
                ))}
                {(selectedAuthenticationCapability?.secret_roles.length ?? 0) === 0 && unknownSecretRoleBindings.length === 0 ? (
                  <p className="text-sm text-slate-500 dark:text-slate-400">This authentication method declares no SecretRef roles.</p>
                ) : null}
                </div>
              </fieldset>

              <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
                <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Credential volume metadata
                </legend>
                <dl data-advanced-field="volume_ref" tabIndex={-1} className="grid gap-3 text-sm md:grid-cols-2">
                  <div className="rounded-xl bg-slate-50 dark:bg-slate-900 p-3">
                    <dt className="font-medium text-slate-700 dark:text-slate-300">Volume reference</dt>
                    <dd className="font-mono text-slate-600 dark:text-slate-400">{volumeReferenceLabel}</dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 dark:bg-slate-900 p-3">
                    <dt data-advanced-field="volume_mount_path" tabIndex={-1} className="font-medium text-slate-700 dark:text-slate-300">Mount path</dt>
                    <dd className="font-mono text-slate-600 dark:text-slate-400">{volumeMountPathLabel}</dd>
                  </div>
                </dl>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Source: {volumeMetadataSource} · Locked: {selectedAuthenticationCapability?.imported_volume.lock_reason ?? 'The active runtime strategy owns volume metadata.'}
                </p>
                {selectedAuthenticationCapability?.imported_volume.supported ? (
                  <div className="space-y-3">
                    <button
                      type="button"
                      className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300"
                      onClick={() => {
                        setShowImportedVolume((current) => !current);
                        setImportedVolumeValidated(false);
                      }}
                    >
                      Use an existing credential volume
                    </button>
                    {showImportedVolume ? (
                      <div className="rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/20 p-4 space-y-3">
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                          <span>Existing credential volume</span>
                          <input
                            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                            value={importedVolumeRef}
                            onChange={(event) => {
                              setImportedVolumeRef(event.target.value);
                              setImportedVolumeValidated(false);
                            }}
                          />
                        </label>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                          Derived mount path: {selectedAuthenticationCapability.imported_volume.mount_path}
                        </p>
                        <button
                          type="button"
                          className="rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900"
                          disabled={importedVolumeMutation.isPending}
                          onClick={() => importedVolumeMutation.mutate()}
                        >
                          Validate imported volume
                        </button>
                        {importedVolumeValidated ? (
                          <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">Validated imported volume</p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </fieldset>

              <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
                <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Advanced profile policy</legend>
                <label>
                  Execution configuration
                  <select value={form.executionConfiguration || 'null'} onChange={(event) => setForm((current) => ({ ...current, executionConfiguration: event.target.value }))}>
                    <option value="null">Automatic compatible configuration</option>
                    {form.executionConfiguration && form.executionConfiguration !== 'null' ? <option value={form.executionConfiguration}>Saved configuration</option> : null}
                    {(executionConfigurations.data || []).flatMap((configuration) => configuration.versions.filter((version) => version.version === configuration.activeVersion).map((version) => (
                      <option key={configuration.profileId} value={JSON.stringify({profileId: configuration.profileId, version: version.version, digest: version.digest})}>{configuration.displayName}</option>
                    )))}
                  </select>
                </label>
                {executionConfigurations.isError ? <p role="status">Execution configurations could not be loaded. Your saved selection is preserved.</p> : null}
                <a href="/omnigent/agents">Manage execution configurations</a>
                <div className="grid gap-4 md:grid-cols-2">
                  <label data-advanced-field="provider_label" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Provider label override</span>
                    <input className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={form.providerLabel} onChange={(event) => setForm((current) => ({ ...current, providerLabel: event.target.value }))} />
                  </label>
                  <label data-advanced-field="cooldown_after_429_seconds" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Cooldown after 429 (seconds)</span>
                    <input type="number" min="0" className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={form.cooldownAfter429Seconds} onChange={(event) => setForm((current) => ({ ...current, cooldownAfter429Seconds: event.target.value }))} />
                  </label>
                  <label data-advanced-field="rate_limit_policy" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Rate limit policy</span>
                    <select className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={form.rateLimitPolicy} onChange={(event) => setForm((current) => ({ ...current, rateLimitPolicy: event.target.value }))}>
                      <option value="backoff">backoff</option>
                      <option value="queue">queue</option>
                      <option value="fail_fast">fail_fast</option>
                    </select>
                  </label>
                  <label data-advanced-field="priority" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Priority</span>
                    <input type="number" min="0" className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value }))} placeholder="100" />
                  </label>
                  <label data-advanced-field="tags" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Tags</span>
                    <input className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={form.tagsText} onChange={(event) => setForm((current) => ({ ...current, tagsText: event.target.value }))} placeholder="team, preferred" />
                  </label>
                  <label data-advanced-field="command_behavior" className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                    <span>Command behavior</span>
                    <textarea rows={4} className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-sm" value={form.commandBehavior} onChange={(event) => setForm((current) => ({ ...current, commandBehavior: event.target.value }))} />
                  </label>
                </div>
                {manualCreationAllowed ? (
                  <label data-advanced-field="clear_env_keys" className="flex flex-col gap-1.5 text-sm font-medium text-amber-800 dark:text-amber-300">
                    <span>Clear env keys — manual expert path (validated; overrides are audited)</span>
                    <textarea
                      rows={3}
                      className="w-full rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 font-mono text-sm"
                      value={form.clearEnvKeysText}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          clearEnvKeysText: event.target.value,
                        }))
                      }
                    />
                    <p className="text-xs text-amber-700 dark:text-amber-300">Warning: freeform clear_env_keys is only allowed for unsupported combinations via the manual creation path. Values are validated by the backend (known keys, valid names, bounded list, no unsafe keys); updates to supported profiles require superuser permission with an audit reason and warning acknowledgement, and are recorded with actor metadata. Bypassing backend-recommended isolation can break launches or leak credentials, so review it carefully.</p>
                  </label>
                ) : (
                  <div data-advanced-field="clear_env_keys" tabIndex={-1} className="rounded-xl bg-slate-50 dark:bg-slate-900 p-3 text-xs text-slate-500 dark:text-slate-400">
                    <div className="font-medium text-slate-700 dark:text-slate-300">Launch environment isolation — clear environment keys</div>
                    {isEditing && editingProfile?.launch_isolation ? (
                      <>
                        <div>Classification: {editingProfile.launch_isolation.classification} · Strategy: {editingProfile.launch_isolation.strategy_id}</div>
                        <div>Effective keys: {editingProfile.launch_isolation.effective_keys.join(', ') || 'empty'}</div>
                        <div>Source: {editingProfile.launch_isolation.source}{editingProfile.launch_isolation.derived ? ' (backend-derived)' : ''} · {editingProfile.launch_isolation.editable ? 'Editable' : 'Locked by backend launch-safety policy'}</div>
                        <div>Lock reason: {editingProfile.launch_isolation.lock_reason}</div>
                        {editingProfile.launch_isolation.classification === 'expert_override' ? (
                          <div>Audited expert override recorded{editingProfile.launch_isolation.audit_reason_present ? ' with audit reason' : ' (audit reason missing)'}. Standard edits remain locked.</div>
                        ) : null}
                        {Object.entries(editingProfile.launch_isolation.explanations ?? {}).length > 0 ? (
                          <ul className="mt-1 list-disc pl-5">
                            {Object.entries(editingProfile.launch_isolation.explanations ?? {}).map(([key, explanation]) => (
                              <li key={key}>{key}: {String(explanation)}</li>
                            ))}
                          </ul>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <div>Value: {selectedAuthenticationCapability?.fields.clear_env_keys ? String((selectedAuthenticationCapability.fields.clear_env_keys.value as string[]).join(', ') || 'empty') : (form.clearEnvKeysText || (editingProfile?.clear_env_keys?.join(', ') || 'Backend strategy — runtime_provider_isolation_policy'))}</div>
                        <div>Source: {selectedAuthenticationCapability?.fields.clear_env_keys?.source ?? 'runtime_provider_isolation_policy'} · Locked by backend launch-safety policy</div>
                        <div>Lock reason: {selectedAuthenticationCapability?.fields.clear_env_keys?.lock_reason ?? 'Environment clearing is backend-owned launch security policy.'}</div>
                      </>
                    )}
                  </div>
                )}
              </fieldset>
              <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
                <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Reset advanced options</legend>
                {!creationPreset && !isEditing ? (
                  <p className="text-sm text-slate-500 dark:text-slate-400">Load a backend preset to preview recommended values.</p>
                ) : isEditing && !creationPreset ? (
                  creationPresetLoading ? (
                    <p className="text-sm text-slate-500 dark:text-slate-400">Loading backend-recommended values…</p>
                  ) : (
                    <>
                      <p className="text-sm text-slate-500 dark:text-slate-400">
                        No backend preset is available for this profile type
                        {creationPresetError ? ` (${creationPresetError})` : ''}, so
                        recommended values cannot be previewed. Discarding unsaved
                        advanced edits restores the last saved values instead.
                      </p>
                      <button
                        type="button"
                        className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300"
                        onClick={() => {
                          setForm(formBaseline);
                          setTierDrafts(tierBaseline ? cloneTierDrafts(tierBaseline) : [runtimeDefaultTierDraft()]);
                          if (tierBaselineDefaultId) setDefaultTierClientId(tierBaselineDefaultId);
                          onNotice({ level: 'ok', text: 'Unsaved advanced edits discarded; restored last saved values.' });
                        }}
                      >
                        Discard unsaved advanced edits
                      </button>
                    </>
                  )
                ) : (
                  <>
                    {!showResetPreview ? (
                      <button
                        type="button"
                        className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold text-slate-700 dark:text-slate-300"
                        onClick={() => setShowResetPreview(true)}
                      >
                        Reset advanced options to recommended
                      </button>
                    ) : (
                      <div className="space-y-3">
                        <div className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 text-sm text-amber-800 dark:text-amber-300">
                          <p className="font-semibold">Preview — recommended values will replace draft overrides:</p>
                          <ul className="mt-2 list-disc pl-5 text-xs">
                            {(() => {
                              const preset = creationPreset;
                              const fields = preset?.fields ?? {};
                              const items: string[] = [];
                              if (creationPreset) {
                                if (fields.cooldown_after_429_seconds) items.push(`cooldown_after_429_seconds: ${String(fields.cooldown_after_429_seconds.value)} (concrete recommended)`);
                                if (fields.rate_limit_policy) items.push(`rate_limit_policy: ${String(fields.rate_limit_policy.value)} (concrete recommended)`);
                                if (fields.priority) items.push(`priority: ${String(fields.priority.value)} (concrete recommended)`);
                                if (fields.clear_env_keys) items.push(`clear_env_keys: ${(fields.clear_env_keys.value as string[]).join(', ') || 'empty'} — recalculated security fields (runtime_provider_isolation_policy)`);
                                items.push('credential_source / runtime_materialization_mode — omitted to inherit backend-derived values');
                                items.push('secret_refs / volume refs — omitted unless explicitly bound');
                              } else if (isEditing && editingProfile) {
                                items.push('Backend preset unavailable — preview unavailable; use “Discard unsaved advanced edits” to restore last saved values');
                              } else {
                                items.push('No preset available — preview unavailable');
                              }
                              return items.map((it) => <li key={it}>{it}</li>);
                            })()}
                          </ul>
                          <p className="mt-2 text-xs">Fields changed from explicit override to inherited/omitted will be normalized by the server. Generated security fields will be recalculated.</p>
                        </div>
                        <div className="flex gap-3">
                          <button
                            type="button"
                            className="rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900"
                            onClick={() => {
                              if (creationPreset) {
                                setForm((cur) => {
                                  const next = applyCreationPresetToForm(cur, creationPreset);
                                  // isDefault/enabled are identity toggles outside
                                  // advanced options; an advanced reset while
                                  // editing must not flip them as a side effect.
                                  return isEditing
                                    ? { ...next, isDefault: cur.isDefault, enabled: cur.enabled }
                                    : next;
                                });
                              } else if (isEditing && editingProfile) {
                                setForm(formBaseline);
                                setTierDrafts(tierBaseline ? cloneTierDrafts(tierBaseline) : [runtimeDefaultTierDraft()]);
                                if (tierBaselineDefaultId) setDefaultTierClientId(tierBaselineDefaultId);
                              }
                              setShowResetPreview(false);
                              onNotice({ level: 'ok', text: 'Advanced options reset to recommended preview applied.' });
                            }}
                          >
                            Apply reset
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm"
                            onClick={() => setShowResetPreview(false)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                    <p className="text-xs text-slate-500 dark:text-slate-400">Distinguishes concrete recommended values vs omit-to-inherit vs recalculated security fields and launch impact.</p>
                  </>
                )}
              </fieldset>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              className="inline-flex items-center justify-center rounded-lg bg-slate-900 dark:bg-slate-100 px-5 py-2.5 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending
                ? 'Saving...'
                : isEditing
                  ? 'Update provider profile'
                  : 'Create profile'}
            </button>
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-lg border border-slate-300 dark:border-slate-700 px-5 py-2.5 text-sm font-semibold text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
              onClick={resetForm}
            >
              {isEditing ? 'Cancel edit' : 'Reset form'}
            </button>
          </div>
        </form>
      </div>
      ) : null}
    </section>
  );
}
