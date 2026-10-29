/** Coalesce a burst of streamed updates into one paint, using the latest data. */
export function frameBatch(render: () => void, frames: {
  request: (callback: () => void) => number;
  cancel: (handle: number) => void;
} = {
  request: callback => globalThis.requestAnimationFrame
    ? globalThis.requestAnimationFrame(callback)
    : globalThis.setTimeout(callback, 16),
  cancel: handle => globalThis.cancelAnimationFrame
    ? globalThis.cancelAnimationFrame(handle)
    : globalThis.clearTimeout(handle),
}): { schedule(): void; flush(): void; cancel(): void } {
  let handle: number | null = null;
  const cancel = () => { if (handle !== null) frames.cancel(handle); handle = null; };
  return {
    schedule() {
      if (handle !== null) return;
      handle = frames.request(() => { handle = null; render(); });
    },
    flush() { if (handle !== null) { cancel(); render(); } },
    cancel,
  };
}
