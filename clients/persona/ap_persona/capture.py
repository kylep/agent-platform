from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

from .adapters import normalize, redact
from .storage import spool


def transcript_path(harness, raw, native_id, home=None):
    home = Path(home or Path.home())
    path = Path(raw).absolute()
    root = home / ('.claude/projects' if harness == 'claude' else '.codex/sessions')
    if path.resolve() != path or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('transcript must be a regular bound harness file without symlinks')
    if (harness == 'claude' and path.name != native_id + '.jsonl') or (
            harness == 'codex' and not path.name.endswith('-' + native_id + '.jsonl')):
        raise ValueError('transcript filename does not match native session binding')
    if path.stat().st_uid != os.getuid():
        raise ValueError('foreign transcript owner')
    return path


def append(db, event_id, turn, kind, payload, source, timestamp=''):
    value = {'schema_version': 1, 'event_id': event_id, 'local_turn_id': turn,
        'kind': kind, 'payload': redact(payload), 'source': source,
        'occurred_at': timestamp if isinstance(timestamp, str) else ''}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    existing = db.execute('SELECT payload FROM events WHERE event_id=?', (event_id,)).fetchone()
    if existing:
        if existing[0] != encoded:
            raise ValueError('native record was rewritten after capture')
        return
    db.execute('INSERT INTO events(event_id,payload) VALUES(?,?)', (event_id, encoded))


def capture(state_dir, harness, native_id, path, hook=None, writer_epoch=None):
    """Atomic capture cursor + spool, bounded to 4MiB per event. No network
    while the SQLite writer lock is held. Partial trailing lines are retried.
    """
    db = spool(Path(state_dir) / 'spool.sqlite')
    source = hashlib.sha256(str(path).encode()).hexdigest()[:20]
    with db:
        db.execute('BEGIN IMMEDIATE')
        epoch = db.execute("SELECT value FROM metadata WHERE key='writer_epoch'").fetchone()
        if writer_epoch is not None and (not epoch or int(epoch[0]) != writer_epoch):
            raise ValueError('local writer was superseded; capture refused')
        row = db.execute('SELECT offset,native_id,turn_id,skipped,inode FROM sources WHERE path=?', (str(path),)).fetchone()
        info = path.stat()
        inode = f'{info.st_dev}:{info.st_ino}'
        offset, bound, turn, skipping, prior_inode = row or (0, native_id, (hook or {}).get('turn_id') or native_id, 0, inode)
        if bound != native_id:
            raise ValueError('transcript source binding changed')
        if info.st_size < offset or inode != prior_inode:
            append(db, f'{source}:truncated:{offset}', turn, 'adapter_gap',
                   {'reason': 'native transcript truncated; records deduplicated on re-read'}, source)
            offset, skipping = 0, 0
        read_bytes = 0
        with path.open('rb') as f:
            f.seek(offset)
            while read_bytes < 4 * 1024 * 1024:
                start = f.tell()
                line = f.readline(1024 * 1024)
                if not line:
                    break
                read_bytes += len(line)
                if skipping:
                    offset = f.tell()
                    skipping = int(not line.endswith(b'\n'))
                    continue
                if not line.endswith(b'\n'):
                    if len(line) < 1024 * 1024:
                        break
                    append(db, f'{source}:oversized:{start}', turn, 'adapter_gap',
                           {'reason': 'native record exceeds 1MiB; omitted'}, source)
                    offset, skipping = f.tell(), 1
                    continue
                # A contradictory session id is a binding failure, not a
                # parse gap followed by permission to ingest later records.
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    record = {}
                if not isinstance(record, dict):
                    record = {}
                rid = record.get('sessionId') if harness == 'claude' else (
                    (record.get('payload') or {}).get('id') if record.get('type') == 'session_meta' else None)
                if rid and rid != native_id:
                    raise ValueError('foreign native session inside transcript')
                try:
                    key, turn, events = normalize(harness, record, turn)
                    key = key or hashlib.sha256(line).hexdigest()
                    identity = source + ':' + str(key)
                    fingerprint = hashlib.sha256(line).hexdigest()
                    previous = db.execute('SELECT digest FROM native_records WHERE id=?', (identity,)).fetchone()
                    if previous and previous[0] != fingerprint:
                        raise ValueError('native record rewritten')
                    db.execute('INSERT OR IGNORE INTO native_records VALUES(?,?)', (identity, fingerprint))
                    for i, (kind, payload) in enumerate(events):
                        append(db, f'{source}:{key}:{i}', turn, kind, payload, source, record.get('timestamp', ''))
                except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
                    append(db, f'{source}:gap:{start}', turn, 'adapter_gap',
                           {'reason': 'malformed, unsupported or conflicting native record'}, source)
                offset = f.tell()
        if hook:
            event = hook.get('hook_event_name', '')
            # Claude's transcript doesn't distinguish commentary/final blocks.
            # Stop's final text is the observed terminal fact, stored once.
            if event == 'Stop' and harness == 'claude' and hook.get('last_assistant_message'):
                full_final = redact(hook['last_assistant_message'])
                key = hashlib.sha256((turn + full_final).encode()).hexdigest()
                final = full_final[:24000]
                if len(full_final) > len(final):
                    append(db, f'{source}:final-gap:{key}', turn, 'adapter_gap',
                        {'reason': 'final text exceeds preview limit; bounded preview retained'}, source)
                append(db, f'{source}:final:{key}', turn, 'assistant_message', {'text': final, 'final': True}, source)
                append(db, f'{source}:end:{key}', turn, 'turn_end', {'outcome': 'complete'}, source)
            if event == 'Stop' and harness == 'codex':
                append(db, f'{source}:stop:{turn}', turn, 'turn_end', {'outcome': 'complete'}, source)
            if event == 'Interrupt':
                append(db, f'{source}:interrupt:{offset}', turn, 'interrupt', {}, source)
            if event in ('SubagentStart', 'SubagentStop'):
                # No globbing or trusting arbitrary child transcript paths.
                # Child call authority is session-scoped; source fidelity is
                # explicitly degraded until a verified parent binding exists.
                append(db, f'{source}:child:{offset}:{event}', turn, 'adapter_gap',
                       {'reason': 'child transcript not independently bound; parent tool summary retained'}, source)
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)', (str(path), offset, native_id, turn, skipping, inode))
    db.close()


def refresh_sources(state_dir, harness, writer_epoch=None):
    db = spool(Path(state_dir) / 'spool.sqlite')
    before = db.execute('SELECT coalesce(max(sequence),0) FROM events').fetchone()[0]
    sources = db.execute('SELECT path,native_id FROM sources').fetchall()
    db.close()
    for raw, native in sources:
        path = transcript_path(harness, raw, native)
        capture(state_dir, harness, native, path, writer_epoch=writer_epoch)
    db = spool(Path(state_dir) / 'spool.sqlite')
    after = db.execute('SELECT coalesce(max(sequence),0) FROM events').fetchone()[0]
    if after > before and writer_epoch is not None:
        # Explicit recovery after a gap cannot prove whether the old process
        # was offline, crashed, or resumed without the launcher.
        with db:
            db.execute('BEGIN IMMEDIATE')
            epoch = db.execute("SELECT value FROM metadata WHERE key='writer_epoch'").fetchone()
            if not epoch or int(epoch[0]) != writer_epoch:
                raise ValueError('recovery writer was superseded')
    db.close()
    return after - before


def note_resume_recovery(state_dir, writer_epoch):
    db = spool(Path(state_dir) / 'spool.sqlite')
    with db:
        db.execute('BEGIN IMMEDIATE')
        epoch = db.execute("SELECT value FROM metadata WHERE key='writer_epoch'").fetchone()
        if not epoch or int(epoch[0]) != writer_epoch:
            raise ValueError('recovery writer was superseded')
        append(db, f'launcher:recovery:{writer_epoch}', f'recovery:{writer_epoch}', 'adapter_gap',
               {'reason': 'history recovered after an unobserved/offline or unsupported native resume gap'}, 'launcher')
    db.close()


def register_writer(state_dir, writer_epoch):
    db = spool(Path(state_dir) / 'spool.sqlite')
    with db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute("SELECT value FROM metadata WHERE key='writer_epoch'").fetchone()
        if prior and int(prior[0]) >= writer_epoch:
            raise ValueError('local writer epoch did not advance')
        db.execute("INSERT OR REPLACE INTO metadata VALUES('writer_epoch',?)", (str(writer_epoch),))
    db.close()


def pending(state_dir, limit=100):
    db = spool(Path(state_dir) / 'spool.sqlite')
    ack = int((db.execute("SELECT value FROM metadata WHERE key='ack'").fetchone() or ('0',))[0])
    rows = db.execute('SELECT sequence,payload FROM events WHERE sequence>? ORDER BY sequence LIMIT ?', (ack, limit)).fetchall()
    db.close()
    result, size = [], 0
    for sequence, payload in rows:
        value = json.loads(payload)
        size += len(payload.encode())
        if size > 450000:
            break
        result.append({**value, 'sequence': sequence})
    return result


def acknowledge(state_dir, sequence):
    db = spool(Path(state_dir) / 'spool.sqlite')
    with db:
        maximum = db.execute('SELECT coalesce(max(sequence),0) FROM events').fetchone()[0]
        if type(sequence) is not int or not 0 <= sequence <= maximum:
            raise ValueError('server cursor exceeds captured spool')
        old = int((db.execute("SELECT value FROM metadata WHERE key='ack'").fetchone() or ('0',))[0])
        db.execute("INSERT OR REPLACE INTO metadata VALUES('ack',?)", (str(max(old, sequence)),))
    db.close()
