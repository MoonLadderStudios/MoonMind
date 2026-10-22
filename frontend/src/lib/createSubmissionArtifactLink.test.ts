import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CreateSubmissionIntents, createSubmissionScope } from './createSubmissionIntent';

const body = { payload: { task: { instructions: 'PRIVATE_DRAFT_CONTENT' } } };

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState({}, '');
});
afterEach(() => vi.restoreAllMocks());

describe('Create artifact link transition ownership', () => {
  it('only releases the rejected attempt and never an uncertain newer POST', () => {
    const scope = createSubmissionScope();
    const oldStore = new CreateSubmissionIntents(scope);
    const old = oldStore.forRequest(body);
    const first = oldStore.beginArtifactLink(old, 'link')!;
    const returnedStore = new CreateSubmissionIntents(scope);
    const returned = returnedStore.forRequest(body);
    expect(returnedStore.beginArtifactLink(returned, 'link')).toBeNull();
    oldStore.rejectArtifactLink(old, 'link', first);
    returnedStore.save(returned);
    expect(returned.artifactLinks?.link).toBe('rejected');

    const second = returnedStore.beginArtifactLink(returned, 'link')!;
    expect(second).not.toBe(first);
    oldStore.rejectArtifactLink(old, 'link', first);
    expect(oldStore.beginArtifactLink(old, 'link')).toBeNull();
    expect(old).toMatchObject({ artifactLinks: { link: 'uncertain' }, artifactLinkAttempts: { link: second } });

    returnedStore.rejectArtifactLink(returned, 'link', second);
    expect(new CreateSubmissionIntents(scope).forRequest(body).artifactLinks?.link).toBe('rejected');
  });

  it('keeps completed links and accepted admission dominant over late rejections', () => {
    const scope = createSubmissionScope();
    const oldStore = new CreateSubmissionIntents(scope);
    const old = oldStore.forRequest(body);
    old.accepted = { workflowId: 'mm:accepted', runId: 'run-1' };
    const attempt = oldStore.beginArtifactLink(old, 'link')!;
    const restoredStore = new CreateSubmissionIntents(scope);
    const restored = restoredStore.forRequest(body);
    restoredStore.completeArtifactLink(restored, 'link');
    oldStore.rejectArtifactLink(old, 'link', attempt);
    expect(oldStore.beginArtifactLink(old, 'link')).toBeNull();
    expect(new CreateSubmissionIntents(scope).forRequest(body)).toMatchObject({
      accepted: { workflowId: 'mm:accepted', runId: 'run-1' },
      artifactLinks: { link: 'complete' }, uncertain: false,
    });
  });

  it('does not permit an ownerless rejection to release legacy uncertainty', () => {
    const scope = createSubmissionScope();
    new CreateSubmissionIntents(scope).forRequest(body);
    const key = window.sessionStorage.key(0)!;
    const legacy = JSON.parse(window.sessionStorage.getItem(key)!);
    legacy.artifactLinks = { link: 'uncertain' };
    delete legacy.artifactLinkAttempts;
    window.sessionStorage.setItem(key, JSON.stringify(legacy));
    const store = new CreateSubmissionIntents(scope);
    const intent = store.forRequest(body);
    store.rejectArtifactLink(intent, 'link', 'not-the-original-attempt');
    expect(store.beginArtifactLink(intent, 'link')).toBeNull();
    expect(intent.artifactLinks?.link).toBe('uncertain');
  });

  it('retains owned retry transitions during failed storage writes and later reconstruction', () => {
    const scope = createSubmissionScope();
    const store = new CreateSubmissionIntents(scope);
    const intent = store.forRequest(body);
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    const first = store.beginArtifactLink(intent, 'link')!;
    const restoredStore = new CreateSubmissionIntents(scope);
    const restored = restoredStore.forRequest(body);
    expect(restoredStore.beginArtifactLink(restored, 'link')).toBeNull();
    store.rejectArtifactLink(intent, 'link', first);
    const second = restoredStore.beginArtifactLink(restored, 'link')!;
    expect(second).toBeTypeOf('string');
    expect(second).not.toBe(first);
    store.rejectArtifactLink(intent, 'link', first);
    expect(store.beginArtifactLink(intent, 'link')).toBeNull();
    set.mockRestore();
    restoredStore.save(restored);
    const reloaded = new CreateSubmissionIntents(scope).forRequest(body);
    expect(reloaded).toMatchObject({ artifactLinks: { link: 'uncertain' }, artifactLinkAttempts: { link: second } });
    expect(JSON.stringify(window.sessionStorage)).not.toContain('PRIVATE_DRAFT_CONTENT');
  });

  it('persists whitelisted link identity metadata, not added request or response fields', () => {
    const store = new CreateSubmissionIntents(createSubmissionScope());
    const intent = store.forRequest(body);
    const attempt = store.beginArtifactLink(intent, 'link')!;
    Object.assign(intent, { body: 'PRIVATE_REQUEST', response: 'PRIVATE_RESPONSE' });
    Object.assign(intent.artifactLinkAttempts!, { invalid: { secret: 'PRIVATE_TOKEN' } });
    Object.assign(intent.artifactLinks!, { invalid: { secret: 'PRIVATE_FILE' } });
    store.save(intent);
    const stored = JSON.stringify(window.sessionStorage);
    expect(stored).toContain(attempt);
    expect(stored).not.toMatch(/PRIVATE_/);
  });
});
