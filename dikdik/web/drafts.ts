export interface Draft { scope: string; content: string; revision: number; updated: number }
export function parseDraft(value: unknown): Draft | null {
  if (typeof value !== 'object' || value === null) return null;
  const row = value as Record<string, unknown>;
  if (typeof row.scope !== 'string' || !/^(home|[a-f0-9]{32})$/.test(row.scope) || typeof row.content !== 'string' || row.content.length > 8000 || row.content.includes('\0') ||
      typeof row.revision !== 'number' || !Number.isSafeInteger(row.revision) || row.revision < 0 || typeof row.updated !== 'number' || !Number.isFinite(row.updated)) return null;
  return { scope: row.scope, content: row.content, revision: row.revision, updated: row.updated };
}
type Entry = { scope: string; content: string; revision: number | null; dirty: boolean; sequence: number;
  conflict: Draft | null; error: boolean; loading: Promise<void> | null; saving: Promise<void> | null; timer?: ReturnType<typeof setTimeout> | undefined };
export interface DraftStatus { state: 'loading' | 'saving' | 'saved' | 'unsaved' | 'conflict'; dirty: boolean; remote?: string }

/** Server storage is injected; no model call or browser-local credential store exists here. */
export class ComposerDrafts {
  scope = 'home';
  private entries = new Map<string, Entry>();
  constructor(private request: (path: string, method: string, body?: unknown) => Promise<unknown>,
              private render: (content: string | null, status: DraftStatus) => void) {}
  private entry(scope: string): Entry {
    let entry = this.entries.get(scope);
    if (!entry) { entry = { scope, content: '', revision: null, dirty: false, sequence: 0, conflict: null, error: false, loading: null, saving: null }; this.entries.set(scope, entry); }
    return entry;
  }
  private paint(entry: Entry, apply = false): void {
    if (entry.scope !== this.scope) return;
    const state = entry.conflict ? 'conflict' : entry.error ? 'unsaved' : entry.saving || entry.dirty ? 'saving' : entry.loading ? 'loading' : 'saved';
    this.render(apply ? entry.content : null, { state, dirty: entry.dirty || !!entry.conflict, ...(entry.conflict ? { remote: entry.conflict.content } : {}) });
  }
  display(): void { this.paint(this.entry(this.scope), true); }
  hasUnsaved(): boolean { return [...this.entries.values()].some(entry => entry.dirty || entry.conflict); }
  async select(scope: string): Promise<void> {
    const previous = this.scope;
    if (previous !== scope) void this.flush(previous);
    this.scope = scope;
    const entry = this.entry(scope);
    this.paint(entry, true);
    await this.load(entry);
  }
  private async load(entry: Entry): Promise<void> {
    if (entry.loading) return entry.loading;
    entry.loading = (async () => {
      await Promise.resolve();
      if (entry.saving) await entry.saving;
      try {
        const row = parseDraft(await this.request(`/api/drafts/${entry.scope}`, 'GET'));
        if (!row || row.scope !== entry.scope) throw new Error('Invalid draft response');
        if (entry.dirty && row.content !== entry.content && (entry.revision === null ? !!row.content : entry.revision !== row.revision)) entry.conflict = row;
        else { entry.revision = row.revision; if (!entry.dirty) entry.content = row.content; else if (row.content === entry.content) entry.dirty = false; }
        entry.error = false;
      } catch { entry.error = true; }
      finally { entry.loading = null; this.paint(entry, true); }
    })();
    this.paint(entry);
    return entry.loading;
  }
  update(content: string): void {
    const entry = this.entry(this.scope);
    entry.content = content; entry.dirty = true; entry.sequence++; entry.error = false;
    this.paint(entry); this.schedule(entry);
  }
  private schedule(entry: Entry): void {
    if (entry.timer !== undefined) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => { entry.timer = undefined; void this.flush(entry.scope); }, 300);
  }
  async flush(scope = this.scope): Promise<void> {
    const entry = this.entry(scope);
    if (entry.timer !== undefined) { clearTimeout(entry.timer); entry.timer = undefined; }
    if (!entry.dirty || entry.conflict) return;
    if (entry.saving) return entry.saving;
    if (entry.revision === null) await this.load(entry);
    // Navigation and the debounce timer can both wait for the first read.
    if (entry.saving) return entry.saving;
    if (!entry.dirty) return;
    if (entry.revision === null || entry.conflict || entry.error) { this.paint(entry); return; }
    entry.saving = (async () => {
      await Promise.resolve();
      try {
        for (let attempt = 0; attempt < 3 && entry.dirty && !entry.conflict; attempt++) {
          const content = entry.content, sequence = entry.sequence, revision = entry.revision;
          const row = parseDraft(await this.request(`/api/drafts/${scope}`, 'PATCH', { content, revision }));
          if (!row || row.scope !== scope || row.revision <= revision!) throw new Error('Invalid draft response');
          entry.revision = row.revision; entry.error = false;
          if (entry.sequence === sequence && entry.content === content) entry.dirty = false;
        }
      } catch (error) {
        const failure = error as { status?: number; data?: { current?: unknown } };
        const current = failure?.status === 409 ? parseDraft(failure.data?.current) : null;
        if (current && current.scope === scope) entry.conflict = current;
        else entry.error = true;
      } finally {
        entry.saving = null; this.paint(entry);
        if (entry.dirty && !entry.conflict && !entry.error) this.schedule(entry);
      }
    })();
    this.paint(entry);
    return entry.saving;
  }
  async resolve(keep: 'local' | 'server'): Promise<void> {
    const entry = this.entry(this.scope), current = entry.conflict;
    if (!current) { entry.error = false; await this.flush(); return; }
    entry.revision = current.revision; entry.conflict = null; entry.error = false; entry.sequence++;
    if (keep === 'server') { entry.content = current.content; entry.dirty = false; this.paint(entry, true); }
    else { entry.dirty = true; await this.flush(); }
  }
  async sent(scope: string, content: string): Promise<void> {
    const entry = this.entry(scope);
    if (entry.content !== content || entry.conflict) return;
    entry.content = ''; entry.dirty = true; entry.sequence++;
    this.paint(entry, true);
    await this.flush(scope);
  }
}
