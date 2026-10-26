import { expect, test } from 'bun:test';
import { buildTaskRequest, eventToMessages, modePolicy, parsePlan, taskChecklist, type TaskEvent } from '../../dikdik/web/contracts';

test('planning narrows stale browser and execution approvals', () => {
  const request = buildTaskRequest({ objective: 'Plan a change', mode: 'plan', verification: 'docker', approveExternal: true, approveExecution: true, approveBrowser: true, edits: '' });
  expect(request).toMatchObject({ mode: 'plan', verification: 'syntax', approve_execution: false, approve_external: true });
  expect(request).not.toHaveProperty('approve_browser');
  expect(modePolicy('plan', 'docker').locksVerification).toBe(true);
});

test('plan parser validates task scope and revision counters', () => {
  const plan = { task_id: 'a'.repeat(32), planner_id: 'b'.repeat(32), content: '# Plan', revision: 2, built_revision: 1 };
  expect(parsePlan(plan)).toEqual(plan);
  expect(parsePlan({ ...plan, task_id: '../outside' })).toBeNull();
  expect(parsePlan({ ...plan, built_revision: 3 })).toBeNull();
  expect(parsePlan({ ...plan, content: 'x'.repeat(32769) })).toBeNull();
});

test('checklist appears only after a valid tracker event and preserves reported statuses', () => {
  const event: TaskEvent = { seq: 1, task_id: 'task', time: 1, kind: 'task_tracker', data: { tasks: [{ id: 'one', title: '<script>data</script>', status: 'in_progress' }] } };
  expect(taskChecklist([])).toEqual({ used: false, tasks: [] });
  expect(taskChecklist([event])).toEqual({ used: true, tasks: event.data.tasks });
  expect(taskChecklist([{ ...event, data: { tasks: [{ title: 'missing identity' }] } }]).used).toBe(false);
  expect(eventToMessages(event, 'objective')).toEqual([]);
  expect(eventToMessages({ ...event, kind: 'plan_updated', data: { content: '# Plan', revision: 1 } }, 'objective')[0]?.body).toBe('# Plan');
});
