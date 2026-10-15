from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import capture as cap, harness
from .storage import read_json, secure_dir, write_json
from .transport import APIError, request, secure_url

ROOT = Path.home() / '.local/state/ap-persona'


def slug(value):
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', value):
        raise ValueError('use a lowercase persona name')
    return value


def local_id(value):
    if not __import__('re').fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('invalid persona session id')
    return value


@contextmanager
def invocation_lock(path):
    # Short credential renewal lock; capture uses its own SQLite transaction.
    lock_path = Path(path).with_suffix('.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def access(path):
    with invocation_lock(path):
        state = read_json(path)
        check_writer(path, state)
        if state.get('expires_at', '') <= __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc).isoformat():
            renewed = request(state['origin'], state['renewal'], 'POST',
                              f"/sessions/{state['session_id']}/renew", {})
            if renewed['writer_epoch'] != state['writer_epoch']:
                raise ValueError('writer epoch changed; use explicit resume')
            state.update(token=renewed['token'], expires_at=renewed['expires_at'])
            write_json(path, state)
        return state


def call(path, method, suffix, body=None):
    state = access(path)
    return request(state['origin'], state['token'], method,
                   f"/sessions/{state['session_id']}/" + suffix, body)


def flush(path):
    state = read_json(path)
    batch = cap.pending(state['state_dir'])
    if batch:
        ack = call(path, 'POST', 'events', {'events': batch})
        cap.acknowledge(state['state_dir'], ack['accepted_sequence'])
    return len(cap.pending(state['state_dir']))


def hook(path):
    raw = sys.stdin.read(4 * 1024 * 1024 + 1)
    if len(raw.encode()) > 4 * 1024 * 1024:
        raise ValueError('hook payload exceeds 4MiB')
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError('hook payload must be an object')
    state = read_json(path)
    check_writer(path, state)
    native = payload.get('session_id') or payload.get('thread_id')
    event = payload.get('hook_event_name', '')
    if not native:
        raise ValueError('hook lacks a native session id')
    if event in ('SessionEnd', 'Interrupt') and state.get('native_session_id') == native:
        transcript = payload.get('transcript_path')
        if transcript and Path(transcript).exists():
            source = cap.transcript_path(state['harness'], transcript, native)
            cap.capture(state['state_dir'], state['harness'], native, source, payload, state['writer_epoch'])
        return
    try:
        call(path, 'POST', 'bind', {'native_session_id': native})
    except APIError as exc:
        # A network outage does not permit capturing a different conversation.
        if exc.status != 503 or state.get('native_session_id') != native:
            raise
    if state.get('native_session_id') not in (None, native):
        raise ValueError('native id changed; persona tools suspended until launcher resume')
    if state.get('native_session_id') is None:
        with invocation_lock(path):
            state = read_json(path)
            state['native_session_id'] = native
            write_json(path, state)
        write_json(Path(state['state_dir']) / 'binding.json', {
            'session_id': state['session_id'], 'agent': state['agent'], 'origin': state['origin'],
            'harness': state['harness'], 'native_session_id': native,
            'invocation': str(path)})
    transcript = payload.get('transcript_path')
    if transcript and Path(transcript).exists():
        source = cap.transcript_path(state['harness'], transcript, native)
        cap.capture(state['state_dir'], state['harness'], native, source, payload, state['writer_epoch'])
    try:
        flush(path)
        if event in ('SessionStart', 'UserPromptSubmit'):
            package = call(path, 'GET', 'bootstrap')
            verify_bootstrap(package, state)
            if event == 'SessionStart':
                # Codex developer_instructions is creation-only. This hook
                # reintroduces canonical identity after native resume/compact.
                print(harness.render_prompt(package))
    except APIError as exc:
        if exc.status != 503:
            raise
        # Established offline work keeps its immutable identity and spool.
        if event == 'SessionStart':
            cached = read_json(Path(path).parent / 'bootstrap.json')
            verify_bootstrap(cached, state)
            print(harness.render_prompt(cached))
        print('AP offline; tools unavailable and history pending.', file=sys.stderr)
    # SessionEnd is only a hint. /clear and Codex's delayed "other" event must
    # never drain the active writer. The launcher closes on actual process exit.


def verify_bootstrap(package, state):
    content = {k: v for k, v in package.items() if k != 'sha256'}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()
    if package['sha256'] != digest or any(package[k] != state[k] for k in
            ('agent', 'harness', 'session_id', 'writer_epoch', 'conversation_id')):
        raise ValueError('bootstrap checksum or immutable binding mismatch')


def make_invocation(root, enrollment, result, cwd):
    state_dir = secure_dir(root / 'sessions' / local_id(result['session_id']))
    directory = secure_dir(state_dir / 'invocations' / uuid.uuid4().hex)
    path = directory / 'invocation.json'
    state = {**result, 'origin': enrollment['origin'], 'state_dir': str(state_dir),
             'cwd': str(cwd), 'created_at': time.time()}
    write_json(path, state)
    cap.register_writer(state_dir, result['writer_epoch'])
    write_json(state_dir / 'writer.json', {'writer_epoch': result['writer_epoch'], 'invocation': str(path)})
    return path


def check_writer(path, state):
    writer = read_json(Path(state['state_dir']) / 'writer.json')
    if writer['writer_epoch'] != state['writer_epoch'] or writer['invocation'] != str(path):
        raise ValueError('persona writer was superseded; use the latest launcher invocation')


def launch(args, root):
    name = slug(args.persona)
    enrollment = read_json(root / 'enrollments' / (name + '.json'))
    cwd = Path(args.cwd or os.getcwd()).resolve()
    harness.check_project(args.harness, cwd)
    installed = harness.version(args.harness)
    servers = harness.ambient_mcp(args.harness)
    paths = harness.deny_paths(root, cwd, [*enrollment.get('credential_paths', []), *args.credential_path])
    resume_id = None
    if args.session_id:
        binding = read_json(root / 'sessions' / local_id(args.session_id) / 'binding.json')
        if binding['agent'] != name or binding['harness'] != args.harness or binding['origin'] != enrollment['origin']:
            raise ValueError('foreign persona, harness or AP origin in resume binding')
        resume_id = binding['native_session_id']
        result = request(enrollment['origin'], enrollment['token'], 'POST',
            f'/sessions/{args.session_id}/resume', {'native_session_id': resume_id})
    else:
        result = request(enrollment['origin'], enrollment['token'], 'POST', '/sessions',
                         {'harness': args.harness, 'launch_nonce': uuid.uuid4().hex})
    if result['agent'] != name or result['harness'] != args.harness:
        raise ValueError('enrollment returned a foreign persona')
    path = make_invocation(root, enrollment, result, cwd)
    from .selftest import verify_fence
    try:
        package = call(path, 'GET', 'bootstrap')
        verify_bootstrap(package, result)
        if resume_id and cap.refresh_sources(read_json(path)['state_dir'], args.harness, result['writer_epoch']):
            cap.note_resume_recovery(read_json(path)['state_dir'], result['writer_epoch'])
        argv = harness.build(args.harness, path, package, paths, cwd, resume_id, servers)
        if args.harness == 'codex':
            harness.verify_mcp(argv)
        verify_fence(args.harness, argv, paths, cwd, path)
        print(f"{name} · {args.harness} {installed} · session {result['session_id']}\n"
              'AP records this conversation and tool activity. Use ap-persona resume to continue it.', file=sys.stderr)
        proc = subprocess.run(argv, cwd=cwd, env=harness.clean_environment())
        return proc.returncode
    finally:
        try:
            final_state = read_json(path)
            cap.refresh_sources(final_state['state_dir'], args.harness, final_state['writer_epoch'])
            pending = flush(path)
            call(path, 'POST', 'close', {})
            if not pending:
                call(path, 'POST', 'finalize', {})
        except (APIError, ValueError):
            print('Final synchronization pending; use ap-persona sync. Local records retained.', file=sys.stderr)


def parser():
    p = argparse.ArgumentParser(prog='ap-persona')
    p.add_argument('--state-root', type=Path, default=ROOT)
    sub = p.add_subparsers(dest='verb', required=True)
    enroll = sub.add_parser('enroll', help='store a human-created enrollment; token read privately')
    enroll.add_argument('--persona', required=True)
    enroll.add_argument('--origin', required=True)
    enroll.add_argument('--credential-path', action='append', default=[])
    for verb in ('launch', 'resume'):
        launch_p = sub.add_parser(verb)
        launch_p.add_argument('--persona', required=True)
        launch_p.add_argument('--harness', required=True, choices=['claude', 'codex'])
        launch_p.add_argument('--cwd')
        launch_p.add_argument('--credential-path', action='append', default=[])
        launch_p.add_argument('session_id', nargs=None if verb == 'resume' else '?')
    for verb in ('status', 'sync'):
        command = sub.add_parser(verb)
        command.add_argument('session_id')
        command.add_argument('--recover', action='store_true')
    for verb in ('hook', 'headers'):
        command = sub.add_parser(verb)
        command.add_argument('--invocation', type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.verb == 'headers':
            state = access(args.invocation)
            print(json.dumps({'Authorization': 'Bearer ' + state['token']}))
        elif args.verb == 'hook':
            try:
                hook(args.invocation)
            except (APIError, ValueError, OSError) as exc:
                print(str(exc), file=sys.stderr)
                state = read_json(args.invocation)
                if state['harness'] == 'codex':
                    print(json.dumps({'continue': False, 'stopReason': 'Persona binding or sync unavailable'}))
                return 2  # Claude UserPromptSubmit's fail-closed boundary.
        else:
            root = secure_dir(args.state_root)
            if args.verb == 'enroll':
                token = getpass.getpass('Enrollment token from AP Settings: ').strip()
                if not token.startswith('lpe_') or len(token) < 40:
                    raise ValueError('expected a persona enrollment token')
                write_json(root / 'enrollments' / (slug(args.persona) + '.json'),
                           {'origin': secure_url(args.origin), 'token': token,
                            'credential_paths': harness.deny_paths(root, Path.cwd(), args.credential_path)})
                print('Enrollment stored. Ordinary harness configuration is unchanged.')
            elif args.verb in ('launch', 'resume'):
                return launch(args, root)
            else:
                directory = root / 'sessions' / local_id(args.session_id)
                binding = read_json(directory / 'binding.json')
                path = Path(binding['invocation'])
                if args.recover:
                    enrollment = read_json(root / 'enrollments' / (slug(binding['agent']) + '.json'))
                    result = request(enrollment['origin'], enrollment['token'], 'POST',
                        f'/sessions/{args.session_id}/recover', {})
                    result['renewal'] = ''  # upload-only, no domain renewal
                    path = make_invocation(root, enrollment, result, Path.cwd())
                if args.verb == 'sync':
                    cap.refresh_sources(directory, binding['harness'], read_json(path)['writer_epoch'])
                    # Explicit sync may drain several bounded batches, no daemon.
                    for _ in range(100):
                        if not flush(path):
                            break
                state = read_json(path)
                try:
                    remote = call(path, 'GET', 'status')
                except APIError as exc:
                    remote = {'unavailable': str(exc)}
                print(json.dumps({'session_id': args.session_id, 'pending_batch_events':
                    len(cap.pending(state['state_dir'])), 'remote': remote}, indent=2))
        return 0
    except (APIError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
