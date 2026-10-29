"""Real engine dispatch keeps Auto quiet and flushes edits before browser checks."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from tests.test_agent_recovery import Script, call


class WorkflowEngineTests(unittest.TestCase):
    def test_auto_runs_preview_and_verification_without_approval_questions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'; root.mkdir()
            root.joinpath('index.html').write_text('<h1>Old design</h1>')
            engine = Engine(root, Path(temp) / 'data')
            try:
                responses = [call(1, 'ask_question', question='Can I run the checks now?'),
                    call(2, 'edit_file', path='index.html', before='Old design', after='New design'),
                    call(3, 'preview_app', command='npm run dev -- --port {port}'),
                    call(4, 'run_tests'), call(5, 'finish')]
                screenshots = []
                def preview(project, command, cancel, **kwargs):
                    self.assertIn('New design', project.joinpath('index.html').read_text())
                    view = {'url': 'http://preview.dikdik.example/', 'title': 'Fixture', 'text': 'New design'}
                    screenshots.append(kwargs['save_screenshot'](view, b'\x89PNG\r\n\x1a\n' + b'x' * 32))
                    return {'status': 'passed', 'views': [view]}
                with patch.dict('os.environ', {'DIKDIK_MODEL_URL': 'https://provider.example/v1/chat/completions', 'DIKDIK_MODEL': 'fixture', 'DIKDIK_API_KEY': 'fixture'}), \
                        patch('dikdik.engine.CompatibleAgentModel', return_value=Script(responses)), \
                        patch('dikdik.local_preview.preview', side_effect=preview), \
                        patch('dikdik.local_exec.run_tests', return_value={'kind': 'tests', 'status': 'passed'}), \
                        patch.object(engine, '_ask_question', side_effect=AssertionError('Auto asked for redundant permission')):
                    task = engine.submit({'objective': 'Fix the website design', 'mode': 'agent', 'verification': 'local',
                        'approve_external': True, 'approve_execution': True, 'approve_browser': True, 'confirmation': 'never'})
                    deadline = time.monotonic() + 10
                    while engine.busy(task) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(engine.busy(task))
                data = engine.store.get(task)
                self.assertEqual(data['state'], 'completed', data['result'])
                self.assertTrue(data['result']['tests_verified'])
                self.assertFalse(any(event['kind'] in {'confirmation_requested', 'question_requested'} for event in data['events']))
                self.assertEqual(len(screenshots), 1)
                self.assertTrue(engine.browser.screenshot(task, screenshots[0].rsplit('/', 1)[-1]).startswith(b'\x89PNG'))
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()
