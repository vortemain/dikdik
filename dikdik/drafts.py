"""Private, workspace-scoped composer drafts with optimistic concurrency."""

import re
import time


class DraftConflict(Exception):
    def __init__(self, current):
        self.current = current


class Drafts:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS composer_drafts (scope TEXT PRIMARY KEY, content TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL, updated REAL NOT NULL)")

    @staticmethod
    def _scope(db, scope):
        if not isinstance(scope, str) or (scope != 'home' and not re.fullmatch('[a-f0-9]{32}', scope)):
            raise ValueError('Choose home or an existing conversation for this draft')
        if scope != 'home' and db.execute('SELECT 1 FROM tasks WHERE id=?', (scope,)).fetchone() is None:
            raise KeyError(scope)

    @staticmethod
    def _get(db, scope):
        row = db.execute('SELECT * FROM composer_drafts WHERE scope=?', (scope,)).fetchone()
        return dict(row) if row else {'scope': scope, 'content': '', 'revision': 0, 'updated': 0.0}

    def get(self, scope):
        with self.store.connect() as db:
            self._scope(db, scope)
            return self._get(db, scope)

    def save(self, scope, body):
        if not isinstance(body, dict) or set(body) != {'content', 'revision'}:
            raise ValueError('Draft updates require content and the last known revision')
        if not isinstance(body['content'], str) or len(body['content']) > 4000 or '\0' in body['content']:
            raise ValueError('Drafts must contain at most 4000 characters without NUL')
        if type(body['revision']) is not int or not 0 <= body['revision'] < 2**53:
            raise ValueError('Draft revision must be a non-negative integer')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._scope(db, scope)
            current = self._get(db, scope)
            if current['revision'] != body['revision']:
                raise DraftConflict(current)
            revision, updated = current['revision'] + 1, time.time()
            db.execute('INSERT INTO composer_drafts VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET content=excluded.content,revision=excluded.revision,updated=excluded.updated',
                       (scope, body['content'], revision, updated))
            return {'scope': scope, 'content': body['content'], 'revision': revision, 'updated': updated}
