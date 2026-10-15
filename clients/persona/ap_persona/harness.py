from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tomllib

from .storage import write_json

SUPPORTED = {'claude': {'2.1.299', '2.1.300'}, 'codex': {'0.162.1'}}
EVENTS = {'claude': ['SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure',
                     'Stop', 'PreCompact', 'SessionEnd', 'SubagentStart', 'SubagentStop'],
          'codex': ['SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure',
                    'Stop', 'Interrupt', 'SessionEnd', 'SubagentStart', 'SubagentStop']}


def clean_environment(environ=None):
    return {k: v for k, v in (os.environ if environ is None else environ).items()
            if not k.startswith(('AP_', 'PERSONA_'))}


def version(harness):
    result = subprocess.run([harness, '--version'], capture_output=True, text=True,
                            check=True, env=clean_environment(), timeout=10)
    match = re.search(r'\b(\d+\.\d+\.\d+)\b', result.stdout)
    if not match or match[1] not in SUPPORTED[harness]:
        raise ValueError('unsupported harness version; run compatibility probes before adding support')
    return match[1]


def command(verb, invocation):
    return shlex.join([sys.executable, '-I', str(Path(__file__).resolve().parent / 'entry.py'), verb, '--invocation', str(invocation)])


def deny_paths(state_root, cwd, extra=(), home=None):
    home = Path(home or Path.home())
    paths = {home / '.claude', home / '.claude.json', home / '.codex',
             home / '.ssh', home / '.aws', home / '.git-credentials',
             *[home / name for name in ('.zshrc','.zshenv','.zprofile','.profile','.bashrc','.bash_profile')],
             Path(state_root), Path(__file__).resolve().parent}
    for directory in [Path(cwd), *Path(cwd).parents]:
        paths.update(directory / name for name in ('.claude', '.codex', '.mcp.json'))
    source = Path(__file__).resolve()
    for directory in [Path(cwd), *Path(cwd).parents, *source.parents]:
        for name in ('exports.sh', '.exports.sh', '.env', '.env.local'):
            if (directory / name).exists():
                paths.add(directory / name)
    if sys.prefix != sys.base_prefix:
        paths.add(Path(sys.prefix).absolute())
    paths.update(Path(p).absolute() for p in extra)
    return sorted({str(p.absolute()) for p in paths} | {str(p.resolve()) for p in paths})


def _toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + _toml(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(_toml(v) for v in value) + ']'
    if isinstance(value, bool):
        return str(value).lower()
    return json.dumps(value)


def check_project(harness, cwd, home=None):
    home = Path(home or Path.home())
    for directory in [Path(cwd), *Path(cwd).parents, home]:
        if harness == 'codex':
            root = directory / '.codex'
            for name in ('hooks', 'hooks.json', 'hooks.toml', 'config.toml'):
                path = root / name
                if path.exists() and directory != home:
                    # Flag trusts repository hooks too; conservatively reject
                    # project configs rather than trying to emulate merging.
                    raise ValueError('persona mode refuses repository .codex hook/config files')
        else:
            for name in ('.claude/settings.json', '.claude/settings.local.json'):
                path = directory / name
                if path.exists():
                    config = json.loads(path.read_text())
                    if config.get('sandbox', {}).get('excludedCommands'):
                        raise ValueError('persona mode refuses unsandboxed excludedCommands')


def ambient_mcp(harness):
    if harness != 'codex':
        return []
    result = subprocess.run(['codex', 'mcp', 'list', '--json'], capture_output=True,
                            text=True, timeout=15, check=True, env=clean_environment())
    data = json.loads(result.stdout)
    if not isinstance(data, list) or any(not isinstance(d.get('name'), str) for d in data):
        raise ValueError('cannot enumerate effective Codex MCP servers')
    return {d['name']: ('http' if (d.get('transport') or {}).get('type') == 'streamable_http' or (d.get('transport') or {}).get('url') else 'stdio') for d in data}


def build(harness, invocation, bootstrap, paths, cwd, resume_id=None, servers=()):
    invocation = Path(invocation)
    hook_command = command('hook', invocation)
    headers = command('headers', invocation)
    binding = json.loads(invocation.read_text())
    url = binding['origin'] + '/persona/mcp'
    prompt = render_prompt(bootstrap)
    write_json(invocation.parent / 'bootstrap.json', bootstrap)
    prompt_file = invocation.parent / 'persona.md'
    prompt_file.write_text(prompt)
    prompt_file.chmod(0o600)
    hooks = {event: [{'hooks': [{'type': 'command', 'command': hook_command, 'timeout': (3 if event in ('SessionEnd', 'Interrupt') else 12), **({'additionalContextLimit': 256000} if harness == 'codex' and event == 'SessionStart' else {})}]}]
             for event in EVENTS[harness]}
    if harness == 'claude':
        settings = {'hooks': hooks, 'sandbox': {'enabled': True,
            'allowUnsandboxedCommands': False, 'failIfUnavailable': True,
            'filesystem': {'denyRead': paths, 'denyWrite': paths, 'disabled': False},
            # No direct shell networking: MCP and hooks are the AP boundary.
            'network': {'allowedDomains': [], 'deniedDomains': [__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(binding['origin']).hostname]},
            'autoAllowBashIfSandboxed': False,
            'credentials': {'envVars': [{'name': k, 'mode': 'deny'} for k in os.environ if k.startswith(('AP_', 'PERSONA_'))]}},
            'permissions': {'deny': [f'{tool}(/{target})' for p in paths
                                    for target in [p, p + '/**']
                                    for tool in ('Read', 'Edit', 'Write', 'NotebookEdit')]}}
        write_json(invocation.parent / 'settings.json', settings)
        write_json(invocation.parent / 'mcp.json', {'mcpServers': {'platform': {
            'type': 'http', 'url': url, 'headersHelper': headers}}})
        args = ['claude', '--settings', str(invocation.parent / 'settings.json'),
                '--mcp-config', str(invocation.parent / 'mcp.json'), '--strict-mcp-config',
                '--append-system-prompt-file', str(prompt_file)]
        if resume_id:
            args += ['--resume', resume_id]
        return args
    filesystem = {':minimal': 'read', ':workspace_roots': {'.': 'write'},
                  **{p: 'deny' for p in paths}}
    config = {'developer_instructions': prompt, 'default_permissions': 'persona',
        'permissions.persona.filesystem': filesystem,
        'permissions.persona.network.enabled': False,
        'shell_environment_policy.ignore_default_excludes': False,
        'shell_environment_policy.filters': {'AP_*': 'exclude', 'PERSONA_*': 'exclude'},
        'mcp_servers.platform': {'url': url, 'http_headers_helper': headers,
                                 'default_tools_approval_mode': 'approve'}}
    for name in servers:
        if name == 'platform':
            raise ValueError('ambient MCP server named platform conflicts with persona mode')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('cannot safely override this ambient MCP server name')
        transport = servers[name] if isinstance(servers, dict) else 'stdio'
        # Plugin/built-in servers appear in the effective list without a
        # user-config transport. Supply a disabled, inert complete transport
        # so an override doesn't create an invalid empty server definition.
        config['mcp_servers.' + name] = ({'url': 'http://127.0.0.1:9/disabled', 'enabled': False}
            if transport == 'http' else {'command': '/usr/bin/false', 'enabled': False})
    for name, value in hooks.items():
        config['hooks.' + name] = value
    args = ['codex', '--dangerously-bypass-hook-trust']
    for key, value in config.items():
        args += ['-c', key + '=' + _toml(value)]
    if resume_id:
        args += ['resume', resume_id]
    return args


def verify_mcp(argv):
    overrides = []
    for i, arg in enumerate(argv):
        if arg == '-c' and argv[i+1].startswith('mcp_servers.'):
            overrides += ['-c', argv[i+1]]
    result = subprocess.run(['codex', *overrides, 'mcp', 'list', '--json'],
        capture_output=True, text=True, check=True, timeout=15, env=clean_environment())
    data = json.loads(result.stdout)
    if any(server['enabled'] and server['name'] != 'platform' for server in data):
        raise ValueError('ambient MCP authority remains enabled; persona launch refused')


def render_prompt(bootstrap):
    prompt = bootstrap['prompt'] + '\n\n' + bootstrap['runtime_instructions']
    prompt += '\n\n<retrieved_persona_memory>\n' + json.dumps(bootstrap['memories'], ensure_ascii=False) + '\n</retrieved_persona_memory>'
    for skill in bootstrap['skills']:
        import hashlib
        if hashlib.sha256(skill['text'].encode()).hexdigest() != skill['sha256']:
            raise ValueError('assigned skill checksum mismatch')
        prompt += '\n\n<assigned_skill name=' + json.dumps(skill['name']) + '>\n' + skill['text'] + '\n</assigned_skill>'
    if len(prompt.encode()) > 200000:
        raise ValueError('persona context exceeds the 200KB launch limit')
    return prompt
