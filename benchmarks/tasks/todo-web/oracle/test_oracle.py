"""Judged in real Chromium. Without Playwright the oracle fails rather than passing unjudged."""

import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Oracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AssertionError("This oracle needs Playwright Chromium (pip install \"dikdik[desktop]\")") from None
        cls.port = free_port()
        cls.server = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--port", str(cls.port)], cwd=HERE,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while True:
            if cls.server.poll() is not None:
                raise AssertionError("serve.py exited during start-up")
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    cls.tearDownClass()
                    raise AssertionError("serve.py did not listen on its --port") from None
                time.sleep(0.1)
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "browser", None):
            cls.browser.close()
            cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=10)

    def open(self, width=1280, height=800):
        context = self.browser.new_context(viewport={"width": width, "height": height})
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="load")
        return page

    def add(self, page, text):
        page.fill("#new-todo", text)
        page.press("#new-todo", "Enter")

    def items(self, page):
        return page.locator("#todo-list > li")

    def count(self, page):
        return page.locator("#count").inner_text().strip()

    def test_add_complete_delete_and_count(self):
        page = self.open()
        self.add(page, "  Write report  ")
        self.add(page, "Call Ana")
        self.add(page, "   ")
        items = self.items(page)
        self.assertEqual(items.count(), 2)
        self.assertIn("Write report", items.nth(0).inner_text())
        self.assertIn("Call Ana", items.nth(1).inner_text())
        self.assertEqual(page.input_value("#new-todo"), "")
        self.assertEqual(self.count(page), "2 items left")
        items.nth(0).locator("input[type=checkbox]").check()
        self.assertIn("done", (items.nth(0).get_attribute("class") or "").split())
        self.assertNotIn("done", (items.nth(1).get_attribute("class") or "").split())
        self.assertEqual(self.count(page), "1 item left")
        items.nth(1).get_by_role("button", name="Delete").click()
        self.assertEqual(self.items(page).count(), 1)
        self.assertEqual(self.count(page), "0 items left")
        self.assertEqual(self.errors, [])

    def test_tasks_survive_a_reload(self):
        page = self.open()
        for text in ("Café ✓ 日本", "Second", "Third"):
            self.add(page, text)
        self.items(page).nth(1).locator("input[type=checkbox]").check()
        self.items(page).nth(2).get_by_role("button", name="Delete").click()
        page.reload(wait_until="load")
        items = self.items(page)
        self.assertEqual(items.count(), 2)
        self.assertIn("Café ✓ 日本", items.nth(0).inner_text())
        self.assertIn("done", (items.nth(1).get_attribute("class") or "").split())
        self.assertTrue(items.nth(1).locator("input[type=checkbox]").is_checked())
        self.assertEqual(self.count(page), "1 item left")
        self.assertEqual(self.errors, [])

    def test_usable_on_a_phone(self):
        page = self.open(390, 844)
        self.add(page, "A task with a fairly long description that has to wrap on a narrow screen")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(overflow, 0, "the page scrolls sideways on a phone")
        self.assertTrue(page.locator("#new-todo").is_visible())
        self.assertTrue(self.items(page).nth(0).get_by_role("button", name="Delete").is_visible())


if __name__ == "__main__":
    unittest.main()
