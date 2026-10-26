import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.engine import Engine


PLAN = '# Reviewed plan\n\n1. Change x from 1 to 2.\n2. Syntax check the candidate.\n3. Review and Apply.\n'
REQUEST = {'objective': 'Plan changing x', 'mode': 'plan', 'verification': 'syntax', 'approve_external': True, 'approve_execution': False}


class Script:
    def __init__(self, replies):
        self.replies, self.tools, self.messages = list(replies), [], []
    def respond(self, messages, tools):
        self.tools.append([tool['function']['name'] for tool in tools])
        self.messages.append(messages)
        return self.replies.pop(0)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'repo'
        self.root.mkdir()
        (self.root / 'app.py').write_bytes(b'x = 1\n')
        self.engine = Engine(self.root, base / 'data')
        self.addCleanup(self.engine.close)

    def idle(self, task):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not self.engine.busy(task):
                return self.engine.store.get(task)
            time.sleep(.01)
        self.fail('turn did not finish')

    def plan(self):
        model = Script([ModelResponse('', [ToolCall('p', 'write_plan', {'content': PLAN})]), ModelResponse('Plan ready for review.', [])])
        with patch('dikdik.engine.CompatibleAgentModel', return_value=model):
            task = self.engine.submit(REQUEST)
            data = self.idle(task)
        return task, data, model

    def test_plan_is_a_separate_read_only_conversation_with_private_artifact(self):
        child, data, model = self.plan()
        plan = self.engine.planning.get(child)
        self.assertNotEqual(plan['task_id'], child)
        self.assertEqual(plan['planner_id'], child)
        self.assertEqual(plan['content'], PLAN)
        self.assertEqual((self.engine.data / 'plans' / plan['task_id'] / 'PLAN.md').read_bytes(), PLAN.encode())
        self.assertEqual((self.root / 'app.py').read_bytes(), b'x = 1\n')
        self.assertFalse((self.root / 'PLAN.md').exists())
        self.assertEqual(set(model.tools[0]), {'list_files', 'read_file', 'search_text', 'write_plan', 'task_tracker'})
        self.assertFalse(data['result']['tests_verified'])
        self.assertEqual(self.engine.store.get(plan['task_id'])['state'], 'draft')

    def test_planner_rejects_write_execution_and_permission_escalation(self):
        for delta in [{'approve_external': False}, {'verification': 'docker', 'approve_execution': True}, {'approve_execution': True}, {'approve_browser': True}]:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.engine.submit({**REQUEST, **delta})
        model = Script([ModelResponse('', [ToolCall('evil', 'edit_file', {'path': 'app.py', 'before': '1', 'after': '9'})]), ModelResponse('Cannot edit in plan mode.', [])])
        with patch('dikdik.engine.CompatibleAgentModel', return_value=model):
            child = self.engine.submit(REQUEST)
            self.idle(child)
        self.assertEqual((self.root / 'app.py').read_bytes(), b'x = 1\n')
        self.assertEqual(self.engine.planning.get(child)['revision'], 0)
        with self.assertRaisesRegex(ValueError, 'parent code'):
            self.engine.send(child, {'objective': 'Edit now', 'mode': 'agent', 'verification': 'syntax', 'approve_external': True})

    def test_build_requires_review_current_revision_and_new_approvals(self):
        child, _, _ = self.plan()
        body = {'planner_id': child, 'revision': 1, 'confirm': True, 'verification': 'syntax', 'approve_external': True, 'approve_execution': False}
        for change in [{'confirm': False}, {'revision': 0}, {'approve_external': False}, {'verification': 'docker'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.engine.build_plan(child, {**body, **change})
        model = Script([
            ModelResponse('', [ToolCall('read', 'read_plan', {})]),
            ModelResponse('', [ToolCall('edit', 'edit_file', {'path': 'app.py', 'before': 'x = 1', 'after': 'x = 2'})]),
            ModelResponse('', [ToolCall('check', 'run_tests', {})]),
            ModelResponse('', [ToolCall('end', 'finish', {})]),
        ])
        with patch('dikdik.engine.CompatibleAgentModel', return_value=model):
            built = self.engine.build_plan(child, body)
            data = self.idle(built['id'])
        self.assertEqual(data['state'], 'completed', data['result'])
        self.assertIn('+x = 2', data['result']['diff'])
        self.assertFalse(data['result']['tests_verified'])
        self.assertNotIn('run_command', model.tools[0])
        self.assertNotIn('browse_url', model.tools[0])
        self.assertTrue(any('UNTRUSTED REVIEWED PLAN' in message.get('content', '') for message in model.messages[0]))
        self.assertEqual((self.root / 'app.py').read_bytes(), b'x = 1\n')
        with self.assertRaisesRegex(ValueError, 'already been handed'):
            self.engine.build_plan(child, body)

    def test_plan_init_is_idempotent_and_cancelled_writes_are_rejected(self):
        parent = self.engine.store.create({'objective': 'Existing work'})
        self.engine.store.state(parent, 'completed', {})
        one = self.engine.prepare_planner(parent)
        self.assertEqual(one, self.engine.prepare_planner(parent))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.engine.planning.write(one['planner_id'], PLAN, cancel)
        with self.assertRaises(ValueError):
            self.engine.planning.write(one['planner_id'], 'x' * 32769, threading.Event())
        self.assertEqual(self.engine.planning.get(parent)['revision'], 0)

    def test_checklist_validation_durability_and_deletion(self):
        child, _, _ = self.plan()
        items = [{'id': 'first', 'title': '<script>untrusted</script>', 'status': 'in_progress'}, {'title': 'Review', 'status': 'todo'}]
        self.engine.planning.tracker(child, items)
        self.assertEqual(self.engine.planning.progress(child)['revision'], 1)
        for bad in [[{'title': 'bad', 'status': 'completed'}], [{'title': 'bad\ncontrol', 'status': 'todo'}], items * 2, [{'title': 'x', 'status': 'in_progress'}, {'title': 'y', 'status': 'in_progress'}], [{}] * 51]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.engine.planning.tracker(child, bad)
        parent = self.engine.planning.get(child)['task_id']
        self.engine.delete(parent)
        with self.assertRaises(KeyError):
            self.engine.store.get(child)
        self.assertFalse((self.engine.data / 'plans' / parent).exists())

    def test_plan_and_checklist_survive_service_reconstruction(self):
        from dikdik.planning import Planning
        child, _, _ = self.plan()
        self.engine.planning.tracker(child, [{'title': 'Read code', 'status': 'done'}])
        self.engine.planning = Planning(self.engine)
        self.assertEqual(self.engine.planning.get(child)['content'], PLAN)
        self.assertEqual(self.engine.planning.progress(child)['tasks'][0]['status'], 'done')

    def test_build_records_execution_consent_without_borrowing_planner_grants(self):
        child, _, _ = self.plan()
        model = Script([ModelResponse('No changes requested for this permission fixture.', [])])
        with patch('dikdik.engine.CompatibleAgentModel', return_value=model):
            parent = self.engine.build_plan(child, {'planner_id': child, 'confirm': True, 'revision': 1, 'verification': 'docker', 'approve_external': True, 'approve_execution': True})['id']
            self.idle(parent)
        self.engine.bash._approved_task(parent)
        with self.assertRaises(PermissionError):
            self.engine.bash._approved_task(child)

    def test_deleted_planner_identity_cannot_approve_a_recreated_revision(self):
        child, _, _ = self.plan()
        parent = self.engine.planning.get(child)['task_id']
        self.engine.delete(child)
        new_plan = self.engine.prepare_planner(parent)
        self.engine.planning.write(new_plan['planner_id'], PLAN, threading.Event())
        self.assertEqual(self.engine.planning.get(parent)['revision'], 1)
        with self.assertRaisesRegex(ValueError, 'planner changed'):
            self.engine.build_plan(parent, {'confirm': True, 'revision': 1, 'planner_id': child})

    def test_http_requires_auth_and_preserves_plan_scope(self):
        from dikdik.server import make_server
        server = make_server(self.engine, 0, 'token')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        child, _, _ = self.plan()
        def call(method, resource, body=None, auth='token'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request(method, resource, json.dumps(body) if body is not None else None, {'Authorization': 'Bearer ' + auth})
            response = connection.getresponse()
            value = response.status, json.loads(response.read())
            connection.close()
            return value
        for method, resource in [('GET', f'/api/tasks/{child}/planner'), ('POST', f'/api/tasks/{child}/planner'), ('POST', f'/api/tasks/{child}/planner/build'), ('GET', f'/api/tasks/{child}/progress')]:
            self.assertEqual(call(method, resource, {}, auth='wrong')[0], 401)
        self.assertEqual(call('GET', f'/api/tasks/{child}/planner')[1]['content'], PLAN)
        self.assertEqual(call('POST', f'/api/tasks/{child}/planner/build', {'confirm': False})[0], 400)
        self.assertEqual(call('GET', '/api/tasks/unknown/planner')[0], 404)
