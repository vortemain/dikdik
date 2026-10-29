"""Build a self-contained desktop bundle on the target operating system."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    # Ship the matching browser with each OS build; end users do not install it.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
               "--name", "Dikdik", "--distpath", str(ROOT / "dist" / "desktop"),
               "--workpath", str(ROOT / "build" / "desktop-freeze"),
               "--specpath", str(ROOT / "build"), "--collect-all", "webview",
               "--collect-all", "dikdik", "--collect-all", "playwright", "--collect-all", "desktop_notifier", "--copy-metadata", "dikdik"]
    icon = ROOT / "dikdik" / "web" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    command += ["--icon", str(icon)]
    if sys.platform.startswith("linux"):
        command += ["--collect-all", "PySide6"]
    elif sys.platform == "win32":
        command += ["--collect-all", "winrt"]
    elif sys.platform == "darwin":
        command += ["--osx-bundle-identifier", "org.dikdik.desktop"]
    command += [str(ROOT / "Dikdik.pyw")]
    subprocess.run(command, cwd=ROOT, check=True)

if __name__ == "__main__":
    main()
