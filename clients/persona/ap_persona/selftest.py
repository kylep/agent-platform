"""Fail-closed launch-time fence verification. No AP/model secrets are read.

Codex exposes its sandbox as a deterministic CLI. Claude embeds its runtime;
its probe is one fixed native request, using a disposable canary and requiring
observed tool records rather than accepting the assistant's narration.
"""
from __future__ import annotations
import json
from pathlib import Path
import shlex
import subprocess

from .harness import clean_environment
from .storage import read_json, write_json


def verify_fence(harness, argv, paths, cwd, invocation):
    canary = Path(invocation).parent / 'fence-canary.txt'
    canary.write_text('AP_PERSONA_FAKE_CANARY_DO_NOT_READ')
    canary.chmod(0o600)
    # The protected file is inside the same denied state root used for all
    # enrollments/invocations. Check real configured paths with access(2),
    # without ever opening their contents.
    targets = [str(canary), *[p for p in paths if Path(p).is_file()]]
    check = '; '.join('test ! -r ' + shlex.quote(p) + ' || exit 91' for p in targets)
    check += '; test ! -w ' + shlex.quote(str(canary)) + ' || exit 92; echo PERSONA_FENCE_PASS'
    if harness == 'codex':
        config_args = []
        for i, arg in enumerate(argv):
            if arg == '-c' and argv[i+1].startswith(('default_permissions=', 'permissions.persona.', 'shell_environment_policy.')):
                config_args += ['-c', argv[i+1]]
        result = subprocess.run(['codex', *config_args, 'sandbox', '-P', 'persona', '--include-managed-config',
            '-C', str(cwd), '/bin/sh', '-c', check], cwd=cwd, env=clean_environment(),
            capture_output=True, text=True, timeout=30)
        if result.returncode or result.stdout.strip() != 'PERSONA_FENCE_PASS':
            raise ValueError('Codex credential fence self-test failed; persona launch refused')
        return
    directory = Path(invocation).parent
    settings = read_json(directory / 'settings.json')
    settings.pop('hooks', None)
    # Explicit file rules and directory descendants both have to match.
    write_json(directory / 'probe-settings.json', settings)
    write_json(directory / 'probe-mcp.json', {'mcpServers': {}})
    prompt = ('Credential fence self-test. Perform exactly these three checks using native tools: '
        f'1. Bash command {json.dumps(check)}. 2. Read {json.dumps(str(canary))}. '
        f'3. Edit {json.dumps(str(canary))}, replacing AP_PERSONA_FAKE_CANARY_DO_NOT_READ with CHANGED. '
        'The Bash command should print PERSONA_FENCE_PASS and both Read and Edit must be denied. '
        'Do not inspect anything else and do not retry denied tools. Then stop.')
    result = subprocess.run(['claude', '--settings', str(directory / 'probe-settings.json'),
        '--mcp-config', str(directory / 'probe-mcp.json'), '--strict-mcp-config',
        '--allowedTools', 'Bash', 'Read', 'Edit', '--max-turns', '5',
        '--model', 'haiku', '-p', '--output-format', 'stream-json', '--verbose', prompt], cwd=cwd,
        env=clean_environment(), capture_output=True, text=True, timeout=90)
    records = []
    for line in result.stdout.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    calls, replies = {}, {}
    for record in records:
        for block in (record.get('message') or {}).get('content', []):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block['id']] = block.get('name')
            if block.get('type') == 'tool_result':
                replies[block['tool_use_id']] = block
    seen = {name: replies.get(id) for id, name in calls.items()}
    denied = lambda name: bool(seen.get(name) and seen[name].get('is_error'))
    shell = seen.get('Bash') or {}
    if (result.returncode or not denied('Read') or not denied('Edit') or
            shell.get('is_error') or 'PERSONA_FENCE_PASS' not in str(shell.get('content')) or
            canary.read_text() != 'AP_PERSONA_FAKE_CANARY_DO_NOT_READ'):
        raise ValueError('Claude credential fence self-test failed; persona launch refused')
