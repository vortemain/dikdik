"""Process-only credentials, shared by stores for the same private data path.

Never serialized or exported to command environments. Expire after eight hours.
Python cannot guarantee erasure of copies or exclusion from OS crash/swap files.
"""
import os
import threading
import time

_lock = threading.RLock()
_values = {}
TTL = 8 * 60 * 60


def entries(path):
    namespace = os.path.normcase(str(path.resolve()))
    with _lock:
        now = time.monotonic()
        for key, value in list(_values.items()):
            if value[1] <= now:
                del _values[key]
        return {ref: value[0] for (scope, ref), value in _values.items() if scope == namespace}


def put(path, ref, value):
    with _lock:
        _values[(os.path.normcase(str(path.resolve())), ref)] = (bytes(value), time.monotonic() + TTL)


def remove(path, ref):
    with _lock:
        return _values.pop((os.path.normcase(str(path.resolve())), ref), None) is not None


def clear(path):
    namespace = os.path.normcase(str(path.resolve()))
    with _lock:
        for key in list(_values):
            if key[0] == namespace:
                del _values[key]
