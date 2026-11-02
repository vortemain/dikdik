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

    def test_crlf_project_edit_succeeds_on_the_first_attempt(self):
        self.original = {'src/Footer.tsx': '<footer className="border-t">\r\n  Old\r\n</footer>\r\n'}
        result = self.run_script([call('edit', 'edit_file', path='src/Footer.tsx',
                                       before='<footer className="border-t">\n  Old\n', after='<footer className="bg-ink">\n  New\n'),
                                  agent.ModelResponse('Updated the footer.', [])])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.files['src/Footer.tsx'], '<footer className="bg-ink">\r\n  New\r\n</footer>\r\n')

    def test_overwrite_rewrites_an_existing_file_without_the_shell(self):
        result = self.run_script([call('rewrite', 'create_file', path='src/routes/index.tsx', overwrite=True,
                                       content='<footer>Premium</footer>\n'),
                                  call('verify', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.files['src/routes/index.tsx'], '<footer>Premium</footer>\n')
        rewritten = [data['observation'] for kind, data in self.events if kind == 'tool' and data.get('phase') == 'end' and data['id'] == 'rewrite']
        self.assertEqual(rewritten[0]['rewritten'], 'src/routes/index.tsx')

    def test_overwrite_keeps_tests_protected(self):
        self.original['tests/test_nav.py'] = 'assert True\n'
        result = self.run_script([call('rewrite', 'create_file', path='tests/test_nav.py', overwrite=True, content='pass\n'),
                                  agent.ModelResponse('Could not change the test.', [])])
        self.assertEqual(result.files['tests/test_nav.py'], 'assert True\n')
        errors = [data['observation'].get('error', '') for kind, data in self.events if kind == 'tool' and data.get('phase') == 'end']
        self.assertIn('protected', errors[0])

    def test_unrelated_preexisting_parse_quirks_do_not_block_completion(self):
        # A project whose untouched TSX the brace checker cannot parse must still let an edit finish.
        self.original['app/blog/page.tsx'] = 'const pattern = /["\']/;\n'
        result = self.run_script([self.edit(), call('verify', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.checks[0]['status'], 'passed')

    def test_compaction_frees_real_room_instead_of_running_every_step(self):
        self.original = {f'src/file{i}.ts': f'export const v{i} = "{"x" * 9000}";\n' for i in range(40)}
        reads = [call(f'r{i}', 'read_file', path=f'src/file{i}.ts') for i in range(40)]
        # "Refactor" compacts at 60%: the old fixed 80 KB target sat above that trigger,
        # so 40 reads compacted 30 times and discarded the prompt cache each time.
        result = agent.run_agent(self.original, 'Refactor these modules', Script(reads + [agent.ModelResponse('Done.', [])]),
                                 None, lambda kind, data: self.events.append((kind, data)), threading.Event())
        self.assertEqual(result.state, 'completed', result.error)
        compactions = [data for kind, data in self.events if kind == 'context_compacted']
        self.assertTrue(compactions)
        self.assertLessEqual(len(compactions), 12)

    def test_provider_context_window_overflow_compacts_and_retries(self):
        from dikdik.model_errors import ProviderError
        self.original = {f'src/file{i}.ts': f'export const v{i} = "{"x" * 9000}";\n' for i in range(8)}
        replies = [call(f'r{i}', 'read_file', path=f'src/file{i}.ts') for i in range(8)]
        class Small(Script):
            def respond(self, messages, tools):
                if agent.context_size(messages) > 48 * 1024:
                    raise ProviderError('context_length', 'too long')
                return super().respond(messages, tools)
        result = agent.run_agent(self.original, 'Explain these modules', Small(replies + [agent.ModelResponse('Done.', [])]),
                                 None, lambda kind, data: self.events.append((kind, data)), threading.Event())
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(any(data.get('reason') == 'provider_context_window' for kind, data in self.events if kind == 'context_compacted'))

    def test_checkpoints_hold_changes_only_and_resume_rebuilds_the_project(self):
        self.original.update({f'large{i}.txt': 'x' * 100000 for i in range(20)})
        earlier = {**self.original, 'notes.md': 'from an earlier turn\n'}  # a follow-up starts from earlier changes
        saved = []
        first = self.run_script([self.edit()], budget=agent.AgentBudget(max_model_calls=1), start=earlier,
                                checkpoint=saved.append)
        self.assertEqual(first.state, 'paused')
        self.assertLess(len(agent._json(saved[-1])), 20000)  # not the 2 MB project
        self.assertEqual(sorted(saved[-1]['candidate_changes']), ['notes.md', 'src/routes/index.tsx'])
        resumed = self.run_script([agent.ModelResponse('Removed the border.', [])], resume=first.transcript)
        self.assertEqual(resumed.state, 'completed', resumed.error)
        self.assertEqual(resumed.files, {**earlier, 'src/routes/index.tsx': first.files['src/routes/index.tsx']})
        self.assertEqual(len(self.checked), 1)  # the rebuilt turn start still knew the edit needed verification

    def test_several_replacements_in_one_call_apply_together_or_not_at_all(self):
        path = 'src/routes/index.tsx'
        bad = call('bad', 'edit_file', path=path, edits=[{'before': 'sticky', 'after': 'fixed'}, {'before': 'missing', 'after': 'x'}])
        good = call('good', 'edit_file', path=path, edits=[{'before': 'sticky', 'after': 'fixed'}, {'before': 'Menu', 'after': 'Nav'}])
        result = self.run_script([bad, good, call('verify', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.files[path], '<header className="fixed top-0 border-b bg-background/95">Nav</header>\n')
        ends = {data['id']: data for kind, data in self.events if kind == 'tool' and data.get('phase') == 'end'}
        self.assertIn('not found', ends['bad']['observation']['error'])  # nothing from the failed batch was applied

    def test_file_text_reaches_the_model_as_numbered_plain_lines(self):
        self.original['src/Card.tsx'] = '<a href="/shop">Kenya’s rigs</a>\r\n<p>two</p>\r\n'
        script = Script([call('read', 'read_file', path='src/Card.tsx'), call('find', 'search_text', query='two'),
                         agent.ModelResponse('Read it.', [])])
        result = agent.run_agent(self.original, 'Explain the card', script, None, lambda *a: None, threading.Event())
        self.assertEqual(result.state, 'completed', result.error)
        read, found = script.seen[1][-1]['content'], script.seen[2][-1]['content']
        self.assertIn('1\t<a href="/shop">Kenya’s rigs</a>\n2\t<p>two</p>', read)  # no \\" or \\u2019 escapes
        self.assertEqual(found, '1 matches\nsrc/Card.tsx:2:4: <p>two</p>')

    def test_common_argument_habits_work_instead_of_failing(self):
        # From real runs: sandbox-style /workspace paths, ./ prefixes, end_line, an out-of-range
        # budget and arguments passed to finish all used to cost a failed call each.
        self.original['src/a.ts'] = ''.join(f'line {i}\n' for i in range(1, 11))
        result = self.run_script([
            call('read', 'read_file', path='/workspace/src/a.ts', start_line=2, end_line=4),
            call('list', 'list_files', path='/'),
            call('read2', 'read_file', path='./src/a.ts', max_lines=999),
            self.edit(), call('verify', 'run_tests'), call('finish', 'finish', path='src/routes/index.tsx')])
        self.assertEqual(result.state, 'completed', result.error)
        ends = {data['id']: data for kind, data in self.events if kind == 'tool' and data.get('phase') == 'end'}
        self.assertFalse(any(data['failed'] for data in ends.values()), ends)
        self.assertEqual(ends['read']['observation']['line_numbers'], [2, 3, 4])

    def test_editing_check_configuration_is_called_out(self):
        self.original['eslint.config.js'] = 'export default [{ rules: { "prettier/prettier": "error" } }];\n'
        result = self.run_script([call('relax', 'edit_file', path='eslint.config.js', before='"error"', after='"warn"'),
                                  call('restore', 'edit_file', path='eslint.config.js', before='"warn"', after='"error"'),
                                  self.edit(), call('verify', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        ends = {d['id']: d['observation'] for k, d in self.events if k == 'tool' and d.get('phase') == 'end'}
        self.assertIn('Do not relax rules', ends['relax']['warning'])
        self.assertNotIn('warning', ends['edit'])

    def test_other_agents_argument_names_are_understood(self):
        result = self.run_script([
            call('read', 'read_file', file_path='src/routes/index.tsx'),
            call('edit', 'edit_file', file_path='src/routes/index.tsx', old_string='border-b ', new_string=''),
            call('verify', 'run_tests'), call('finish', 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertNotIn('border-b', result.files['src/routes/index.tsx'])

    def test_repeated_refused_finishes_stop_with_the_reason(self):
        # The real 34-refusal loop: a small edit, then finish without verification, again and again.
        steps = []
        for i in range(6):
            steps += [self.edit(f'e{i}', 'Menu' + '!' * i, 'Menu' + '!' * (i + 1)), call(f'f{i}', 'finish')]
        result = agent.run_agent(self.original, 'Remove the navbar bottom border', Script(steps),
                                 lambda files: {'kind': 'tests', 'status': 'failed'},
                                 lambda kind, data: self.events.append((kind, data)), threading.Event())
        self.assertEqual(result.state, 'blocked')
        self.assertIn('Completion was refused 6 times', result.error)
        self.assertEqual(result.model_calls, 12)

    def test_reading_through_the_shell_is_steered_back_to_file_tools_once(self):
        shell = lambda files, command, timeout, image: {'status': 'passed', 'exit_code': 0, 'output': 'x', 'changed': {}}
        result = self.run_script([call('c1', 'run_command', command='cat src/routes/index.tsx'),
                                  call('c2', 'run_command', command='head -5 src/routes/index.tsx'),
                                  agent.ModelResponse('It renders the menu.', [])], shell=shell)
        self.assertEqual(result.state, 'completed', result.error)
        ends = {data['id']: data['observation'] for kind, data in self.events if kind == 'tool' and data.get('phase') == 'end'}
        self.assertIn('read_file', ends['c1']['tip'])
        self.assertNotIn('tip', ends['c2'])

    def test_local_shell_tool_describes_the_project_folder_not_a_sandbox(self):
        self.assertNotIn('Docker', agent.LOCAL_SHELL_TOOL['function']['description'])
        self.assertNotIn('image', agent.LOCAL_SHELL_TOOL['function']['parameters']['properties'])

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
