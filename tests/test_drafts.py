import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.drafts import DraftConflict, Drafts
from dikdik.engine import Engine
from dikdik.server import make_server


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        root = base / 'repo'
        root.mkdir()
        (root / 'a.py').write_bytes(b'x = 1\n')
        self.engine = Engine(root, base / 'data')
        self.addCleanup(self.engine.close)
        self.drafts = Drafts(self.engine.store)
        self.task = self.engine.store.create({'objective': 'fixture'})

    def test_scoped_drafts_survive_reconstruction_and_never_run_tasks(self):
        before = self.engine.store.get(self.task)
        self.assertEqual(self.drafts.get('home')['revision'], 0)
        self.drafts.save('home', {'content': 'Home text', 'revision': 0})
        self.drafts.save(self.task, {'content': '<script>data</script>\nUnicode 🦌', 'revision': 0})
        reopened = Drafts(self.engine.store)
        self.assertEqual(reopened.get('home')['content'], 'Home text')
        self.assertIn('<script>', reopened.get(self.task)['content'])
        self.assertEqual(self.engine.store.get(self.task), before)
        self.assertFalse(self.engine.active)

    def test_conflict_does_not_overwrite_and_clear_preserves_revision(self):
        first = self.drafts.save(self.task, {'content': 'first tab', 'revision': 0})
        with self.assertRaises(DraftConflict) as caught:
            self.drafts.save(self.task, {'content': 'other tab', 'revision': 0})
        self.assertEqual(caught.exception.current, first)
        cleared = self.drafts.save(self.task, {'content': '', 'revision': 1})
        self.assertEqual(cleared['revision'], 2)
        with self.assertRaises(DraftConflict):
            self.drafts.save(self.task, {'content': 'stale resurrection', 'revision': 1})
        self.assertEqual(self.drafts.get(self.task)['content'], '')

    def test_input_bounds_unknown_scope_and_permission_fields(self):
        for body in [{'content': 'x' * 4001, 'revision': 0}, {'content': '\0', 'revision': 0}, {'content': 'x', 'revision': True}, {'content': 'x', 'revision': -1}, {'content': 'x', 'revision': 0.0}, {'content': 'x', 'revision': 0, 'approve_execution': True}]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.drafts.save(self.task, body)
        for scope in ('../outside', 'not-a-task', 'HOME'):
            with self.assertRaises(ValueError):
                self.drafts.get(scope)
        with self.assertRaises(KeyError):
            self.drafts.get('0' * 32)

    def test_concurrent_writers_have_one_winner(self):
        barrier, outcomes = threading.Barrier(2), []
        def write(text):
            barrier.wait(2)
            try:
                self.drafts.save(self.task, {'content': text, 'revision': 0})
                outcomes.append('saved')
            except DraftConflict:
                outcomes.append('conflict')
        threads = [threading.Thread(target=write, args=(text,)) for text in ('one', 'two')]
        for worker in threads: worker.start()
        for worker in threads: worker.join(5)
        self.assertEqual(sorted(outcomes), ['conflict', 'saved'])
        self.assertEqual(self.drafts.get(self.task)['revision'], 1)

    def test_task_deletion_purges_only_its_draft(self):
        self.drafts.save(self.task, {'content': 'private task draft', 'revision': 0})
        self.drafts.save('home', {'content': 'home survives', 'revision': 0})
        self.engine.delete(self.task)
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM composer_drafts WHERE scope=?', (self.task,)).fetchone()[0], 0)
        self.assertEqual(self.drafts.get('home')['content'], 'home survives')

    def test_http_auth_validation_and_conflict(self):
        server = make_server(self.engine, 0, 'token')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def call(method, scope, body=None, token='token'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request(method, '/api/drafts/' + scope, json.dumps(body) if body is not None else None, {'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            value = response.status, json.loads(response.read())
            connection.close()
            return value
        self.assertEqual(call('GET', 'home', token='wrong')[0], 401)
        self.assertEqual(call('PATCH', 'home', {'content': 'x', 'revision': 0}, token='wrong')[0], 401)
        self.assertEqual(call('PATCH', 'home', {'content': 'x', 'revision': 0})[0], 200)
        status, conflict = call('PATCH', 'home', {'content': 'overwrite', 'revision': 0})
        self.assertEqual(status, 409)
        self.assertEqual(conflict['current']['content'], 'x')
        self.assertEqual(call('GET', '0' * 32)[0], 404)
        self.assertEqual(call('PATCH', 'home', {'content': 'x'})[0], 400)
