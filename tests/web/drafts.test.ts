import { expect, test } from 'bun:test';
import { ComposerDrafts, parseDraft, type Draft, type DraftStatus } from '../../dikdik/web/drafts';

function fixture() {
  const rows = new Map<string, Draft>();
  const request = async (path: string, method: string, body?: unknown): Promise<unknown> => {
    const scope = path.split('/').at(-1)!;
    const current = rows.get(scope) ?? { scope, content: '', revision: 0, updated: 0 };
    if (method === 'GET') return structuredClone(current);
    const change = body as { content: string; revision: number };
    if (change.revision !== current.revision) throw { status: 409, data: { current: structuredClone(current) } };
    const next = { scope, content: change.content, revision: current.revision + 1, updated: 1 };
    rows.set(scope, next); return structuredClone(next);
  };
  return { rows, request };
}

test('drafts restore per scope and clear only the acknowledged sent text', async () => {
  const { rows, request } = fixture();
  let text = '';
  const drafts = new ComposerDrafts(request, value => { if (value !== null) text = value; });
  await drafts.select('home'); drafts.update('home draft'); await drafts.flush();
  await drafts.select('a'.repeat(32)); drafts.update('conversation draft'); await drafts.flush();
  await drafts.select('home'); expect(text).toBe('home draft');
  await drafts.sent('home', 'different message'); expect(rows.get('home')?.content).toBe('home draft');
  await drafts.sent('home', 'home draft'); expect(rows.get('home')?.content).toBe('');
  await drafts.select('a'.repeat(32)); expect(text).toBe('conversation draft');
  drafts.update('new text while an earlier send finishes');
  await drafts.sent('a'.repeat(32), 'conversation draft'); await drafts.flush();
  expect(rows.get('a'.repeat(32))?.content).toBe('new text while an earlier send finishes');
});

test('two tabs surface conflicts and resolve only after an explicit choice', async () => {
  const { rows, request } = fixture();
  let status: DraftStatus | undefined;
  const one = new ComposerDrafts(request, () => {}), two = new ComposerDrafts(request, (_value, next) => { status = next; });
  await one.select('home'); await two.select('home');
  one.update('first tab'); await one.flush();
  two.update('second tab'); await two.flush();
  expect(status?.state).toBe('conflict'); expect(status?.remote).toBe('first tab');
  expect(rows.get('home')?.content).toBe('first tab');
  await two.resolve('local'); expect(rows.get('home')?.content).toBe('second tab');
  await one.select('home'); one.update('third edit'); await one.flush();
  two.update('local edit to discard'); await two.flush(); await two.resolve('server');
  expect(two.hasUnsaved()).toBe(false); expect(rows.get('home')?.content).toBe('third edit');
});

test('a delayed old scope cannot overwrite the current composer', async () => {
  const { rows, request } = fixture();
  rows.set('a'.repeat(32), { scope: 'a'.repeat(32), content: 'old draft', revision: 1, updated: 1 });
  rows.set('b'.repeat(32), { scope: 'b'.repeat(32), content: 'current draft', revision: 1, updated: 1 });
  let release!: () => void, text = '';
  const gate = new Promise<void>(resolve => { release = resolve; });
  const drafts = new ComposerDrafts(async (path, method, body) => { if (path.endsWith('a'.repeat(32)) && method === 'GET') await gate; return request(path, method, body); }, value => { if (value !== null) text = value; });
  const old = drafts.select('a'.repeat(32));
  await drafts.select('b'.repeat(32)); drafts.update('keep current typing'); text = 'keep current typing';
  release(); await old; expect(text).toBe('keep current typing');
  await drafts.flush();
  expect(drafts.scope).toBe('b'.repeat(32));
});

test('a concurrent saved clear cannot be silently resurrected on navigation', async () => {
  const { rows, request } = fixture();
  const drafts = new ComposerDrafts(request, () => {});
  await drafts.select('home'); drafts.update('old text'); await drafts.flush();
  await request('/api/drafts/home', 'PATCH', { content: '', revision: 1 });
  drafts.update('stale edit'); await drafts.select('home'); await drafts.flush();
  expect(rows.get('home')?.content).toBe(''); expect(drafts.hasUnsaved()).toBe(true);
  await drafts.resolve('server'); expect(drafts.hasUnsaved()).toBe(false);
});

test('failed saves retain dirty text and can retry without a silent clear', async () => {
  const { rows, request } = fixture();
  let fail = true, status: DraftStatus | undefined;
  const drafts = new ComposerDrafts((path, method, body) => { if (fail && method === 'PATCH') throw new Error('offline'); return request(path, method, body); }, (_value, next) => { status = next; });
  await drafts.select('home'); drafts.update('<script>untrusted text</script>'); await drafts.flush();
  expect(status?.state).toBe('unsaved'); expect(drafts.hasUnsaved()).toBe(true);
  expect(rows.has('home')).toBe(false);
  fail = false; await drafts.resolve('local');
  expect(rows.get('home')?.content).toBe('<script>untrusted text</script>'); expect(drafts.hasUnsaved()).toBe(false);
  expect(parseDraft({ scope: '../outside', content: '', revision: 0, updated: 0 })).toBeNull();
  expect(parseDraft({ scope: 'home', content: '', revision: -1, updated: 0 })).toBeNull();
});

test('concurrent flushes waiting on the first read share one save', async () => {
  const { rows, request } = fixture();
  let release!: () => void, patches = 0;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const drafts = new ComposerDrafts(async (path, method, body) => {
    if (method === 'GET') await gate; else patches++;
    return request(path, method, body);
  }, () => {});
  const loading = drafts.select('home'); drafts.update('typed before load');
  const first = drafts.flush(), second = drafts.flush();
  release(); await Promise.all([loading, first, second]);
  expect(patches).toBe(1); expect(rows.get('home')?.content).toBe('typed before load'); expect(drafts.hasUnsaved()).toBe(false);
});
