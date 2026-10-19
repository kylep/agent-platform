import concurrent.futures
import json
from pathlib import Path
import sys
import tomllib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ap_persona import capture, harness
from ap_persona.adapters import normalize, redact
from ap_persona.cli import verify_bootstrap
from ap_persona.storage import read_json, secure_dir, write_json
from ap_persona.transport import secure_url


def claude_record(id, type='assistant', content=None):
    return {'uuid': id, 'sessionId': 'native', 'type': type,
            'message': {'id': 'same-assistant-id', 'content': content or [{'type':'text', 'text': id}]}}


def test_record_ids_are_not_message_ids_and_credential_capture(tmp_path):
    path = tmp_path / 'native.jsonl'
    records = [claude_record('user', 'user'), claude_record('a'), claude_record('b'),
               claude_record('tool', content=[{'type':'tool_use', 'id':'call', 'name':'memory',
                   'input': {'token':'fake-secret', 'content':'lps_'+'x'*32}}])]
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    state = tmp_path / 'state'
    capture.capture(state, 'claude', 'native', path)
    capture.capture(state, 'claude', 'native', path)
    events = capture.pending(state)
    assert [e['kind'] for e in events] == ['turn_start', 'user_message', 'assistant_message', 'assistant_message', 'tool_call']
    assert len({e['event_id'] for e in events}) == 5
    assert 'fake-secret' not in json.dumps(events) and 'x'*32 not in json.dumps(events)
    capture.acknowledge(state, 3)
    assert [e['sequence'] for e in capture.pending(state)] == [4,5]
    with pytest.raises(ValueError):
        capture.acknowledge(state, 999)


def test_partial_unicode_truncation_and_concurrent_hooks(tmp_path):
    path = tmp_path / 'native.jsonl'
    data = json.dumps(claude_record('one', content=[{'type':'text','text':'🤖 café'}]), ensure_ascii=False).encode()+b'\n'
    path.write_bytes(data[:-3])
    state = tmp_path / 'state'
    capture.capture(state, 'claude', 'native', path)
    assert capture.pending(state) == []
    path.write_bytes(data)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: capture.capture(state, 'claude', 'native', path), range(2)))
    assert len(capture.pending(state)) == 1
    path.write_text(json.dumps(claude_record('x'))+'\n')
    capture.capture(state, 'claude', 'native', path)
    assert any(e['kind'] == 'adapter_gap' for e in capture.pending(state))


def test_claude_final_is_observed_only_at_stop(tmp_path):
    path = tmp_path / 'native.jsonl'
    path.write_text(json.dumps(claude_record('a'))+'\n')
    state = tmp_path / 'state'
    for _ in range(2):
        capture.capture(state, 'claude', 'native', path,
            {'hook_event_name':'Stop', 'last_assistant_message':'Final answer'})
    assert [e['payload']['final'] for e in capture.pending(state) if e['kind']=='assistant_message'] == [False, True]
    assert len([e for e in capture.pending(state) if e['kind']=='turn_end']) == 1


def test_codex_native_steering_and_private_record_filtering():
    _, turn, blocks = normalize('codex', {'ordinal':1, 'type':'turn_context', 'payload':{'turn_id':'turn1'}}, 'old')
    assert turn == 'turn1' and not blocks
    for private in ['session_meta', 'world_state', 'token_usage_record']:
        assert normalize('codex', {'type':private, 'payload':{'secret':'private'}}, turn)[2] == []
    key, next_turn, blocks = normalize('codex', {'ordinal':3, 'type':'response_item', 'payload':{
        'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'done'}]}}, turn)
    assert key == '3' and next_turn == 'turn1' and blocks == [('assistant_message', {'text':'done','final':True})]
    with pytest.raises(ValueError):
        normalize('codex', {'type':'response_item', 'payload':{'type':'function_call'}}, turn)
    assert normalize('codex', {'ordinal':4,'type':'new-unknown','payload':{}}, turn)[2][0][0] == 'adapter_gap'


def test_state_symlinks_and_binding_paths(tmp_path):
    state = secure_dir(tmp_path / 'state')
    write_json(state/'a.json', {'ok':True})
    assert read_json(state/'a.json')['ok']
    (state/'evil.json').symlink_to(state/'a.json')
    with pytest.raises(OSError):
        read_json(state/'evil.json')
    with pytest.raises(ValueError):
        write_json(state/'evil.json', {})
    root = tmp_path/'.claude/projects/work'
    root.mkdir(parents=True)
    transcript = root/'native.jsonl'
    transcript.write_text('{}\n')
    assert capture.transcript_path('claude', transcript, 'native', tmp_path) == transcript
    with pytest.raises(ValueError):
        capture.transcript_path('claude', transcript, 'foreign', tmp_path)


@pytest.mark.parametrize('url', ['http://pai:8090', 'https://user:secret@pai', 'https://pai/path', 'https://pai?token=x'])
def test_transport_refuses_bearer_leaks(url):
    with pytest.raises(ValueError):
        secure_url(url)


def test_transport_allows_tls_and_explicit_local_tunnel():
    assert secure_url('https://pai.example/') == 'https://pai.example'
    assert secure_url('http://127.0.0.1:8090') == 'http://127.0.0.1:8090'


def test_invocation_only_flags_and_isolated_personas(tmp_path, monkeypatch):
    home = tmp_path/'home'
    root = secure_dir(tmp_path/'state')
    cwd = secure_dir(tmp_path/'work')
    monkeypatch.setenv('AP_MCP_TOKEN', 'must-not-inherit')
    assert 'AP_MCP_TOKEN' not in harness.clean_environment()
    assert harness.clean_environment({'NORMAL':'keep', 'PERSONA_TOKEN':'no'}) == {'NORMAL':'keep'}
    paths = harness.deny_paths(root, cwd, home=home)
    assert str(cwd/'.claude') in paths and str(cwd/'.codex') in paths
    ordinary_files = set(cwd.rglob('*'))
    for persona in ('alice','bob'):
        inv = root/persona/'invocation.json'
        write_json(inv, {'origin':'https://ap.example', 'agent':persona})
        package = {'prompt':persona, 'runtime_instructions':'facts', 'memories':[], 'skills':[]}
        args = harness.build('claude', inv, package, paths, cwd)
        assert '--strict-mcp-config' in args
        settings = read_json(inv.parent/'settings.json')
        assert f'Edit(/{cwd}/.claude/**)' in settings['permissions']['deny']
        assert settings['sandbox']['allowUnsandboxedCommands'] is False
        assert settings['sandbox']['filesystem']['disabled'] is False
        assert settings['sandbox']['credentials']['envVars'] == [{'name':'AP_MCP_TOKEN','mode':'deny'}]
        assert all(str(inv) in h['hooks'][0]['command'] for h in settings['hooks']['SessionStart'])
        codex = harness.build('codex', inv, package, paths, cwd, servers=['ap','apps'])
        config = {}
        for i, a in enumerate(codex):
            if a == '-c':
                parsed = tomllib.loads(codex[i+1])
                assert parsed
                config.update(parsed)
        assert config['permissions']['persona']['network']['enabled'] is False
        assert '--dangerously-bypass-hook-trust' in codex
        assert any(a.startswith('mcp_servers.ap=') and 'enabled' in a and 'false' in a for a in codex)
    assert set(cwd.rglob('*')) == ordinary_files
    assert not home.exists()


def test_repo_hook_trust_fails_closed(tmp_path):
    config = tmp_path/'.codex/config.toml'
    config.parent.mkdir()
    config.write_text('[hooks]\n')
    with pytest.raises(ValueError):
        harness.check_project('codex', tmp_path, home=tmp_path/'other-home')


def test_writer_takeover_fences_old_capture_and_keeps_pending_history(tmp_path):
    path = tmp_path/'native.jsonl'
    path.write_text(json.dumps(claude_record('first'))+'\n')
    state = tmp_path/'state'
    capture.register_writer(state, 1)
    capture.capture(state, 'claude', 'native', path, writer_epoch=1)
    capture.register_writer(state, 2)
    path.write_text(path.read_text()+json.dumps(claude_record('second'))+'\n')
    with pytest.raises(ValueError, match='superseded'):
        capture.capture(state, 'claude', 'native', path, writer_epoch=1)
    assert len(capture.pending(state)) == 1
    capture.capture(state, 'claude', 'native', path, writer_epoch=2)
    assert [e['sequence'] for e in capture.pending(state)] == [1,2]


def test_private_native_records_are_omitted_and_unknown_shapes_degrade():
    for type in ('prompt_snapshot','attachment','api-request-blob','api-request','cost-state'):
        assert normalize('claude', {'type':type,'payload':'private'}, 't')[2] == []
    assert normalize('claude', {'type':'future-unrecognized'}, 't')[2][0][0] == 'adapter_gap'
    assert redact({'input_tokens':4, 'access_token':'secret'}) == {'input_tokens':4,'access_token':'[redacted]'}


def test_foreign_native_source_rolls_back_capture(tmp_path):
    path = tmp_path/'native.jsonl'
    path.write_text(json.dumps({**claude_record('foreign'), 'sessionId':'other'})+'\n')
    with pytest.raises(ValueError, match='foreign native'):
        capture.capture(tmp_path/'state', 'claude','native',path)
    assert capture.pending(tmp_path/'state') == []


def test_attachments_keep_metadata_without_binary_or_file_access():
    record = claude_record('image', 'user', content=[{'type':'image','source':{
        'type':'base64','media_type':'image/png','data':'DO_NOT_UPLOAD_BINARY'}}])
    blocks = normalize('claude',record,'t')[2]
    assert ('attachment', {'media_type':'image/png','name':'image'}) in blocks
    assert 'DO_NOT_UPLOAD_BINARY' not in json.dumps(blocks)
    blocks = normalize('codex', {'ordinal':3,'type':'response_item','payload':{
        'type':'message','role':'user','content':[{'type':'input_image','image_url':'file:///private/file.png'}]}}, 't')[2]
    assert 'file:///private/file.png' not in json.dumps(blocks)
    assert blocks[0][0] == 'attachment'


def test_malformed_known_native_content_becomes_gap_not_unuploadable_event():
    _, _, events = normalize('codex', {'ordinal':1, 'type':'response_item',
        'payload':{'type':'function_call','name':'memory'}}, 'turn')
    assert events == [('adapter_gap', {'reason':'unsupported native event fields'})]
    _, _, events = normalize('claude', claude_record('bad', content=[{'type':'text','text':None}]), 'turn')
    assert events == [('adapter_gap', {'reason':'unsupported native event fields'})]


def test_invalid_json_text_is_explicitly_recovered_and_literal_escapes_survive():
    _,_,events = normalize('claude', claude_record('nul', content=[{'type':'text','text':'a\x00b'}]),'turn')
    assert events[0][0] == 'adapter_gap'
    assert events[1] == ('assistant_message',{'text':'a\ufffdb','final':False})
    _,_,events = normalize('claude',claude_record('literal',content=[{'type':'text','text':r'\u0000'}]),'turn')
    assert events == [('assistant_message',{'text':r'\u0000','final':False})]


@pytest.mark.parametrize('reported,accepted', [
    ('codex-cli 0.162.1', True),
    ('codex-cli 0.163.0', True),
    ('codex-cli 0.164.0', False),
    ('unrecognized version', False),
])
def test_harness_version_gate_reports_verified_versions(reported, accepted, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(harness.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(stdout=reported))
    if accepted:
        assert harness.version('codex') == reported.split()[-1]
    else:
        with pytest.raises(ValueError, match='verified versions: 0.162.1, 0.163.0'):
            harness.version('codex')
