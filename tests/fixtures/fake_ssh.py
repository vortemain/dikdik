"""A stand-in for OpenSSH's `ssh` used by tests/test_ssh_remote.py.

It records every call, answers host-key and sign-in checks from a scenario file, runs
the bootstrap's Python steps on this computer with a fake home folder (so the real
install and server start are exercised), and implements `-L` forwarding with plain
sockets. Nothing here talks to a real SSH server.
"""

import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_SSH_DIR"])
KEY_LINE = "fakehost ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKeyForDikdikTestsOnlyAAAAAAAAAAAAAA"


def scenario():
    try:
        return json.loads((STATE / "scenario.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def parse(argv):
    options, forwards, flags, i = [], [], [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg in {"-o", "-L"}:
            (options if arg == "-o" else forwards).append(argv[i + 1])
            i += 2
        elif arg.startswith("-"):
            flags.append(arg)
            i += 1
        else:
            break
    return options, forwards, flags, argv[i], (argv[i + 1] if len(argv) > i + 1 else None)


def option(options, name):
    for entry in options:
        key, _, value = entry.partition("=")
        if key.lower() == name.lower():
            return value
    return None


def pump(source, target):
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, target):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def forward(spec):
    if spec.startswith("["):
        bind, rest = spec[1:].split("]:", 1)
        family = socket.AF_INET6
    else:
        bind, rest = spec.split(":", 1)
        family = socket.AF_INET
    port, _, target_port = rest.split(":")
    server = socket.socket(family, socket.SOCK_STREAM)
    server.bind((bind, int(port)))
    server.listen()

    def serve():
        while True:
            client, _ = server.accept()
            upstream = socket.create_connection(("127.0.0.1", int(target_port)))
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()


def main():
    argv = sys.argv[1:]
    options, forwards, flags, host, command = parse(argv)
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "options": options, "forwards": forwards, "flags": flags,
                              "host": host, "command": command}) + "\n")
    plan = scenario()
    known = STATE / "known_hosts"
    if "-G" in flags:
        print(f"hostname {host}\nuserknownhostsfile {known.as_posix()}")
        return 0
    if plan.get("host_key") == "changed":
        sys.stderr.write("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                         "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
                         "Host key verification failed.\n")
        return 255
    strict = option(options, "StrictHostKeyChecking")
    if strict == "accept-new":
        if option(options, "PubkeyAuthentication") != "no" or option(options, "PasswordAuthentication") != "no":
            sys.stderr.write("fake ssh: credentials offered to an unverified host\n")
            return 99
        scratch = option(options, "UserKnownHostsFile").strip('"').replace("%%", "%")
        Path(scratch).write_text(KEY_LINE + "\n", encoding="utf-8")  # dikdik-scan: ignore[PY013] test stand-in writes where ssh's UserKnownHostsFile points
        sys.stderr.write(f"Warning: Permanently added '{host}' (ED25519) to the list of known hosts.\n"
                         f"{host}: Permission denied (no authentication methods available).\n")
        return 255
    if strict != "yes" or option(options, "BatchMode") != "yes":
        sys.stderr.write("fake ssh: called without BatchMode=yes and StrictHostKeyChecking=yes\n")
        return 98
    if plan.get("host_key") == "unknown" and (not known.is_file() or KEY_LINE not in known.read_text(encoding="utf-8")):
        sys.stderr.write(f"No ED25519 host key is known for {host} and you have requested strict checking.\n"
                         "Host key verification failed.\n")
        return 255
    if plan.get("auth") == "denied":
        sys.stderr.write(f"{host}: Permission denied (publickey).\n")
        return 255
    if plan.get("unreachable"):
        sys.stderr.write(f"ssh: Could not resolve hostname {host}: Name or service not known\n")
        return 255
    if "-N" in flags:
        if plan.get("tunnel") == "fail":
            sys.stderr.write("bind [127.0.0.1]:1: Address already in use\nCould not request local forwarding.\n")
            return 255
        for spec in forwards:
            forward(spec)
        while True:
            time.sleep(1)
    home = STATE / "home"
    if command.startswith("sh -c "):
        for version, path in plan.get("pythons", [["3.13.1", "/usr/bin/python3"]]):
            print("DIKDIK-PY", version, path)
        print("DIKDIK-OS", plan.get("os", "Linux"))
        stamps = list(home.glob(".dikdik-remote/*/stamp"))
        if stamps:
            print("DIKDIK-STAMP", stamps[0].read_text(encoding="utf-8").strip())
        return 0
    parts = shlex.split(command)
    if parts[1:3] == ["-I", "-c"]:
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        if plan.get("start") == "fail" and "--ssh-session" in parts:
            sys.stderr.write("usage: dikdik [-h]\ndikdik: error: Workspace /missing does not exist\n")
            return 2
        return subprocess.run([sys.executable, *parts[1:]], env=env).returncode
    sys.stderr.write(f"fake ssh: unexpected command {command!r}\n")
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
