"""Build a self-contained desktop bundle on the target operating system."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = "Vorte by Lijohtech Developers"  # must match the code-signing certificate's subject


def windows_version_file(version: str) -> str:
    """A PyInstaller version resource, so Windows shows the publisher and version on Dikdik.exe."""
    numbers = tuple(int(part) for part in (version.split("-")[0].split(".") + ["0", "0", "0"])[:4])
    fields = {"CompanyName": PUBLISHER, "FileDescription": "Dikdik", "FileVersion": version,
              "InternalName": "Dikdik", "LegalCopyright": f"(c) {PUBLISHER}", "OriginalFilename": "Dikdik.exe",
              "ProductName": "Dikdik", "ProductVersion": version}
    strings = ",\n            ".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    return (f"VSVersionInfo(\n  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,\n"
            f"    fileType=0x1, subtype=0x0, date=(0, 0)),\n  kids=[\n    StringFileInfo([StringTable('040904B0', [\n"
            f"            {strings}])]),\n    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n  ]\n)\n")


def main():
    # Ship the matching browser with each OS build; end users do not install it.
    browsers = ROOT / "build" / "browser-runtime"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--only-shell"], check=True, timeout=1800)
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
        sys.path.insert(0, str(ROOT))
        from dikdik import __version__
        version_file = ROOT / "build" / "windows-version.txt"
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(windows_version_file(__version__), encoding="utf-8")
        command += ["--collect-all", "winrt", "--version-file", str(version_file)]
    elif sys.platform == "darwin":
        command += ["--osx-bundle-identifier", "org.dikdik.desktop"]
    command += [str(ROOT / "Dikdik.pyw")]
    subprocess.run(command, cwd=ROOT, check=True, timeout=3600)
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
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True, timeout=600)

if __name__ == "__main__":
    main()
