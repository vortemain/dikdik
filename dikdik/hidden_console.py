"""Keep child processes from opening console windows when Dikdik has no console.

On Windows a GUI process (the desktop app, pythonw, a windowed frozen build)
has no console. Every console program it starts - git, node, npm, python,
PowerShell, MCP servers, language tools - then gets a brand-new console
window unless it is created with CREATE_NO_WINDOW. Many call sites pass that
flag, but third-party libraries and new code easily miss it, and each miss
flashes a terminal window in front of the user.

install() makes CREATE_NO_WINDOW the default for every subprocess.Popen in
this process (asyncio and libraries included). A caller that explicitly asks
for its own console (CREATE_NEW_CONSOLE) or a detached process keeps it.
When Dikdik was started from a terminal, children share that terminal and no
windows appear, so nothing changes. Other platforms are unaffected.
"""

import functools
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
_CREATIONFLAGS_INDEX = 13  # Position of creationflags after args in Popen(...)
_MARK = "_dikdik_hidden_console"


def hidden_flags(flags: int | None) -> int:
    flags = int(flags or 0)
    if flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS):
        return flags
    return flags | CREATE_NO_WINDOW


def _has_console() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def install(popen=None, *, force: bool = False) -> bool:
    """Patch Popen once. Returns True when child consoles are now hidden by default."""
    if popen is None:
        if sys.platform != "win32" or (not force and _has_console()):
            return False
        popen = subprocess.Popen
    original = popen.__init__
    if getattr(original, _MARK, False):
        return True

    @functools.wraps(original)
    def __init__(self, *args, **kwargs):
        if len(args) > _CREATIONFLAGS_INDEX:
            args = list(args)
            args[_CREATIONFLAGS_INDEX] = hidden_flags(args[_CREATIONFLAGS_INDEX])
        else:
            kwargs["creationflags"] = hidden_flags(kwargs.get("creationflags", 0))
        original(self, *args, **kwargs)

    setattr(__init__, _MARK, True)
    popen.__init__ = __init__
    return True
