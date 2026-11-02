"""Reference solution: a to-do list on the command line."""

import json
import os
import sys


def path():
    return os.environ.get("TODO_FILE") or os.path.join(os.getcwd(), "todo.json")


def load():
    try:
        with open(path(), encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {"next_id": 1, "tasks": []}


def save(data):
    with open(path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


def fail(message):
    print(message, file=sys.stderr)
    return 1


def find(data, raw):
    if not raw.isdigit():
        return None
    return next((task for task in data["tasks"] if task["id"] == int(raw)), None)


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        return fail("Usage: todo.py add TEXT | list [--open] | done ID | remove ID")
    command, args = argv[0], argv[1:]
    data = load()
    if command == "add":
        text = " ".join(args).strip()
        if not text:
            return fail("Give the task text: todo.py add TEXT")
        task = {"id": data["next_id"], "text": text, "done": False}
        data["next_id"] += 1
        data["tasks"].append(task)
        save(data)
        print(f"Added {task['id']}: {text}")
        return 0
    if command == "list":
        shown = [t for t in data["tasks"] if not (args[:1] == ["--open"] and t["done"])]
        if not data["tasks"]:
            print("No tasks")
        for task in sorted(shown, key=lambda t: t["id"]):
            print(f"[{'x' if task['done'] else ' '}] {task['id']} {task['text']}")
        return 0
    if command in ("done", "remove"):
        if not args:
            return fail(f"Give the task id: todo.py {command} ID")
        task = find(data, args[0])
        if task is None:
            return fail(f"No task with id {args[0]}")
        if command == "done":
            task["done"] = True
        else:
            data["tasks"].remove(task)
        save(data)
        return 0
    return fail(f"Unknown command: {command}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
