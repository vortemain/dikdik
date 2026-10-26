"""Developer browser fixture: scripted actions, no AI request or test execution."""

import argparse
import secrets
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dikdik.agent import AgentBudget, ModelResponse, ToolCall
from dikdik.engine import Engine
from dikdik.server import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--temp-parent", help="Optional parent directory for uniquely owned temporary QA files")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dikdik-review-qa-", dir=args.temp_parent) as temp:
        base = Path(temp)
        root = base / "review-qa"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        engine = Engine(root, base / "state")
        try:
            model = Mock()
            model.respond.side_effect = [
                ModelResponse("Scripted QA edit", [ToolCall("edit", "edit_file", {"path": "app.py", "before": "1", "after": "2"})]),
                ModelResponse("Scripted QA addition", [ToolCall("add", "create_file", {"path": "feature/café.py", "content": "# 🦌 candidate only\nvalue = 2\n"})]),
                ModelResponse("Scripted QA empty package", [ToolCall("empty", "create_file", {"path": "feature/__init__.py", "content": ""})]),
            ]
            with patch("dikdik.engine.CompatibleAgentModel", return_value=model), patch("dikdik.engine.AgentBudget", return_value=AgentBudget(max_model_calls=3)), patch("dikdik.engine.run_tests", side_effect=AssertionError("QA fixture must never execute project tests")) as verifier:
                task = engine.submit({"objective": "UI QA fixture: scripted file actions; no live AI or test verification", "mode": "agent", "verification": "docker", "approve_external": True, "approve_execution": True})
                engine.pool.submit(lambda: None).result(timeout=20)
                data = engine.store.get(task)
                verifier.assert_not_called()
                if data["state"] != "failed" or data["result"]["tests_verified"] or len(data["result"]["file_changes"]) != 3:
                    raise RuntimeError("Review fixture did not produce the intended unverified, budget-stopped candidate")
            token = secrets.token_urlsafe(32)
            server = make_server(engine, args.port, token)
            print(f"QA ONLY — scripted model, no AI or tests executed. Task correctly stopped unverified.\nhttp://127.0.0.1:{server.server_port}\nAccess token: {token}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            engine.close()


if __name__ == "__main__":
    main()
