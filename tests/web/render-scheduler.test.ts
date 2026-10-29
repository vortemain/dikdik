import { expect, test } from 'bun:test';
import { frameBatch } from '../../dikdik/web/render-scheduler';

test('a streamed burst paints the newest text once per frame', () => {
  let next = 0, text = '', paints: string[] = [];
  const pending = new Map<number, () => void>();
  const batch = frameBatch(() => paints.push(text), {
    request: callback => { pending.set(++next, callback); return next; },
    cancel: handle => { pending.delete(handle); },
  });
  for (let i = 0; i < 100; i++) { text += 'x'; batch.schedule(); }
  expect(pending.size).toBe(1);
  const callback = [...pending.values()][0]!; pending.clear(); callback();
  expect(paints).toEqual(['x'.repeat(100)]);
  text += '!'; batch.schedule(); batch.flush();
  expect(pending.size).toBe(0);
  expect(paints).toEqual(['x'.repeat(100), 'x'.repeat(100) + '!']);
});

test('navigation cancels a pending paint and the next conversation can schedule one', () => {
  let renderCount = 0, next = 0;
  const pending = new Map<number, () => void>();
  const batch = frameBatch(() => renderCount++, {
    request: callback => { pending.set(++next, callback); return next; },
    cancel: handle => { pending.delete(handle); },
  });
  batch.schedule(); batch.cancel(); batch.flush();
  expect(renderCount).toBe(0);
  expect(pending.size).toBe(0);
  batch.schedule(); batch.flush();
  expect(renderCount).toBe(1);
});
