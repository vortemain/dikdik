import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from dikdik.html_preview import HTMLPreviews, PREVIEW_CSP, static_preview
from dikdik.server import make_server


class StaticPreviewTests(unittest.TestCase):
    def test_only_static_design_markup_survives(self):
        source = ('<meta http-equiv="refresh" content="0;url=https://evil.example"><base href="https://evil.example">'
                  '<script>fetch("/api/tasks")</script><iframe src="https://evil.example"></iframe>'
                  '<svg><a href="javascript:alert(1)">bad</a></svg><form action="https://evil.example">'
                  '<button onclick="alert(1)">Send</button></form><a href="https://evil.example">Outside</a>'
                  '<a href="#details">Details</a><img src="https://evil.example/pixel" onerror="alert(1)">'
                  '<style>.card{color:red}</style><section class="card"><details open><summary>More</summary>Safe &amp; useful</details></section>')
        page = static_preview(source).decode()
        for blocked in ('evil.example', '<script', '<iframe', '<svg', '<form', 'onclick', 'onerror', 'http-equiv', '<base'):
            self.assertNotIn(blocked, page)
        self.assertIn('<button disabled>', page)
        self.assertIn('href="#details"', page)
        self.assertIn('.card{color:red}', page)
        self.assertIn('<details open="">', page)
        self.assertIn('Safe &amp; useful', page)

    def test_encoded_text_cannot_turn_back_into_markup(self):
        page = static_preview('<p>&lt;script&gt;alert(1)&lt;/script&gt;</p><img src="data:image/svg+xml,evil">').decode()
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        self.assertNotIn('svg+xml', page)
        for bad in ('', ' ', None, {'html': 'x'}, 'x' * 50001):
            with self.subTest(value=type(bad).__name__), self.assertRaises(ValueError):
                static_preview(bad)

    def test_previews_are_project_scoped_bounded_and_expire(self):
        previews = HTMLPreviews()
        with patch('dikdik.html_preview.time.monotonic', return_value=100):
            first = previews.create('<p>First</p>', 'project-a')['url'].split('/')[-1]
            self.assertIn(b'First', previews.get(first, 'project-a'))
            with self.assertRaises(KeyError):
                previews.get(first, 'project-b')
            for _ in range(16):
                previews.create('<p>Next</p>', 'project-a')
            self.assertEqual(len(previews.entries), 16)
            with self.assertRaises(KeyError):
                previews.get(first, 'project-a')
        with patch('dikdik.html_preview.time.monotonic', return_value=701), self.assertRaises(KeyError):
            previews.get(next(iter(previews.entries)), 'project-a')


class PreviewHTTPTests(unittest.TestCase):
    def test_preview_routes_require_authentication_and_send_an_isolated_csp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'; root.mkdir()
            engine = Engine(root, Path(temp) / 'data')
            server = make_server(engine, 0, 'preview-token')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()

            def call(method, path, body=None, auth=True):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request(method, path, body=json.dumps(body) if body is not None else None,
                                   headers={'Authorization': 'Bearer preview-token'} if auth else {})
                response = connection.getresponse()
                result = response.status, dict(response.getheaders()), response.read()
                connection.close()
                return result

            try:
                self.assertEqual(call('POST', '/api/previews', {'html': '<h1>Test</h1>'}, auth=False)[0], 401)
                status, _, body = call('POST', '/api/previews', {'html': '<h1>Test</h1><script>alert(1)</script>'})
                self.assertEqual(status, 201)
                url = json.loads(body)['url']
                self.assertEqual(call('GET', url, auth=False)[0], 401)
                status, headers, page = call('GET', url)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Security-Policy'], PREVIEW_CSP)
                self.assertIn("script-src 'none'", PREVIEW_CSP)
                self.assertIn("default-src 'none'", PREVIEW_CSP)
                self.assertIn('sandbox;', PREVIEW_CSP)
                self.assertNotIn('allow-same-origin', PREVIEW_CSP)
                self.assertIn(b'<h1>Test</h1>', page)
                self.assertNotIn(b'<script', page)
                self.assertEqual(call('POST', '/api/previews', {'html': '<p>Test</p>', 'extra': True})[0], 400)
                self.assertEqual(call('GET', '/api/previews/not-a-preview')[0], 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(2); engine.close()
