import concurrent.futures
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import ANY, MagicMock, Mock, patch

from dikdik.sponsored_gateway import Allowances, handler
from dikdik import sponsored_gateway


class SponsoredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.usage = Allowances(Path(self.temp.name) / "usage.sqlite3", per_user=2, global_daily=3)

    def test_allowances_are_atomic_and_persist_across_restart(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.usage.reserve, ["user-hash"] * 8))
        self.assertEqual(sum(results), 2)
        restarted = Allowances(self.usage.path, per_user=2, global_daily=3)
        self.assertFalse(restarted.reserve("user-hash"))
        self.assertTrue(restarted.reserve("second-user-hash"))
        self.assertFalse(restarted.reserve("third-user-hash"))
        with patch("dikdik.sponsored_gateway.time.time", return_value=10**12):
            self.assertTrue(restarted.reserve("user-hash"))

    def test_authenticated_proxy_fixes_model_bounds_output_and_enforces_allowance(self):
        token = "invitation-" + "x" * 32
        app = handler("nvidia/test-model", "private-provider-key", [token], self.usage)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as unauth:
            client.open(base + "/v1/models")
        self.assertEqual(unauth.exception.code, 401)
        with client.open(base + "/healthz") as health:
            self.assertEqual(json.load(health), {"ok": True})
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"choices": []}'))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        payload = {"model": "nvidia/test-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 99999}
        request = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with patch("dikdik.sponsored_gateway.urllib.request.build_opener", return_value=opener):
            with client.open(request) as response:
                self.assertEqual(json.load(response), {"choices": []})
        upstream = opener.open.call_args.args[0]
        self.assertEqual(json.loads(upstream.data)["max_tokens"], 2048)
        self.assertEqual(upstream.get_header("Authorization"), "Bearer private-provider-key")
        self.assertNotIn(token, upstream.data.decode())
        identity = __import__("hashlib").sha256(token.encode()).hexdigest()
        self.assertTrue(self.usage.reserve(identity))
        with self.assertRaises(urllib.error.HTTPError) as exhausted:
            client.open(request)
        self.assertEqual(exhausted.exception.code, 429)
        self.assertNotIn("private-provider-key", exhausted.exception.read().decode())

    def test_judge_invitations_use_a_separate_allowance_pool(self):
        token = "invitation-" + "x" * 32
        judge = "judge-invitation-" + "y" * 32
        path = Path(self.temp.name) / "shared.sqlite3"
        users = Allowances(path, "user", per_user=2, global_daily=2)
        judges = Allowances(path, "judge", per_user=2, global_daily=2)
        app = handler("nvidia/test-model", "private-provider-key", [token], users,
                      judge_invitations=[judge], judge_allowances=judges)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        opener = MagicMock()
        opener.open.return_value.__enter__.side_effect = lambda: io.BytesIO(b'{"choices": []}')
        opener.open.return_value.__exit__.side_effect = lambda *args: False
        payload = {"model": "nvidia/test-model", "messages": [{"role": "user", "content": "hello"}]}
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = lambda bearer: urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                                        headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"})
        with patch("dikdik.sponsored_gateway.urllib.request.build_opener", return_value=opener):
            with client.open(request(token)) as response:
                self.assertEqual(json.load(response), {"choices": []})
            with client.open(request(judge)) as response:
                self.assertEqual(json.load(response), {"choices": []})
            with client.open(request(judge)) as response:
                self.assertEqual(json.load(response), {"choices": []})
            with self.assertRaises(urllib.error.HTTPError) as judge_exhausted:
                client.open(request(judge))
            self.assertEqual(judge_exhausted.exception.code, 429)
            self.assertNotIn("private-provider-key", judge_exhausted.exception.read().decode())
            self.assertFalse(judges.reserve(__import__("hashlib").sha256(judge.encode()).hexdigest()))
            # The judge requests above did not consume the public allowance.
            with client.open(request(token)) as response:
                self.assertEqual(json.load(response), {"choices": []})
            with self.assertRaises(urllib.error.HTTPError) as user_exhausted:
                client.open(request(token))
            self.assertEqual(user_exhausted.exception.code, 429)
            self.assertFalse(users.reserve(__import__("hashlib").sha256(token.encode()).hexdigest()))

    def test_overlapping_invitation_lists_are_rejected(self):
        token = "invitation-" + "x" * 32
        path = Path(self.temp.name) / "overlap.sqlite3"
        users = Allowances(path, "user", per_user=1, global_daily=1)
        judges = Allowances(path, "judge", per_user=1, global_daily=1)
        with self.assertRaisesRegex(ValueError, "both"):
            handler("nvidia/test-model", "key", [token], users, judge_invitations=[token], judge_allowances=judges)

    def test_bind_host_is_configurable_for_container_deployment(self):
        token = "invitation-" + "x" * 32
        usage = Path(self.temp.name) / "bind.sqlite3"
        env = {"NEBIUS_MODEL": "nvidia/test-model", "NEBIUS_API_KEY": "private-provider-key",
               "DIKDIK_GATEWAY_INVITATIONS": token, "DIKDIK_GATEWAY_USAGE_DB": str(usage),
               "DIKDIK_GATEWAY_HOST": "0.0.0.0", "DIKDIK_GATEWAY_PORT": "8788"}
        with patch.dict(os.environ, env):
            with patch("dikdik.sponsored_gateway.ThreadingHTTPServer") as server_class:
                instance = server_class.return_value
                instance.serve_forever.side_effect = KeyboardInterrupt
                sponsored_gateway.main()
        server_class.assert_called_once_with(("0.0.0.0", 8788), ANY)
        self.assertTrue(instance.server_close.called)
