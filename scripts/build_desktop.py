"""Build a self-contained desktop bundle on the target operating system."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    # Ship the matching browser with each OS build; end users do not install it.
    browsers = ROOT / "build" / "browser-runtime"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--only-shell"], check=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
               "--name", "Dikdik", "--distpath", str(ROOT / "dist" / "desktop"),
               "--workpath", str(ROOT / "build" / "desktop-freeze"),
               "--specpath", str(ROOT / "build"), "--collect-all", "webview",
               "--collect-all", "dikdik", "--collect-all", "playwright", "--collect-all", "desktop_notifier", "--copy-metadata", "dikdik"]
    icon = ROOT / "dikdik" / "web" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    command += ["--icon", str(icon)]
    if sys.platform.startswith("linux"):
        os.environ["QT_API"] = "pyside6"
        command += ["--collect-all", "PySide6", "--exclude-module", "PyQt5", "--exclude-module", "PyQt6", "--exclude-module", "PySide2"]
    elif sys.platform == "win32":
        command += ["--collect-all", "winrt"]
    elif sys.platform == "darwin":
        command += ["--osx-bundle-identifier", "org.dikdik.desktop"]
    command += [str(ROOT / "Dikdik.pyw")]
    subprocess.run(command, cwd=ROOT, check=True)
    # Chromium's binaries already have their own loader paths and signatures.
    # Copy the vendor runtime intact after freezing Python; rewriting its Mach-O
    # load commands can exceed Chromium's reserved header space on macOS.
    bundle = ROOT / "dist" / "desktop" / "Dikdik"
    if sys.platform == "darwin":
        bundle = bundle.with_suffix(".app")
        destination = bundle / "Contents" / "Resources" / "browser"
    else:
        destination = bundle / "browser"
    shutil.copytree(browsers, destination, dirs_exist_ok=True, symlinks=True)
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)

if __name__ == "__main__":
    main()
