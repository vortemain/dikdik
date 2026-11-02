"""Reference solution: a notes JSON API on the standard library."""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Notes:
    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
        except FileNotFoundError:
            saved = {"next_id": 1, "notes": []}
        self.next_id, self.notes = saved["next_id"], saved["notes"]

    def save(self):
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"next_id": self.next_id, "notes": self.notes}, handle, ensure_ascii=False)
        os.replace(temporary, self.path)

    def find(self, note_id):
        return next((note for note in self.notes if note["id"] == note_id), None)


def check(fields, partial):
    if not isinstance(fields, dict):
        return "Send a JSON object"
    if "title" in fields or not partial:
        title = fields.get("title")
        if not isinstance(title, str) or not title or len(title) > 200:
            return "title must be a non-empty string of at most 200 characters"
    if "body" in fields and not isinstance(fields["body"], str):
        return "body must be a string"
    return None


class Handler(BaseHTTPRequestHandler):
    notes: Notes

    def log_message(self, *args):
        pass

    def answer(self, status, payload=None):
        body = b"" if status == 204 else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "null")
        except ValueError:
            return ValueError

    def target(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts[0] != "notes" or len(parts) > 2:
            return False, None
        if len(parts) == 1:
            return True, None
        return True, int(parts[1]) if parts[1].isdigit() else -1

    def do_GET(self):
        known, note_id = self.target()
        with self.notes.lock:
            if known and note_id is None:
                return self.answer(200, sorted(self.notes.notes, key=lambda n: n["id"], reverse=True))
            note = self.notes.find(note_id) if known else None
        self.answer(200, note) if note else self.answer(404, {"error": "Not found"})

    def do_POST(self):
        known, note_id = self.target()
        if not known or note_id is not None:
            return self.answer(404, {"error": "Not found"})
        fields = self.read()
        problem = "Body must be valid JSON" if fields is ValueError else check(fields, partial=False)
        if problem:
            return self.answer(400, {"error": problem})
        with self.notes.lock:
            note = {"id": self.notes.next_id, "title": fields["title"], "body": fields.get("body", ""),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            self.notes.next_id += 1
            self.notes.notes.append(note)
            self.notes.save()
        self.answer(201, note)

    def do_PUT(self):
        known, note_id = self.target()
        with self.notes.lock:
            note = self.notes.find(note_id) if known and note_id is not None else None
        if note is None:
            return self.answer(404, {"error": "Not found"})
        fields = self.read()
        problem = "Body must be valid JSON" if fields is ValueError else check(fields, partial=True)
        if problem:
            return self.answer(400, {"error": problem})
        with self.notes.lock:
            note.update({key: fields[key] for key in ("title", "body") if key in fields})
            self.notes.save()
        self.answer(200, note)

    def do_DELETE(self):
        known, note_id = self.target()
        with self.notes.lock:
            note = self.notes.find(note_id) if known and note_id is not None else None
            if note is None:
                return self.answer(404, {"error": "Not found"})
            self.notes.notes.remove(note)
            self.notes.save()
        self.answer(204)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data", default="notes.json")
    args = parser.parse_args()
    Handler.notes = Notes(os.path.abspath(args.data))
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
