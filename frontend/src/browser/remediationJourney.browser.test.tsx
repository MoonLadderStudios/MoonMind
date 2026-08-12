import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Controlling real-browser journey for MoonLadderStudios/MoonMind#3623. This
// mounts the production dashboard router and entrypoints, then follows the
// same source Detail -> normal Create -> remediation Detail route transitions
// that an operator uses. API reads are bounded canonical projections; the
// ordinary POST /api/executions request is captured as the authority handoff.

const DESKTOP = { width: 1280, height: 900 } as const;
const MOBILE = { width: 390, height: 844 } as const;

const sourceExecution = {
  taskId: 'source-workflow',
  workflowId: 'source-workflow',
  namespace: 'default',
  temporalRunId: 'source-run',
  runId: 'source-run',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  entry: 'user_workflow',
  title: 'Failed source workflow',
  summary: 'The test step failed and needs bounded repair.',
  taskInstructions: 'Implement and verify the source change.',
  status: 'failed',
  state: 'failed',
  rawState: 'failed',
  temporalStatus: 'failed',
  attentionRequired: true,
  repository: 'MoonLadderStudios/MoonMind',
  targetRuntime: 'omnigent',
  model: 'gpt-5.6-codex',
  effort: 'high',
  profileId: 'oauth-1',
  publishMode: 'pr',
  startingBranch: 'main',
  createdAt: '2026-08-12T00:00:00Z',
  updatedAt: '2026-08-12T00:05:00Z',
  actions: { canSetTitle: true },
  agentProfile: {
    profileId: 'team-codex',
    version: 1,
    providerProfileRef: 'oauth-1',
  },
  inputParameters: {
    omnigent: {
      executionTargetRef: 'omnigent-codex-default',
      launchPolicyRef: 'on-demand-v1',
    },
    agentProfile: { profileId: 'team-codex', version: 1 },
    agentProfileSnapshot: {
      profileId: 'team-codex',
      version: 1,
      providerProfileRef: 'oauth-1',
      executionProfileRef: 'omnigent-codex-default',
      launchPolicyRef: 'on-demand-v1',
    },
    workflow: {
      git: { startingBranch: 'main', branch: 'main' },
    },
  },
  checkpoints: [
    {
      logicalStepId: 'run-tests',
      checkpointRef: 'artifact://checkpoint/run-tests',
      checkpointDigest: 'sha256:run-tests',
      attempt: 2,
    },
  ],
};

const createdExecution = {
  taskId: 'mm:remediation-created',
  workflowId: 'mm:remediation-created',
  namespace: 'default',
  temporalRunId: 'remediation-run',
  runId: 'remediation-run',
  source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow',
  entry: 'user_workflow',
  title: 'Created remediation workflow',
  summary: 'Repair is running against the pinned source run.',
  status: 'running',
  state: 'executing',
  rawState: 'executing',
  temporalStatus: 'running',
  repository: 'MoonLadderStudios/MoonMind',
  targetRuntime: 'omnigent',
  model: 'gpt-5.6-codex',
  effort: 'high',
  profileId: 'oauth-1',
  createdAt: '2026-08-12T00:06:00Z',
  updatedAt: '2026-08-12T00:07:00Z',
  actions: { canSetTitle: true },
};

const createdRelationship = {
  remediationWorkflowId: 'mm:remediation-created',
  remediationRunId: 'remediation-run',
  targetWorkflowId: 'source-workflow',
  targetRunId: 'source-run',
  mode: 'snapshot_then_follow',
  authorityMode: 'approval_gated',
  status: 'executing',
  deliveryStatus: 'pending',
  verificationOutcome: null,
  contextArtifactRef: 'art_remediation_context',
  selectedSteps: ['run-tests'],
  currentTargetState: 'failed',
  authoredContract: {
    instructions: 'Repair the routed browser failure with bounded evidence.',
    runtime: { mode: 'omnigent' },
    remediation: { authorityMode: 'approval_gated' },
  },
  selectedStepEvidence: [
    {
      logicalStepId: 'run-tests',
      checkpointRef: 'artifact://checkpoint/run-tests',
    },
  ],
  contextEvidenceAvailability: [
    { class: 'step_ledger', status: 'available', bounded: true },
  ],
  contextBoundedness: { rawLogBodiesIncluded: false, maxTailLines: 2000 },
  lifecycleSummary: {
    repair: { repairOutcome: 'running' },
    prevention: { status: 'pending' },
    cleanup: 'pending',
  },
  checkpointBranches: [
    {
      workflowId: 'source-workflow',
      branchId: 'cbr-routed-remediation',
      branchTurnId: 'turn-routed-1',
      branchState: 'active',
      gitBaseBranch: 'main',
      gitWorkBranch: 'remediation/routed-browser-edited',
      checkpointRef: 'artifact://checkpoint/run-tests',
      turns: [
        {
          branchTurnId: 'turn-routed-1',
          status: 'running',
          createdStepExecutionId: 'repair:execution:1',
          runtimeAgentRunId: 'agent-run-routed-1',
          providerSessionId: 'provider-session-routed-1',
          instructionRef: 'artifact://instructions/routed-1',
          instructionDigest: 'sha256:instructions-routed-1',
          sourceCheckpointRef: 'artifact://checkpoint/run-tests',
          startedAt: '2026-08-12T00:06:30Z',
          createdAt: '2026-08-12T00:06:20Z',
          updatedAt: '2026-08-12T00:06:30Z',
          outputArtifacts: { result: 'art_routed_turn_output' },
          comparisonArtifacts: {},
        },
      ],
    },
  ],
  approvalState: {
    requestId: 'approval-routed-1',
    actionKind: 'checkpoint_branch.create',
    riskTier: 'medium',
    decision: 'pending',
    canDecide: false,
  },
  createdAt: '2026-08-12T00:06:00Z',
  updatedAt: '2026-08-12T00:07:00Z',
};

const readyOmnigentCatalog = {
  schemaVersion: 'moonmind.omnigent-codex-readiness.v2',
  runtimeId: 'omnigent',
  displayName: 'Codex via Omnigent',
  available: true,
  defaultExecutionProfileRef: 'omnigent-codex-default',
  executionProfiles: [
    {
      ref: 'omnigent-codex-default',
      displayName: 'Codex default',
      available: true,
      providerRuntime: 'codex_cli',
      launchPolicies: [
        {
          ref: 'on-demand-v1',
          displayName: 'On-demand Docker',
          hostMode: 'on_demand_docker',
          isDefault: true,
        },
      ],
      gateReasons: [],
    },
  ],
  eligibleProviderProfiles: [
    {
      profileId: 'oauth-1',
      label: 'Codex OAuth',
      providerId: 'openai',
      runtimeId: 'codex_cli',
      busy: false,
      queueWhenBusy: true,
    },
  ],
  ineligibleProviderProfiles: [],
  hostModes: ['on_demand_docker'],
  gateReasons: [],
};

const uiInfo = {
  app: 'moonmind',
  buildId: 'remediation-browser-test',
  apiBase: '/api',
  features: {
    workflowList: true,
    workflowActions: true,
    workflowLiveUpdates: false,
    artifacts: true,
    schedules: true,
    skills: true,
    settings: true,
    manifests: true,
    remediationCollection: true,
    omnigentAgents: true,
    omnigentPolicies: true,
  },
  limits: {},
  endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: {
      temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' },
      github: { branches: '/api/github/branches?repository={repository}' },
    },
    system: {
      defaultRepository: 'MoonLadderStudios/MoonMind',
      defaultAgentRuntime: 'codex_cli',
      defaultTaskModel: 'gpt-5.6-codex',
      defaultTaskEffort: 'high',
      defaultPublishMode: 'pr',
      supportedAgentRuntimes: ['omnigent', 'codex_cli', 'claude_code'],
      providerProfiles: { list: '/api/v1/provider-profiles' },
      presetCatalog: { enabled: true, list: '/api/presets' },
      omnigentExecutionCatalog: {
        profiles: [
          {
            ref: 'omnigent-codex-default',
            displayName: 'Codex default',
            defaultPolicyRef: 'on-demand-v1',
            providerRuntime: 'codex_cli',
          },
        ],
        policies: [
          {
            ref: 'on-demand-v1',
            displayName: 'On-demand Docker',
            hostMode: 'on_demand_docker',
          },
        ],
      },
    },
    features: {
      temporalDashboard: {
        actionsEnabled: true,
        listEnabled: false,
        workspaceShellEnabled: false,
      },
    },
  },
  settingsPermissions: [],
};

const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let createRequests: Array<Record<string, unknown>>;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/source-workflow/evidence?source=temporal');
  createRequests = [];
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
        createRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return jsonResponse({
          workflowId: 'mm:remediation-created',
          runId: 'remediation-run',
          redirectPath: '/workflows/mm%3Aremediation-created/evidence?source=temporal',
        }, 201);
      }
      if (url.includes('mm%3Aremediation-created/remediations?direction=outbound')) {
        return jsonResponse({ direction: 'outbound', items: [createdRelationship] });
      }
      if (url.includes('mm%3Aremediation-created/remediations?direction=inbound')) {
        return jsonResponse({ direction: 'inbound', items: [] });
      }
      if (url.includes('/executions/source-workflow/remediations?')) {
        const direction = url.includes('direction=outbound') ? 'outbound' : 'inbound';
        return jsonResponse({ direction, items: [] });
      }
      if (url.includes('/executions/mm%3Aremediation-created')) {
        if (url.includes('/checkpoint-branches')) return jsonResponse({ items: [] });
        if (url.includes('/artifacts')) return jsonResponse({ artifacts: [] });
        return jsonResponse(createdExecution);
      }
      if (url.includes('/executions/source-workflow')) {
        if (url.includes('/checkpoint-branches')) return jsonResponse({ items: [] });
        if (url.includes('/artifacts')) return jsonResponse({ artifacts: [] });
        return jsonResponse(sourceExecution);
      }
      if (url.startsWith('/api/omnigent/codex-catalog-readiness')) {
        return jsonResponse(readyOmnigentCatalog);
      }
      if (url === '/api/omnigent/agent-profiles') {
        return jsonResponse([
          {
            profileId: 'team-codex',
            displayName: 'Team Codex',
            state: 'active',
            activeVersion: 1,
            defaultForRuntime: true,
            versions: [
              {
                version: 1,
                digest: `sha256:${'a'.repeat(64)}`,
                document: {
                  execution: {
                    defaultExecutionProfileRef: 'omnigent-codex-default',
                    allowedLaunchPolicyRefs: ['on-demand-v1'],
                  },
                  policyRef: 'on-demand-v1',
                },
                validationResult: { ready: true },
              },
            ],
          },
        ]);
      }
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([
          {
            profile_id: 'oauth-1',
            account_label: 'Codex OAuth',
            provider_id: 'openai',
            default_model: 'gpt-5.6-codex',
            default_effort: 'high',
            enabled: true,
            launch_ready: true,
          },
        ]);
      }
      if (url.startsWith('/api/github/branches')) {
        return jsonResponse({
          items: [{ value: 'main', label: 'main', source: 'github' }],
          defaultBranch: 'main',
          error: null,
        });
      }
      if (url.startsWith('/api/workflows/skills')) return jsonResponse({ items: {} });
      if (url.startsWith('/api/presets')) return jsonResponse({ items: [] });
      if (url.startsWith('/api/artifacts')) return jsonResponse({ artifacts: [] });
      if (url.startsWith('/api/executions')) return jsonResponse({ items: [] });
      return jsonResponse({});
    },
  );
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('routed remediation operator journey', () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`authors through normal Create and inspects the created lifecycle at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      expect(await screen.findByText('Failed source workflow', {}, { timeout: 10_000 })).toBeTruthy();
      const actionTrigger = screen.getByRole('button', { name: 'Workflow actions' });
      expect(actionTrigger.getAttribute('aria-haspopup')).toBe('menu');
      actionTrigger.focus();
      expect(document.activeElement).toBe(actionTrigger);
      await userEvent.keyboard('{Enter}');
      const actionMenu = screen.getByRole('menu', { name: 'Workflow actions' });
      const remediate = await within(actionMenu).findByRole('menuitem', { name: 'Remediate' });
      remediate.focus();
      await userEvent.keyboard('{Enter}');

      await waitFor(() => expect(window.location.pathname).toBe('/workflows/new'));
      expect(await screen.findByText('Remediation Draft', {}, { timeout: 10_000 })).toBeTruthy();
      const pinned = screen.getByLabelText('Pinned target identity');
      const editable = screen.getByLabelText('Editable repair intent');
      expect(pinned).toBeTruthy();
      expect(editable).toBeTruthy();
      expect((screen.getByLabelText('Target workflow') as HTMLInputElement).value).toBe('source-workflow');
      expect((screen.getByLabelText('Target workflow') as HTMLInputElement).readOnly).toBe(true);
      expect((screen.getByLabelText('Pinned run') as HTMLInputElement).value).toBe('source-run');
      expect((screen.getByLabelText('Pinned run') as HTMLInputElement).readOnly).toBe(true);
      expect((screen.getByLabelText('Selected evidence') as HTMLInputElement).value).toContain('run-tests');

      const startingBranch = screen.getByLabelText('Starting branch') as HTMLInputElement;
      const workBranch = screen.getByLabelText('Checkpoint work branch') as HTMLInputElement;
      startingBranch.focus();
      await userEvent.tab();
      expect(document.activeElement).toBe(workBranch);
      await userEvent.fill(workBranch, 'remediation/routed-browser-edited');
      await userEvent.fill(
        screen.getByLabelText('Instructions'),
        'Repair the routed browser failure with bounded evidence.',
      );
      await userEvent.fill(screen.getByLabelText('Action policy'), 'operator_review_only');
      await userEvent.selectOptions(screen.getByLabelText('Publish Mode'), 'branch');

      const createButton = screen.getByRole('button', { name: 'Start Workflow' });
      await waitFor(() => expect((createButton as HTMLButtonElement).disabled).toBe(false), {
        timeout: 10_000,
      });
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
      await userEvent.click(createButton);

      await waitFor(
        () => expect(window.location.pathname).toBe('/workflows/mm%3Aremediation-created/evidence'),
        { timeout: 10_000 },
      );
      expect(createRequests).toHaveLength(1);
      expect(createRequests[0]).toMatchObject({
        payload: {
          targetRuntime: 'omnigent',
          publishMode: 'branch',
          task: {
            instructions: 'Repair the routed browser failure with bounded evidence.',
            remediation: {
              target: {
                workflowId: 'source-workflow',
                runId: 'source-run',
              },
              authorityMode: 'approval_gated',
              actionPolicyRef: 'operator_review_only',
              checkpointBranchPolicy: {
                gitWorkBranch: 'remediation/routed-browser-edited',
              },
            },
          },
        },
      });

      expect(await screen.findByText('Created remediation workflow', {}, { timeout: 10_000 })).toBeTruthy();
      expect(await screen.findByRole('heading', { name: 'Remediation Target' })).toBeTruthy();
      expect(screen.getAllByText('source-workflow').length).toBeGreaterThan(0);
      expect(screen.getByText('Authored remediation contract')).toBeTruthy();
      const turnList = screen.getByRole('list', {
        name: 'Checkpoint Branch turns for cbr-routed-remediation',
      });
      expect(within(turnList).getByText('turn-routed-1')).toBeTruthy();
      expect(within(turnList).getByText('agent-run-routed-1')).toBeTruthy();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    }, 30_000);
  }

  it('shows and safely discards a copied cross-tab draft URL', async () => {
    await page.viewport(MOBILE.width, MOBILE.height);
    window.localStorage.setItem(
      'moonmind.remediation-create-draft-presence.copied-tab',
      JSON.stringify({ createdAt: new Date().toISOString() }),
    );
    window.history.replaceState(
      {},
      '',
      '/workflows/new?intent=remediate&draftId=copied-tab',
    );
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;

    expect((await screen.findAllByText(/belongs to another browser tab/i)).length).toBeGreaterThan(0);
    expect(screen.queryByText('Remediation Draft')).toBeNull();
    const discard = screen.getByRole('button', { name: 'Discard draft reference' });
    discard.focus();
    expect(document.activeElement).toBe(discard);
    await userEvent.keyboard('{Enter}');
    await waitFor(() => expect(window.location.search).toBe(''));
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
  }, 20_000);
});
