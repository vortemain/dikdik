"""Benchmark checker for fixtures/landing, judged in a real browser (needs Playwright).

Passes when the header flows into the hero: no visible bottom border or shadow on the
header, no gap between them, and the same background colour. Run from the project copy:
    python check_landing.py
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dikdik import local_preview  # noqa: E402

root = Path.cwd()
result = local_preview.preview(root, f'"{sys.executable}" -m http.server {{port}} --bind 127.0.0.1', threading.Event(),
                               inspect=[".site-header", ".site-header + *"])
if result.get("status") != "passed":
    sys.exit(f"preview failed: {result.get('error') or result.get('issues')}")
problems = []
for width, items in result["inspection"].items():
    header, after = items
    styles, below = header.get("styles", {}), after.get("styles", {})
    border = styles.get("border-bottom", "")
    if border and not border.startswith("0px") and " none " not in f" {border} ":
        problems.append(f"{width}: header border-bottom {border}")
    if styles.get("box-shadow"):
        problems.append(f"{width}: header box-shadow {styles['box-shadow']}")
    if header.get("gap_to_next_sibling", 0) != 0:
        problems.append(f"{width}: {header['gap_to_next_sibling']}px gap below the header")
    top, bottom = styles.get("background-color", "transparent"), below.get("background-color", "transparent")
    if top != bottom and "rgba(0, 0, 0, 0)" not in (top, bottom) and top != "transparent":
        problems.append(f"{width}: header {top} vs next section {bottom}")
print("\n".join(problems) or "header flows into the page at desktop and mobile width")
sys.exit(1 if problems else 0)
