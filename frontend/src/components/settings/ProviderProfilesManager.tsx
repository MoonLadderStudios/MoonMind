import { useEffect, useState } from 'react';
import { QueryClient, useMutation } from '@tanstack/react-query';

export interface ProviderProfile {
  profile_id: string;
  runtime_id: string;
  provider_id: string;
  provider_label?: string | null;
  default_model?: string | null;
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
  command_behavior?: Record<string, unknown> | null;
  tags?: string[] | null;
  priority?: number | null;
  clear_env_keys?: string[] | null;
  account_label?: string | null;
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

interface ProviderProfilesManagerProps {
  profiles: ProviderProfile[];
  secretSlugs: string[];
  onNotice: (notice: Notice | null) => void;
  queryClient: QueryClient;
  /** Map of canonical runtime_id → default model from the boot config. */
  defaultTaskModelByRuntime?: Record<string, string>;
}

interface ProviderProfileFormState {
  profileId: string;
  runtimeId: string;
  providerId: string;
  providerLabel: string;
  defaultModel: string;
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
  profile_id: string;
  runtime_id: string;
  provider_id: string;
  provider_label: string | null;
  default_model: string | null;
  credential_source: string;
  runtime_materialization_mode: string;
  secret_refs: Record<string, string>;
  volume_ref: string | null;
  volume_mount_path: string | null;
  max_parallel_runs: number;
  cooldown_after_429_seconds: number;
  rate_limit_policy: string;
  enabled: boolean;
  is_default: boolean;
  command_behavior: Record<string, unknown> | null;
  tags: string[] | null;
  priority: number | null;
  clear_env_keys: string[] | null;
  account_label: string | null;
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
  session_transport?: string | null;
  failure_reason?: string | null;
}

interface OAuthSessionState {
  sessionId: string;
  status: OAuthSessionStatus;
  failureReason?: string | null | undefined;
}

export const PROVIDER_PROFILE_QUERY_KEY = ['provider-profiles'] as const;

export function defaultFormState(): ProviderProfileFormState {
  return {
    profileId: '',
    runtimeId: '',
    providerId: '',
    providerLabel: '',
    defaultModel: '',
    credentialSource: 'secret_ref',
    runtimeMaterializationMode: 'api_key_env',
    secretRefsText: '{}',
    volumeRef: '',
    volumeMountPath: '',
    maxParallelRuns: '1',
    cooldownAfter429Seconds: '300',
    rateLimitPolicy: 'backoff',
    enabled: true,
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
    profileId: profile.profile_id,
    runtimeId: profile.runtime_id,
    providerId: profile.provider_id,
    providerLabel: profile.provider_label ?? '',
    defaultModel: profile.default_model ?? '',
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

function summarizeSecretRefs(secretRefs: Record<string, string>): string {
  const entries = Object.entries(secretRefs);
  if (entries.length === 0) {
    return 'No secret refs';
  }
  return entries.map(([key, value]) => `${key}: ${value}`).join(', ');
}

function isCodexOAuthCapable(profile: ProviderProfile): boolean {
  return profile.runtime_id === 'codex_cli';
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
  return status === 'failed' || status === 'cancelled' || status === 'expired' || status === 'succeeded';
}

function buildSavePayload(form: ProviderProfileFormState): ProviderProfileSavePayload {
  const payload = {
    profile_id: form.profileId.trim(),
    runtime_id: form.runtimeId.trim(),
    provider_id: form.providerId.trim(),
    provider_label: form.providerLabel.trim() || null,
    default_model: form.defaultModel.trim() || null,
    credential_source: form.credentialSource,
    runtime_materialization_mode: form.runtimeMaterializationMode,
    secret_refs: parseSecretRefs(form.secretRefsText),
    volume_ref: form.volumeRef.trim() || null,
    volume_mount_path: form.volumeMountPath.trim() || null,
    max_parallel_runs: Number(form.maxParallelRuns),
    cooldown_after_429_seconds: Number(form.cooldownAfter429Seconds),
    rate_limit_policy: form.rateLimitPolicy,
    enabled: form.enabled,
    is_default: form.isDefault,
    command_behavior: parseCommandBehavior(form.commandBehavior),
    tags: parseTags(form.tagsText),
    priority: parsePriority(form.priority),
    clear_env_keys: parseClearEnvKeys(form.clearEnvKeysText),
    account_label: form.accountLabel.trim() || null,
  };

  if (!payload.profile_id) {
    throw new Error('Profile ID is required.');
  }
  if (!payload.runtime_id) {
    throw new Error('Runtime ID is required.');
  }
  if (!payload.provider_id) {
    throw new Error('Provider ID is required.');
  }

  return payload;
}

export function ProviderProfilesManager({
  profiles,
  secretSlugs,
  onNotice,
  queryClient,
  defaultTaskModelByRuntime = {},
}: ProviderProfilesManagerProps) {
  const [form, setForm] = useState<ProviderProfileFormState>(() => defaultFormState());
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [oauthSessions, setOauthSessions] = useState<Record<string, OAuthSessionState>>({});

  const isEditing = editingProfileId !== null;
  const defaultFormValues = defaultFormState();

  const resetForm = () => {
    setEditingProfileId(null);
    setForm(defaultFormState());
    onNotice(null);
  };

  const saveMutation = useMutation({
    mutationFn: async (formState: ProviderProfileFormState) => {
      const payload = buildSavePayload(formState);
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
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : `Failed to ${isEditing ? 'update' : 'create'} provider profile.`;
        throw new Error(detail);
      }
      return response.json() as Promise<ProviderProfile>;
    },
    onSuccess: (savedProfile, submittedForm) => {
      onNotice({
        level: 'ok',
        text: isEditing
          ? `Provider profile "${editingProfileId}" updated.`
          : `Provider profile "${submittedForm.profileId.trim()}" created.`,
      });
      setEditingProfileId(null);
      setForm(defaultFormState());
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
    },
    onError: (error: Error) => {
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
      onNotice({ level: 'ok', text: `Provider profile "${profileId}" deleted.` });
      if (editingProfileId === profileId) {
        setEditingProfileId(null);
        setForm(defaultFormState());
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
        text: `Provider profile "${variables.profileId}" ${
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
      setOauthSessions((current) => ({
        ...current,
        [session.profile_id]: {
          sessionId: session.session_id,
          status: session.status,
          failureReason: session.failure_reason,
        },
      }));
      window.open(
        `/oauth-terminal?session_id=${encodeURIComponent(session.session_id)}`,
        '_blank',
        'noopener,noreferrer',
      );
      onNotice({
        level: 'ok',
        text: `OAuth session "${session.session_id}" started for "${session.profile_id}".`,
      });
    },
    onError: (error: Error) => {
      onNotice({ level: 'error', text: error.message });
    },
  });

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
        [profileId]: { ...current[profileId], sessionId: current[profileId]?.sessionId ?? '', status: 'cancelled' },
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
        [profileId]: { sessionId, status: 'succeeded' },
      }));
      queryClient.invalidateQueries({ queryKey: PROVIDER_PROFILE_QUERY_KEY });
      onNotice({ level: 'ok', text: `OAuth session for "${profileId}" finalized.` });
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
      setOauthSessions((current) => ({
        ...current,
        [profileId]: {
          sessionId: session.session_id,
          status: session.status,
          failureReason: session.failure_reason,
        },
      }));
      window.open(
        `/oauth-terminal?session_id=${encodeURIComponent(session.session_id)}`,
        '_blank',
        'noopener,noreferrer',
      );
      onNotice({ level: 'ok', text: `OAuth session for "${profileId}" retried.` });
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
        for (const { profileId, session } of appliedUpdates) {
          next[profileId] = {
            sessionId: session.session_id,
            status: session.status,
            failureReason: session.failure_reason,
          };
        }
        return next;
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

  return (
    <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-200 dark:border-slate-800 pb-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Provider Profiles</h3>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Manage your configured provider profiles. Select a profile below to edit, or scroll down to create a new one.
          </p>
        </div>
      </div>

      <div className="mt-6 overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800 text-left text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800/50">
            <tr>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Profile</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Runtime</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Provider</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Credential</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Secret refs</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Status</th>
              <th className="px-3 py-3 font-medium text-slate-600 dark:text-slate-400">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-mm-border/80 bg-transparent">
            {profiles.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-slate-500 dark:text-slate-400" colSpan={7}>
                  No provider profiles configured yet.
                </td>
              </tr>
            ) : (
              profiles.map((profile) => {
                const oauthSession = oauthSessions[profile.profile_id];
                const canStartOAuth = isCodexOAuthCapable(profile);
                return (
                <tr key={profile.profile_id}>
                  <td className="px-3 py-4">
                    <div className="font-medium text-slate-900 dark:text-white">{profile.profile_id}</div>
                    {profile.is_default ? (
                      <div className="text-xs font-medium text-emerald-700 dark:text-emerald-400">
                        Runtime default
                      </div>
                    ) : null}
                    {profile.default_model ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        Model: {profile.default_model}
                      </div>
                    ) : (
                      <div className="text-xs text-slate-400 dark:text-slate-500 italic">
                        {defaultTaskModelByRuntime[profile.runtime_id]
                          ? `Inherits: ${defaultTaskModelByRuntime[profile.runtime_id]}`
                          : 'No model (runtime default)'}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-4 text-slate-700 dark:text-slate-300">{profile.runtime_id}</td>
                  <td className="px-3 py-4">
                    <div className="text-slate-700 dark:text-slate-300">{profile.provider_id}</div>
                    {profile.provider_label ? (
                      <div className="text-xs text-slate-500 dark:text-slate-400">{profile.provider_label}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-4 text-slate-700 dark:text-slate-300">
                    <div>{profile.credential_source}</div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      {profile.runtime_materialization_mode}
                    </div>
                  </td>
                  <td className="px-3 py-4 font-mono text-xs text-slate-600 dark:text-slate-400">
                    {summarizeSecretRefs(profile.secret_refs)}
                  </td>
                  <td className="px-3 py-4">
                    <span
                      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
                        profile.enabled
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                          : 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
                      }`}
                    >
                      {profile.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                    {oauthSession ? (
                      <div className="mt-2 text-xs font-medium text-slate-600 dark:text-slate-400">
                        OAuth: {oauthStatusLabel(oauthSession.status)}
                      </div>
                    ) : null}
                    {oauthSession?.failureReason ? (
                      <div className="mt-1 text-xs text-rose-600 dark:text-rose-400">
                        {oauthSession.failureReason}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-4">
                    <div className="flex flex-wrap gap-2">
                      {canStartOAuth ? (
                        <button
                          type="button"
                          className="rounded-full border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition hover:border-emerald-500 dark:hover:border-emerald-500"
                          onClick={() => startOAuthMutation.mutate(profile)}
                          disabled={startOAuthMutation.isPending}
                          aria-label={`Auth ${profile.profile_id}`}
                        >
                          Auth
                        </button>
                      ) : null}
                      {oauthSession && isActiveOAuthStatus(oauthSession.status) ? (
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
                      {oauthSession && canFinalizeOAuthStatus(oauthSession.status) ? (
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
                      {oauthSession && canRetryOAuthStatus(oauthSession.status) ? (
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
                      <button
                        type="button"
                        className="rounded-full border border-slate-300 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-300 transition hover:border-slate-400 dark:hover:border-slate-500 hover:text-slate-900 dark:hover:text-white"
                        onClick={() => {
                          setEditingProfileId(profile.profile_id);
                          setForm(toFormState(profile));
                          onNotice(null);
                        }}
                      >
                        Edit
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
                    </div>
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* ── Form Section ── */}
      <div className="mt-8 border-t border-slate-200 dark:border-slate-700 pt-8">
        <div className="flex flex-col gap-1 mb-6 md:flex-row md:items-end md:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
              {isEditing ? `Edit Profile: ${editingProfileId}` : 'Create Provider Profile'}
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
          {/* ── Required: Identity ── */}
          <fieldset className="rounded-2xl border border-amber-200/60 dark:border-amber-800/40 bg-amber-50/30 dark:bg-amber-900/10 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-amber-700 dark:text-amber-400">
              Identity <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; required</span>
            </legend>
            <div className="grid gap-4 md:grid-cols-3">
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
            </div>
          </fieldset>

          {/* ── Provider Settings (have smart defaults) ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
              Provider Settings <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; defaults provided</span>
            </legend>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Provider label</span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.providerLabel}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, providerLabel: event.target.value }))
                  }
                  placeholder="OpenAI"
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">Optional display name</p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Credential source</span>
                <select
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.credentialSource}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      credentialSource: event.target.value,
                    }))
                  }
                >
                  <option value="secret_ref">secret_ref</option>
                  <option value="oauth_volume">oauth_volume</option>
                  <option value="none">none</option>
                </select>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Default: {defaultFormValues.credentialSource}
                </p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Materialization mode</span>
                <select
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.runtimeMaterializationMode}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      runtimeMaterializationMode: event.target.value,
                    }))
                  }
                >
                  <option value="api_key_env">api_key_env</option>
                  <option value="env_bundle">env_bundle</option>
                  <option value="config_bundle">config_bundle</option>
                  <option value="composite">composite</option>
                  <option value="oauth_home">oauth_home</option>
                </select>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Default: {defaultFormValues.runtimeMaterializationMode}
                </p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 xl:col-span-2">
                <span>Default model</span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.defaultModel}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, defaultModel: event.target.value }))
                  }
                  placeholder={
                    defaultTaskModelByRuntime[form.runtimeId]
                      ? `Inherited: ${defaultTaskModelByRuntime[form.runtimeId]}`
                      : 'Leave blank to inherit runtime default'
                  }
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Leave blank to inherit the runtime default
                  {defaultTaskModelByRuntime[form.runtimeId]
                    ? ` (${defaultTaskModelByRuntime[form.runtimeId]})`
                    : ''}.
                  Set a value to override.
                </p>
              </label>
              <div className="flex gap-4 items-start xl:col-span-1">
                <label className="flex items-center gap-3 rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800 px-4 py-3 text-sm font-medium text-slate-700 dark:text-slate-300 flex-1">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, enabled: event.target.checked }))
                    }
                  />
                  Enabled
                </label>
                <label className="flex items-center gap-3 rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800 px-4 py-3 text-sm font-medium text-slate-700 dark:text-slate-300 flex-1">
                  <input
                    type="checkbox"
                    checked={form.isDefault}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, isDefault: event.target.checked }))
                    }
                  />
                  Runtime default
                </label>
              </div>
            </div>
          </fieldset>

          {/* ── Runtime Limits (have smart defaults) ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
              Runtime Limits <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; defaults provided</span>
            </legend>
            <div className="grid gap-4 md:grid-cols-3">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Max parallel runs</span>
                <input
                  type="number"
                  min="1"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.maxParallelRuns}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      maxParallelRuns: event.target.value,
                    }))
                  }
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Default: {defaultFormValues.maxParallelRuns}
                </p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Cooldown after 429 (seconds)</span>
                <input
                  type="number"
                  min="0"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.cooldownAfter429Seconds}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      cooldownAfter429Seconds: event.target.value,
                    }))
                  }
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Default: {defaultFormValues.cooldownAfter429Seconds}
                </p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Rate limit policy</span>
                <select
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.rateLimitPolicy}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      rateLimitPolicy: event.target.value,
                    }))
                  }
                >
                  <option value="backoff">backoff</option>
                  <option value="queue">queue</option>
                  <option value="fail_fast">fail_fast</option>
                </select>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Default: {defaultFormValues.rateLimitPolicy}
                </p>
              </label>
            </div>
          </fieldset>

          {/* ── Credentials & Volumes ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
              Credentials & Volumes <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; optional</span>
            </legend>
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
              <div className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <label htmlFor="provider-profile-secret-refs">
                  Secret refs (JSON object of string refs)
                </label>
                <textarea
                  id="provider-profile-secret-refs"
                  rows={6}
                  className="w-full rounded-2xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.secretRefsText}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      secretRefsText: event.target.value,
                    }))
                  }
                />
                {secretSlugs.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1.5 items-center">
                    <span className="text-xs text-slate-400 dark:text-slate-500">Available slugs:</span>
                    {secretSlugs.map((slug) => (
                      <code
                        key={slug}
                        className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs text-sky-700 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-400"
                      >
                        db://{slug}
                      </code>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-400 dark:text-slate-500">
                    No managed secrets yet. Create them in the Managed Secrets section below.
                  </p>
                )}
              </div>
              <div className="space-y-4">
                <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                  <span>Volume ref</span>
                  <input
                    className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                    value={form.volumeRef}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, volumeRef: event.target.value }))
                    }
                  />
                  <p className="text-xs text-slate-400 dark:text-slate-500">Only needed for volume-based credentials</p>
                </label>
                <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                  <span>Volume mount path</span>
                  <input
                    className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                    value={form.volumeMountPath}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        volumeMountPath: event.target.value,
                      }))
                    }
                  />
                  <p className="text-xs text-slate-400 dark:text-slate-500">Only needed for volume-based credentials</p>
                </label>
              </div>
            </div>
          </fieldset>

          {/* ── Advanced Options ── */}
          <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-5 space-y-4">
            <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
              Advanced Options <span className="font-normal text-slate-500 dark:text-slate-400">&mdash; optional</span>
            </legend>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Command behavior</span>
                <textarea
                  rows={4}
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.commandBehavior}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      commandBehavior: event.target.value,
                    }))
                  }
                  placeholder='{"suppress_default_model_flag": true}'
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Tags</span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.tagsText}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      tagsText: event.target.value,
                    }))
                  }
                  placeholder="openrouter, qwen, codex"
                />
                <p className="text-xs text-slate-400 dark:text-slate-500">Comma-separated</p>
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Priority</span>
                <input
                  type="number"
                  min="0"
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.priority}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      priority: event.target.value,
                    }))
                  }
                  placeholder="100"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                <span>Account label</span>
                <input
                  className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                  value={form.accountLabel}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      accountLabel: event.target.value,
                    }))
                  }
                  placeholder="team-default"
                />
              </label>
            </div>
            <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
              <span>Clear env keys</span>
              <textarea
                rows={3}
                className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-sm text-slate-900 dark:text-white shadow-sm"
                value={form.clearEnvKeysText}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    clearEnvKeysText: event.target.value,
                  }))
                }
                placeholder={"OPENAI_API_KEY\nOPENAI_BASE_URL"}
              />
              <p className="text-xs text-slate-400 dark:text-slate-500">One key per line</p>
            </label>
          </fieldset>

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
                  : 'Create provider profile'}
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
    </section>
  );
}
