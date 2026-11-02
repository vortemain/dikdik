import { expect, test } from 'bun:test';
import { buildTaskRequest, eventToMessages, modePolicy, parsePlan, parsePlans, planStatus, planTitle, taskChecklist, type TaskEvent } from '../../dikdik/web/contracts';

test('planning narrows stale browser and execution approvals', () => {
  const request = buildTaskRequest({ objective: 'Plan a change', mode: 'plan', verification: 'local', approveExternal: true, approveExecution: true, approveBrowser: true, edits: '' });
  expect(request).toMatchObject({ mode: 'plan', verification: 'syntax', approve_execution: false, approve_external: true });
  expect(request).not.toHaveProperty('approve_browser');
  expect(modePolicy('plan', 'local').locksVerification).toBe(true);
});

test('plan parser validates task scope and revision counters', () => {
  const plan = { task_id: 'a'.repeat(32), planner_id: 'b'.repeat(32), content: '# Plan', revision: 2, built_revision: 1 };
  expect(parsePlan(plan)).toEqual(plan);
  expect(parsePlan({ ...plan, task_id: '../outside' })).toBeNull();
  expect(parsePlan({ ...plan, built_revision: 3 })).toBeNull();
  expect(parsePlan({ ...plan, content: 'x'.repeat(32769) })).toBeNull();
});

test('the planner list reads each plan with where it stands', () => {
  const base = { task_id: 'a'.repeat(32), planner_id: 'b'.repeat(32), content: '# Plan', revision: 2, built_revision: 0,
    objective: 'Add search', title: '', task_state: 'draft', planner_state: 'completed', updated: 10 };
  const [plan] = parsePlans({ plans: [base, { ...base, task_id: 'bad' }, { ...base, planner_id: null }] });
  expect(parsePlans({ plans: [base] })).toHaveLength(1);
  expect(parsePlans(null)).toEqual([]);
  expect(planStatus(plan!)).toEqual({ label: 'Ready to build', kind: 'ready' });
  expect(planStatus({ ...plan!, planner_state: 'running', content: '' }).label).toBe('Planning');
  expect(planStatus({ ...plan!, built_revision: 2, task_state: 'running' }).label).toBe('Building');
  expect(planStatus({ ...plan!, built_revision: 2, task_state: 'completed' })).toEqual({ label: 'Built', kind: 'done' });
  expect(planStatus({ ...plan!, built_revision: 1 }).label).toBe('Changed since build');
  expect(planStatus({ ...plan!, content: '', planner_state: 'failed' }).kind).toBe('failed');
  expect(planTitle({ ...plan!, objective: 'Plan: Add search' })).toBe('Add search');
  expect(planTitle({ ...plan!, title: 'Search box' })).toBe('Search box');
});

test('checklist appears only after a valid tracker event and preserves reported statuses', () => {
  const event: TaskEvent = { seq: 1, task_id: 'task', time: 1, kind: 'task_tracker', data: { tasks: [{ id: 'one', title: '<script>data</script>', status: 'in_progress' }] } };
  expect(taskChecklist([])).toEqual({ used: false, tasks: [] });
  expect(taskChecklist([event])).toEqual({ used: true, tasks: event.data.tasks });
  expect(taskChecklist([{ ...event, data: { tasks: [{ title: 'missing identity' }] } }]).used).toBe(false);
  expect(eventToMessages(event, 'objective')).toEqual([]);
  expect(eventToMessages({ ...event, kind: 'plan_updated', data: { content: '# Plan', revision: 1 } }, 'objective')[0]?.body).toBe('# Plan');
});
