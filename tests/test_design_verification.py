"""Design work is judged by what renders: previews, screenshots, styles and the right skills."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import dikdik.agent as agent
from dikdik.model_errors import ProviderError
from dikdik.skills import _refresh_bundled, _route_skills
from dikdik.workflow import visual_request
from tests.test_agent_recovery import Script, call

PNG = "data:image/png;base64,iVBORw0KGgo="
FOOTER = {"src/Footer.tsx": '<footer className="border-t">Links</footer>\n'}


def preview_result(**extra):
    return {"status": "passed", "views": [], "issues": [], "interactions_checked": 0, "expected_text_checked": False,
            "inspection": {"desktop": [{"selector": "footer", "styles": {"border-top": "1px solid rgb(229, 231, 235)"}}]},
            "attach_images": [PNG, PNG], **extra}


class Recorder(Script):
    def __init__(self, responses, fail_with_images=None):
        super().__init__(responses)
        self.fail_with_images = fail_with_images

    def respond(self, messages, tools):
        has_images = any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages)
        if self.fail_with_images and has_images:
            self.seen.append(messages)
            raise ProviderError(self.fail_with_images, "image input is not supported")
        return super().respond(messages, tools)


def images_in(messages):
    return sum(1 for m in messages if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "image_url")


class DesignVerificationTests(unittest.TestCase):
    def run_design(self, model, preview, verify=None):
        events = []
        result = agent.run_agent(dict(FOOTER), "Redesign the footer so it looks premium", model,
                                 verify or (lambda files: {"kind": "tests", "status": "passed"}),
                                 lambda kind, data: events.append((kind, data)), threading.Event(),
                                 preview=preview, extra_tools=[], extra_call=lambda name, args: {})
        return result, events

    def test_the_model_sees_the_screenshots_and_only_the_newest_stay(self):
        previews = []
        model = Recorder([call("before", "preview_app", command="npm run dev -- --port {port}", focus="footer", inspect=["footer"]),
                          call("edit", "edit_file", path="src/Footer.tsx", before="border-t", after="border-t-0"),
                          call("after", "preview_app", command="npm run dev -- --port {port}", focus="footer"),
                          call("verify", "run_tests"), call("finish", "finish")])
        result, events = self.run_design(model, lambda files, args: previews.append(args) or preview_result())
        self.assertEqual(result.state, "completed", result.error)
        self.assertEqual(previews[0]["focus"], "footer")
        self.assertEqual(images_in(model.seen[1]), 2)   # after the first preview
        self.assertEqual(images_in(model.seen[3]), 2)   # the older pair was replaced, not added to
        ends = [d for k, d in events if k == "tool" and d.get("phase") == "end" and d["name"] == "preview_app"]
        self.assertNotIn("attach_images", ends[0]["observation"])  # images never enter events or observations
        self.assertIn("border-top", json.dumps(ends[0]["observation"]["inspection"]))

    def test_a_text_only_model_continues_without_images(self):
        for refusal in ("bad_request", "not_found"):  # OpenRouter answers 404 for a text-only model
            with self.subTest(refusal=refusal):
                model = Recorder([call("look", "preview_app", command="npm run dev -- --port {port}", focus="footer"),
                                  call("edit", "edit_file", path="src/Footer.tsx", before="border-t", after="border-t-0"),
                                  call("again", "preview_app", command="npm run dev -- --port {port}"),
                                  call("verify", "run_tests"), call("finish", "finish")], fail_with_images=refusal)
                result, events = self.run_design(model, lambda files, args: preview_result())
                self.assertEqual(result.state, "completed", result.error)
                self.assertTrue(any(d.get("action") == "continue_without_images" for k, d in events if k == "recovery"))
                self.assertEqual(images_in(model.seen[-1]), 0)

    def test_design_work_cannot_skip_the_rendered_check(self):
        model = Recorder([call("edit", "edit_file", path="src/Footer.tsx", before="border-t", after="border-t-0"),
                          call("verify", "run_tests"), call("finish", "finish"),
                          call("look", "preview_app", command="npm run dev -- --port {port}"),
                          call("verify2", "run_tests"), call("finish2", "finish")])
        result, events = self.run_design(model, lambda files, args: preview_result(attach_images=[]))
        self.assertEqual(result.state, "completed", result.error)
        refused = [d for k, d in events if k == "tool" and d.get("phase") == "end" and d["id"] == "finish"]
        self.assertIn("preview_app", refused[0]["observation"]["error"])

    def test_a_project_that_cannot_render_finishes_honestly_instead_of_looping(self):
        model = Recorder([call("edit", "edit_file", path="src/Footer.tsx", before="border-t", after="border-t-0"),
                          call("look", "preview_app", command="npm run dev -- --port {port}"),
                          call("look2", "preview_app", command="npm start -- --port {port}"),
                          call("verify", "run_tests"), call("finish", "finish")])
        failure = {"status": "failed", "error": "The preview server exited before becoming ready", "output": "Missing DATABASE_URL"}
        result, events = self.run_design(model, lambda files, args: dict(failure))
        self.assertEqual(result.state, "completed", result.error)
        second = [d for k, d in events if k == "tool" and d.get("phase") == "end" and d["id"] == "look2"][0]
        self.assertIn("not checked in a browser", second["observation"]["next_step"])

    def test_system_prompt_asks_for_before_and_after_previews(self):
        model = Recorder([agent.ModelResponse("Which style do you prefer?", [])] * 3)
        self.run_design(model, lambda files, args: preview_result())
        system = model.seen[0][0]["content"]
        self.assertIn("Design verification:", system)
        self.assertIn("Before editing, run preview_app with focus", system)


class PreviewSignalTests(unittest.TestCase):
    def test_engine_stop_signal_can_be_waited_on_like_an_event(self):
        # Preview polls its dev server with cancel.wait(); the engine's combined stop/pause
        # signal lacked wait(), so every preview_app call through the engine failed.
        from dikdik.engine import _Either
        stop, pause = threading.Event(), threading.Event()
        signal = _Either(stop, pause)
        self.assertFalse(signal.wait(0.05))
        threading.Timer(0.05, pause.set).start()
        started = time.monotonic()
        self.assertTrue(signal.wait(5))
        self.assertLess(time.monotonic() - started, 2)


class LoopbackTests(unittest.TestCase):
    def test_dev_servers_listening_only_on_ipv6_localhost_are_reached(self):
        # vite dev binds "localhost", which is only ::1 on current Node/Windows.
        import socket
        from dikdik.host_browser import connect_loopback
        try:
            server = socket.create_server(("::1", 0), family=socket.AF_INET6)
        except OSError:
            self.skipTest("IPv6 loopback unavailable")
        with server:
            with connect_loopback(server.getsockname()[1], 2) as client:
                self.assertEqual(client.family, socket.AF_INET6)


class ReadSeveralFilesTests(unittest.TestCase):
    def test_one_call_reads_several_files_and_keeps_each_error(self):
        files = {"a.ts": "export const a = 1;\n", "b.ts": "export const b = 2;\n"}
        model = Script([call("read", "read_file", paths=["./a.ts", "/workspace/b.ts", "missing.ts"]),
                        agent.ModelResponse("a is 1 and b is 2.", [])])
        result = agent.run_agent(files, "Explain a and b", model, None, lambda *a: None, threading.Event())
        self.assertEqual(result.state, "completed", result.error)
        text = model.seen[1][-1]["content"]
        self.assertIn("a.ts (lines 1-1", text)
        self.assertIn("1\texport const b = 2;", text)
        self.assertIn("missing.ts: error:", text)


class DesignIntentTests(unittest.TestCase):
    def test_real_design_requests_are_recognised(self):
        for request in ("redesign the footer to a modern and premium design concept",
                        "update the website so there is no separation between the navbar and the page",
                        "can we have the images have skeleton loading", "fix the padding above the content"):
            self.assertTrue(visual_request(request), request)
        for request in ("Fix the build failure for the mobile app", "change the api url", "What makes a website look AI-generated?"):
            self.assertFalse(visual_request(request), request)

    def test_design_requests_get_the_design_and_verification_skills(self):
        rows = [{"id": "ui-ux-design", "triggers": "website, UI"}, {"id": "browser-verification", "triggers": "visual check"},
                {"id": "planning", "triggers": "multi-step work"}, {"id": "debugging", "triggers": "fix, crash"}]
        names = lambda objective: [row["id"] for row in _route_skills(rows, objective, "agent")]
        self.assertEqual(names("redesign the footer"), ["ui-ux-design", "browser-verification", "planning"])
        self.assertNotIn("ui-ux-design", names("build the api client"))  # "ui" inside "build" is not a trigger
        self.assertIn("debugging", names("fix the crash"))


class SkillRefreshTests(unittest.TestCase):
    def test_untouched_bundled_skills_update_and_edited_ones_stay(self):
        with tempfile.TemporaryDirectory() as temp:
            shipped = Path(temp) / "shipped" / "SKILL.md"
            shipped.parent.mkdir()
            shipped.write_text("new guidance\n")
            for name, edit in (("untouched", False), ("edited", True)):
                target = Path(temp) / name
                target.mkdir()
                (target / "SKILL.md").write_text("old guidance\n")
                installed_at = time.time()
                (target / ".installed.json").write_text(json.dumps({"source": "bundled", "installed_at": installed_at}))
                if edit:
                    (target / "SKILL.md").write_text("my own rules\n")
                    os.utime(target / "SKILL.md", (installed_at + 60, installed_at + 60))
                _refresh_bundled(shipped, target)
            self.assertEqual((Path(temp) / "untouched" / "SKILL.md").read_text(), "new guidance\n")
            self.assertEqual((Path(temp) / "edited" / "SKILL.md").read_text(), "my own rules\n")


if __name__ == "__main__":
    unittest.main()
