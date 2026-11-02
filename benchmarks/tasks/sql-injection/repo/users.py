import sqlite3


def connect():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (name TEXT, role TEXT)")
    db.executemany("INSERT INTO users VALUES (?, ?)", [("ada", "admin"), ("bob", "user")])
    return db


def find_user(db, name):
    """Return (name, role) for the user called `name`, or None."""
    return db.execute(f"SELECT name, role FROM users WHERE name = '{name}'").fetchone()
