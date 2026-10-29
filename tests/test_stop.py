"""Stop must not wait for a silent provider or restart queued messages."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from dikdik.engine import Engine
from dikdik.model_config import ModelConfig
from dikdik.providers import ProviderError, chat_completion


class StopTests(unittest.TestCase):
    def test_stop_interrupts_a_silent_stream_and_closes_the_socket(self):
        self.check_silent_request(streaming=True)

    def test_stop_before_headers_closes_the_inflight_connection(self):
        self.check_silent_request(streaming=False)

    def check_silent_request(self, streaming):
        entered, disconnected = threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if streaming:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n')
                    self.wfile.flush()
                entered.set()
                self.connection.settimeout(3)
                try:
                    if not self.rfile.read(1):
                        disconnected.set()
                except OSError:
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cancel, results, text = threading.Event(), [], []
        config = ModelConfig(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', 'm', '', 'none', 10, {})

        def request():
            try:
                results.append(chat_completion([], config=config, on_text=text.append, cancel=cancel))
            except ProviderError as exc:
                results.append(exc.kind)

        client = threading.Thread(target=request)
        client.start()
        try:
            self.assertTrue(entered.wait(3))
            start = time.monotonic()
            cancel.set()
            client.join(1)
            self.assertFalse(client.is_alive(), 'Stop waited for the provider timeout')
            self.assertLess(time.monotonic() - start, 1)
            self.assertEqual(results, ['cancelled'])
            self.assertTrue(disconnected.wait(1), 'The cancelled stream socket was left open')
        finally:
            cancel.set()
            client.join(3)
            server.shutdown()
            server.server_close()

    def test_stop_before_headers_releases_engine_and_discards_late_tool_calls(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        late = {"choices": [{"message": {"content": "Late answer", "tool_calls": [
            {"id": "late", "type": "function", "function": {"name": "run_command", "arguments": '{"command":"must-not-run"}'}}
        ]}}]}

        class Response(io.BytesIO):
            headers = {'Content-Type': 'application/json'}

            def close(self):
                super().close()
                closed.set()

        def open_request(*args, **kwargs):
            entered.set()
            release.wait(5)
            return Response(json.dumps(late).encode())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            (root / 'app.py').write_text('x = 1\n')
            engine = Engine(root, Path(temp) / 'data')
            env = {'DIKDIK_MODEL_URL': 'https://provider.example/v1/chat/completions', 'DIKDIK_MODEL': 'm', 'DIKDIK_API_KEY': 'k'}
            try:
                with patch.dict(os.environ, env), patch('urllib.request.build_opener') as opener:
                    opener.return_value.open.side_effect = open_request
                    request = {'objective': 'Wait for the model', 'mode': 'agent', 'verification': 'syntax', 'approve_external': True}
                    task = engine.submit(request)
                    self.assertTrue(entered.wait(3))
                    self.assertTrue(engine.send(task, {**request, 'objective': 'Queued message'}))
                    start = time.monotonic()
                    self.assertTrue(engine.interrupt(task))
                    deadline = time.monotonic() + 1
                    while engine.busy(task) and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertFalse(engine.busy(task), 'Stop did not release the worker promptly')
                    self.assertLess(time.monotonic() - start, 1)
                    self.assertEqual(engine.store.get(task)['state'], 'paused')
                    self.assertNotIn(task, engine.pending)
                    release.set()
                    self.assertTrue(closed.wait(2))
                    events = engine.store.get(task)['events']
                    self.assertFalse(any(event['kind'] == 'agent_tool' for event in events))
                    self.assertNotIn(task, engine.live)
                    self.assertEqual(opener.return_value.open.call_count, 1)
            finally:
                release.set()
                engine.close()

    def test_already_cancelled_requests_never_start_network_io(self):
        cancel = threading.Event()
        cancel.set()
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ProviderError, 'cancelled'):
                chat_completion([], cancel=cancel)
        opener.assert_not_called()
