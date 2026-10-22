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

it('does not erase newer admission uncertainty when stale artifact preparation saves', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: { instructions: 'Recurring large input' } } };
  const preparingStore = new CreateSubmissionIntents(scope);
  const preparing = preparingStore.forRequest(body);
  const sendingStore = new CreateSubmissionIntents(scope);
  const sending = sendingStore.forRequest(body);
  sendingStore.beginSubmission(sending, true);
  preparing.inputArtifactRef = 'prepared-late';
  preparingStore.save(preparing);
  expect(new CreateSubmissionIntents(scope).forRequest(body).uncertain).toBe(true);
});

it('refreshes admission state at the send boundary and permits only owned rejection retries', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  const oldStore = new CreateSubmissionIntents(scope);
  const oldIntent = oldStore.forRequest(body);
  const currentStore = new CreateSubmissionIntents(scope);
  const current = currentStore.forRequest(body);
  const attempt = currentStore.beginSubmission(current, true)!;
  expect(oldStore.beginSubmission(oldIntent, true)).toBeNull();
  expect(oldIntent.uncertain).toBe(true);

  currentStore.rejectSubmission(current, attempt);
  // Late preparation cannot restore the uncertainty that was definitively cleared.
  oldStore.save(oldIntent);
  expect(oldIntent.uncertain).toBe(false);
  const nextAttempt = oldStore.beginSubmission(oldIntent, true)!;
  expect(nextAttempt).not.toBe(attempt);
  // An older response cannot clear the newer send's uncertainty.
  currentStore.rejectSubmission(current, attempt);
  expect(new CreateSubmissionIntents(scope).forRequest(body)).toMatchObject({ uncertain: true, attempt: nextAttempt });
  oldStore.rejectSubmission(oldIntent, nextAttempt);
  expect(new CreateSubmissionIntents(scope).forRequest(body).uncertain).toBe(false);
});

it('never reopens accepted admission at the send boundary', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  const oldStore = new CreateSubmissionIntents(scope);
  const oldIntent = oldStore.forRequest(body);
  const acceptedStore = new CreateSubmissionIntents(scope);
  const accepted = acceptedStore.forRequest(body);
  accepted.accepted = { workflowId: 'mm:accepted' };
  acceptedStore.save(accepted);
  expect(oldStore.beginSubmission(oldIntent, false)).toBeNull();
  expect(oldIntent.accepted).toEqual(accepted.accepted);
  expect(oldIntent.uncertain).toBe(false);
});

it('keeps admission transitions safe in memory when session storage is unavailable', () => {
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
  try {
    const store = new CreateSubmissionIntents('offline-storage');
    const intent = store.forRequest({ payload: { task: {} } });
    const attempt = store.beginSubmission(intent, true)!;
    expect(intent.uncertain).toBe(true);
    expect(store.beginSubmission(intent, true)).toBeNull();
    store.rejectSubmission(intent, attempt);
    expect(intent.uncertain).toBe(false);
    expect(store.beginSubmission(intent, true)).toBeTypeOf('string');
  } finally { get.mockRestore(); set.mockRestore(); }
});

it('preserves legacy uncertain entries that predate attempt ownership metadata', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  new CreateSubmissionIntents(scope).forRequest(body);
  const key = window.sessionStorage.key(0)!;
  const legacy = JSON.parse(window.sessionStorage.getItem(key)!);
  legacy.uncertain = true;
  delete legacy.attempt;
  window.sessionStorage.setItem(key, JSON.stringify(legacy));
  const store = new CreateSubmissionIntents(scope);
  const intent = store.forRequest(body);
  expect(store.beginSubmission(intent, true)).toBeNull();
  expect(intent.uncertain).toBe(true);
});

it('does not trust older persisted certainty after a quota failure, including a reconstructed store', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  const store = new CreateSubmissionIntents(scope);
  const intent = store.forRequest(body);
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  try {
    const attempt = store.beginSubmission(intent, true)!;
    expect(store.beginSubmission(intent, true)).toBeNull();
    const reconstructed = new CreateSubmissionIntents(scope);
    const returned = reconstructed.forRequest(body);
    expect(reconstructed.beginSubmission(returned, true)).toBeNull();
    reconstructed.rejectSubmission(returned, attempt);
    store.save(intent);
    expect(intent.uncertain).toBe(false);
    expect(store.beginSubmission(intent, true)).toBeTypeOf('string');
  } finally { set.mockRestore(); }
  // Once writes recover, the live transition becomes durable, not old false.
  store.save(intent);
  expect(new CreateSubmissionIntents(scope).forRequest(body).uncertain).toBe(true);
});

it('reuses failed-to-persist uploaded metadata across live reconstruction without storing file contents', async () => {
  const scope = createSubmissionScope();
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  try {
    const ref = { artifactId: 'prepared-upload', filename: 'same.png', contentType: 'image/png', sizeBytes: 9, secret: 'PRIVATE_FILE' };
    const original = await new CreateSubmissionIntents(scope).prepareAttachment('same-bytes', async () => ref);
    const uploadAgain = vi.fn(async () => ({ ...ref, artifactId: 'duplicate' }));
    const reconstructed = await new CreateSubmissionIntents(scope).prepareAttachment('same-bytes', uploadAgain);
    expect(uploadAgain).not.toHaveBeenCalled();
    expect(original).toMatchObject(reconstructed);
    expect(reconstructed.artifactId).toBe('prepared-upload');
    expect(reconstructed).not.toHaveProperty('secret');
  } finally { set.mockRestore(); }
});

it('refreshes only a definitively rejected relative time and never resurrects it from stale metadata', () => {
  const scope = createSubmissionScope();
  const body = { payload: { schedule: { mode: 'deferred_minutes', minutes: 10 } } };
  const store = new CreateSubmissionIntents(scope);
  const intent = store.forRequest(body);
  const first = store.prepareScheduledFor(intent, '2030-01-01T09:10:00Z');
  const attempt = store.beginSubmission(intent, false)!;
  const staleStore = new CreateSubmissionIntents(scope);
  const stale = staleStore.forRequest(body);
  expect(staleStore.prepareScheduledFor(stale, '2030-01-01T10:10:00Z')).toBe(first);
  store.rejectSubmission(intent, attempt);
  staleStore.save(stale);
  expect(stale.scheduledFor).toBeUndefined();
  const refreshed = staleStore.prepareScheduledFor(stale, '2030-01-01T10:10:00Z');
  const next = staleStore.beginSubmission(stale, false)!;
  store.rejectSubmission(intent, attempt);
  expect(intent).toMatchObject({ uncertain: true, attempt: next, scheduledFor: refreshed });
  stale.accepted = { workflowId: 'mm:accepted' };
  staleStore.save(stale);
  staleStore.rejectSubmission(stale, next);
  expect(staleStore.prepareScheduledFor(stale, '2030-01-01T11:10:00Z')).toBe(refreshed);
  expect(stale.accepted).toEqual({ workflowId: 'mm:accepted' });
});

it('retains a rejected time reset across live reconstruction when only storage writes fail', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  const store = new CreateSubmissionIntents(scope);
  const intent = store.forRequest(body);
  store.prepareScheduledFor(intent, '2030-01-01T09:10:00Z');
  const attempt = store.beginSubmission(intent, false)!;
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  try {
    store.rejectSubmission(intent, attempt);
    const returnedStore = new CreateSubmissionIntents(scope);
    const returned = returnedStore.forRequest(body);
    expect(returned.scheduledFor).toBeUndefined();
    expect(returnedStore.prepareScheduledFor(returned, '2030-01-01T10:10:00Z')).toBe('2030-01-01T10:10:00Z');
  } finally { set.mockRestore(); }
});

it('does not mistake a rejected replay for rejection of its earlier uncertain admission', () => {
  const scope = createSubmissionScope();
  const body = { payload: { task: {} } };
  const store = new CreateSubmissionIntents(scope);
  const intent = store.forRequest(body);
  store.prepareScheduledFor(intent, '2030-01-01T09:10:00Z');
  store.beginSubmission(intent, false);
  const replay = store.beginSubmission(intent, false)!;
  store.rejectSubmission(intent, replay);
  expect(new CreateSubmissionIntents(scope).forRequest(body)).toMatchObject({
    uncertain: true, priorUncertain: true, scheduledFor: '2030-01-01T09:10:00Z',
  });
});
