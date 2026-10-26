"""Real-provider/Docker demonstration. No scripted model or fake verifier."""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dikdik.engine import Engine, TERMINAL


def main():
    parser = argparse.ArgumentParser(description="Run DIKDIK's original agent on its broken addition fixture")
    parser.add_argument("--approve-external", action="store_true", help="Approve disclosure to the configured model; may incur charges")
    parser.add_argument("--approve-execution", action="store_true", help="Approve fixture test execution in restricted Docker")
    args = parser.parse_args()
    if not (args.approve_external and args.approve_execution):
        parser.error("Both --approve-external and --approve-execution are required; no model or tests have run")
    with tempfile.TemporaryDirectory(prefix="dikdik-agent-demo-") as temp:
        base = Path(temp)
        workspace = base / "repo"
        workspace.mkdir()
        fixture = Path(__file__).resolve().parents[1] / "examples" / "repair"
        for source in fixture.glob("*.py"):
            (workspace / source.name).write_bytes(source.read_bytes())
        engine = Engine(workspace, base / "data")
        task = None
        try:
            task = engine.submit({
                "objective": "Investigate and fix the addition bug. Establish the failure with tests, make a minimal production-code repair without modifying tests, rerun them and finish only when the current candidate passes.",
                "mode": "agent", "verification": "docker",
                "approve_external": True, "approve_execution": True,
            })
            while True:
                data = engine.store.get(task)
                if data["state"] in TERMINAL:
                    break
                time.sleep(.1)
            report = {"mode": "real_model_and_docker", "state": data["state"], "result": data["result"], "events": [{"kind": event["kind"], "data": event["data"]} for event in data["events"]]}
            print(json.dumps(report, indent=2))
            return 0 if data["state"] == "completed" and data["result"].get("tests_verified") else 1
        except KeyboardInterrupt:
            if task:
                engine.cancel(task)
            return 130
        finally:
            engine.close()


if __name__ == "__main__":
    sys.exit(main())
