"""Inert standalone exports from the existing resolved layout and painter.

HTML is editable absolute-positioned DOM for basic shapes/text. Complex scenes
use the existing outlined SVG painter; browser text metrics are approximate.
"""
from __future__ import annotations

import base64
import html
import io
import json

from . import svg
from .errors import UiDesignerError
from .schema import REF_RE, index_nodes, resolve_token

MIMES = {'svg': 'image/svg+xml', 'pdf': 'application/pdf', 'html': 'text/html'}


def bounded(data: bytes, cap: int) -> bytes:
    if len(data) > cap:
        raise UiDesignerError('UD-IMAGE-TOO-LARGE', f'export exceeds {cap} bytes',
                              detail={'bytes': len(data), 'limit': cap})
    return data


def standalone_svg(resolved: dict, node: str | None, assets: dict, cap: int, *, asset_bytes=None, effect_scale=1):
    # All hrefs are synthesized by the renderer from cached, verified PNGs.
    painted = svg.paint(resolved, node, assets=assets, asset_bytes=asset_bytes, effect_scale=effect_scale)
    data = bounded(painted.svg.encode(), cap)
    return data, painted


def raster_pdf(png: bytes, width: float, height: float, cap: int) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        flat = Image.new('RGB', image.size, '#ffffff')
        rgba = image.convert('RGBA')
        flat.paste(rgba, mask=rgba.getchannel('A'))
        stream = io.BytesIO()
        # Preserve the document's physical aspect and its CSS-pixel size.
        flat.save(stream, 'PDF', dpi=(image.width * 96 / width, image.height * 96 / height),
                  quality=95, title='UI Designer export')
    return bounded(stream.getvalue(), cap)


def _number(value) -> str:
    return f'{float(value):.5f}'.rstrip('0').rstrip('.') or '0'


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _token_vars(doc: dict, mode: str | None):
    declarations = []
    for group, entries in doc.get('tokens', {}).items():
        for name in entries:
            value = resolve_token(f'${group}.{name}', doc.get('tokens', {}), mode, doc.get('modes'))
            if group == 'type':
                for field, v in value.items():
                    if field == 'family':
                        encoded = json.dumps(v)
                    elif field == 'weight' or field == 'line':
                        encoded = _number(v)
                    else:
                        encoded = _number(v) + 'px'
                    declarations.append(f'--type-{name}-{field}:{encoded}')
            else:
                declarations.append(f'--{group}-{name}:{value if group == "color" else _number(value) + "px"}')
    return ';'.join(declarations)


def html_document(doc: dict, resolved: dict, node: str | None, assets: dict, cap: int, *, asset_bytes=None, effect_scale=1):
    root = svg.find_node(resolved, node)[0]
    pending, count = [root], 0
    while pending:
        current = pending.pop()
        count += 1
        if count > 200:
            raise UiDesignerError('UD-IMAGE-TOO-LARGE', 'HTML export is at most 200 expanded nodes')
        pending.extend(current.get('children', []))
    cap = min(cap, 1 << 20)
    painted = svg.paint(resolved, node, assets=assets, asset_bytes=asset_bytes, effect_scale=effect_scale)
    authored = index_nodes(doc)
    warnings = ['HTML uses fixed absolute layout; components are expanded and prototype links are omitted.']
    def complex_node(n):
        if n.get('bounds') or n.get('type') in ('path', 'line') or any(n.get(k) for k in ('mask','shadow','blur','backdrop_blur','blend')):
            return True
        if not isinstance(n.get('fill', ''), str) or isinstance(n.get('radius'), list):
            return True
        stroke = n.get('stroke') or {}
        if stroke and (stroke.get('align', 'inside') != 'inside' or any(k in stroke for k in ('dash','sides'))):
            return True
        text = n.get('text') or {}
        if isinstance(text,dict) and (text.get('align')=='justify' or any(key in text for key in ('vertical_align','text_case','decoration'))):
            return True
        if isinstance(text, dict) and any(len(line.get('runs', [])) > 1 for line in text.get('lines', [])):
            return True
        return any(complex_node(k) for k in n.get('children', []))
    def authored_node(n):
        ref = authored.get(n.get('id', '').split('/')[-1])
        return ref.node if ref is not None else {}
    def css_value(n, key, fallback):
        value = authored_node(n).get(key)
        match = REF_RE.fullmatch(value) if isinstance(value, str) else None
        if match:
            resolved_value = resolve_token(value, doc.get('tokens', {}), resolved.get('mode'), doc.get('modes'))
            token_css = (resolved_value if match.group(1) == 'color' else
                         _number(resolved_value) + 'px' if isinstance(resolved_value, (int, float)) else None)
            # A legacy override or component property may replace the authored
            # token. Never reintroduce its original value into the export.
            if token_css is not None and str(token_css).lower() == str(fallback).lower():
                return f'var(--{match.group(1)}-{match.group(2)})'
        return fallback
    def emit(n, ox, oy):
        if n.get('visible') is False:
            return ''
        kind = n.get('type')
        if kind == 'page':
            return ''.join(emit(k, ox, oy) for k in n.get('children', []))
        x, y, w, h = (n.get(k, 0) for k in ('x','y','w','h'))
        css = [f'left:{_number(x-ox)}px', f'top:{_number(y-oy)}px',
               f'width:{_number(w)}px', f'height:{_number(h)}px']
        if n.get('fill'):
            css.append('background:' + css_value(n, 'fill', n['fill']))
        if n.get('radius'):
            css.append('border-radius:' + css_value(n, 'radius', _number(n['radius']) + 'px'))
        if kind == 'ellipse': css.append('border-radius:50%')
        if n.get('opacity') is not None: css.append('opacity:' + _number(n['opacity']))
        if n.get('rotate'): css.append('transform:rotate(' + _number(n['rotate']) + 'deg)')
        if n.get('clip'): css.append('overflow:hidden')
        stroke = n.get('stroke') or {}
        if stroke:
            css.append('border:' + _number(stroke.get('width', 1)) + 'px solid ' + stroke['color'])
        children = ''.join(emit(k, x, y) for k in n.get('children', []))
        if kind == 'image':
            info = assets.get(n.get('asset'))
            if info:
                css.append('object-fit:' + {'fill':'fill','cover':'cover','contain':'contain'}.get(n.get('fit'), 'cover'))
                return f'<img class="ud-node" alt="" style="{_escape(";".join(css))}" src="{_escape(info.href)}">'
        if kind == 'text':
            lines = n.get('text', {}).get('lines', [])
            spec = authored_node(n).get('style')
            for line in lines:
                runs = line.get('runs') or []
                run = runs[0] if runs else {}
                face = run.get('face', 'Inter:400').split(':')
                family, weight = face[0], face[1] if len(face) > 1 else '400'
                size = run.get('size', 14)
                typography = {'family': json.dumps(family), 'weight': weight, 'size': _number(size)+'px'}
                if isinstance(spec, str) and spec.startswith('$type.'):
                    name = spec.split('.', 1)[1]
                    token_style = resolve_token(spec, doc.get('tokens', {}), resolved.get('mode'), doc.get('modes'))
                    for field in typography:
                        if field not in token_style:
                            continue
                        candidate = (json.dumps(token_style[field]) if field == 'family' else
                                     _number(token_style[field]) + ('px' if field == 'size' else ''))
                        if candidate == typography[field]:
                            typography[field] = f'var(--type-{name}-{field})'
                color = css_value(n, 'color', run.get('color', '#000000'))
                style = (f'left:{_number(line["x"]-x)}px;top:{_number(line["y"]-y-size*.8)}px;'
                         f'font-family:{typography["family"]},{"monospace" if family == "JetBrains Mono" else "sans-serif"};font-weight:{typography["weight"]};'
                         f'font-size:{typography["size"]};color:{color};white-space:pre;line-height:1')
                children += f'<span class="ud-node" style="{_escape(style)}">{_escape(line.get("text", ""))}</span>'
        return f'<div class="ud-node" data-node="{_escape(n.get("id", ""))}" style="{_escape(";".join(css))}">{children}</div>'
    if doc.get("fonts") or complex_node(root):
        # The painter has a closed vocabulary, outlined glyphs and trusted data
        # assets. It emits no scripts, foreignObject, navigation, or remote URLs.
        content = painted.svg
        warnings.append('Complex paints, paths, rich text, typography or effects use an outlined SVG fallback; edit the source document to change them.')
    else:
        content = emit(root, painted.x, painted.y)
        warnings.append('HTML text uses installed browser fonts; shaping, baseline and wrapping may differ from the server painter.')
    styles = (_token_vars(doc, resolved.get('mode')) + ';position:relative;overflow:hidden;'
              f'width:{_number(painted.w)}px;height:{_number(painted.h)}px')
    csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    data = ('<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            f'content="{_escape(csp)}"><title>{_escape(doc.get("title", "UI design"))}</title>'
            '<style>body{margin:0}.ud-node{position:absolute;box-sizing:border-box}</style></head>'
            f'<body><main style="{_escape(styles)}">{content}</main></body></html>').encode()
    return bounded(data, cap), painted, warnings
