import type { TaskEvent } from "./contracts";

/** A terminal or browser session followed beside the conversation instead of replacing it.
 *  Its events stream in while it works (the stream ends by itself once it is idle);
 *  without EventSource it polls until the session reports nothing running. */
export class ToolSession {
  events: TaskEvent[] = [];
  seq = 0;
  private source: EventSource | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private closed = false;

  constructor(
    readonly id: string,
    private fetchEvents: (id: string) => Promise<TaskEvent[]>,
    private onChange: (session: ToolSession) => void,
    private canStream: () => boolean,
    private busy: (events: TaskEvent[]) => boolean,
  ) {}

  async load(): Promise<void> {
    const events = await this.fetchEvents(this.id);
    if (this.closed) return;
    this.events = [...events];
    this.seq = this.events.reduce((max, event) => Math.max(max, event.seq ?? 0), 0);
    this.onChange(this);
  }

  /** Watch for new events until the session is idle again. */
  follow(): void {
    if (this.closed) return;
    if (this.canStream() && typeof globalThis.EventSource === "function") {
      if (this.source) return;
      const source = new EventSource(`/api/tasks/${encodeURIComponent(this.id)}/stream?after=${this.seq}`);
      this.source = source;
      source.addEventListener("task", event => {
        const record = JSON.parse((event as MessageEvent).data) as TaskEvent;
        if (record.seq <= this.seq) return;
        this.seq = record.seq;
        this.events.push(record);
        this.onChange(this);
      });
      source.addEventListener("end", () => this.stopStream());
      source.onerror = () => { if (source.readyState === EventSource.CLOSED) this.stopStream(); };
      return;
    }
    if (this.timer !== undefined) return;
    const tick = async (): Promise<void> => {
      this.timer = undefined;
      try { await this.load(); } catch { return; }
      if (!this.closed && this.busy(this.events)) this.timer = setTimeout(() => { void tick(); }, 700);
    };
    this.timer = setTimeout(() => { void tick(); }, 300);
  }

  close(): void {
    this.closed = true;
    this.stopStream();
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  private stopStream(): void {
    this.source?.close();
    this.source = null;
  }
}
