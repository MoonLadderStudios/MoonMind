import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render, screen as rtlScreen, within } from '@testing-library/react';

import { DataTable, type Column } from '../components/tables/DataTable';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { configure, renderWithClient, screen, waitFor } from '../utils/test-utils';
import '../styles/dashboard.css';

// The first render of each lazily loaded route waits for Vite to transform
// its chunk, which can outlast the 1s default on a cold WebKit start.
configure({ asyncUtilTimeout: 5_000 });

// Real-browser phone guardrail for MoonLadderStudios/MoonMind#4559. The
// production Agents inventory and Providers & Secrets pages render inside the
// real dashboard shell (DashboardApp -> AppShell -> panel) with synthetic,
// credential-free API fixtures. `.dashboard-root` clips horizontal overflow,
// so document scroll width cannot prove a repair: every visible control and
// text element is compared with its viewport and its actual clipping/scroll
// ancestors, and controls are hit-tested and focused.
//
// Root font-size 200% is text-only enlargement (WCAG 1.4.4 style), and the
// 320px viewport is the reflow equivalent of a 1280px window at 400% zoom;
// neither is CSS scaling. WebKit runs in its own CI leg and is automation,
// not a physical iOS keyboard observation.

const TOLERANCE_PX = 1.5;
const LONG_ID = 'synthetic-agent-identity-with-a-very-long-unbroken-name-that-must-wrap-0123456789';
const LONG_TEXT =
  'A long human-readable summary that must wrap naturally inside the available content width. '.repeat(3);
const DIGEST = `sha256:${'a'.repeat(64)}`;

const LAYOUTS = [
  { name: 'narrow phone', width: 320, height: 568 },
  { name: 'phone', width: 390, height: 844 },
  { name: 'short landscape phone', width: 844, height: 390 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;

const uiInfo = {
  app: 'moonmind',
  apiBase: '/api',
  features: { omnigentAgents: true, omnigentPolicies: true, artifacts: true },
  endpoints: { omnigentAgents: '/api/omnigent/api/agents', omnigentPolicies: '/api/omnigent/api/policies' },
  limits: {},
  settingsPermissions: [
    'provider_profiles.read',
    'provider_profiles.write',
    'secrets.metadata.read',
    'secrets.write',
  ],
  dashboardConfig: { pollIntervalsMs: { list: 60_000, detail: 60_000 } },
};

const agentRows = [
  { id: LONG_ID, name: 'Team codex', status: 'active', description: LONG_TEXT, updatedAt: '2026-10-01T00:00:00Z' },
  { id: 'short-agent', name: 'Reviewer', status: 'idle', description: 'Short summary.' },
];

const evidenceRow = {
  artifact_id: `art_${LONG_ID}`,
  created_at: '2026-10-01T00:00:00Z',
  content_type: 'text/plain',
  size_bytes: 2048,
  status: 'complete',
  retention_class: 'standard',
  link_type: 'output.primary',
  label: 'Final output',
  workflow_id: 'mm:synthetic-workflow',
  run_id: 'run-1',
  download_url: '/api/artifacts/art_synthetic/download',
};

const executionConfigurations = [
  {
    profileId: LONG_ID,
    displayName: 'Synthetic execution configuration with a long display name',
    state: 'active',
    activeVersion: 2,
    versions: [
      { version: 2, digest: DIGEST, validationResult: { ready: true }, document: { model: 'synthetic' } },
      { version: 1, digest: DIGEST, validationResult: null },
    ],
  },
];

type SavedTier = { label: string; model: string | null; effort: string | null };

function savedProfile(tiers: SavedTier[], defaultTier: number) {
  return {
    profile_id: LONG_ID,
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    is_default: true,
    model_tiers: tiers,
    default_model_tier: defaultTier,
  };
}

const INITIAL_TIERS: SavedTier[] = [
  { label: 'Plan and verify with a very long tier label that must wrap', model: 'gpt-5.5', effort: 'medium' },
  { label: 'Implementation', model: 'gpt-5.5', effort: 'high' },
  { label: 'Docs and follow-through', model: null, effort: null },
];

const creationChoices = {
  version: 'provider-profile-creation-v1',
  profile_id_max_length: 128,
  harnesses: [
    {
      runtime_id: 'codex_cli',
      label: 'Codex CLI',
      providers: [{ provider_id: 'openai', label: 'OpenAI' }],
      custom_provider_allowed: false,
    },
  ],
};

const tierCapabilities = {
  version: 'tier-cap-v1-synthetic',
  profile_id: null,
  runtime_id: 'codex_cli',
  provider_id: 'openai',
  evidence: { source: 'runtime_draft', credential_generation: null, image_ref: null, observed_at: null, stale: false },
  tier_constraints: { min_count: 1, max_count: null },
  model: {
    runtime_default: 'gpt-5.5',
    allow_custom: true,
    options: [{ value: 'gpt-5.5', label: 'GPT-5.5', description: null, status: 'available', recommended: true }],
  },
  effort: {
    supported: true,
    runtime_default: 'medium',
    allow_custom: false,
    application: 'native',
    options: ['low', 'medium', 'high'].map((value) => ({
      value,
      label: value,
      description: null,
      status: 'available',
      compatible_models: null,
    })),
  },
  diagnostics: [],
};

// Policies kind of the same inventory page: a long policy id, three
// immutable versions, failing validation diagnostics, dependents with long
// references and a normalized diff from an older version.
const POLICIES_ENDPOINT = '/api/omnigent/api/policies';
const POLICY_ID = 'synthetic-policy-with-a-very-long-unbroken-identifier-that-must-wrap-0123456789';
const POLICY_DOCUMENT = {
  host: { mode: 'static_compose', profileRef: `host-profile-${LONG_ID}` },
  network: { egress: 'restricted', allowedHosts: ['registry.example.test', 'packages.example.test'] },
  retention: { artifactsDays: 30, summary: LONG_TEXT },
};
const POLICY_DIAGNOSTICS = [
  { code: 'host_profile_missing', path: '/host/profileRef', message: `No deployment host profile matches ${LONG_ID}.` },
  { code: 'retention_too_long', path: '/retention/artifactsDays', message: 'Retention exceeds the deployment maximum.' },
];

function policyVersion(version: number, state: string, valid: boolean) {
  return {
    policyId: POLICY_ID,
    version,
    ref: `${POLICY_ID}@${version}`,
    state,
    digest: DIGEST,
    document: { ...POLICY_DOCUMENT, revision: version },
    validation: valid ? { valid: true, diagnostics: [] } : { valid: false, diagnostics: POLICY_DIAGNOSTICS },
  };
}

const policyVersions = [policyVersion(3, 'active', false), policyVersion(2, 'superseded', true), policyVersion(1, 'superseded', true)];

function policyResponse(url: URL, method: string, init?: RequestInit): Response {
  const path = url.pathname.slice(POLICIES_ENDPOINT.length);
  if (path === '') {
    return json({ items: [{
      id: POLICY_ID, name: 'Synthetic static Codex policy', status: 'active', defaultVersion: 3, summary: LONG_TEXT,
      updatedAt: '2026-10-01T00:00:00Z', version: { validation: policyVersions[0]!.validation, document: policyVersions[0]!.document },
    }] });
  }
  if (method === 'POST') {
    api.policySaves.push({ url: url.pathname, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    return json(policyVersion(4, 'draft', true), 201);
  }
  if (path.endsWith('/versions')) return json({ items: policyVersions });
  if (path.endsWith('/audit')) {
    return json({ items: [3, 2, 1].map((version) => ({
      eventId: `event-${version}`, version, type: 'default_changed', actor: `operator-${LONG_ID}`, createdAt: '2026-10-01T00:00:00Z',
    })) });
  }
  if (path.endsWith('/usage')) {
    const active = path.includes('/versions/3/');
    return json({
      policyRef: `${POLICY_ID}@3`,
      default: active,
      dependents: {
        hostBindings: [`host-binding-${LONG_ID}`], hostBindingCount: 1,
        providerProfiles: [`provider-profile-${LONG_ID}`], providerProfileCount: 1,
        workflows: [`mm:workflow-${LONG_ID}`], workflowCount: 1,
        bridgeSessions: ['bridge-1', 'bridge-2'], bridgeSessionCount: 2,
        activeBridgeSessions: ['bridge-1'], activeBridgeSessionCount: 1,
      },
      activationImpact: { willSwitchDefault: !active, compatible: false, diagnostics: [] },
      unavailabilityBlockers: active ? ['Switch the policy default before disabling or deprecating this version.'] : [],
    });
  }
  if (path.endsWith('/diff')) {
    return json({ diff: `--- ${POLICY_ID}@1\n+++ ${POLICY_ID}@3\n-  "revision": 1\n+  "revision": 3\n+  "summary": "${LONG_TEXT.trim()}"` });
  }
  return json({ detail: `No synthetic policy fixture for ${method} ${url.pathname}` }, 404);
}

// Saved profiles whose credential actions open the Anthropic and OpenCode
// API-key enrollment drawers. They carry no secret values.
const CLAUDE_PROFILE_ID = 'claude-team-api-key-profile-with-a-long-unbroken-identifier-0123456789';
const OPENCODE_PROFILE_ID = 'opencode-go-api-key-profile-with-a-long-unbroken-identifier-0123456789';
const enrollmentProfiles = [
  {
    ...savedProfile([{ label: 'Default', model: null, effort: null }], 1),
    profile_id: CLAUDE_PROFILE_ID,
    runtime_id: 'claude_code',
    provider_id: 'anthropic',
    is_default: false,
    auth_state: 'not_configured',
  },
  {
    ...savedProfile([{ label: 'Default', model: null, effort: null }], 1),
    profile_id: OPENCODE_PROFILE_ID,
    runtime_id: 'opencode',
    provider_id: 'opencode-go',
    provider_label: 'OpenCode Go',
    runtime_materialization_mode: 'composite',
    is_default: false,
    auth_state: 'not_configured',
    creation_capabilities: {
      version: 'provider-profile-creation-v1',
      runtime_id: 'opencode',
      provider_id: 'opencode-go',
      supported: true,
      authentication_methods: [{
        id: 'api_key', label: 'API key', setup_action: 'api_key', launch_ready_after_setup: true, fields: {},
        secret_roles: [{ role: 'opencode_api_key', label: 'OpenCode API key', required: true, compatible_schemes: ['db', 'env'] }],
        imported_volume: { supported: false, mount_path: null, source: 'runtime_provider_strategy', lock_reason: null },
      }],
      diagnostics: [],
    },
  },
];

// Instance and Operations settings: the generated workspace catalog with
// every ordinary control type, a failing diagnostic and long values, plus the
// worker, shard and deployment state the Operations cards read.
function settingDescriptor(overrides: Record<string, unknown>) {
  return {
    description: LONG_TEXT, category: 'Workflow', section: 'user-workspace', scopes: ['workspace'],
    default_value: null, override_value: null, source: 'config_or_default',
    source_explanation: 'Resolved from application settings.', apply_mode: 'next_task',
    activation_state: 'active', active: true, pending_value: null, affected_process_or_worker: 'task_creation',
    completion_guidance: 'New tasks will use this value when they are created.', options: null, constraints: null,
    sensitive: false, secret_role: null, read_only: false, read_only_reason: null, requires_reload: false,
    requires_worker_restart: false, requires_process_restart: false, applies_to: ['task_creation'], depends_on: [],
    audit: { store_old_value: true, store_new_value: true, redact: false }, value_version: 1, diagnostics: [],
    ...overrides,
  };
}

const workspaceCatalog = {
  section: 'user-workspace',
  scope: 'workspace',
  categories: {
    Workflow: [
      settingDescriptor({
        key: 'workflow.default_publish_mode', title: 'Default Publish Mode', type: 'enum', ui: 'select', order: 10,
        effective_value: 'pr', options: [{ value: 'none', label: 'None' }, { value: 'pr', label: 'Pull Request' }],
      }),
      settingDescriptor({
        key: 'skills.canary_percent', title: 'Skills Canary Percent', type: 'integer', ui: 'number', order: 20,
        effective_value: 25, constraints: { minimum: 0, maximum: 100 },
        diagnostics: [{ code: 'out_of_range', message: 'The stored override is outside the allowed range.', severity: 'error' }],
      }),
      settingDescriptor({
        key: 'workflow.branch_prefix', title: 'Branch prefix', type: 'string', ui: 'text', order: 30,
        effective_value: LONG_ID,
      }),
      settingDescriptor({
        key: 'workflow.auto_publish', title: 'Publish automatically', type: 'boolean', ui: 'toggle', order: 40,
        effective_value: true,
      }),
      settingDescriptor({
        key: 'workflow.deployment_root', title: 'Deployment root', type: 'string', ui: 'readonly', order: 50,
        effective_value: `/srv/${LONG_ID}`, read_only: true, read_only_reason: 'Owned by the deployment environment.',
      }),
    ],
  },
};

const workerSnapshot = {
  system: { workersPaused: false, mode: null, version: 1, updatedAt: '2026-10-01T00:00:00Z', reason: LONG_TEXT },
  metrics: { queued: 1, running: 2, staleRunning: 0, isDrained: true, metricsSource: 'temporal' },
  commands: [
    {
      id: 'pause-workers', label: 'Pause Workers', target: 'workers', impact: 'Blocks new worker claims or quiesces active workers.',
      requiresConfirmation: true, requiredPermission: 'operations.invoke', available: true, rollbackAction: 'resume-workers',
    },
    {
      id: 'enable-maintenance-mode', label: 'Enable Maintenance Mode', target: 'scheduler',
      impact: 'Prevents normal launch scheduling while maintenance is active.', requiresConfirmation: true,
      requiredPermission: 'operations.invoke', available: false, unavailableReason: 'Maintenance mode subsystem is not connected.',
    },
  ],
  audit: { latest: [{
    action: 'pause', target: 'workers', mode: 'drain', reason: LONG_TEXT, actorUserId: '00000000-0000-0000-0000-000000000001',
    resultStatus: 'succeeded', signalStatus: 'succeeded', idempotencyKey: `worker-pause-${LONG_ID}`, createdAt: '2026-10-01T00:01:00Z',
  }] },
};

const DEPLOYMENT_REPOSITORY = 'ghcr.io/example/moonmind';
const failedUpdate = {
  id: 'ctl-synthetic', kind: 'update', status: 'FAILED', owner: 'controller', operationId: `op-${LONG_ID}`,
  requestedImage: `${DEPLOYMENT_REPOSITORY}:20261001.1200`, installedImage: null,
  errorSummary: `Image pull failed for ${DEPLOYMENT_REPOSITORY}@${DIGEST}`,
  attempts: [{ attempt: 1, error: `manifest unknown: ${DIGEST}`, at: '2026-10-01T00:02:00Z' }],
  attemptGroup: 1, verification: [], retryAllowed: true, startedAt: '2026-10-01T00:00:00Z', completedAt: '2026-10-01T00:03:00Z',
  runDetailUrl: null, logsArtifactUrl: null, rollbackEligibility: null,
};

function settingsResponse(url: URL): Response | null {
  switch (url.pathname) {
    case '/api/v1/settings/catalog':
      return json(workspaceCatalog);
    case '/api/v1/settings/audit':
      return json({ items: [{
        id: 'audit-1', event_type: 'setting_updated', key: 'workflow.branch_prefix', scope: 'workspace',
        actor_user_id: '00000000-0000-0000-0000-000000000001', old_value: 'mm/', new_value: LONG_ID, redacted: false,
        reason: LONG_TEXT, validation_outcome: 'valid', affected_systems: ['task_creation'], created_at: '2026-10-01T00:00:00Z',
      }] });
    case '/api/workers':
      return json(workerSnapshot);
    case '/api/v1/operations/codex/shards':
      return json({ shards: [{
        queueName: `codex-shard-${LONG_ID}`, status: 'offline', hashModulo: 4, workerHostname: `codex-worker-${LONG_ID}`,
        volumeName: 'codex-auth-0', volumeStatus: 'missing', latestRunId: '00000000-0000-0000-0000-000000000010',
        latestRunStatus: 'failed', latestPreflightStatus: 'failed', latestPreflightMessage: 'Auth volume missing',
        latestPreflightCheckedAt: '2026-10-01T00:02:00Z',
      }] });
    case '/api/v1/operations/deployment/stacks/moonmind':
      return json({
        stack: 'moonmind', projectName: 'moonmind', buildId: '20260930.0900',
        currentImage: {
          requestedImage: `${DEPLOYMENT_REPOSITORY}:latest`, deployedImage: `${DEPLOYMENT_REPOSITORY}:latest`,
          repository: DEPLOYMENT_REPOSITORY, reference: 'latest', resolvedDigest: DIGEST, evidence: 'desired_state',
        },
        latestAction: failedUpdate,
        recentActions: [failedUpdate],
        controller: { installed: true, reachable: true, message: null },
        policy: {
          repository: DEPLOYMENT_REPOSITORY, defaultReference: 'latest', allowedReferences: ['latest'],
          recentTags: ['20261001.1200'], mutableReferences: ['latest'], allowedModes: ['changed_services', 'force_recreate'],
        },
      });
    case '/api/v1/operations/deployment/image-targets':
      return json({ stack: 'moonmind', repositories: [{
        repository: DEPLOYMENT_REPOSITORY, allowedReferences: ['latest'], recentTags: ['20261001.1200'],
        digestPinningRecommended: true, allowedModes: ['changed_services', 'force_recreate'],
      }] });
    default:
      return null;
  }
}

const settingsUiInfo = {
  ...uiInfo,
  settingsPermissions: [...uiInfo.settingsPermissions, 'settings.catalog.read', 'settings.audit.read', 'operations.read', 'operations.invoke'],
  workerPause: { get: '/api/workers', post: '/api/worker-action', shardHealth: '/api/v1/operations/codex/shards', pollIntervalMs: 60_000 },
};

interface ApiState {
  uiInfo: Record<string, unknown>;
  agentResponses: Array<() => Promise<Response> | Response>;
  agentRequests: number;
  profile: ReturnType<typeof savedProfile>;
  enrollmentProfiles: Array<Record<string, unknown>>;
  patchResponses: Array<(body: Record<string, unknown>) => Response>;
  patches: Array<Record<string, unknown>>;
  policySaves: Array<{ url: string; body: Record<string, unknown> }>;
}

let api: ApiState;
let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(async () => {
  await page.viewport(1280, 800);
  document.body.style.margin = '0';
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/omnigent/agents');
  api = {
    uiInfo,
    agentResponses: [],
    agentRequests: 0,
    profile: savedProfile(INITIAL_TIERS, 2),
    enrollmentProfiles: [],
    patchResponses: [],
    patches: [],
    policySaves: [],
  };
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? 'GET';
    if (url.pathname === '/api/ui/info') return json(api.uiInfo);
    if (url.pathname === '/api/omnigent/api/agents') {
      api.agentRequests += 1;
      const next = api.agentResponses.shift();
      return next ? next() : json(agentRows);
    }
    if (url.pathname === '/api/omnigent/agent-profiles') return json(executionConfigurations);
    if (url.pathname.startsWith(POLICIES_ENDPOINT)) return policyResponse(url, method, init);
    const settingsFixture = settingsResponse(url);
    if (settingsFixture) return settingsFixture;
    if (url.pathname === '/api/v1/secrets') return json({ items: [] });
    if (url.pathname === '/api/artifacts/collection') {
      const query = url.searchParams.get('q') ?? '';
      const items = url.searchParams.get('category') === 'artifacts'
        && evidenceRow.label.toLowerCase().includes(query.toLowerCase()) ? [evidenceRow] : [];
      return json({
        category: url.searchParams.get('category'),
        items,
        has_more: false,
        offset: 0,
        limit: 25,
        refreshed_at: '2026-10-01T00:01:00Z',
      });
    }
    if (url.pathname === '/api/v1/provider-profiles/creation-choices') return json(creationChoices);
    if (url.pathname.startsWith('/api/v1/provider-profiles') && url.pathname.endsWith('/capabilities')) {
      return json(tierCapabilities);
    }
    if (url.pathname === `/api/v1/provider-profiles/${encodeURIComponent(LONG_ID)}` && method === 'PATCH') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      api.patches.push(body);
      const respond = api.patchResponses.shift();
      if (respond) return respond(body);
      api.profile = { ...api.profile, ...body } as ReturnType<typeof savedProfile>;
      return json(api.profile);
    }
    if (url.pathname === '/api/v1/provider-profiles' && method === 'GET') {
      return json([api.profile, ...api.enrollmentProfiles]);
    }
    return json({ detail: `No synthetic fixture for ${method} ${url.pathname}` }, 404);
  });
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  document.documentElement.classList.remove('dark');
  document.documentElement.style.fontSize = '';
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(1280, 800);
});

function renderDashboard(): void {
  cleanupRender = renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />).unmount;
}

async function settle(): Promise<void> {
  await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 50)));
}

async function useLayout(width: number, height: number): Promise<void> {
  await page.viewport(width, height);
  await settle();
}

function describeElement(element: Element | null): string {
  if (!element) return 'nothing';
  const html = element as HTMLElement;
  const classes = typeof html.className === 'string' ? html.className.split(/\s+/).filter(Boolean).slice(0, 2) : [];
  const name = (html.getAttribute('aria-label') ?? html.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40);
  const rect = html.getBoundingClientRect();
  return `<${html.tagName.toLowerCase()}${classes.length ? `.${classes.join('.')}` : ''}> "${name}" [${rect.left.toFixed(0)}..${rect.right.toFixed(0)} x ${rect.height.toFixed(0)}]`;
}

function isRendered(element: HTMLElement): boolean {
  if (element.getClientRects().length === 0) return false;
  const style = getComputedStyle(element);
  if (style.visibility === 'hidden') return false;
  // Closed disclosures keep their content out of the layout's reach.
  const closed = element.closest('details:not([open])');
  if (closed && element.closest('summary')?.parentElement !== closed) return false;
  // Visually hidden headers of the stacked record cards are screen-reader only.
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    if (getComputedStyle(node).clipPath === 'inset(50%)') return false;
  }
  return true;
}

const CONTROL_SELECTOR = 'button, input:not([type="hidden"]), select, textarea, summary, a[href]';
const CONTENT_SELECTOR = `${CONTROL_SELECTOR}, h1, h2, h3, h4, p, small, strong, legend, label, td, th, li, [role="alert"], [role="status"]`;

/**
 * Lists every visible control or text element under `root` that escapes the
 * viewport, is cut off by a clipping ancestor, needs an ordinary local
 * horizontal scroll, is covered at its centre, or cannot take focus. Technical
 * `pre` blocks may scroll locally. Phone widths also require the 16px form
 * text baseline and 44px activation areas inside the page panel; links
 * inside prose are exempt from the size check.
 */
function layoutProblems(root: Element): string[] {
  const problems = new Set<string>();
  const viewportWidth = document.documentElement.clientWidth;
  const phone = window.innerWidth <= 720;
  for (const element of Array.from(root.querySelectorAll<HTMLElement>(CONTENT_SELECTOR))) {
    if (!isRendered(element) || element.closest('pre')) continue;
    const rect = element.getBoundingClientRect();
    if (rect.left < -TOLERANCE_PX || rect.right > viewportWidth + TOLERANCE_PX) {
      problems.add(`outside the ${viewportWidth}px viewport: ${describeElement(element)}`);
    }
    for (let ancestor = element.parentElement; ancestor && ancestor !== document.documentElement; ancestor = ancestor.parentElement) {
      const overflowX = getComputedStyle(ancestor).overflowX;
      if (overflowX === 'visible') continue;
      const bounds = ancestor.getBoundingClientRect();
      if (rect.left < bounds.left - TOLERANCE_PX || rect.right > bounds.right + TOLERANCE_PX) {
        problems.add(`clipped by ${describeElement(ancestor)} (${overflowX}): ${describeElement(element)}`);
        break;
      }
      if ((overflowX === 'auto' || overflowX === 'scroll') && ancestor.scrollWidth > ancestor.clientWidth + TOLERANCE_PX) {
        problems.add(`needs sideways scrolling in ${describeElement(ancestor)}: ${describeElement(element)}`);
        break;
      }
    }
    if (!element.matches(CONTROL_SELECTOR)) continue;
    const inPanel = Boolean(element.closest('.panel'));
    if (phone && inPanel && element.matches('input, select, textarea') && parseFloat(getComputedStyle(element).fontSize) < 16) {
      problems.add(`form text below 16px: ${describeElement(element)}`);
    }
    const inlineLink = element.matches('a') && getComputedStyle(element).display === 'inline';
    if (phone && inPanel && !inlineLink && !element.matches('input[type="radio"], input[type="checkbox"]') && rect.height < 44 - TOLERANCE_PX) {
      problems.add(`activation area below 44px: ${describeElement(element)}`);
    }
    element.scrollIntoView({ block: 'center', inline: 'nearest' });
    // A link that wraps onto two lines is hit-tested on its first line box:
    // the centre of its bounding box can fall between the line fragments.
    const centred = element.getClientRects()[0] ?? element.getBoundingClientRect();
    const hit = document.elementFromPoint(centred.left + centred.width / 2, centred.top + centred.height / 2);
    if (!hit || !(hit === element || element.contains(hit) || hit.contains(element) && hit.tagName === 'LABEL')) {
      problems.add(`covered by ${describeElement(hit)}: ${describeElement(element)}`);
    }
    if (!(element as HTMLButtonElement).disabled) {
      element.focus({ preventScroll: true });
      if (document.activeElement !== element) problems.add(`cannot take focus: ${describeElement(element)}`);
    }
  }
  window.scrollTo(0, 0);
  return Array.from(problems);
}

function expectUsableLayout(label: string): void {
  const problems = layoutProblems(document.querySelector('.dashboard-root')!);
  expect(problems, `${label}:\n${problems.join('\n')}`).toEqual([]);
}

function visible<T extends HTMLElement>(elements: T[]): T[] {
  return elements.filter((element) => isRendered(element));
}

async function openAgents(): Promise<void> {
  renderDashboard();
  await waitFor(() => expect(visible(screen.queryAllByText('Team codex')).length).toBe(1));
}

async function openProviders(): Promise<void> {
  window.history.replaceState({}, '', '/settings/providers-secrets');
  renderDashboard();
  await screen.findByRole('list', { name: 'Model and effort tiers' });
  await screen.findByText('Managed Secrets');
}

describe('Agents inventory in the dashboard shell (MoonMind#4559)', () => {
  it.each(LAYOUTS)('keeps records, filter and actions usable on a $name ($width x $height)', async ({ width, height }) => {
    await openAgents();
    await useLayout(width, height);
    // One representation per width: the record shows once, as a card on
    // phones and as a table row on wider screens.
    expect(visible(screen.getAllByText('Team codex'))).toHaveLength(1);
    expectUsableLayout(`Agents at ${width}x${height}`);
  });

  it('stays usable with 200% text in the dark theme', async () => {
    await openAgents();
    document.documentElement.classList.add('dark');
    document.documentElement.style.fontSize = '200%';
    await useLayout(390, 844);
    expectUsableLayout('Agents with 200% text, dark');
    await useLayout(1280, 800);
    expectUsableLayout('Agents desktop with 200% text, dark');
  });

  it('retries, filters and refreshes through the inventory API without remounting', async () => {
    await useLayout(320, 568);
    api.agentResponses.push(() => json({ detail: 'unavailable' }, 503));
    renderDashboard();
    const retry = await waitFor(() => {
      const buttons = visible(screen.queryAllByRole('button', { name: 'Try again' }));
      expect(buttons).toHaveLength(1);
      return buttons[0]!;
    });
    expect(visible(screen.getAllByText('Agents request failed (503)'))).toHaveLength(1);
    expectUsableLayout('Agents error state at 320px');

    await userEvent.click(retry);
    await waitFor(() => expect(visible(screen.queryAllByText('Team codex'))).toHaveLength(1));
    expect(api.agentRequests).toBe(2);

    const filter = screen.getByRole('searchbox');
    await userEvent.click(filter);
    await userEvent.keyboard('no-such-agent');
    // Typing filters in place: the same field keeps focus (a phone keyboard
    // stays open) and the inventory is not requested again.
    expect(screen.getByRole('searchbox')).toBe(filter);
    expect(document.activeElement).toBe(filter);
    expect(window.location.search).toContain('omnigent_agents_q=no-such-agent');
    expect(visible(screen.getAllByText('No agents match this filter.'))).toHaveLength(1);
    expectUsableLayout('Agents filter-empty state at 320px');
    await userEvent.clear(filter);
    await waitFor(() => expect(visible(screen.queryAllByText('Team codex'))).toHaveLength(1));
    expect(api.agentRequests).toBe(2);

    let release: () => void = () => undefined;
    api.agentResponses.push(() => new Promise<Response>((resolve) => {
      release = () => resolve(json(agentRows));
    }));
    const refresh = screen.getByRole('button', { name: 'Refresh' });
    await userEvent.click(refresh);
    await waitFor(() => expect((refresh as HTMLButtonElement).disabled).toBe(true));
    release();
    await waitFor(() => expect((refresh as HTMLButtonElement).disabled).toBe(false));
    await settle();
    expect(api.agentRequests).toBe(3);
  });
});

describe('Providers & Secrets in the dashboard shell (MoonMind#4559)', () => {
  it.each(LAYOUTS)('keeps saved records, the profile form and secrets usable on a $name ($width x $height)', async ({ width, height }) => {
    await openProviders();
    await useLayout(width, height);
    expectUsableLayout(`Providers & Secrets at ${width}x${height}`);
    if (width <= 720) {
      // Stacked records: the label sits above its value and actions use the
      // full record width instead of a squeezed label column.
      const record = document.querySelector<HTMLElement>('.provider-profiles-table tbody tr')!;
      const actions = document.querySelector<HTMLElement>('.provider-profiles-table td[data-label="Actions"]')!;
      expect(actions.getBoundingClientRect().width).toBeGreaterThanOrEqual(record.getBoundingClientRect().width - 2);
      // Tier names stay in the legend; tier actions have their own area.
      expect(document.querySelectorAll('.provider-tier-fieldset > legend button')).toHaveLength(0);
    }
  });

  it('stays usable with 200% text in the dark theme', async () => {
    await openProviders();
    document.documentElement.classList.add('dark');
    document.documentElement.style.fontSize = '200%';
    await useLayout(390, 844);
    expectUsableLayout('Providers & Secrets with 200% text, dark');
  });

  it('keeps the tier draft through resize, confirmation, validation and save', async () => {
    await openProviders();
    await useLayout(390, 844);
    const edit = visible(screen.getAllByRole('button')).filter((button) => button.textContent?.trim() === 'Edit');
    expect(edit).toHaveLength(1);
    await userEvent.click(edit[0]!);
    await screen.findByRole('heading', { name: `Edit Profile: ${LONG_ID}` });

    const firstLabel = screen.getByLabelText('Tier 1 label') as HTMLInputElement;
    await userEvent.click(firstLabel);
    await userEvent.keyboard('{End} (phone)');
    const editedLabel = `${INITIAL_TIERS[0]!.label} (phone)`;
    // Crossing the desktop/phone breakpoint keeps the same draft.
    await useLayout(1280, 800);
    await useLayout(320, 568);
    expect((screen.getByLabelText('Tier 1 label') as HTMLInputElement).value).toBe(editedLabel);

    // Removing the default middle tier asks for confirmation. Escape dismisses
    // it without changes and returns focus to the initiating action.
    await useLayout(844, 390);
    const removeTier2 = screen.getByRole('button', { name: 'Remove Tier 2' });
    await userEvent.click(removeTier2);
    const dialog = await screen.findByRole('dialog', { name: 'Remove Tier 2?' });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    for (const name of ['Cancel', 'Remove and renumber']) {
      const action = within(dialog).getByRole('button', { name });
      action.scrollIntoView({ block: 'nearest' });
      const rect = action.getBoundingClientRect();
      expect(rect.top, `${name} reachable at 844x390`).toBeGreaterThanOrEqual(-TOLERANCE_PX);
      expect(rect.bottom, `${name} reachable at 844x390`).toBeLessThanOrEqual(window.innerHeight + TOLERANCE_PX);
      expect(rect.right, `${name} inside 844px`).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
    }
    const title = within(dialog).getByRole('heading', { name: 'Remove Tier 2?' });
    title.scrollIntoView({ block: 'nearest' });
    expect(title.getBoundingClientRect().top, 'dialog title reachable at 844x390').toBeGreaterThanOrEqual(-TOLERANCE_PX);
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove Tier 2?' })).toBeNull());
    expect(document.activeElement).toBe(removeTier2);
    expect(screen.getAllByRole('button', { name: /^Remove Tier \d$/ })).toHaveLength(3);

    await userEvent.click(removeTier2);
    const confirm = within(await screen.findByRole('dialog', { name: 'Remove Tier 2?' })).getByRole('button', {
      name: 'Remove and renumber',
    });
    await userEvent.click(confirm);
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^Remove Tier \d$/ })).toHaveLength(2));

    // A server validation error aimed at a collapsed advanced field reveals
    // and focuses that field and keeps the whole draft.
    await useLayout(320, 568);
    api.patchResponses.push(() =>
      json({ detail: { code: 'invalid_cooldown', field: 'cooldown_after_429_seconds', message: 'Cooldown must be positive.' } }, 422),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));
    const cooldown = await screen.findByLabelText('Cooldown after 429 (seconds)');
    await waitFor(() => expect(document.activeElement).toBe(cooldown));
    expect((screen.getByLabelText('Tier 1 label') as HTMLInputElement).value).toBe(editedLabel);
    expect(screen.getAllByRole('button', { name: /^Remove Tier \d$/ })).toHaveLength(2);
    expectUsableLayout('Provider edit form with revealed advanced field at 320px');

    await userEvent.click(screen.getByRole('button', { name: 'Update provider profile' }));
    await waitFor(() => expect(api.patches).toHaveLength(2));
    // The canonical tier payload: order kept, 1-based default, no client ids.
    const saved = api.patches[1]!;
    expect(saved.model_tiers).toEqual([
      { ...INITIAL_TIERS[0]!, label: editedLabel, parameters: {}, annotations: {} },
      { ...INITIAL_TIERS[2]!, parameters: {}, annotations: {} },
    ]);
    expect(saved.default_model_tier).toBe(2);
    expect(JSON.stringify(saved)).not.toContain('clientId');
    await waitFor(() =>
      expect(screen.getByLabelText(`${LONG_ID} model tier mapping`).textContent).toContain(
        `Tier 2 default · ${INITIAL_TIERS[2]!.label}`,
      ),
    );
    await settle();
    expect(api.patches).toHaveLength(2);
  });
});

const OVERLAY_LAYOUTS = [
  { name: 'narrow phone', width: 320, height: 568 },
  { name: 'short landscape phone', width: 844, height: 390 },
] as const;

describe('Policies inventory detail and editor in the dashboard shell (MoonMind#4559)', () => {
  const POLICY_LAYOUTS = [LAYOUTS[0], LAYOUTS[2], LAYOUTS[4]] as const;

  async function inspectPolicy(): Promise<HTMLElement> {
    window.history.replaceState({}, '', '/omnigent/policies');
    renderDashboard();
    const inspect = await waitFor(() => {
      const buttons = visible(screen.queryAllByRole('button', { name: 'Inspect' }));
      expect(buttons).toHaveLength(1);
      return buttons[0]!;
    });
    await userEvent.click(inspect);
    const detail = screen.getByRole('region', { name: 'Immutable policy version' });
    await within(detail).findByRole('button', { name: `${POLICY_ID}@1 · superseded` });
    await within(detail).findByText('Dependent host profiles: 1');
    return detail;
  }

  it.each(POLICY_LAYOUTS)('keeps version history, diagnostics, lifecycle actions, diff and editor usable on a $name ($width x $height)', async ({ width, height }) => {
    await useLayout(width, height);
    const detail = await inspectPolicy();
    expect(within(detail).getAllByRole('alert').map((alert) => alert.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining('host_profile_missing')]),
    );
    expect((within(detail).getByRole('button', { name: `Disable ${POLICY_ID}@3` }) as HTMLButtonElement).disabled).toBe(true);
    expectUsableLayout(`Policy detail at ${width}x${height}`);

    // An older version shows its normalized diff and the rollback action.
    await userEvent.click(within(detail).getByRole('button', { name: `${POLICY_ID}@1 · superseded` }));
    await within(detail).findByRole('heading', { name: 'Normalized diff to current default' });
    expect(within(detail).getByRole('button', { name: `Roll back default to ${POLICY_ID}@1` })).toBeTruthy();
    expectUsableLayout(`Policy diff and rollback at ${width}x${height}`);

    await userEvent.click(within(detail).getByRole('button', { name: 'Edit as new version' }));
    const editor = await screen.findByRole('heading', { name: 'Edit as immutable new version' });
    const document = screen.getByLabelText('Complete policy document (JSON)') as HTMLTextAreaElement;
    expect(JSON.parse(document.value)).toMatchObject({ revision: 1 });
    expectUsableLayout(`Policy editor at ${width}x${height}`);

    await userEvent.click(screen.getByRole('button', { name: 'Validate and save draft' }));
    await waitFor(() => expect(api.policySaves).toHaveLength(1));
    expect(api.policySaves[0]).toEqual({
      url: `${POLICIES_ENDPOINT}/${encodeURIComponent(POLICY_ID)}/versions`,
      body: { expectedParentRef: `${POLICY_ID}@3`, document: { ...POLICY_DOCUMENT, revision: 1 } },
    });
    await waitFor(() => expect(editor.isConnected).toBe(false));
  });
});

describe('Provider enrollment drawers in the dashboard shell (MoonMind#4559)', () => {
  const DRAWERS = [
    { name: 'Anthropic', profileId: CLAUDE_PROFILE_ID, opener: 'Use Anthropic API key', credential: 'Anthropic API key' },
    { name: 'OpenCode', profileId: OPENCODE_PROFILE_ID, opener: 'Use OpenCode API key', credential: 'OpenCode API key' },
  ] as const;

  /** Scroll containers inside the drawer other than the drawer itself. */
  function nestedScrollRegions(drawer: HTMLElement): string[] {
    return Array.from(drawer.querySelectorAll<HTMLElement>('*'))
      .filter((element) => {
        const style = getComputedStyle(element);
        return /auto|scroll/.test(`${style.overflowY} ${style.overflowX}`)
          && (element.scrollHeight > element.clientHeight + TOLERANCE_PX || element.scrollWidth > element.clientWidth + TOLERANCE_PX);
      })
      .map(describeElement);
  }

  function expectReachable(drawer: HTMLElement, name: string, label: string): void {
    const action = within(drawer).getByRole('button', { name });
    action.scrollIntoView({ block: 'nearest' });
    const rect = action.getBoundingClientRect();
    expect(rect.top, `${label}: ${name} top`).toBeGreaterThanOrEqual(-TOLERANCE_PX);
    expect(rect.bottom, `${label}: ${name} bottom`).toBeLessThanOrEqual(window.innerHeight + TOLERANCE_PX);
    expect(rect.left, `${label}: ${name} left`).toBeGreaterThanOrEqual(-TOLERANCE_PX);
    expect(rect.right, `${label}: ${name} right`).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  }

  for (const drawerCase of DRAWERS) {
    it.each(OVERLAY_LAYOUTS)(`keeps the ${drawerCase.name} drawer reachable, dismissible and the profile draft intact on a $name ($width x $height)`, async ({ width, height }) => {
      api.enrollmentProfiles = enrollmentProfiles;
      await openProviders();
      await useLayout(width, height);
      const label = `${drawerCase.name} drawer at ${width}x${height}`;

      // An unsaved edit in the profile form survives the enrollment detour.
      const edit = visible(screen.getAllByRole('button')).filter((button) => button.textContent?.trim() === 'Edit');
      await userEvent.click(edit[0]!);
      await screen.findByRole('heading', { name: `Edit Profile: ${LONG_ID}` });
      await userEvent.click(screen.getByLabelText('Tier 1 label'));
      await userEvent.keyboard('{End} (draft)');
      const editedLabel = `${INITIAL_TIERS[0]!.label} (draft)`;

      const opener = screen.getByRole('button', { name: `${drawerCase.opener} ${drawerCase.profileId}` });
      await userEvent.click(opener);
      const drawer = await screen.findByRole('dialog', { name: `${drawerCase.credential} enrollment for ${drawerCase.profileId}` });
      await waitFor(() => expect(drawer.contains(document.activeElement)).toBe(true));
      await settle();

      expect(nestedScrollRegions(drawer), `${label}: nested scroll regions`).toEqual([]);
      expect(layoutProblems(drawer), `${label}:\n${layoutProblems(drawer).join('\n')}`).toEqual([]);
      within(drawer).getByRole('heading', { name: `${drawerCase.credential} enrollment for ${drawerCase.profileId}` }).scrollIntoView({ block: 'nearest' });
      expect(within(drawer).getByRole('heading', { level: 4 }).getBoundingClientRect().top, `${label}: title reachable`).toBeGreaterThanOrEqual(-TOLERANCE_PX);
      expectReachable(drawer, 'Cancel API key enrollment', label);

      // The paste step keeps its field and footer save action reachable in
      // the drawer's one scroll container.
      await userEvent.click(within(drawer).getByRole('button', { name: 'Continue to API key paste' }));
      await within(drawer).findByLabelText(drawerCase.credential);
      expect(nestedScrollRegions(drawer), `${label} paste step: nested scroll regions`).toEqual([]);
      const pasteProblems = layoutProblems(drawer);
      expect(pasteProblems, `${label} paste step:\n${pasteProblems.join('\n')}`).toEqual([]);
      expectReachable(drawer, `Validate and save ${drawerCase.credential}`, label);
      expectReachable(drawer, 'Cancel API key enrollment', label);

      // Escape dismisses the drawer and focus returns to the opening action.
      await userEvent.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(document.activeElement, `${label}: focus returns to ${drawerCase.opener}`).toBe(opener);
      expect((screen.getByLabelText('Tier 1 label') as HTMLInputElement).value).toBe(editedLabel);
      expect(api.patches).toHaveLength(0);
    });
  }
});

describe('Instance and Operations settings in the dashboard shell (MoonMind#4559)', () => {
  const SETTINGS_LAYOUTS = [LAYOUTS[0], LAYOUTS[1], LAYOUTS[2]] as const;

  it.each(SETTINGS_LAYOUTS)('keeps Instance settings controls, diagnostics and audit usable on a $name ($width x $height)', async ({ width, height }) => {
    api.uiInfo = settingsUiInfo;
    window.history.replaceState({}, '', '/settings/instance');
    renderDashboard();
    await screen.findByText('Skills Canary Percent');
    await screen.findByText('The stored override is outside the allowed range.');
    await screen.findByRole('button', { name: 'Reset dashboard preferences' });
    await useLayout(width, height);
    expectUsableLayout(`Instance settings at ${width}x${height}`);
  });

  it.each(SETTINGS_LAYOUTS)('keeps Operations worker, shard and update cards usable on a $name ($width x $height)', async ({ width, height }) => {
    api.uiInfo = settingsUiInfo;
    window.history.replaceState({}, '', '/settings/operations');
    renderDashboard();
    const workers = await screen.findByRole('region', { name: /worker operations/i });
    await within(workers).findByText(`codex-worker-${LONG_ID}`);
    await within(workers).findByText('Pause Workers');
    const update = await screen.findByRole('region', { name: /moonmind update/i });
    await within(update).findByLabelText(/update to/i);
    await within(update).findByRole('button', { name: /retry/i });
    await useLayout(width, height);
    expectUsableLayout(`Operations settings at ${width}x${height}`);
  });
});

describe('Artifacts & Observability in the dashboard shell (MoonMind#4559)', () => {
  it.each(LAYOUTS)('keeps evidence records and their actions usable on a $name ($width x $height)', async ({ width, height }) => {
    window.history.replaceState({}, '', '/artifacts');
    renderDashboard();
    await waitFor(() => expect(visible(screen.queryAllByRole('link', { name: 'Download' }))).toHaveLength(1));
    await useLayout(width, height);
    expectUsableLayout(`Artifacts at ${width}x${height}`);
  });

  it('keeps every typed character in the filter', async () => {
    await useLayout(320, 568);
    window.history.replaceState({}, '', '/artifacts');
    renderDashboard();
    await waitFor(() => expect(visible(screen.queryAllByRole('link', { name: 'Download' }))).toHaveLength(1));
    const filter = screen.getByLabelText('Filter artifacts') as HTMLInputElement;
    await userEvent.click(filter);
    await userEvent.keyboard('final');
    expect(screen.getByLabelText('Filter artifacts')).toBe(filter);
    expect(filter.value).toBe('final');
    await waitFor(() => expect(window.location.search).toContain('artifacts_q=final'));
    expect(visible(screen.getAllByRole('link', { name: 'Download' }))).toHaveLength(1);
  });
});

interface RecordRow {
  id: string;
  name: string;
}

const recordColumns: Column<RecordRow>[] = [
  { key: 'name', header: 'Name' },
  { key: 'id', header: 'Identity' },
];

const recordRows: RecordRow[] = [{ id: LONG_ID, name: 'Team codex' }];

describe('DataTable responsive states (MoonMind#4559)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('exposes loading, empty, and error states in the mobile card fallback', async () => {
    await page.viewport(320, 800);
    const states: Array<{ props: Partial<React.ComponentProps<typeof DataTable<RecordRow>>>; text: string }> = [
      { props: { isLoading: true, loadingMessage: 'Loading records…' }, text: 'Loading records…' },
      { props: { data: [], emptyMessage: 'No records yet.' }, text: 'No records yet.' },
      { props: { isError: true, errorMessage: 'Records failed to load.' }, text: 'Records failed to load.' },
    ];
    for (const state of states) {
      const view = render(
        <DataTable
          data={recordRows}
          columns={recordColumns}
          getRowKey={(row) => row.id}
          ariaLabel="Records"
          responsive
          {...state.props}
        />,
      );
      const cards = document.querySelector<HTMLElement>('.data-table-cards')!;
      expect(getComputedStyle(cards).display, `${state.text}: cards hidden at 320px`).not.toBe('none');
      expect(within(cards).getByText(state.text), `${state.text}: missing from mobile cards`).toBeTruthy();
      const table = document.querySelector<HTMLElement>('.data-table')!;
      expect(getComputedStyle(table).display, `${state.text}: table still shown at 320px`).toBe('none');
      view.unmount();
      document.body.innerHTML = '';
    }
  });

  it('shows rows as full-width stacked cards without sideways scrolling at 320px', async () => {
    await page.viewport(320, 800);
    render(
      <DataTable
        data={recordRows}
        columns={recordColumns}
        getRowKey={(row) => row.id}
        ariaLabel="Records"
        responsive
        rowActions={() => <button type="button">Inspect Team codex record</button>}
      />,
    );
    expect(rtlScreen.getAllByRole('button', { name: 'Inspect Team codex record' }).length).toBeGreaterThanOrEqual(1);
    const card = document.querySelector<HTMLElement>('.data-table-card')!;
    const cards = document.querySelector<HTMLElement>('.data-table-cards')!;
    expect(card.getBoundingClientRect().width).toBeLessThanOrEqual(cards.getBoundingClientRect().width + TOLERANCE_PX);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + TOLERANCE_PX);
  });
});

describe('overlay sizing selectors (MoonMind#4559)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('leaves dialog backdrops, drawers, and queue-dialog panels at their own sizing', async () => {
    // The phone overlay sizing rule applies to confirmation dialog panels
    // only. Fixed inset-0 backdrops that carry role="dialog" keep
    // full-viewport coverage, the Create-page preset dialog panel keeps its
    // own sizing, and full-height enrollment drawers keep their layout.
    const host = document.createElement('div');
    host.className = 'dashboard-content';
    host.innerHTML = `
      <div class="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true" aria-label="Remove tier backdrop">
        <div class="w-full max-w-lg"><h2>Remove Tier 2?</h2></div>
      </div>
      <section class="queue-dialog-panel stack queue-preset-dialog" role="dialog" aria-modal="true" aria-label="Save preset">
        <h3>Save preset</h3>
      </section>
      <div class="fixed inset-0 z-50 flex justify-end">
        <div class="h-full w-full max-w-2xl" role="dialog" aria-modal="true" aria-label="Enrollment drawer"><h2>Enrollment</h2></div>
      </div>
      <div role="dialog" aria-modal="true" aria-label="Remove profile confirmation"><h2>Remove profile?</h2></div>
    `;
    document.body.appendChild(host);
    await page.viewport(1280, 800);
    await settle();
    const backdrop = host.querySelector<HTMLElement>('[aria-label="Remove tier backdrop"]')!;
    expect(getComputedStyle(backdrop).maxWidth, 'backdrop keeps viewport coverage').toBe('none');
    const presetPanel = host.querySelector<HTMLElement>('[aria-label="Save preset"]')!;
    expect(getComputedStyle(presetPanel).maxWidth, 'preset dialog keeps its own sizing').toBe('none');
    expect(presetPanel.getBoundingClientRect().width, 'preset dialog keeps its 440px width').toBeCloseTo(440, 0);
    const confirmation = host.querySelector<HTMLElement>('[aria-label="Remove profile confirmation"]')!;
    expect(getComputedStyle(confirmation).maxWidth, 'confirmation stays bounded').not.toBe('none');
    await page.viewport(320, 568);
    await settle();
    expect(getComputedStyle(backdrop).maxWidth, 'backdrop keeps coverage on mobile').toBe('none');
    const drawer = host.querySelector<HTMLElement>('[aria-label="Enrollment drawer"]')!;
    expect(getComputedStyle(drawer).maxHeight, 'drawer keeps its full-height layout').toBe('none');
  });
});
