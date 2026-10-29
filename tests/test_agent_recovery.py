"""Regressions from real task failures: folder arguments, cumulative usage and recovery."""
import json
import threading
import unittest
from unittest.mock import patch

from dikdik.agent import AgentBudget, ModelResponse, ToolCall, context_size, run_agent


class Script:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def respond(self, messages, tools):
        self.seen.append(messages)
        return next(self.responses)


def call(identity, name, usage=0, **arguments):
    return ModelResponse('', [ToolCall(str(identity), name, arguments)], usage,
                         usage={'input_tokens': max(0, usage - 20), 'output_tokens': min(20, usage), 'total_tokens': usage})


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original = {'app.py': 'value = 1\n', 'client/src/Products.tsx': 'export const title = "Products";\n'}
        self.events = []
        self.checked = []

    def verify(self, files):
        self.checked.append(dict(files))
        return {'kind': 'unittest', 'status': 'passed'}

    def run_script(self, script, **kwargs):
        return run_agent(self.original, 'Fix the value and verify it', script, self.verify,
                         lambda kind, data: self.events.append((kind, data)), threading.Event(), **kwargs)

    def edit(self, identity, usage=0):
        return call(identity, 'edit_file', usage, path='app.py', before='1', after='2')

    def assert_protocol(self, messages):
        pending = set()
        for message in messages:
            if message['role'] == 'tool':
                self.assertIn(message['tool_call_id'], pending)
                pending.remove(message['tool_call_id'])
            else:
                self.assertFalse(pending, 'Tool batch lost its results')
                pending = {item['id'] for item in message.get('tool_calls', [])}
        self.assertFalse(pending)

    def test_actual_folder_arguments_and_usage_above_old_32000_cap_complete(self):
        model = Script([
            call(1, 'list_files', 6000, path='client/src', limit=100, query='products'),
            call(2, 'read_file', 6200, path='client/src/Products.tsx'),
            call(3, 'list_files', 6400, path='.'),
            self.edit(4, 6600), call(5, 'run_tests', 6800), call(6, 'finish', 7000),
        ])
        result = self.run_script(model)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.usage_tokens, 39000)
        self.assertEqual(result.usage['total_tokens'], 39000)
        self.assertTrue(result.tests_verified)
        listed = json.loads(model.seen[1][-1]['content'])
        self.assertEqual(listed['files'], ['client/src/Products.tsx'])
        self.assertFalse(any(kind == 'tool' and data.get('failed') for kind, data in self.events))

    def test_two_identical_argument_errors_can_recover_and_finish(self):
        model = Script([
            call(1, 'list_files', glob='client/src/*'), call(2, 'list_files', glob='client/src/*'),
            call(3, 'list_files', path='client/src'), self.edit(4), call(5, 'run_tests'), call(6, 'finish'),
        ])
        result = self.run_script(model)
        self.assertEqual(result.state, 'completed', result.error)
        error = json.loads(model.seen[2][-1]['content'])
        self.assertIn('Unexpected: glob', error['error'])
        self.assertIn('path', error['error'])
        self.assertIn('Change its arguments', error['recovery'])
        self.assertTrue(any(kind == 'recovery' for kind, _ in self.events))

    def test_repeated_external_failure_is_not_executed_four_times(self):
        executions = []
        def shell(*args):
            executions.append(args)
            return {'status': 'failed', 'exit_code': 1, 'output': 'missing executable', 'changed': {}}
        model = Script([call(i, 'run_command', command='missing-command') for i in range(4)])
        result = self.run_script(model, shell=shell)
        self.assertEqual(result.state, 'paused')
        self.assertEqual(len(executions), 2)
        self.assertEqual(result.files, self.original)
        self.assertIn('still failing', result.error)
        self.assertIn('missing executable', result.error)
        self.assert_protocol(result.transcript['messages'])

    def test_explicit_call_limit_keeps_high_usage_and_resumes_with_verified_state(self):
        first = Script([self.edit(1, 600000), call(2, 'run_tests', 600000)])
        result = self.run_script(first, budget=AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, 'paused')
        self.assertEqual(result.usage_tokens, 1200000)
        self.assertTrue(result.transcript['verified'])
        self.assertIn('model-call', result.error)
        self.original = dict(result.files)  # Local mode already wrote the checkpointed candidate.
        resumed = self.run_script(Script([call(3, 'finish')]), resume=result.transcript)
        self.assertEqual(resumed.state, 'completed', resumed.error)
        self.assertTrue(resumed.tests_verified)
        self.assertEqual(resumed.files['app.py'], 'value = 2\n')
        self.assertEqual(len(self.checked), 1)

    def test_final_response_is_not_limited_by_reported_tokens(self):
        result = self.run_script(Script([self.edit(1), call(2, 'run_tests'), call(3, 'finish', 5000000)]))
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(result.usage_tokens, 5000000)

    def test_long_task_crosses_former_call_tool_and_token_caps_with_context_refresh(self):
        self.original['large.txt'] = 'x' * 20000
        replies = []
        for batch in range(72):
            tools = [ToolCall(f'{batch}-{item}', 'read_file', {'path': 'large.txt', 'start_column': batch * 3 + item + 1, 'max_chars': 512}) for item in range(3)]
            replies.append(ModelResponse('', tools, 70000, usage={'input_tokens': 69900, 'output_tokens': 100, 'total_tokens': 70000}))
        model = Script([*replies, self.edit('edit'), call('tests', 'run_tests'), call('finish', 'finish')])
        result = self.run_script(model)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual((result.model_calls, result.tool_calls), (75, 219))
        self.assertEqual(result.usage_tokens, 5040000)
        self.assertEqual(result.usage['total_tokens'], 5040000)
        self.assertTrue(any(kind == 'context_compacted' for kind, _ in self.events))
        for messages in model.seen:
            self.assertLessEqual(context_size(messages), 128 * 1024)
            self.assert_protocol(messages)

    def test_default_run_has_no_elapsed_time_cutoff(self):
        clock = [0]
        class LongModel:
            def respond(self, messages, tools):
                clock[0] += 7200
                return ModelResponse('Investigation complete.', [], 2000000)
        with patch('dikdik.agent.time.monotonic', side_effect=lambda: clock[0]):
            result = self.run_script(LongModel())
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(clock[0], 7200)

    def test_default_repairs_can_continue_with_actual_progress(self):
        replies = []
        for value in range(1, 6):
            replies.extend([call(f'edit-{value}', 'edit_file', path='app.py', before=str(value), after=str(value + 1)), call(f'test-{value}', 'run_tests')])
        replies.append(call('finish', 'finish'))
        result = run_agent(self.original, 'Repair and verify', Script(replies),
                           lambda files: {'kind': 'unittest', 'status': 'passed' if files['app.py'] == 'value = 6\n' else 'failed'},
                           lambda *args: None, threading.Event())
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(result.repair_attempts, 4)

    def test_tool_limit_closes_unexecuted_batch_and_resume_does_not_replay_edit(self):
        model = Script([ModelResponse('', [self.edit(1).tool_calls[0], call(2, 'run_tests').tool_calls[0]])])
        result = self.run_script(model, budget=AgentBudget(max_tool_calls=1))
        self.assertEqual(result.state, 'paused')
        self.assertEqual(self.checked, [])
        self.assertEqual(result.files['app.py'], 'value = 2\n')
        self.assert_protocol(result.transcript['messages'])
        resumed = Script([call(3, 'run_tests'), call(4, 'finish')])
        done = self.run_script(resumed, resume=result.transcript)
        self.assertEqual(done.state, 'completed', done.error)
        self.assert_protocol(resumed.seen[0])
        self.assertEqual(len(self.checked), 1)

    def test_long_workflow_compacts_observations_without_losing_files_or_verification(self):
        for i in range(18):
            self.original[f'file{i}.txt'] = f'File {i}: ' + 'x' * 12000
        replies = [call(i, 'read_file', 12000, path=f'file{i}.txt') for i in range(18)]
        model = Script([*replies, self.edit(19), call(20, 'run_tests'), call(21, 'finish')])
        result = self.run_script(model)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(result.files['file0.txt'], self.original['file0.txt'])
        self.assertTrue(any(kind == 'context_compacted' for kind, _ in self.events))
        for messages in model.seen:
            self.assertLessEqual(context_size(messages), 128 * 1024)
            self.assertTrue(any(message.get('content') == 'Fix the value and verify it' for message in messages))
            self.assert_protocol(messages)
        self.assertEqual(result.usage_tokens, 216000)

    def test_successful_loop_can_change_approach_after_guidance(self):
        model = Script([*[call(i, 'read_file', path='app.py') for i in range(4)], self.edit(5), call(6, 'run_tests'), call(7, 'finish')])
        result = self.run_script(model)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertIn('choose a different approach', model.seen[4][-1]['content'])


if __name__ == '__main__':
    unittest.main()
