"""Building a new app: the preview accepts what models really send, servers are not started
with run_command, and an empty project gets the modern-stack guidance and environment facts."""
import socket
import tempfile
import unittest
from pathlib import Path

from dikdik import local_exec
from dikdik.local_preview import STATIC_SERVER, page_path, server_command
from dikdik.skills import _route_skills


class PreviewCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.static = Path(self.temp.name) / "static"
        self.static.mkdir()
        (self.static / "index.html").write_text("<h1>Tasks</h1>", encoding="utf-8")
        self.empty = Path(self.temp.name) / "empty"
        self.empty.mkdir()

    def test_a_static_site_is_served_without_a_command(self):
        for command in (None, "", "  ", "open http://127.0.0.1:34307", "start", "http://localhost:5173"):
            with self.subTest(command=command):
                self.assertEqual(server_command(self.static, command), STATIC_SERVER)
        self.assertIn("{port}", STATIC_SERVER)
        self.assertIn("127.0.0.1", STATIC_SERVER)

    def test_without_index_html_nothing_is_invented(self):
        self.assertIsNone(server_command(self.empty, None))
        self.assertEqual(server_command(self.empty, "open http://x"), "open http://x")

    def test_literal_ports_become_the_assigned_port(self):
        cases = {
            "python -m http.server 34312": "python -m http.server {port}",
            "python -m http.server": "python -m http.server {port}",
            "npx http-server -p 34307": "npx http-server -p {port}",
            "next dev --port 3000": "next dev --port {port}",
            "npm run dev": "npm run dev -- --port {port}",
            "npm run dev -- --port {port}": "npm run dev -- --port {port}",
            "bun run dev": "bun run dev --port {port}",
            "bun dev": "bun dev --port {port}",
            "pnpm dev": "pnpm dev --port {port}",
            "yarn run dev": "yarn run dev --port {port}",
            "bun run dev --port {port}": "bun run dev --port {port}",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(server_command(self.empty, given), expected)

    def test_folder_and_file_paths_become_url_paths(self):
        for given, expected in {".": "/", "": "/", "./": "/", None: "/", "/": "/",
                                "index.html": "/index.html", "./about.html": "/about.html", "/x": "/x"}.items():
            with self.subTest(given=given):
                self.assertEqual(page_path(given), expected)
        self.assertEqual(page_path("//example.com"), "//example.com")  # still refused by preview()


class BackgroundCommandTests(unittest.TestCase):
    def test_servers_sent_to_the_background_are_refused(self):
        for command in ("python -m http.server 34306 &\nsleep 2", "npm run dev &", "nohup node server.js > /dev/null 2>&1 &",
                        "Start-Process node server.js", "setsid npm start", "start /b npm run dev"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "preview_app"):
                local_exec.check_background(command)

    def test_ordinary_commands_are_allowed(self):
        for command in ("npm install && npm run build", "npm test || true", "pytest 2>&1 | tail -5", "cmd &> log.txt",
                        "echo 'a & b'", 'echo "fish & chips"', "npx prisma migrate dev --name init"):
            with self.subTest(command=command):
                local_exec.check_background(command)


class NewProjectTests(unittest.TestCase):
    rows = [{"id": name, "triggers": "unrelated words"} for name in
            ("new-app", "ui-ux-design", "browser-verification", "planning", "debugging")]

    def test_an_empty_project_always_gets_the_new_app_guidance(self):
        names = [row["id"] for row in _route_skills(self.rows, "make me something nice", "agent", new_project=True)]
        self.assertIn("new-app", names)
        self.assertIn("ui-ux-design", names)

    def test_an_existing_project_does_not(self):
        names = [row["id"] for row in _route_skills(self.rows, "fix the login bug", "agent", new_project=False)]
        self.assertNotIn("new-app", names)

    def test_the_bundled_skill_chooses_by_product_and_keeps_the_backend_real(self):
        text = (Path(__file__).parents[1] / "dikdik" / "skills" / "new-app" / "SKILL.md").read_text(encoding="utf-8")
        # The stack follows the product: websites, web apps, mobile, APIs, desktop, CLIs, data.
        for needle in ("Astro", "Next.js", "Expo", "Hono", "FastAPI", "Tauri", "uv"):
            self.assertIn(needle, text)
        # No credentials still means a real engine and real tests, never a fake path.
        for needle in ("PGlite", "mongodb-memory-server", "request_secret", ".env.example", "MSW"):
            self.assertIn(needle, text)
        for needle in ("record_requirements", "preview_app", "bun run dev --port {port}", "lockfile"):
            self.assertIn(needle, text)
        self.assertLess(len(text.encode()), 8 * 1024)  # a skill body above SKILL_LIMIT is cut off for the model

    def test_mobile_and_api_requests_route_to_it_too(self):
        rows = [{"id": "new-app", "triggers": (Path(__file__).parents[1] / "dikdik" / "skills" / "new-app" / "SKILL.md")
                 .read_text(encoding="utf-8").split('triggers: "', 1)[1].split('"', 1)[0]}, {"id": "debugging", "triggers": "bug"}]
        for request in ("make a mobile app for my gym", "I need a backend api for invoices", "create a website for my bakery"):
            with self.subTest(request=request):
                self.assertIn("new-app", [row["id"] for row in _route_skills(rows, request, "agent")])


class ToolchainTests(unittest.TestCase):
    def test_a_listening_database_is_reported_and_the_url_value_never_is(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".env").write_text("DATABASE_URL=postgresql://admin:hunter2@db:5432/app\n", encoding="utf-8")
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            original = local_exec.DATABASE_PORTS
            local_exec.DATABASE_PORTS = (("PostgreSQL", port),)
            try:
                text = local_exec.describe_toolchain(Path(folder))
            finally:
                local_exec.DATABASE_PORTS = original
                listener.close()
        self.assertTrue(text.startswith("environment: "))
        self.assertIn(f"PostgreSQL is accepting connections on 127.0.0.1:{port}", text)
        self.assertIn("DATABASE_URL (postgresql)", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("admin", text)
        self.assertIn("Node.js", text)
        self.assertTrue("also installed: " in text or "no other toolchains on PATH" in text)


if __name__ == "__main__":
    unittest.main()
