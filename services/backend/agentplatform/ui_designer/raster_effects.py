"""Continuous conic paints and backdrop snapshots, isolated to renderer workers.

Ordinary SVG remains vector. Only sweep paints and bounded backdrop samples are
rasterized; the existing painter still owns glyphs, geometry, masks and effects.
"""
from __future__ import annotations

import base64
import copy
import json
import math
from typing import Mapping

from . import svg
from .errors import UiDesignerError, UD_RENDER_FAILED
from .schema import paints_of, MAX_BACKDROPS, MAX_BLUR, MAX_STEPPED

MAX_PIXELS = 10_000_000
MAX_WORK_PIXELS = 64_000_000
MAX_LIVE_BYTES = 128 << 20
MAX_BYTES = 8 << 20


def _fail(message: str):
    return UiDesignerError(UD_RENDER_FAILED, message)


def _page(resolved: dict, node_id: str | None):
    node, ancestors = svg.find_node(resolved, node_id)
    for page in resolved.get('pages', []):
        if node_id is None or page.get('id') == node_id:
            return page, node, ancestors
        pending = list(page.get('children', []))
        while pending:
            candidate = pending.pop()
            if candidate.get('id') == node_id:
                return page, node, ancestors
            pending.extend(candidate.get('children', []))
    raise _fail('effect target has no containing page')


def required(resolved: dict, node_id: str | None = None) -> bool:
    page, target, _ = _page(resolved, node_id)
    pending = list(page.get('children', [])) if target.get('type') == 'page' else [target]
    while pending:
        node = pending.pop()
        if node.get('visible') is False:
            continue
        if node.get('backdrop_blur', 0) or any(
                isinstance(paint, dict) and paint.get('gradient') == 'angular'
                for paint in paints_of(node.get('fill')) + paints_of(node.get('stroke'))):
            return True
        pending.extend(node.get('children', []))
    return False


class _BoundedDefs(list):
    def __init__(self, budget):
        super().__init__()
        self.bytes = 0
        self.budget = budget

    def append(self, value):
        added = len(value.encode())
        self.bytes += added
        self.budget.resident += added * 3  # definitions plus bounded snapshot/output strings
        if self.budget.resident > self.budget.live_limit:
            raise _fail('raster effect live data exceeds 128MiB')
        if self.bytes > MAX_BYTES:
            raise _fail('effect definitions exceed 8MiB')
        super().append(value)


class _Budget:
    def __init__(self, max_pixels, max_work_pixels, max_live_bytes):
        self.max_pixels = min(MAX_PIXELS, max_pixels)
        self.work_limit = min(MAX_WORK_PIXELS, max_work_pixels)
        self.live_limit = min(MAX_LIVE_BYTES, max_live_bytes)
        self.work = 0
        self.resident = 0
        self.raw_assets = set()
        self.decoded_assets = set()

    def allocate(self, width: int, height: int, *, copies: int = 3):
        pixels = width * height
        if width < 1 or height < 1 or max(width, height) > 8192 or pixels > self.max_pixels:
            raise _fail('raster effect surface exceeds 10MP or 8192px edge')
        if self.resident + pixels * 4 * copies > self.live_limit:
            raise _fail('raster effect live surfaces exceed 128MiB')
        self.work += pixels * copies
        if self.work > self.work_limit:
            raise _fail('raster effect aggregate work exceeds 64MP')


def new_budget():
    """One request-wide budget shared by render batches and visual comparisons."""
    return _Budget(MAX_PIXELS, MAX_WORK_PIXELS, MAX_LIVE_BYTES)


def _uri(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise _fail('raster effect PNG exceeds 8MiB')
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


def _rotation(node: dict) -> str:
    angle = node.get('rotate', 0)
    if not angle:
        return ''
    cx = node.get('x', 0) + node.get('w', 0) / 2
    cy = node.get('y', 0) + node.get('h', 0) / 2
    return f'rotate({svg._n(angle)} {svg._n(cx)} {svg._n(cy)})'


def _wrap(content: str, transforms: list[str]) -> str:
    return ('<g transform="' + ' '.join(transforms) + '">' + content + '</g>') if transforms else content


def _inverse(nodes: list[dict]) -> list[str]:
    result = []
    for node in reversed(nodes):
        if node.get('rotate'):
            unrotate = dict(node, rotate=-node['rotate'])
            result.append(_rotation(unrotate))
    return result


class _Assets(dict):
    def __init__(self, infos, data, budget):
        super().__init__(infos or {})
        self.data = data or {}
        self.budget = budget
        self.loaded = {}

    def get(self, sha, default=None):
        if sha in self.loaded:
            return self.loaded[sha]
        info = super().get(sha)
        if info is None:
            return default
        data = self.data.get(sha)
        if data is None or info.w is None or info.h is None or info.w < 1 or info.h < 1:
            raise _fail('raster effects require cached image bytes and validated dimensions')
        if sha not in self.budget.decoded_assets:
            self.budget.resident += info.w * info.h * 4
            self.budget.decoded_assets.add(sha)
        if self.budget.resident > self.budget.live_limit:
            raise _fail('raster effect cached images exceed 128MiB')
        self.loaded[sha] = svg.AssetInfo(_uri(data), info.w, info.h)
        return self.loaded[sha]


class _Painter(svg._Painter):
    """Reuse the vector painter with a real sweep shader replacing flat wedges."""
    def __init__(self, assets, budget, scale):
        super().__init__(assets, None)
        self.budget = budget
        self.scale = scale
        self.angular = {}
        self.defs = _BoundedDefs(budget)

    def body(self, node, out, white=False):
        if node.get('_effect_shell'):
            out.append('@@EFFECT_CONTENT@@')
        else:
            super().body(node, out, white)

    def gradient(self, gradient, box):
        if gradient.get('gradient') != 'angular':
            return super().gradient(gradient, box)
        key = json.dumps([gradient, box], sort_keys=True)
        if key in self.angular:
            return self.angular[key]
        self.stepped += 1
        if self.stepped > MAX_STEPPED:
            raise _fail('too many conic paints')
        import skia
        x, y, w, h = box
        w, h = max(w, 1), max(h, 1)
        pw, ph = max(1, math.ceil(w * self.scale)), max(1, math.ceil(h * self.scale))
        self.budget.allocate(pw, ph)
        colors, positions = [], []
        for position, value in gradient['stops']:
            color, alpha = svg._color(value)
            colors.append(skia.ColorSetARGB(round(alpha * 255), int(color[1:3], 16),
                                           int(color[3:5], 16), int(color[5:7], 16)))
            positions.append(float(position))
        shader = skia.GradientShader.MakeSweep(pw / 2, ph / 2, colors, positions,
            localMatrix=skia.Matrix.RotateDeg(float(gradient.get('angle', 0)) - 90, skia.Point(pw / 2, ph / 2)))
        if shader is None:
            raise _fail('conic shader could not be created')
        surface = skia.Surface(pw, ph)
        surface.getCanvas().drawPaint(skia.Paint(Shader=shader))
        data = bytes(surface.makeImageSnapshot().encodeToData())
        self.budget.resident += pw * ph * 4
        identifier = self.uid('conic')
        self.defs.append(f'<pattern id="{identifier}" patternUnits="userSpaceOnUse" '
            f'x="{svg._n(x)}" y="{svg._n(y)}" width="{svg._n(w)}" height="{svg._n(h)}">'
            f'<image x="0" y="0" width="{svg._n(w)}" height="{svg._n(h)}" '
            f'href="{_uri(data)}"/></pattern>')
        self.angular[key] = identifier
        return identifier


class _Composer:
    def __init__(self, painter: _Painter, budget: _Budget, scale: float):
        self.p = painter
        self.budget = budget
        self.scale = scale
        self.backdrops = 0
        self.selected = None
        self.target = None
        self.target_node = None
        self.target_ancestors = []
        self.backdrop_nodes = set()

    def fragment(self, node: dict, *, shell=False) -> str:
        n = copy.copy(node)
        n['children'] = []
        n.pop('backdrop_blur', None)
        if shell:
            n['_effect_shell'] = True
            n.pop('shadow', None)
        else:
            for prop in ('opacity', 'rotate', 'blur', 'blend'):
                n.pop(prop, None)
        out = []
        self.p.node(n, out)
        return ''.join(out)

    def backdrop(self, node: dict, context: str, ancestors: list[dict]) -> str:
        self.backdrops += 1
        radius = float(node['backdrop_blur'])
        if self.backdrops > MAX_BACKDROPS or radius > MAX_BLUR:
            raise _fail('backdrop effect count or radius exceeded')
        import skia
        import resvg_py
        nodes = ancestors + [node]
        x, y, w, h = node.get('x', 0), node.get('y', 0), node.get('w', 0), node.get('h', 0)
        if w <= 0 or h <= 0:
            return ''
        points = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
        for ancestor in reversed(nodes):
            angle = math.radians(ancestor.get('rotate', 0))
            cx = ancestor.get('x', 0) + ancestor.get('w', 0) / 2
            cy = ancestor.get('y', 0) + ancestor.get('h', 0) / 2
            points = [(cx + math.cos(angle) * (px - cx) - math.sin(angle) * (py - cy),
                       cy + math.sin(angle) * (px - cx) + math.cos(angle) * (py - cy)) for px, py in points]
        # Four sigma of transparent halo avoids a clipped filter kernel.
        pad = radius * 2 + 2 / self.scale
        x0 = math.floor((min(p[0] for p in points) - pad) * self.scale) / self.scale
        y0 = math.floor((min(p[1] for p in points) - pad) * self.scale) / self.scale
        pw = math.ceil((max(p[0] for p in points) + pad - x0) * self.scale)
        ph = math.ceil((max(p[1] for p in points) + pad - y0) * self.scale)
        self.budget.allocate(pw, ph, copies=4)
        bw, bh = pw / self.scale, ph / self.scale
        source = self.document(context, (x0, y0, bw, bh))
        png = resvg_py.svg_to_bytes(svg_string=source, width=pw, height=ph,
                                   skip_system_fonts=True, font_dirs=[])
        image = skia.Image.MakeFromEncoded(bytes(png))
        surface = skia.Surface(pw, ph)
        paint = skia.Paint(ImageFilter=skia.ImageFilters.Blur(radius * self.scale / 2,
                           radius * self.scale / 2, skia.TileMode.kDecal))
        surface.getCanvas().drawImage(image, 0, 0, paint=paint)
        data = bytes(surface.makeImageSnapshot().encodeToData())
        self.budget.resident += pw * ph * 4
        image_svg = (f'<image x="{svg._n(x0)}" y="{svg._n(y0)}" width="{svg._n(bw)}" '
                     f'height="{svg._n(bh)}" href="{_uri(data)}"/>')
        clip = self.p.uid('realbackdrop')
        if node.get('type') == 'path':
            outline = f'<path transform="translate({svg._n(x)} {svg._n(y)})" d="{svg._esc(node.get("d", ""))}"/>'
        else:
            outline = f'<path d="{svg._shape_d(node)}"/>'
        self.p.defs.append(f'<clipPath id="{clip}">{outline}</clipPath>')
        return f'<g clip-path="url(#{clip})">' + _wrap(image_svg, _inverse(nodes)) + '</g>'

    def document(self, content: str, box) -> str:
        x, y, w, h = box
        result = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg._n(w)}" height="{svg._n(h)}" '
                  f'viewBox="{svg._n(x)} {svg._n(y)} {svg._n(w)} {svg._n(h)}">'
                  f'<defs>{"".join(self.p.defs)}</defs>{content}</svg>')
        if len(result.encode()) > MAX_BYTES:
            raise _fail('effect SVG exceeds 8MiB')
        return result

    def node(self, node: dict, context: str, ancestors: list[dict]) -> str:
        if node.get('visible') is False:
            return ''
        if node.get('id') not in self.backdrop_nodes and not any(a.get('id') == node.get('id') for a in self.target_ancestors):
            out = []
            self.p.node(node, out)
            result = ''.join(out)
            if node.get('id') == self.target:
                self.selected = _wrap(result, [_rotation(a) for a in ancestors if a.get('rotate')])
            elif self.target_node is not None and any(a.get('id') == node.get('id') for a in self.target_ancestors):
                selected = []
                self.p.node(self.target_node, selected)
                self.selected = _wrap(''.join(selected), [_rotation(a) for a in self.target_ancestors if a.get('rotate')])
            return result
        ancestors_rot = [_rotation(a) for a in ancestors if a.get('rotate')]
        rotations = ancestors_rot + ([_rotation(node)] if node.get('rotate') else [])
        shell = self.fragment(node, shell=True)
        body = self.fragment(node)
        if node.get('backdrop_blur', 0) > 0:
            body = self.backdrop(node, context, ancestors) + body
        isolated = (node.get('opacity', 1) < 1 or node.get('blur', 0) > 0 or
                    node.get('blend', 'normal') != 'normal' or node.get('mask') or
                    node.get('backdrop_blur', 0) > 0)
        kids = node.get('children') or []
        child_prefix = ''
        child_suffix = ''
        if node.get('clip'):
            cid = self.p.uid('realclip')
            self.p.defs.append(f'<clipPath id="{cid}"><path d="{svg._shape_d(node)}"/></clipPath>')
            child_prefix += f'<g clip-path="url(#{cid})">'
            child_suffix = '</g>' + child_suffix
        if node.get('mask') in ('alpha', 'vector', 'luminance') and kids:
            mask_id = self.p.uid('realmask')
            mask_parts = []
            # Mask sources form their own backdrop root, separate from the
            # scene they mask. Vector masks use geometry only.
            if node['mask'] == 'vector':
                self.p.node(kids[0], mask_parts, white=True)
            else:
                mask_parts.append(self.node(kids[0], '', []))
            mode = 'alpha' if node['mask'] == 'alpha' else 'luminance'
            x, y, w, h = (node.get(k, 0) for k in ('x', 'y', 'w', 'h'))
            self.p.defs.append(f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="{svg._n(x)}" '
                f'y="{svg._n(y)}" width="{svg._n(w)}" height="{svg._n(h)}" mask-type="{mode}">'
                + ''.join(mask_parts) + '</mask>')
            child_prefix += f'<g mask="url(#{mask_id})">'
            child_suffix = '</g>' + child_suffix
            kids = kids[1:]
        previous = ''
        for child in kids:
            # A transformed container is not an isolation boundary. Its prior
            # paints remain over the same root backdrop; group opacity/masks do
            # isolate, so those group effects are applied exactly once later.
            partial = body + previous if isolated else body + child_prefix + previous + child_suffix
            backdrop_context = _wrap(partial, rotations)
            if not isolated:
                backdrop_context = context + backdrop_context
            previous += self.node(child, backdrop_context, ancestors + [node])
            if len(previous.encode()) > MAX_BYTES:
                raise _fail('effect fragment exceeds 8MiB')
            if self.selected is not None and self.target != node.get('id'):
                break
        result = shell.replace('@@EFFECT_CONTENT@@', body + child_prefix + previous + child_suffix)
        if node.get('id') == self.target:
            self.selected = _wrap(result, ancestors_rot)
        return result


def paint_effects(resolved: dict, node_id: str | None = None, *, scale: float = 1,
                  assets: Mapping[str, svg.AssetInfo] | None = None,
                  asset_bytes: Mapping[str, bytes] | None = None, annotate: bool = False,
                  max_pixels: int = MAX_PIXELS, max_work_pixels: int = MAX_WORK_PIXELS,
                  max_live_bytes: int = MAX_LIVE_BYTES, effect_budget=None) -> svg.Painted:
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not math.isfinite(scale) or not 0 < scale <= 3:
        raise _fail('effect scale must be positive and at most 3')
    page, target, ancestors = _page(resolved, node_id)
    x, y, w, h = svg._box(target)
    w, h = max(1, w), max(1, h)
    budget = effect_budget or _Budget(max_pixels, max_work_pixels, max_live_bytes)
    for sha, data in (asset_bytes or {}).items():
        if sha not in budget.raw_assets:
            budget.resident += len(data)
            budget.raw_assets.add(sha)
    budget.allocate(math.ceil(w * scale), math.ceil(h * scale), copies=1)
    infos = _Assets(assets, asset_bytes, budget)
    painter = _Painter(infos, budget, scale)
    composer = _Composer(painter, budget, scale)
    composer.target = target.get('id')
    composer.target_node = target
    composer.target_ancestors = ancestors

    def mark_backdrops(node):
        affected = node.get('visible') is not False and node.get('backdrop_blur', 0) > 0
        for child in node.get('children', []):
            affected = mark_backdrops(child) or affected
        if affected:
            composer.backdrop_nodes.add(node.get('id'))
        return affected

    for child in page.get('children', []):
        mark_backdrops(child)
    output = ''
    for node in page.get('children', []):
        output += composer.node(node, output, [])
        if len(output.encode()) > MAX_BYTES:
            raise _fail('effect page exceeds 8MiB')
        if target.get('type') != 'page' and composer.selected is not None:
            break
    if target.get('type') == 'page':
        content = output
    else:
        content = composer.selected or ''
    if annotate:
        labels = []
        painter.annotate(target.get('children') or [], labels)
        content += ''.join(labels)
    return svg.Painted(composer.document(content, (x, y, w, h)), x, y, w, h, target)
