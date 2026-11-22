"""Bounded pixel differences; no per-pixel Python loops or flood-fill storage."""
from __future__ import annotations
import io
import json
import math
import re
from PIL import Image, ImageChops
from .errors import UiDesignerError

MAX_PIXELS = 10_000_000
MAX_EDGE = 8192
MAX_REGIONS = 200
MAX_IDS = 200
TILE = 64


def union_box(boxes, scale):
    left = math.floor(min(b[0] for b in boxes) * scale)
    top = math.floor(min(b[1] for b in boxes) * scale)
    right = math.ceil(max(b[0] + b[2] for b in boxes) * scale)
    bottom = math.ceil(max(b[1] + b[3] for b in boxes) * scale)
    width, height = max(1, right - left), max(1, bottom - top)
    if width * height > MAX_PIXELS or max(width, height) > MAX_EDGE:
        raise UiDesignerError('UD-IMAGE-TOO-LARGE', 'visual diff union is at most 10 megapixels and 8192 pixels per edge')
    return [left / scale, top / scale, width / scale, height / scale], width, height


def viewport(source, box, width, height):
    """Change only the painter's trusted root viewport, keeping world positions."""
    tag = re.match(r'<svg\b[^>]*>', source)
    if not tag:
        raise UiDesignerError('UD-RENDER-FAILED', 'painter did not return an SVG root')
    root = tag.group()
    for name, value in [('width', str(width)), ('height', str(height)),
                        ('viewBox', ' '.join(f'{v:.8f}' for v in box))]:
        root = re.sub(rf'\b{name}="[^"]*"', f'{name}="{value}"', root)
    return root + source[tag.end():]


def node_boxes(root):
    found, pending = [], [root] if root else []
    while pending:
        node = pending.pop()
        if node.get('visible') is False:
            continue
        box = node.get('bounds') or [node.get(k, 0) for k in ('x','y','w','h')]
        if node.get('id') and box[2] > 0 and box[3] > 0:
            found.append((node['id'], box))
        pending.extend(reversed(node.get('children', [])))
    return found


def _mask(before, after, tolerance):
    before, after = before.convert('RGBA'), after.convert('RGBA')
    a, b = before.split(), after.split()
    # Premultiplication ignores meaningless hidden RGB while retaining alpha
    # differences, including black pixels fading against transparent black.
    delta = ImageChops.difference(a[3], b[3])
    for index in range(3):
        channel = ImageChops.difference(ImageChops.multiply(a[index], a[3]),
                                        ImageChops.multiply(b[index], b[3]))
        delta = ImageChops.lighter(delta, channel)
    return delta.point([0 if value <= tolerance else 255 for value in range(256)])


def _regions(mask):
    """Merge adjacent changed 64-pixel tiles into row runs, then vertical runs."""
    width, height = mask.size
    active, done = {}, []
    for y in range(0, height, TILE):
        runs, start = [], None
        for x in range(0, width, TILE):
            changed = mask.crop((x, y, min(width, x+TILE), min(height, y+TILE))).getbbox() is not None
            if changed and start is None:
                start = x
            if start is not None and (not changed or x + TILE >= width):
                end = min(width, x+TILE) if changed else x
                runs.append((start, end-start))
                start = None
        next_active = {}
        for x, w in runs:
            key = (x, w)
            previous = active.pop(key, None)
            if previous:
                previous[3] += min(TILE, height-y)
                next_active[key] = previous
            else:
                next_active[key] = [x, y, w, min(TILE, height-y)]
        done.extend(active.values())
        active = next_active
    done.extend(active.values())
    return done


def compare(before, after, box, scale, nodes, tolerance=0, max_bytes=8 << 20):
    if before.size != after.size or before.width * before.height > MAX_PIXELS or max(before.size) > MAX_EDGE:
        raise UiDesignerError('UD-IMAGE-TOO-LARGE', 'visual diff canvas is mismatched or above its limit')
    mask = _mask(before, after, tolerance)
    changed = mask.histogram()[255]
    pixels = before.width * before.height
    tiled = _regions(mask) if changed else []
    regions, all_ids = [], set()
    truncated = len(tiled) > MAX_REGIONS
    for x, y, w, h in tiled[:MAX_REGIONS]:
        world = [box[0]+x/scale, box[1]+y/scale, w/scale, h/scale]
        ids = sorted({identifier for identifier, bounds in nodes
                      if bounds[0] < world[0]+world[2] and bounds[0]+bounds[2] > world[0]
                      and bounds[1] < world[1]+world[3] and bounds[1]+bounds[3] > world[1]})
        truncated |= len(ids) > MAX_IDS
        all_ids.update(ids)
        regions.append(dict(zip(('x','y','w','h'), world), node_ids=ids[:MAX_IDS]))
    truncated |= len(all_ids) > MAX_IDS
    context = Image.new('RGBA', after.size, '#ffffff')
    context.alpha_composite(after.convert('RGBA'))
    base_stream = io.BytesIO(); context.save(base_stream, 'PNG')
    base_image = base_stream.getvalue()
    context = Image.blend(context, Image.new('RGBA', after.size, '#ffffff'), .75)
    overlay = Image.new('RGBA', after.size, '#e60073')
    overlay.putalpha(mask.point([0] + [210] * 255))
    context.alpha_composite(overlay)
    stream = io.BytesIO(); context.save(stream, 'PNG')
    data = stream.getvalue()
    if len(data) + len(base_image) > max_bytes:
        raise UiDesignerError('UD-IMAGE-TOO-LARGE', 'visual diff PNG exceeds the output byte cap')
    result = {'image': data, 'base_image': base_image, 'mime': 'image/png', 'width': before.width, 'height': before.height,
            'box': box, 'changed_pixels': changed, 'total_pixels': pixels, 'regions': regions,
            'node_ids': sorted(all_ids)[:MAX_IDS], 'truncated': bool(truncated),
            'warnings': ['Magenta marks changed pixels over a faint target image; regions aggregate 64-pixel tiles.']}

    def metadata_size():
        return len(json.dumps({k:v for k,v in result.items() if k not in ('image','base_image')},
                              separators=(',',':')).encode())
    if metadata_size() > 63 * 1024:
        result['truncated'] = True
        for region in result['regions']:
            region['node_ids'] = region['node_ids'][:10]
        while result['regions'] and metadata_size() > 63 * 1024:
            result['regions'].pop()
    return result
