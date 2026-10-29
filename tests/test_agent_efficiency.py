"""Small-edit call budgets and invalidation of reusable verification evidence."""
import threading
import unittest
from unittest.mock import patch

import dikdik.agent as agent
from tests.test_agent_recovery import Script, call


class AgentEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.original = {'src/routes/index.tsx':
                         '<header className="sticky top-0 border-b bg-background/95">Menu</header>\n'}
        self.checked = []
        self.events = []

    def edit(self, identity='edit', before='border-b ', after=''):
        return call(identity, 'edit_file', path='src/routes/index.tsx', before=before, after=after)

    def run_script(self, responses, **kwargs):
        def verify(files):
            self.checked.append(dict(files))
            return {'kind': 'tests', 'status': 'passed'}
        return agent.run_agent(self.original, 'Remove the navbar bottom border', Script(responses),
                               verify, lambda kind, data: self.events.append((kind, data)),
                               threading.Event(), **kwargs)

    def test_navbar_edit_finishes_in_five_model_calls_and_one_check(self):
        result = self.run_script([
            call('search', 'search_text', query='<header'),
            call('read', 'read_file', path='src/routes/index.tsx'), self.edit(),
            call('verify', 'run_tests'), call('finish', 'finish'),
        ], budget=agent.AgentBudget(max_model_calls=5))
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual((result.model_calls, result.tool_calls, len(self.checked)), (5, 5, 1))
        self.assertNotIn('border-b', result.files['src/routes/index.tsx'])

    def test_plain_completion_verifies_and_stops_without_another_model_call(self):
        result = self.run_script([self.edit(), agent.ModelResponse('Removed the border.', [])],
                                 budget=agent.AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual((result.model_calls, len(self.checked)), (2, 1))

    def test_repeated_checks_reuse_evidence_until_the_next_edit(self):
        result = self.run_script([self.edit(), call('test1', 'run_tests'), call('test2', 'run_tests'),
                                  self.edit('edit2', '/95', '/80'), call('test3', 'run_tests'),
                                  call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(len(self.checked), 2)
        cached = [data['observation'] for kind, data in self.events
                  if kind == 'tool' and data.get('phase') == 'end' and data['id'] == 'test2']
        self.assertTrue(cached[0]['cached'])
        self.assertIn('/95', self.checked[0]['src/routes/index.tsx'])
        self.assertIn('/80', self.checked[1]['src/routes/index.tsx'])

    def test_shell_invalidates_verification_even_without_text_changes(self):
        result = self.run_script([self.edit(), call('test1', 'run_tests'),
                                  call('install', 'run_command', command='npm install'),
                                  call('test2', 'run_tests'), call('finish', 'finish')],
                                 shell=lambda *args: {'status': 'passed', 'exit_code': 0, 'changed': {}})
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(len(self.checked), 2)

    def test_explicit_fresh_verification_can_disprove_a_previous_pass(self):
        results = iter([{'kind': 'tests', 'status': 'passed'}, {'kind': 'tests', 'status': 'failed'}])
        responses = [self.edit(), call('test1', 'run_tests'), call('test2', 'run_tests', force=True),
                     call('finish', 'finish')]
        result = agent.run_agent(self.original, 'Check for flaky tests', Script(responses),
                                  lambda files: next(results), lambda *args: None,
                                  threading.Event(), agent.AgentBudget(max_model_calls=4))
        self.assertEqual(result.state, 'paused')
        self.assertFalse(result.tests_verified)
        self.assertEqual([c['status'] for c in result.checks if c['kind'] == 'tests'], ['passed', 'failed'])

    def test_shell_cannot_finish_using_verification_from_before_the_command(self):
        result = self.run_script([self.edit(), call('test1', 'run_tests'),
                                  call('install', 'run_command', command='npm install'),
                                  call('finish', 'finish')],
                                 budget=agent.AgentBudget(max_model_calls=4),
                                 shell=lambda *args: {'status': 'passed', 'changed': {}})
        self.assertEqual(result.state, 'paused')
        self.assertFalse(result.tests_verified)

    def test_loop_signatures_do_not_serialize_project_contents(self):
        # Track signature payloads rather than asserting a machine-dependent duration.
        # Large unrelated files should never be copied into per-tool loop hashes.
        self.original.update({f'large{i}.txt': 'x' * 200000 for i in range(20)})
        signature_sizes = []
        serialize = agent._json
        def observe(value):
            text = serialize(value)
            if (isinstance(value, list) and len(value) == 3 and isinstance(value[0], str)
                    and value[0] in {'read_file', 'edit_file', 'run_tests', 'finish'}):
                signature_sizes.append(len(text))
            return text
        with patch('dikdik.agent._json', side_effect=observe):
            result = self.run_script([call('read', 'read_file', path='src/routes/index.tsx'),
                                      self.edit(), call('check', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(signature_sizes)
        self.assertLess(max(signature_sizes), 1000)


if __name__ == '__main__':
    unittest.main()
