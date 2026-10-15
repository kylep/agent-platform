"""Allowlist supported native JSONL records; never upload raw prompt snapshots.

Record identity is Claude uuid or Codex ordinal, never assistant message.id.
Reasoning, environment/world state, auth and hook attachments are omitted.
"""
from __future__ import annotations
import json
import math
import re

_SECRET = re.compile(r'\b(?:lps_|lpe_|lpr_|ap_)[A-Za-z0-9_-]{20,}\b')


def redact(value):
    if isinstance(value, str):
        value = ''.join('\ufffd' if c == '\x00' or 0xD800 <= ord(c) <= 0xDFFF else c for c in value)
        return _SECRET.sub('[redacted AP credential]', value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {redact(k): ('[redacted]' if re.search(r'(?:^|_)(?:api_key|access_token|refresh_token|token|password|secret|authorization|cookie)(?:$|_)', k, re.I)
                    else redact(v)) for k, v in value.items()}
    return value


def invalid_native_json(value):
    if isinstance(value, str):
        return '\x00' in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(invalid_native_json(k) or invalid_native_json(v) for k, v in value.items())
    if isinstance(value, list):
        return any(invalid_native_json(v) for v in value)
    return False


def text_of(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in content or []
        if isinstance(b, dict) and b.get('type') in ('text', 'input_text', 'output_text'))


def attachment_blocks(content):
    if not isinstance(content, list):
        return []
    return [('attachment', {'media_type': str((b.get('source') or {}).get('media_type') or 'application/octet-stream')[:128],
                             'name': str(b.get('title') or b.get('type'))[:128]})
            for b in content if isinstance(b, dict) and b.get('type') in
            ('image', 'input_image', 'image_url', 'document', 'input_file')]


def normalize(harness, record, turn):
    """Return native record id, updated turn, normalized (kind,payload) blocks."""
    kind, payload = record.get('type'), record.get('payload') or {}
    out = []
    if harness == 'claude':
        key = record.get('uuid')
        if kind in ('user', 'assistant'):
            message = record.get('message') or {}
            content = message.get('content', [])
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
            # Hook/tool attachments aren't human prompts and may include paths
            # or injected context. Do not mistake them for authenticated input.
            real_user = (kind == 'user' and not record.get('isMeta') and
                         not record.get('isCompactSummary') and
                         not record.get('toolUseResult') and
                         any(b.get('type') == 'text' for b in blocks))
            if real_user:
                turn = key or turn
                out.append(('turn_start', {}))
                out.append(('user_message', {'text': text_of(blocks)}))
            out.extend(attachment_blocks(blocks))
            for b in blocks:
                if b.get('type') == 'text' and kind == 'assistant':
                    out.append(('assistant_message', {'text': b.get('text', ''), 'final': False}))
                elif b.get('type') == 'tool_use':
                    out.append(('tool_call', {'name': b.get('name', ''), 'call_id': b.get('id', ''),
                                              'arguments': b.get('input', {})}))
                elif b.get('type') == 'tool_result':
                    out.extend(attachment_blocks(b.get('content')))
                    out.append(('tool_result', {'call_id': b.get('tool_use_id', ''),
                        'output': text_of(b.get('content', '')), 'is_error': bool(b.get('is_error'))}))
                elif b.get('type') not in ('text', 'thinking', 'redacted_thinking', 'image', 'document'):
                    out.append(('adapter_gap', {'reason': 'unsupported Claude content block'}))
            if not key:
                raise ValueError('Claude content record lacks uuid')
        elif kind == 'system' and record.get('subtype') == 'compact_boundary':
            out.append(('compaction', {}))
        elif kind not in ('system', 'progress', 'file-history-snapshot', 'queue-operation',
                          'summary', 'last-prompt', 'custom-title', 'agent-name', 'pr-link',
                          'prompt_snapshot', 'attachment', 'atis-latch', 'api-request-shape',
                          'api-request-blob', 'api-request', 'cost-state'):
            # Includes new record types: report, never upload their raw data.
            out.append(('adapter_gap', {'reason': 'unsupported Claude transcript record'}))
    else:
        key = str(record['ordinal']) if type(record.get('ordinal')) is int else None
        if kind == 'turn_context':
            turn = payload.get('turn_id') or turn
        elif kind == 'event_msg':
            event = payload.get('type')
            if event == 'task_started':
                turn = payload.get('turn_id') or turn
                out.append(('turn_start', {}))
            elif event == 'task_complete':
                turn = payload.get('turn_id') or turn
                out.append(('turn_end', {'outcome': 'complete'}))
            elif event == 'turn_aborted':
                out.append(('interrupt', {}))
            elif event == 'context_compacted':
                out.append(('compaction', {}))
            # user_message/agent_message duplicate response_item messages.
        elif kind == 'response_item':
            item_type = payload.get('type')
            if item_type == 'message':
                role = payload.get('role')
                if role in ('user', 'assistant'):
                    out.extend(attachment_blocks(payload.get('content')))
                    text = text_of(payload.get('content'))
                    # Developer/system config and compaction payloads are never
                    # user messages. Runtime facts remain in bootstrap only.
                    if role == 'user' and text.startswith(('<environment_context>', '<permissions instructions>')):
                        return key, turn, []
                    out.append(('user_message' if role == 'user' else 'assistant_message',
                                {'text': text} if role == 'user' else
                                {'text': text, 'final': payload.get('phase') == 'final_answer'}))
            elif item_type in ('function_call', 'custom_tool_call'):
                out.append(('tool_call', {'name': payload.get('name', ''),
                    'call_id': payload.get('call_id', ''),
                    'arguments': payload.get('arguments', payload.get('input', ''))}))
            elif item_type in ('function_call_output', 'custom_tool_call_output'):
                output = payload.get('output', '')
                out.extend(attachment_blocks(output))
                out.append(('tool_result', {'call_id': payload.get('call_id', ''),
                    'output': text_of(output) if isinstance(output, list) else output, 'is_error': False}))
            elif item_type not in ('reasoning', 'compaction', 'web_search_call'):
                out.append(('adapter_gap', {'reason': 'unsupported Codex response item'}))
        elif kind not in ('session_meta', 'world_state', 'token_usage_record', 'compacted'):
            out.append(('adapter_gap', {'reason': 'unsupported Codex transcript record'}))
        if out and key is None:
            raise ValueError('Codex content record lacks ordinal')
    # Huge output is explicit degraded fidelity, never silent loss.
    clean = []
    for event, value in out:
        if invalid_native_json(value):
            clean.append(('adapter_gap', {'reason': 'invalid native JSON value normalized'}))
        value = redact(value)
        required = {'tool_call': ('name', 'call_id'), 'tool_result': ('call_id',),
                    'user_message': ('text',), 'assistant_message': ('text',)}.get(event, ())
        if any(not isinstance(value.get(k), str) or
               (k != 'text' and (not value[k] or len(value[k]) > 1024)) for k in required):
            clean.append(('adapter_gap', {'reason': 'unsupported native event fields'}))
            continue
        if len(json.dumps(value).encode()) > 120000:
            clean.append(('adapter_gap', {'reason': 'native record exceeds 120KB; bounded preview retained'}))
            for name in ('text', 'output', 'arguments'):
                if name in value:
                    value[name] = str(value[name])[:24000]
        clean.append((event, value))
    return key, str(turn), clean
