import { describe, expect, it, vi, afterEach } from 'vitest';
import { applyAssessmentInputDefaults, createAssessmentEnvironment, createAssessmentProject, resolveAssessmentProject, resolveSchemaProperty, type AssessmentProjectSnapshot } from './assessmentProjects';

const schema = { 'x-assessment-project-defaults': true, properties: Object.fromEntries(['scope', 'budget', 'reportRefs', 'tools', 'credentialRefs', 'networkRefs', 'assessmentProject'].map((key) => [key, {}])) };
const snapshot: AssessmentProjectSnapshot = {
  schemaVersion: 'assessment-project/v1', projectId: 'payments', projectName: 'Payments',
  environmentId: 'prod', environmentName: 'Production', sourceVersion: 3,
  purpose: 'Review evidence', boundaries: 'Payment API', digest: 'a'.repeat(64),
  configuration: { ...createAssessmentEnvironment(), id: 'prod', name: 'Production',
    assets: ['api'], exclusions: ['sandbox'], artifactRefs: ['art_evidence'],
    credentialRefs: ['db://candidate'], originCandidates: ['https://candidate.example'],
  },
};
afterEach(() => vi.unstubAllGlobals());
describe('assessment input defaults', () => {
  it('creates bounded unique IDs on HTTP LAN origins without randomUUID', () => {
    const nativeCrypto = globalThis.crypto;
    vi.stubGlobal('crypto', { getRandomValues: nativeCrypto.getRandomValues.bind(nativeCrypto) });
    const first = createAssessmentProject();
    const second = createAssessmentProject();
    expect(first.id).not.toBe(second.id);
    for (const id of [first.id, second.id, first.environments[0]!.id, second.environments[0]!.id]) {
      expect(id).toMatch(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/);
    }
  });

  it('fills reviewable scope, bounded budget and retained evidence without granting credentials or network', () => {
    const inputs = applyAssessmentInputDefaults(schema, {}, snapshot);
    expect(inputs).toEqual({ scope: { assets: ['api'], exclusions: ['sandbox'] }, budget: snapshot.configuration.budget,
      reportRefs: ['art_evidence'], tools: ['retained-report'], credentialRefs: [], networkRefs: [], assessmentProject: snapshot });
    expect(inputs.assessmentProject).not.toBe(snapshot);
    expect(inputs.scope).not.toBe(snapshot.configuration.assets);
  });
  it('preserves explicit values including empty arrays and objects during repeated use', () => {
    const overrides = { scope: {}, budget: { baseSeconds: 15, maxSeconds: 20, progressStallSeconds: 5 }, reportRefs: [], tools: [], credentialRefs: [], networkRefs: [] };
    const first = applyAssessmentInputDefaults(schema, overrides, snapshot);
    const second = applyAssessmentInputDefaults(schema, first, { ...snapshot, sourceVersion: 4 });
    expect(first).toMatchObject(overrides);
    expect(second).toMatchObject(overrides);
    expect(second.assessmentProject).toMatchObject({ sourceVersion: 4 });
  });
  it.each([[], ['art_authored'], null])('preserves authored snake-case reference aliases, including %j, before applying defaults', (reportRefs) => {
    const inputs = applyAssessmentInputDefaults(schema, { report_refs: reportRefs, credential_refs: [], network_refs: [] }, snapshot);
    expect(inputs.reportRefs).toEqual(reportRefs);
    expect(inputs.credentialRefs).toEqual([]);
    expect(inputs.networkRefs).toEqual([]);
    expect(inputs).not.toHaveProperty('report_refs');
    expect(inputs).not.toHaveProperty('credential_refs');
    expect(inputs).not.toHaveProperty('network_refs');
    expect(inputs.tools).toEqual(reportRefs?.length ? ['retained-report'] : []);
  });
  it('does not default explicit snake-case credential or network values to empty lists', () => {
    const inputs = applyAssessmentInputDefaults(schema, { credential_refs: ['db://authored'], network_refs: null }, snapshot);
    expect(inputs.credentialRefs).toEqual(['db://authored']);
    expect(inputs.networkRefs).toBeNull();
  });
  it.each(['reportRefs', 'credentialRefs', 'networkRefs'])('does not apply defaults over duplicate %s aliases, even with equal values', (camel) => {
    const snake = camel.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`);
    const ambiguous = { [camel]: [], [snake]: [] };
    expect(applyAssessmentInputDefaults(schema, ambiguous, snapshot)).toEqual(ambiguous);
  });
  it('requires an opted-in schema and fills only declared properties', () => {
    expect(applyAssessmentInputDefaults({ properties: schema.properties }, {}, snapshot)).toEqual({});
    expect(applyAssessmentInputDefaults({ 'x-assessment-project-defaults': true, properties: { scope: {} } }, {}, snapshot)).toEqual({ scope: { assets: ['api'], exclusions: ['sandbox'] } });
  });
  it('resolves local schema types without following external or cyclic references', () => {
    const root = { $defs: { Scope: { type: 'object' }, Cycle: { $ref: '#/$defs/Cycle' } } };
    expect(resolveSchemaProperty({ $ref: '#/$defs/Scope', title: 'Assessment scope' }, root)).toMatchObject({ type: 'object', title: 'Assessment scope' });
    expect(resolveSchemaProperty({ $ref: '#/$defs/Cycle' }, root)).toEqual({ $ref: '#/$defs/Cycle' });
    expect(resolveSchemaProperty({ $ref: 'https://external/schema' }, root)).toEqual({ $ref: 'https://external/schema' });
  });
  it('resolves exact versions and explicit empty overrides, surfacing server failure', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ snapshot, gaps: [] }) });
    vi.stubGlobal('fetch', fetchMock);
    await expect(resolveAssessmentProject({ projectId: 'payments', environmentId: 'prod', expectedVersion: 3, overrides: { assets: [] } })).resolves.toEqual({ snapshot, gaps: [] });
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({ projectId: 'payments', environmentId: 'prod', expectedVersion: 3, overrides: { assets: [] } });
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'Saved context changed. Reload Settings.' }), { status: 409 }));
    await expect(resolveAssessmentProject({ projectId: 'payments', environmentId: 'prod', expectedVersion: 3 })).rejects.toThrow('Saved context changed');
  });
});
