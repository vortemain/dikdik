"""Spending the strong model's tokens and time only where they matter."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dikdik.agent as agent
from dikdik.model_config import ModelConfig
from dikdik.providers import cache_marks
from dikdik.workspace_files import WorkspaceFiles
from tests.test_agent_recovery import Script, call

MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"},
            {"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "tool_call_id": "a", "content": "{}"},
            {"role": "user", "content": "follow-up"}]


def config(url, model):
    return ModelConfig(url, model, "key", "bearer", 60, {})


class CacheMarkTests(unittest.TestCase):
    def test_anthropic_through_openrouter_marks_system_and_newest_user_message(self):
        marked = cache_marks(config("https://openrouter.ai/api/v1/chat/completions", "anthropic/claude-sonnet-4.5"), MESSAGES)
        self.assertEqual(marked[0]["content"], [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(marked[1]["content"], "task")  # only the newest user message carries the second mark
        self.assertEqual(marked[4]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(MESSAGES[0]["content"], "rules")  # the canonical conversation is never changed

    def test_other_providers_are_left_alone(self):
        for url, model in (("https://openrouter.ai/api/v1/chat/completions", "z-ai/glm-5.2"),
                           ("https://api.tokenfactory.nebius.com/v1/chat/completions", "zai-org/GLM-5.3")):
            self.assertIs(cache_marks(config(url, model), MESSAGES), MESSAGES)


class SideWorkTests(unittest.TestCase):
    def test_conversation_summaries_use_the_side_model(self):
        original = {f"src/file{i}.ts": f'export const v{i} = "{"x" * 9000}";\n' for i in range(30)}
        reads = [call(f"r{i}", "read_file", path=f"src/file{i}.ts") for i in range(30)]
        summarizer = MagicMock()
        summarizer.respond.return_value = agent.ModelResponse('{"objective": "Refactor", "decisions": [], "done": ["read files"], "next": "edit"}', [])
        with patch("dikdik.agent._compact_context", return_value=False):  # force the model-written summary path
            result = agent.run_agent(original, "Refactor these modules", Script(reads + [agent.ModelResponse("Done.", [])]), None,
                                     lambda *a: None, threading.Event(), summary_model=summarizer)
        self.assertEqual(result.state, "completed", result.error)
        self.assertTrue(summarizer.respond.called)
        self.assertEqual(summarizer.respond.call_args.args[1], [])  # text only: a summary can take no action

    def test_side_model_is_the_routers_fast_model(self):
        from dikdik.engine import Engine
        engine = MagicMock()
        engine.app_settings.return_value = {"router": "r1"}
        engine.routers.get.return_value = {"fast_profile_id": "fast", "strong_profile_id": "strong"}
        self.assertEqual(Engine._side_model(engine, "strong"), "fast")
        engine.app_settings.return_value = {"router": ""}
        self.assertEqual(Engine._side_model(engine, "strong"), "strong")


class FileListTests(unittest.TestCase):
    def test_listing_is_reused_until_something_may_have_created_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("a\n")
            files = WorkspaceFiles(root)
            with patch("dikdik.workspace_files.workspace_metadata", wraps=__import__("dikdik.workspace_files").workspace_files.workspace_metadata) as scan:
                self.assertEqual(files.paths(), ["a.py"])
                (root / "b.py").write_text("b\n")
                self.assertEqual(files.paths(), ["a.py"])
                files.forget_paths()  # a command ran
                self.assertEqual(files.paths(), ["a.py", "b.py"])
            self.assertEqual(scan.call_count, 2)


if __name__ == "__main__":
    unittest.main()
