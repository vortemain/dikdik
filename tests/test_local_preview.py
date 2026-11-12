"""Preview lifecycle, measured failures, and scoped proxy access."""
import os
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from dikdik.local_preview import preview
from dikdik.host_browser import PREVIEW_HOST, PublicProxy, _ProxyHandler


class PreviewTests(unittest.TestCase):
    def test_only_the_reserved_alias_can_reach_the_assigned_loopback_port(self):
        handler = object.__new__(_ProxyHandler)
        handler.server = SimpleNamespace(preview_port=43210, refused=[])
        with patch('dikdik.host_browser.socket.create_connection') as connect, \
                patch('dikdik.host_browser.resolve_public', side_effect=ValueError('private')):
            handler._open(f'http://{PREVIEW_HOST}/', PREVIEW_HOST, 80)
            connect.assert_called_once_with(('127.0.0.1', 43210), timeout=15)
            connect.reset_mock()
            self.assertIsNone(handler._open('http://127.0.0.1/', '127.0.0.1', 80))
            self.assertIsNone(handler._open('http://169.254.169.254/', '169.254.169.254', 80))
            self.assertIsNone(handler._open(f'https://{PREVIEW_HOST}/', PREVIEW_HOST, 443))
            connect.assert_not_called()

    def test_invalid_command_and_foreign_paths_do_not_start_processes(self):
        with patch('dikdik.local_preview.BackgroundShells') as shells:
            for command, path in [('node server.js', '/'), ('serve --port {port}', '//example.com'),
                                  ('serve --port {port}', '/\\example.com')]:
                with self.assertRaises(ValueError):
                    preview(Path('.'), command, threading.Event(), path=path)
            shells.assert_not_called()

    def test_missing_browser_reports_real_blocker_before_starting_server(self):
        with patch('dikdik.local_preview.available', return_value=False), patch('dikdik.local_preview.BackgroundShells') as shells:
            result = preview(Path('.'), 'serve --port {port}', threading.Event())
            self.assertEqual(result['status'], 'blocked')
            shells.assert_not_called()

    def test_interaction_assertion_and_overflow_are_not_reported_as_passes(self):
        def execute(task, action, args, cancel):
            return {'status': 'passed', 'text': 'Waiting', 'elements': [], 'png': b'png',
                    'layout': {'width': 390, 'scroll_width': 700}}
        with patch('dikdik.local_preview.available', return_value=True), \
                patch('dikdik.local_preview.socket.socket') as reserved, \
                patch('dikdik.local_preview.socket.create_connection'), \
                patch('dikdik.local_preview.BackgroundShells') as shells, \
                patch('dikdik.local_preview.BrowserSessions') as sessions:
            reserved.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 43210)
            shells.return_value.start.return_value = {'id': 'owned-server'}
            shells.return_value.poll.return_value = {'status': 'running'}
            sessions.return_value.execute.side_effect = execute
            result = preview(Path('.'), 'serve --port {port}', threading.Event(), expect_text='Success')
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(any('overflow' in issue for issue in result['issues']))
            self.assertTrue(any('Success' in issue for issue in result['issues']))
            shells.return_value.close.assert_called_once()
            sessions.return_value.close.assert_called_once()


@unittest.skipUnless(os.environ.get('DIKDIK_RUN_INTERACTIVE') == '1', 'Opt-in real browser preview')
class LivePreviewTests(unittest.TestCase):
    def test_real_broken_ui_fails_runtime_layout_and_expected_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.joinpath('index.html').write_text('''<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{width:2200px}</style><button id="save">Save</button><p>Waiting</p><script>throw new Error('fixture runtime error')</script>''')
            result = preview(root, f'"{sys.executable}" -m http.server {{port}} --bind 127.0.0.1', threading.Event(),
                actions=[{'action': 'click', 'selector': '#save'}], expect_text='Saved successfully')
            self.assertEqual(result['status'], 'failed', result)
            self.assertTrue(any('runtime errors' in issue for issue in result['issues']))
            self.assertTrue(any('overflow' in issue for issue in result['issues']))
            self.assertTrue(any('Saved successfully' in issue for issue in result['issues']))

    def test_real_server_desktop_mobile_interaction_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # An accessible fixture: the preview fails pages with serious accessibility failures.
            root.joinpath('index.html').write_text('''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Preview fixture</title><style>body{margin:16px}button{max-width:100%}</style><h1>Working app</h1><label for="name">Name</label><input id="name"><button id="apply">Apply</button><p id="result">Waiting</p><script>document.querySelector('#apply').onclick=()=>document.querySelector('#result').textContent='Hello '+document.querySelector('#name').value</script></html>''')
            shots = []
            result = preview(root, f'"{sys.executable}" -m http.server {{port}} --bind 127.0.0.1', threading.Event(),
                actions=[{'action': 'fill', 'selector': '#name', 'value': 'Dikdik'}, {'action': 'click', 'selector': '#apply'}],
                expect_text='Hello Dikdik', save_screenshot=lambda view, png: shots.append((view['label'], png)) or '/fixture.png')
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(result['interactions_checked'], 2)
            self.assertEqual([view['layout']['width'] for view in result['views']], [1440, 1440, 390])
            self.assertEqual([label for label, _ in shots], ['desktop', 'interaction result', 'mobile'])
            self.assertTrue(all(png.startswith(b'\x89PNG') for _, png in shots))
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', result['server_port']), timeout=0.2)

    FORM = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Form fixture</title>
<style>body{background:#fff} @media (prefers-color-scheme: dark){body{background:#111;color:#eee}}</style>
<h1>Contact</h1><form><label for="phone">Phone</label><input id="phone" required>
<label><input type="checkbox" id="news"> Newsletter</label><button type="submit">Send</button></form><p id="out">Idle</p>
<script>
const phone = document.querySelector('#phone');
phone.addEventListener('input', () => { phone.value = phone.value.replace(/\\D/g, '').replace(/(\\d{3})(?=\\d)/g, '$1-'); });
document.querySelector('form').onsubmit = e => { e.preventDefault(); console.error('saving ' + phone.value);
  fetch('/save', {method: 'POST', body: JSON.stringify({phone: phone.value, news: document.querySelector('#news').checked})}).catch(() => {});
  document.querySelector('#out').textContent = 'Sent ' + phone.value; };
</script></html>'''

    SERVER = '''import http.server, sys
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(204); self.end_headers()
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''

    def test_steps_by_role_and_label_with_expectations_dark_mode_console_and_sent_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.joinpath('index.html').write_text(self.FORM)
            root.joinpath('server.py').write_text(self.SERVER)
            shots = []
            result = preview(root, f'"{sys.executable}" server.py {{port}}', threading.Event(), color_scheme='dark',
                actions=[{'action': 'type', 'label': 'Phone', 'value': '5551234'},
                         {'action': 'check', 'label': 'Newsletter'},
                         {'action': 'click', 'role': 'button', 'name': 'Send', 'expect': 'Sent 555-123-4'}],
                save_screenshot=lambda view, png: shots.append(view['label']) or '/fixture.png')
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual([step['status'] for step in result['steps']], ['passed'] * 3)
            self.assertEqual((result['interactions_checked'], result['expected_text_checked']), (3, True))
            self.assertEqual(shots, ['desktop', 'interaction result', 'mobile', 'dark'])
            self.assertEqual(result['views'][-1]['layout']['color_scheme'], 'dark')
            self.assertIn('saving 555-123-4', [entry['text'] for entry in result['console']])
            self.assertEqual(result['sent_requests'][-1]['method'], 'POST')
            self.assertIn('"phone":"555-123-4","news":true', result['sent_requests'][-1]['body'])

    def test_a_failing_step_is_named_with_a_capture_and_the_run_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.joinpath('index.html').write_text(self.FORM)
            shots = []
            result = preview(root, None, threading.Event(),
                actions=[{'action': 'type', 'label': 'Phone', 'value': '12'},
                         {'action': 'click', 'role': 'button', 'name': 'Send', 'expect': 'Thank you'},
                         {'action': 'check', 'label': 'Newsletter'}],
                save_screenshot=lambda view, png: shots.append(view['label']) or '/fixture.png')
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual([step['status'] for step in result['steps']], ['passed', 'failed'])
            self.assertIn('step 2 (click button "Send") failed: expected text did not appear: Thank you', result['issues'])
            self.assertIn('failed step 2', shots)
            self.assertNotIn('interaction result', shots)
            self.assertEqual(result['interactions_checked'], 1)
            self.assertFalse(result['expected_text_checked'])


if __name__ == '__main__':
    unittest.main()
