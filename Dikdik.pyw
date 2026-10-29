"""Double-click this Windows source launcher after installing desktop support."""

from pathlib import Path
import sys

# A workspace-only dependency installation is useful when trying the desktop
# from source without changing the user's global Python environment.
dependencies = Path(__file__).resolve().parent / "build" / "desktop-deps"
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))

from dikdik.desktop import main

main()
