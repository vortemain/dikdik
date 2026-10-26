"""Build the Rust repository indexer and place it inside the dikdik package.

    python scripts/build_native.py

The app loads `dikdik/dikdik_index.*` automatically and falls back to the Python
walk when it is absent or disagrees with the Python rules.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if shutil.which("cargo") is None:
        print("cargo is not installed; the Python indexer will be used.", file=sys.stderr)
        return 1
    subprocess.run(["cargo", "build", "--release", "-p", "dikdik-index"], cwd=ROOT, check=True)
    release = ROOT / "target" / "release"
    if sys.platform == "win32":
        built, target = release / "dikdik_index.dll", ROOT / "dikdik" / "dikdik_index.pyd"
    elif sys.platform == "darwin":
        built, target = release / "libdikdik_index.dylib", ROOT / "dikdik" / "dikdik_index.abi3.so"
    else:
        built, target = release / "libdikdik_index.so", ROOT / "dikdik" / "dikdik_index.abi3.so"
    shutil.copyfile(built, target)
    check = subprocess.run([sys.executable, "-c", "from dikdik import repository; print(repository.INDEXER)"],
                           cwd=ROOT, capture_output=True, text=True, check=True)
    print(f"Installed {target.relative_to(ROOT)}; active indexer: {check.stdout.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
