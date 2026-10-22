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
