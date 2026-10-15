from __future__ import annotations
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path


def secure_dir(path):
    path = Path(path).absolute()
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise ValueError('persona state cannot traverse symlinks')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError('persona state has a foreign owner')
    path.chmod(0o700)
    return path


def read_json(path):
    path = Path(path)
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError('symlink in persona state path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('persona state must be an owner-only regular file')
        return json.load(f)


def write_json(path, value):
    path = Path(path)
    secure_dir(path.parent)
    if path.is_symlink():
        raise ValueError('refusing persona state symlink')
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def spool(path):
    path = Path(path)
    secure_dir(path.parent)
    if path.is_symlink():
        raise ValueError('refusing persona spool symlink')
    db = sqlite3.connect(path, timeout=3)
    path.chmod(0o600)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS sources (
          path TEXT PRIMARY KEY, offset INTEGER NOT NULL, native_id TEXT NOT NULL,
          turn_id TEXT NOT NULL, skipped INTEGER NOT NULL DEFAULT 0, inode TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS events (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS native_records (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
    ''')
    if 'inode' not in {r[1] for r in db.execute('PRAGMA table_info(sources)')}:
        db.execute("ALTER TABLE sources ADD COLUMN inode TEXT NOT NULL DEFAULT ''")
    return db
