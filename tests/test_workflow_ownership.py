"""The agent owns executable work and verifies requests that need no code edits."""
import threading
import unittest

from dikdik.agent import AgentBudget, ModelResponse, run_agent
from dikdik.control import assess, needs_approval
from dikdik.workflow import approval_question
from dikdik.runners import detect_local_checks
from tests.test_agent_recovery import Script, call


class WorkflowOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.checked, self.commands = [], []
        self.files = {'app.py': 'value = 1\n'}

    def run_script(self, objective, responses, **kwargs):
        def verify(files):
            self.checked.append(files)
            return {'kind': 'tests', 'status': 'passed'}
        def shell(files, command, *args):
            self.commands.append(command)
            return {'status': 'passed', 'changed': {}, 'exit_code': 0}
        return run_agent(self.files, objective, Script(responses), verify, lambda *args: None,
                         threading.Event(), shell=shell, **kwargs)

    def test_test_only_request_runs_verifier_without_requiring_fake_edits(self):
        result = self.run_script('Run and verify the existing tests', [call(1, 'run_tests'), call(2, 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(result.files, self.files)
        self.assertEqual(len(self.checked), 1)

    def test_unsupported_completion_claim_on_test_request_gets_real_verification(self):
        result = self.run_script('Test the project', [ModelResponse('All tests passed.', [])])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(len(self.checked), 1)

    def test_command_handoff_is_redirected_to_available_execution(self):
        result = self.run_script('Run the build', [ModelResponse('Please run `npm run build`.', []),
            call(2, 'run_command', command='npm run build'), call(3, 'run_tests'), call(4, 'finish')])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(self.commands, ['npm run build'])

    def test_repeated_handoff_is_incomplete_not_success(self):
        result = self.run_script('Run the build', [ModelResponse('You should run `npm run build`.', [])] * 2)
        self.assertEqual(result.state, 'blocked')
        self.assertFalse(result.tests_verified)
        self.assertEqual(self.commands, [])  # prose is never executed as a command

    def test_no_workspace_work_cannot_support_an_implementation_claim(self):
        result = self.run_script('Fix the calculation', [ModelResponse('Fixed everything.', [])] * 2)
        self.assertEqual(result.state, 'blocked')
        self.assertFalse(result.tests_verified)

    def test_premature_handoff_does_not_become_a_user_visible_completion_message(self):
        events = []
        result = run_agent(self.files, 'Run the build', Script([
            ModelResponse('Please run `npm run build`.', []), call(2, 'run_tests'), call(3, 'finish')]),
            lambda files: {'kind': 'tests', 'status': 'passed'}, lambda kind, data: events.append((kind, data)),
            threading.Event(), shell=lambda *args: {})
        self.assertEqual(result.state, 'completed', result.error)
        self.assertFalse(any(kind == 'message' and 'Please run' in data.get('content', '') for kind, data in events))

    def test_finish_tool_cannot_bypass_the_command_handoff_guard(self):
        finish = call(2, 'finish')
        finish.content = 'Please run `npm run build` to complete this.'
        result = self.run_script('Run the build', [call(1, 'run_tests'), finish], budget=AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, 'paused')
        self.assertFalse(result.tests_verified)

    def test_explanation_can_include_commands_without_executing_them(self):
        result = self.run_script('How do I run tests?', [ModelResponse('Run `npm test`.', [])])
        self.assertEqual(result.state, 'completed', result.error)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.checked, [])

    def test_design_cannot_finish_on_tests_alone(self):
        result = self.run_script('Fix the navbar design', [
            call(1, 'edit_file', path='app.py', before='1', after='2'), call(2, 'run_tests'), call(3, 'finish')],
            preview=lambda *args: {'status': 'passed'}, budget=AgentBudget(max_model_calls=3))
        self.assertEqual(result.state, 'paused')
        self.assertFalse(result.tests_verified)
        self.assertIn('preview_app', result.transcript['messages'][-1]['content'])

    def test_design_runs_preview_then_current_host_checks(self):
        previews = []
        def preview(files, args):
            previews.append((files, args))
            return {'status': 'passed', 'views': [{'label': 'desktop'}, {'label': 'mobile'}]}
        result = self.run_script('Fix the navbar design', [
            call(1, 'edit_file', path='app.py', before='1', after='2'),
            call(2, 'preview_app', command='npm run dev -- --port {port}'),
            ModelResponse('Changed and checked desktop and mobile views.', [])], preview=preview)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(previews[0][0]['app.py'], 'value = 2\n')
        self.assertEqual(len(self.checked), 1)

    def test_failed_preview_cannot_be_replaced_by_a_success_claim(self):
        result = self.run_script('Verify the responsive design', [
            call(1, 'preview_app', command='npm run dev -- --port {port}'),
            ModelResponse('Looks perfect.', []), ModelResponse('Looks perfect.', [])],
            preview=lambda *args: {'status': 'failed', 'issues': ['mobile overflow']})
        self.assertEqual(result.state, 'blocked')
        self.assertFalse(result.tests_verified)

    def test_auto_does_not_prompt_for_install_or_browser_interactions(self):
        for tool in [call(1, 'run_command', command='npm install').tool_calls[0],
                     call(2, 'preview_app', command='npm install && npm run dev -- --port {port}').tool_calls[0],
                     call(3, 'browser_click', selector='#submit').tool_calls[0]]:
            risk, _ = assess(tool)
            self.assertFalse(needs_approval('never', risk, tool.name))

    def test_permission_question_is_distinguished_from_missing_information(self):
        self.assertTrue(approval_question('Can I run the tests now?'))
        self.assertTrue(approval_question('Would you like me to fix that?'))
        self.assertFalse(approval_question('Which account should the sign-in flow use?'))

    def test_design_checks_include_declared_tests_types_lint_and_build(self):
        files = {'package.json': '{"scripts":{"test":"vitest run","typecheck":"tsc --noEmit","lint":"eslint .","build":"vite build"}}'}
        self.assertEqual([check['kind'] for check in detect_local_checks(files)], ['npm_test'])
        self.assertEqual([check['kind'] for check in detect_local_checks(files, include_static=True)], ['npm_test', 'typecheck', 'lint', 'build'])

    def test_screenshot_alone_does_not_verify_requested_interaction(self):
        result = self.run_script('Test the form submit interaction', [
            call(1, 'preview_app', command='npm run dev -- --port {port}'), call(2, 'finish')],
            preview=lambda *args: {'status': 'passed', 'interactions_checked': 0},
            budget=AgentBudget(max_model_calls=2))
        self.assertEqual(result.state, 'paused')
        self.assertFalse(result.tests_verified)

    def test_mixed_test_and_build_results_preserve_actual_test_evidence(self):
        result = run_agent(self.files, 'Run tests and build', Script([call(1, 'run_tests'), call(2, 'finish')]),
            lambda files: {'kind': 'checks', 'status': 'passed', 'runs': [
                {'kind': 'npm_test', 'status': 'passed'}, {'kind': 'build', 'status': 'passed'}]},
            lambda *args: None, threading.Event(), allow_static_checks=True)
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)

    def test_missing_dependencies_can_be_repaired_with_available_shell(self):
        results = iter([{'kind': 'checks', 'status': 'not_run', 'reason': 'missing_check_tool', 'missing_tools': ['tsc']},
                        {'kind': 'tests', 'status': 'passed'}])
        commands = []
        result = run_agent(self.files, 'Run the project checks', Script([
            call(1, 'run_tests'), call(2, 'run_command', command='npm ci'), call(3, 'run_tests'), call(4, 'finish')]),
            lambda files: next(results), lambda *args: None, threading.Event(), allow_unverified_checks=True,
            shell=lambda files, command, *args: commands.append(command) or {'status': 'passed', 'changed': {}})
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)
        self.assertEqual(commands, ['npm ci'])

    def test_explicit_test_request_cannot_succeed_with_only_syntax(self):
        result = run_agent(self.files, 'Test the project', Script([call(1, 'run_tests')]), None,
                           lambda *args: None, threading.Event())
        self.assertEqual(result.state, 'blocked')
        self.assertFalse(result.tests_verified)

    def test_host_verification_recovery_refreshes_after_dependency_setup(self):
        results = iter([{'kind': 'checks', 'status': 'not_run', 'reason': 'missing_check_tool'},
                        {'kind': 'tests', 'status': 'passed'}])
        result = run_agent(self.files, 'Run the project checks', Script([
            ModelResponse('Done.', []), call(2, 'run_command', command='npm ci'), ModelResponse('Setup is done.', [])]),
            lambda files: next(results), lambda *args: None, threading.Event(), allow_unverified_checks=True,
            shell=lambda *args: {'status': 'passed', 'changed': {}})
        self.assertEqual(result.state, 'completed', result.error)
        self.assertTrue(result.tests_verified)


if __name__ == '__main__':
    unittest.main()
