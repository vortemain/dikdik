import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from dikdik.agent import ModelResponse, ToolCall, run_agent
from dikdik.repository import inventory
from dikdik.workspace_files import WorkspaceFiles, read_page


class WorkspaceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'client/public').mkdir(parents=True)
        self.path = 'client/public/Cover.svg'
        self.text = '<svg>' + 'x' * 300000 + 'fill="red"' + '</svg>'
        (self.root / self.path).write_bytes(self.text.encode())
        self.workspace = WorkspaceFiles(self.root)

    def test_discovery_read_and_search_include_large_minified_assets(self):
        self.assertNotIn(self.path, inventory(self.root))
        self.assertIn(self.path, self.workspace.paths())
        found = self.workspace.search({}, 'fill="red"')
        match = found['matches'][0]
        self.assertEqual((match['path'], match['line'], match['column']), (self.path, 1, 300006))
        page = self.workspace.read(self.path, {}, start_line=1, start_column=match['column'], max_chars=10)
        self.assertEqual(page['lines'], ['fill="red"'])
        self.assertTrue(page['truncated'])
        self.assertEqual(page['next_start_column'], match['column'] + 10)

    def test_every_character_of_a_long_line_can_be_read_by_continuation(self):
        source, combined = '😀' * 10000 + 'é' * 1000, ''
        column = 1
        while True:
            page = read_page(io.StringIO(source), 'unicode.txt', start_column=column, max_chars=32000)
            self.assertLess(len(json.dumps(page, ensure_ascii=True)), 65536)
            combined += ''.join(page['lines'])
            if not page['truncated']:
                break
            self.assertGreater(page['next_start_column'], column)
            column = page['next_start_column']
        self.assertEqual(combined, source)

    def test_search_handles_fragment_boundaries_and_paginates_without_losing_matches(self):
        text = 'x' * 4093 + 'needle' + 'y' * 5000 + 'needle' + 'z'
        (self.root / self.path).write_bytes(text.encode())
        page = self.workspace.search({}, 'needle', max_results=1)
        self.assertEqual(page['matches'][0]['column'], 4094)
        self.assertTrue(page['truncated'])
        following = self.workspace.search({}, 'needle', offset=page['next_offset'], max_results=1)
        self.assertEqual(following['matches'][0]['column'], 9100)
        self.assertFalse(following['truncated'])
        self.assertEqual(self.workspace.search({self.path: 'changed'}, 'needle')['matches'], [])

    def test_read_pages_preserve_line_positions_and_candidate_changes(self):
        page = self.workspace.read(self.path, {self.path: 'first\nsecond\nthird\n'}, max_lines=1)
        self.assertEqual(page['lines'], ['first'])
        self.assertEqual((page['next_start_line'], page['next_start_column']), (2, 1))
        second = self.workspace.read(self.path, {self.path: 'first\nsecond\nthird\n'}, start_line=2, max_lines=1)
        self.assertEqual(second['lines'], ['second'])

    def test_existing_ignores_secrets_and_traversal_remain_protected(self):
        (self.root / '.gitignore').write_text('ignored/\n')
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored/a.svg').write_text('private')
        (self.root / 'token.txt').write_text('private')
        for path in ('../outside.svg', '/absolute.svg', 'client\\public\\Cover.svg', 'token.txt', 'ignored/a.svg'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.read(path, {})

    def test_changed_since_read_is_not_silently_edited(self):
        self.workspace.read(self.path, {}, max_chars=10)
        (self.root / self.path).write_bytes((self.text + 'outside change').encode())
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.workspace.load_for_edit(self.path)

    def test_large_file_content_reaches_model_and_targeted_edit_keeps_the_rest(self):
        replies = iter([
            ModelResponse('', [ToolCall('s', 'search_text', {'query': 'fill="red"'})]),
            ModelResponse('', [ToolCall('r', 'read_file', {'path': self.path, 'start_column': 300006, 'max_chars': 10})]),
            ModelResponse('', [ToolCall('e', 'edit_file', {'path': self.path, 'before': 'fill="red"', 'after': 'fill="blue"'})]),
            ModelResponse('', [ToolCall('t', 'run_tests', {})]),
            ModelResponse('', [ToolCall('f', 'finish', {})]),
        ])
        seen = []
        class Model:
            def respond(inner, messages, tools):
                seen.append(json.loads(json.dumps(messages)))
                return next(replies)
        original = inventory(self.root)
        outcome = run_agent(original, 'Change cover color', Model(), None, lambda *args: None,
                            threading.Event(), workspace=self.workspace)
        self.assertEqual(outcome.state, 'completed', outcome.error)
        self.assertIn('fill=\\"red\\"', seen[2][-1]['content'])
        self.assertEqual(outcome.files[self.path], self.text.replace('fill="red"', 'fill="blue"'))
        self.assertEqual(original[self.path], self.text)
        self.assertEqual((self.root / self.path).read_bytes().decode(), self.text)

    def test_paged_read_respects_cancellation(self):
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            read_page(io.StringIO('text'), 'a.txt', cancel=cancel)


if __name__ == '__main__':
    unittest.main()
