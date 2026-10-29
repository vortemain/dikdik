"""Register the Start menu identity required by unpackaged Windows toasts."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import uuid


def ensure_shortcut():
    """Keep installer shortcuts; also support portable and source desktop launches."""
    if sys.platform != "win32":
        return
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Dikdik/Dikdik.lnk"
    if shortcut.exists():
        return
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    executable = Path(sys.executable)
    arguments = ""
    if not getattr(sys, "frozen", False):
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
        launcher = root / "Dikdik.pyw"
        arguments = subprocess.list2cmdline([str(launcher)]) if launcher.exists() else "-m dikdik.desktop"

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3),
                    ("value", ctypes.c_void_p), ("padding", ctypes.c_void_p)]

    def check(result):
        if result < 0:
            raise OSError(f"Windows shortcut registration failed: 0x{result & 0xffffffff:08x}")

    def call(pointer, index, args, *values):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])
        return method(pointer, *values)

    ole = ctypes.OleDLL("ole32")
    initialized = ole.CoInitializeEx(None, 2)
    check(initialized)
    link, properties, persist = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        check(ole.CoCreateInstance(ctypes.byref(GUID("00021401-0000-0000-c000-000000000046")), None, 1,
                                  ctypes.byref(GUID("000214f9-0000-0000-c000-000000000046")), ctypes.byref(link)))
        check(call(link, 20, [ctypes.c_wchar_p], str(executable)))
        check(call(link, 11, [ctypes.c_wchar_p], arguments))
        check(call(link, 9, [ctypes.c_wchar_p], str(root)))
        check(call(link, 7, [ctypes.c_wchar_p], "Dikdik desktop"))
        check(call(link, 17, [ctypes.c_wchar_p, ctypes.c_int], str(Path(__file__).parent / "web/icon.ico"), 0))
        query = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        check(call(link, 0, query, ctypes.byref(GUID("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")), ctypes.byref(properties)))
        identity = ctypes.create_unicode_buffer("Dikdik.Desktop")
        value = PROPVARIANT(31, (ctypes.c_ushort * 3)(), ctypes.cast(identity, ctypes.c_void_p), None)
        key = PROPERTYKEY(GUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
        check(call(properties, 6, [ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)], ctypes.byref(key), ctypes.byref(value)))
        check(call(properties, 7, []))
        check(call(link, 0, query, ctypes.byref(GUID("0000010b-0000-0000-c000-000000000046")), ctypes.byref(persist)))
        check(call(persist, 6, [ctypes.c_wchar_p, wintypes.BOOL], str(shortcut), True))
    finally:
        for pointer in (persist, properties, link):
            if pointer:
                call(pointer, 2, [])
        ole.CoUninitialize()
