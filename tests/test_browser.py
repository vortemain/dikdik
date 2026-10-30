import base64
import http.client
import io
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from dikdik.agent import ModelResponse, ToolCall
from dikdik.browser_network import fetch_public, public_url, resolve_public
from dikdik.browser_service import BrowserService, browser_command, capture, screenshot_bytes
from dikdik.engine import Engine
from dikdik.server import make_server


def png_fixture():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1440, 900, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\xe8\xf4\xee' * 1440) * 900)) + chunk(b'IEND', b'')


PNG = png_fixture()
APPROVAL = {"approve_execution": True, "approve_browser": True}


def captured(*_args, **_kwargs):
    return {"status": "passed", "url": "https://example.com/", "title": "Example", "text": "Untrusted web text", "png": PNG}


class PublicNetworkTests(unittest.TestCase):
    def test_url_policy_rejects_local_credentials_schemes_and_special_addresses(self):
        for url in ["file:///etc/passwd", "javascript:alert(1)", "https://user:secret@example.com", "https://localhost/", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254/", "http://192.168.1.1", "https://example.com:8787/", "https://example.com\\@127.0.0.1", "https://example.com/\nsecret", "http://[::ffff:8.8.8.8]", "http://224.0.0.1/", "http://internal/"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        self.assertEqual(public_url("https://Example.COM/path#fragment"), "https://example.com/path")

    def test_dns_rejects_mixed_private_answers_and_pins_public_address(self):
        def row(address):
            return (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))
        with patch("socket.getaddrinfo", return_value=[row('93.184.216.34'), row('127.0.0.1')]):
            with self.assertRaisesRegex(ValueError, "private"):
                resolve_public("example.com", 443)
        with patch("socket.getaddrinfo", return_value=[row('93.184.216.34')]):
            self.assertEqual(resolve_public("example.com", 443), '93.184.216.34')

    def test_dns_timeout_is_bounded(self):
        release = threading.Event()
        with patch('socket.getaddrinfo', side_effect=lambda *_args, **_kwargs: release.wait(.2)):
            try:
                with self.assertRaisesRegex(ValueError, 'timed out'):
                    resolve_public('example.com', 443, timeout=.01)
            finally:
                release.set()

    def test_large_resource_is_rejected(self):
        response = MagicMock()
        response.read1.return_value = b'x' * (2 * 1024 * 1024 + 1)
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch('dikdik.browser_network.resolve_public', return_value='93.184.216.34'), patch('socket.create_connection'), patch('http.client.HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, '2 MiB'):
                fetch_public('http://example.com/')

    def test_fetch_uses_pinned_socket_and_no_ambient_credentials_or_proxies(self):
        response = MagicMock()
        response.status = 200
        response.getheader.side_effect = lambda key, default=None: 'text/html' if key == 'Content-Type' else default
        response.read1.side_effect = [b'<h1>safe</h1>', b'']
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch("dikdik.browser_network.resolve_public", return_value='93.184.216.34'), \
                patch("socket.create_connection") as connect, \
                patch("http.client.HTTPConnection", return_value=connection), \
                patch.dict(os.environ, {"HTTP_PROXY": "http://secret:password@localhost", "SESSION_API_KEY": "do-not-forward"}):
            result = fetch_public('http://example.com/')
        connect.assert_called_once_with(('93.184.216.34', 80), timeout=5)
        headers = connection.request.call_args.kwargs['headers']
        self.assertEqual(set(headers), {'User-Agent', 'Accept', 'Accept-Encoding', 'Connection'})
        self.assertNotIn('do-not-forward', json.dumps(result))
        self.assertEqual(base64.b64decode(result['body']), b'<h1>safe</h1>')

    def test_redirect_to_private_address_is_rejected_before_a_second_request(self):
        response = MagicMock(status=302)
        response.getheader.side_effect = lambda key, default=None: 'http://127.0.0.1/' if key == 'Location' else default
        response.read1.return_value = b''
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch("dikdik.browser_network.resolve_public", return_value='93.184.216.34'), patch("socket.create_connection"), patch("http.client.HTTPConnection", return_value=connection):
            with self.assertRaisesRegex(ValueError, "Private"):
                fetch_public('http://example.com/')
        self.assertEqual(connection.request.call_count, 1)

    def test_relay_handles_real_http_close_and_never_sends_cookie_or_auth(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                seen.append(dict(self.headers))
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Set-Cookie', 'do-not-pass=secret')
                self.end_headers()
                self.wfile.write(b'real loopback transport fixture')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connect = socket.create_connection
        with patch('dikdik.browser_network.resolve_public', return_value='93.184.216.34'), \
                patch('dikdik.browser_network.socket.create_connection', side_effect=lambda _target, timeout: connect(('127.0.0.1', server.server_port), timeout=timeout)):
            result = fetch_public('http://example.com/')
        self.assertEqual(base64.b64decode(result['body']), b'real loopback transport fixture')
        self.assertEqual(seen[0]['Host'], 'example.com')
        self.assertNotIn('Cookie', seen[0])
        self.assertNotIn('Authorization', seen[0])
        self.assertNotIn('set-cookie', result['headers'])


class BrowserRunnerTests(unittest.TestCase):
    def test_container_has_no_network_mounts_or_host_credentials(self):
        command = browser_command('dikdik-' + 'a' * 32)
        for flag in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=65534:65534', '--pull=never'):
            self.assertIn(flag, command)
        self.assertNotIn('--mount', command)
        self.assertNotIn('--privileged', command)
        self.assertIn('timeout', command)

    def test_screenshot_rejects_malformed_base64_or_oversize_dimensions(self):
        self.assertEqual(screenshot_bytes(base64.b64encode(PNG).decode()), PNG)
        for value in ['<svg onload=alert(1)>', '%%%%', base64.b64encode(PNG[:16] + struct.pack('>II', 1000000, 1000000) + PNG[24:]).decode()]:
            with self.assertRaises(ValueError):
                screenshot_bytes(value)

    def test_protocol_fetches_through_relay_and_returns_screenshot(self):
        frames = [{'kind': 'ready'}, {'kind': 'request', 'id': 1, 'url': 'https://example.com/'},
                  {'kind': 'result', 'url': 'https://example.com/', 'title': 'Example', 'text': 'safe', 'png': base64.b64encode(PNG).decode()}]
        proc = MagicMock()
        proc.stdout = io.BytesIO(('\n'.join(json.dumps(frame) for frame in frames) + '\n').encode())
        proc.stdin = io.BytesIO()
        proc.poll.return_value = None
        with patch('subprocess.Popen', return_value=proc), patch('dikdik.browser_service.cleanup_container', return_value=None), \
                patch('dikdik.browser_service.fetch_public', return_value={'status': 200, 'headers': {}, 'body': ''}) as fetch:
            result = capture('https://example.com/', threading.Event(), name='dikdik-' + 'a' * 32)
        self.assertEqual((result['status'], result['png']), ('passed', PNG))
        fetch.assert_called_once()
        proc.kill.assert_called_once()

    def test_missing_docker_is_blocked_without_network_access(self):
        with patch('subprocess.Popen', side_effect=FileNotFoundError), patch('dikdik.browser_service.fetch_public') as fetch:
            self.assertEqual(capture('https://example.com/', threading.Event(), name='dikdik-' + 'a' * 32)['status'], 'blocked')
        fetch.assert_not_called()


class BrowserServiceTests(unittest.TestCase):
    def setUp(self):
        # These cases cover the Docker runtime; hide any Edge or Chrome on this machine.
        no_host = patch("dikdik.host_browser.find_browser", return_value=None)
        no_host.start()
        self.addCleanup(no_host.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'repo'
        self.root.mkdir()
        (self.root / 'app.py').write_text('x = 1\n')
        self.engine = Engine(self.root, base / 'data')
        self.addCleanup(self.engine.close)
        self.task = self.engine.store.create({'objective': 'browser fixture'})

    def test_separate_boolean_approvals_are_required(self):
        with patch('dikdik.browser_service.capture') as run:
            for approval in [{}, {'approve_execution': True}, {'approve_browser': True}, {'approve_execution': True, 'approve_browser': 'true'}]:
                with self.subTest(approval=approval), self.assertRaises(PermissionError):
                    self.engine.browser.visit(self.task, 'https://example.com/', approval)
        run.assert_not_called()

    def test_durable_screenshot_is_scoped_and_deleted_with_task(self):
        other = self.engine.store.create({'objective': 'other'})
        with patch('dikdik.browser_service.capture', side_effect=captured):
            result = self.engine.browser.visit(self.task, 'https://example.com/', APPROVAL)
        snapshot = result['screenshot'].split('/')[-1]
        self.assertEqual(self.engine.browser.screenshot(self.task, snapshot), PNG)
        with self.assertRaises(KeyError):
            self.engine.browser.screenshot(other, snapshot)
        self.engine.browser.close()
        self.engine.browser = BrowserService(self.engine)
        self.assertEqual(self.engine.browser.screenshot(self.task, snapshot), PNG)
        self.engine.delete(self.task)
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM browser_snapshots').fetchone()[0], 0)

    def test_screenshot_retention_bounds_private_storage(self):
        with patch('dikdik.browser_service.capture', side_effect=captured):
            first = self.engine.browser.visit(self.task, 'https://example.com/', APPROVAL)['screenshot'].split('/')[-1]
            for _ in range(10):
                self.engine.browser.visit(self.task, 'https://example.com/', APPROVAL)
        with self.assertRaises(KeyError):
            self.engine.browser.screenshot(self.task, first)
        with self.engine.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM browser_snapshots WHERE task_id=?', (self.task,)).fetchone()[0], 10)

    def test_disable_is_persistent_and_prevents_capture(self):
        self.engine.browser.configure({'enabled': False})
        self.engine.browser.close()
        self.engine.browser = BrowserService(self.engine)
        self.assertFalse(self.engine.browser.enabled())
        with patch('dikdik.browser_service.capture') as run, self.assertRaisesRegex(PermissionError, 'disabled'):
            self.engine.browser.visit(self.task, 'https://example.com/', APPROVAL)
        run.assert_not_called()

    def test_discovery_probes_only_docker_image_and_respects_disable(self):
        with patch('dikdik.browser_service.subprocess.run', return_value=MagicMock(returncode=0, stdout=b'1\n')) as probe:
            catalog = self.engine.browser.route('GET', '/api/tools/catalog')[1]['tools']
            browser = next(tool for tool in catalog if tool['name'] == 'browse_url')
            self.assertTrue(browser['usable'])
            self.assertEqual(browser['approval_required'], ['approve_execution', 'approve_browser'])
            self.engine.browser.configure({'enabled': False})
            catalog = self.engine.browser.route('GET', '/api/tools/catalog')[1]['tools']
            self.assertFalse(next(tool for tool in catalog if tool['name'] == 'browse_url')['usable'])
            self.assertNotIn('browse_url', self.engine.browser.route('GET', '/server_info')[1]['usable_tools'])
            self.assertIn('browse_url', self.engine.browser.route('GET', '/api/tools/')[1])
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[0][:3], ['docker', 'image', 'inspect'])

    def test_cancel_and_task_deletion_boundary(self):
        entered = threading.Event()
        parent_cancel = threading.Event()
        def waiting(_url, cancel, **_kwargs):
            entered.set()
            cancel.wait(3)
            return {'status': 'cancelled', 'error': 'cancelled'}
        result = []
        with patch('dikdik.browser_service.capture', side_effect=waiting):
            worker = threading.Thread(target=lambda: result.append(self.engine.browser.visit(self.task, 'https://example.com/', APPROVAL, parent_cancel)))
            worker.start()
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, 'browser'):
                self.engine.delete(self.task)
            self.assertTrue(self.engine.browser.stop(self.task)['stop_requested'])
            worker.join(5)
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertFalse(parent_cancel.is_set(), 'stopping one browser tool must not cancel its agent turn')
        self.assertFalse(self.engine.browser.stop(self.task)['stop_requested'])

    def test_restart_marks_unfinished_capture_interrupted(self):
        self.engine.store.event(self.task, 'browser_started', {'url': 'https://example.com/', 'container': 'dikdik-' + 'a' * 32})
        self.engine.browser.close()
        with patch('dikdik.browser_service.cleanup_container', return_value=None) as cleanup:
            self.engine.browser = BrowserService(self.engine)
        cleanup.assert_called_once_with('dikdik-' + 'a' * 32)
        self.assertEqual(self.engine.store.get(self.task)['events'][-1]['data']['status'], 'interrupted')

    def test_agent_tool_requires_task_consent_and_respects_profile(self):
        class Model:
            def __init__(self):
                self.tools, self.calls = [], 0
            def respond(self, _messages, tools):
                self.tools.append([tool['function']['name'] for tool in tools])
                self.calls += 1
                return ModelResponse('', [ToolCall('browse', 'browse_url', {'url': 'https://example.com/'})]) if self.calls == 1 and 'browse_url' in self.tools[-1] else ModelResponse('Done', [])
        for allow, consent in [(True, True), (True, False), (False, True)]:
            with self.subTest(allow=allow, consent=consent):
                profile = self.engine.agents.create({'name': uuid.uuid4().hex, 'allow_browser': allow})
                model = Model()
                with patch('dikdik.engine.CompatibleAgentModel', return_value=model), patch('dikdik.browser_service.capture', side_effect=captured):
                    task = self.engine.submit({'objective': 'Browse', 'mode': 'agent', 'verification': 'syntax', 'approve_external': True, 'approve_execution': consent, 'approve_browser': consent, 'agent_profile': profile['id']})
                    for _ in range(600):
                        if not self.engine.busy(task):
                            break
                        time.sleep(.01)
                if not model.tools:
                    self.fail("the model was never called before the task ended")
                self.assertEqual('browse_url' in model.tools[0], allow and consent)


class BrowserHTTPTests(unittest.TestCase):
    setUp = BrowserServiceTests.setUp

    def test_http_auth_consent_png_and_isolation(self):
        server = make_server(self.engine, 0, 'token')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def call(method, path, body=None, token='token'):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request(method, path, body=json.dumps(body) if body is not None else None, headers={'Authorization': 'Bearer ' + token})
            response = conn.getresponse()
            out = response.status, response.getheader('Content-Type'), response.read()
            conn.close()
            return out
        prefix = f'/api/tasks/{self.task}/browser'
        for method, path in [('GET', '/api/browser'), ('GET', prefix), ('POST', prefix), ('PATCH', '/api/browser')]:
            self.assertEqual(call(method, path, {}, token='wrong')[0], 401)
        self.assertEqual(call('POST', prefix, {'url': 'https://example.com/'})[0], 403)
        with patch('dikdik.browser_service.capture', side_effect=captured):
            status, _, body = call('POST', prefix, {'url': 'https://example.com/', **APPROVAL})
        self.assertEqual(status, 200)
        screenshot = json.loads(body)['screenshot']
        self.assertEqual(call('GET', screenshot), (200, 'image/png', PNG))
        self.assertEqual(call('GET', screenshot, token='wrong')[0], 401)
        other = self.engine.store.create({'objective': 'other'})
        self.assertEqual(call('GET', screenshot.replace(self.task, other))[0], 404)
        self.assertEqual(call('PATCH', '/api/browser', {'enabled': False})[0], 200)
        self.assertEqual(call('POST', prefix, {'url': 'https://example.com/', **APPROVAL})[0], 403)


@unittest.skipUnless(os.environ.get('DIKDIK_RUN_BROWSER') == '1', 'Live Docker browser and public network access require DIKDIK_RUN_BROWSER=1')
class LiveBrowserTests(unittest.TestCase):
    def test_real_container_captures_public_page(self):
        result = capture('https://example.com/', threading.Event(), name='dikdik-' + uuid.uuid4().hex)
        self.assertEqual(result['status'], 'passed', result)
        self.assertIn('Example', result['title'])
        self.assertGreater(len(result['png']), 100)
