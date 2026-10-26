"""Reproducible local inventory benchmark, not an agent-success benchmark."""

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dikdik.repository import inventory, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 2 <= args.samples <= 100:
        parser.error("Use 2–100 samples")
    with tempfile.TemporaryDirectory(prefix="dikdik-benchmark-") as temp:
        root = Path(temp)
        for i in range(500):
            path = root / f"package_{i // 25}" / f"module_{i}.py"
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"def function_{i}(value):\n    return value + {i}\n" * 20)
        (root / ".env").write_text("DO_NOT_INDEX=fixture\n")
        times = []
        for _ in range(args.samples):
            start = time.perf_counter()
            files = inventory(root)
            info = metadata(files)
            times.append((time.perf_counter() - start) * 1000)
        assert len(info["files"]) == 500 and ".env" not in files
        # Change one file; implementation currently rescans everything.
        (root / "package_0" / "module_0.py").write_text("changed = True\n")
        start = time.perf_counter()
        refreshed = metadata(inventory(root))
        refresh_ms = (time.perf_counter() - start) * 1000
        assert info["files"][0]["sha256"] != refreshed["files"][0]["sha256"]
        report = {
            "scope": "Synthetic 500-file UTF-8 inventory + SHA256 metadata; no model, Docker, retrieval-quality or end-to-end agent performance claims",
            "baseline": "First implementation; no preexisting application or comparative optimization baseline",
            "environment": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(), "logical_cpus": os.cpu_count()},
            "fixture": {"files": 500, "bytes": sum(len(t.encode()) for t in files.values()), "excluded_hidden_files": 1},
            "samples": args.samples,
            "first_scan_ms": round(times[0], 3),
            "repeated_scan_median_ms": round(statistics.median(times[1:]), 3),
            "all_samples_ms": [round(x, 3) for x in times],
            "one_file_change_full_refresh_ms": round(refresh_ms, 3),
            "methodology": "perf_counter wall time; freshly written synthetic fixture; sequential same-process scans; OS cache not flushed; first scan is NOT a cold-cache measurement; no tail percentile claimed at this sample count; no incremental cache yet",
        }
    rendered = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
