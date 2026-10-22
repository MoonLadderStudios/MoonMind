import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CreateSubmissionIntents, createSubmissionScope } from './createSubmissionIntent';

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState({ key: 'router-key', idx: 2 }, '');
});

describe('Create submission identity', () => {
  it('retains intent across reconstruction/reload and history return, not new draft entries', () => {
    const scope = createSubmissionScope();
    expect(window.history.state).toMatchObject({ key: 'router-key', idx: 2 });
    const body = { payload: { task: { instructions: 'One workflow' } } };
    const original = new CreateSubmissionIntents(scope).forRequest(body);
    expect(new CreateSubmissionIntents(createSubmissionScope()).forRequest(body).key).toBe(original.key);
    const oldState = window.history.state;
    window.history.pushState({}, '');
    expect(new CreateSubmissionIntents(createSubmissionScope()).forRequest(body).key).not.toBe(original.key);
    window.history.replaceState(oldState, '');
    expect(new CreateSubmissionIntents(createSubmissionScope()).forRequest(body).key).toBe(original.key);
    expect(JSON.stringify(window.sessionStorage)).not.toContain('One workflow');
  });

  it('retains accepted identity and prepared artifact after follow-up failure', () => {
    const scope = createSubmissionScope();
    const store = new CreateSubmissionIntents(scope);
    const body = { payload: { task: { instructions: 'One workflow' } } };
    const intent = store.forRequest(body);
    intent.accepted = { workflowId: 'mm:accepted', runId: 'run-1' };
    intent.inputArtifactRef = 'artifact-1';
    store.save(intent);
    expect(new CreateSubmissionIntents(scope).forRequest(body)).toEqual(intent);
    expect(store.forRequest({ payload: { task: { instructions: 'Different workflow' } } }).key).not.toBe(intent.key);
    expect(store.forRequest(body)).toBe(intent);
  });

  it('keeps in-memory retry identity when storage is unavailable', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full'); });
    try {
      const store = new CreateSubmissionIntents('scope');
      const first = store.forRequest({ payload: { task: {} } });
      expect(store.forRequest({ payload: { task: {} } })).toBe(first);
    } finally { spy.mockRestore(); }
  });
});

it('persists only uploaded-ref metadata and hashes actual file contents', async () => {
  const { createAttachmentFingerprint } = await import('./createSubmissionIntent');
  const file = (content: string) => new File([content], 'same.png', { type: 'image/png', lastModified: 123 });
  const original = await createAttachmentFingerprint(file('PRIVATE_A'), 'repository/step');
  expect(await createAttachmentFingerprint(file('PRIVATE_A'), 'repository/step')).toBe(original);
  expect(await createAttachmentFingerprint(file('PRIVATE_B'), 'repository/step')).not.toBe(original);
  const scope = createSubmissionScope();
  const store = new CreateSubmissionIntents(scope);
  const ref = { artifactId: 'artifact-1', filename: 'same.png', contentType: 'image/png', sizeBytes: 9, bytes: 'PRIVATE_A', secret: 'SECRET' };
  store.saveAttachment(original, ref);
  expect(new CreateSubmissionIntents(scope).attachment(original)).toEqual({ artifactId: 'artifact-1', filename: 'same.png', contentType: 'image/png', sizeBytes: 9 });
  expect(JSON.stringify(window.sessionStorage)).not.toMatch(/PRIVATE_A|SECRET|bytes|secret/);
});

it('keeps the first prepared ref when two reconstructed stores finish uploads out of order', async () => {
  const scope = createSubmissionScope();
  const oldStore = new CreateSubmissionIntents(scope);
  const returnedStore = new CreateSubmissionIntents(scope);
  const ref = (artifactId: string) => ({ artifactId, filename: 'same.png', contentType: 'image/png', sizeBytes: 9 });
  let finishOld!: (value: ReturnType<typeof ref>) => void;
  const oldUpload = oldStore.prepareAttachment('same-content', () => new Promise((resolve) => { finishOld = resolve; }));
  const returnedRef = await returnedStore.prepareAttachment('same-content', async () => ref('accepted-artifact'));
  const body = { payload: { task: { inputAttachments: [returnedRef] } } };
  const accepted = returnedStore.forRequest(body);
  accepted.accepted = { workflowId: 'mm:accepted' };
  returnedStore.save(accepted);
  finishOld(ref('late-orphan'));
  expect(await oldUpload).toEqual(returnedRef);
  const reloaded = new CreateSubmissionIntents(scope);
  expect(reloaded.attachment('same-content')).toEqual(returnedRef);
  expect(reloaded.forRequest(body).accepted).toEqual({ workflowId: 'mm:accepted' });
});

it('does not let an older store erase accepted identity or completed link progress', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: { instructions: 'Same intent' } } };
  const oldStore = new CreateSubmissionIntents(scope);
  const oldIntent = oldStore.forRequest(body);
  const returnedStore = new CreateSubmissionIntents(scope);
  const accepted = returnedStore.forRequest(body);
  accepted.accepted = { workflowId: 'mm:accepted' };
  accepted.inputArtifactRef = 'canonical-input';
  accepted.artifactLinks = { link: 'complete' };
  returnedStore.save(accepted);
  oldIntent.uncertain = true;
  oldIntent.inputArtifactRef = 'late-input';
  oldIntent.artifactLinks = { link: 'uncertain' };
  oldStore.save(oldIntent);
  expect(new CreateSubmissionIntents(scope).forRequest(body)).toMatchObject({
    accepted: { workflowId: 'mm:accepted' }, uncertain: false,
    inputArtifactRef: 'canonical-input', artifactLinks: { link: 'complete' },
  });
  expect(oldIntent.inputArtifactRef).toBe('canonical-input');
});
