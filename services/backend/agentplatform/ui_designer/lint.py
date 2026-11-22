"""Advisory document findings. Never a compliance verdict or an automatic edit."""
from __future__ import annotations
import json
from collections import Counter
from .schema import (COLOR_RE, index_nodes, iter_token_refs, join_path, lint as schema_lint,
                     resolve_token)
from .layout import layout_document, contrast_ratio, _rgba, _over
from .errors import UiDesignerError

RULES_VERSION = 'lint-1'
MAX_FINDINGS = 2000
MAX_BYTES = 2 << 20


def _short(value):
    return str(value).encode()[:500].decode(errors='ignore')


def _background(fill, background):
    """Only solid paints have a known contrast background; never average gradients."""
    if fill is None:
        return background
    for paint in fill if isinstance(fill, list) else [fill]:
        opacity = 1
        if isinstance(paint, dict):
            if 'color' not in paint:
                background = None
                continue
            opacity = paint.get('opacity', 1)
            paint = paint['color']
        rgba = _rgba(paint)
        if rgba is None:
            background = None
        else:
            background = _over((*rgba[:3], rgba[3]*opacity), background)
    return background


def findings(doc, mode=None, node=None):
    modes = doc.get('modes') or []
    if mode is not None and modes and mode not in modes:
        raise UiDesignerError('UD-INVALID', 'lint mode must be defined by the document')
    mode = (mode if mode is not None else modes[0]) if modes else None
    tree = layout_document(doc, mode)
    from .svg import find_node
    selected = find_node(tree,node)[0] if node is not None else None
    allowed = None
    if selected is not None:
        allowed, pending = set(), [selected]
        while pending:
            current = pending.pop(); allowed.add(current['id'])
            pending.extend(current.get('children', []))
    output, seen, counts = [], set(), Counter()
    total = 0
    def add(code, severity, identifier, message, suggestion, approximate=False):
        nonlocal total
        if allowed is not None and identifier not in allowed:
            return
        entry = {'code':code, 'severity':severity, 'node_id':identifier, 'mode':mode,
                 'message':_short(message), 'suggestion':_short(suggestion), 'approximate':bool(approximate)}
        key = (code, identifier, entry['message'])
        if key in seen: return
        seen.add(key); total += 1; counts[severity] += 1
        if len(output) < MAX_FINDINGS:
            output.append(entry)
    paths = {}
    def authored_walk(n, path):
        if n.get('id'): paths[path] = n['id']
        for i,k in enumerate(n.get('children', [])): authored_walk(k,join_path(path,'children',i))
    for i,p in enumerate(doc.get('pages', [])):
        for j,n in enumerate(p.get('children', [])): authored_walk(n,join_path('$.pages',i,'children',j))
    for key,n in doc.get('components', {}).items():
        paths[join_path('$.components',key)] = key
        authored_walk(n,join_path('$.components',key))
    set_components = {variant['of'] for definition in doc.get('component_sets',{}).values()
                      for variant in definition.get('variants',[])}
    set_components.update(component for definition in doc.get('component_sets',{}).values()
                          for spec in definition.get('properties',{}).values()
                          if spec.get('type') == 'instance-swap' for component in spec.get('allowed',[]))
    suggestions = {'XY-IN-LAYOUT':'Remove x/y or use absolute positioning.',
                   'FILL-IN-HUG':'Use fixed sizing, or give the parent a fixed size.',
                   'TOKEN-MODE':'Define the token in every document mode.',
                   'COMPONENT-UNUSED':'Consider removing this unused definition.',
                   'TEXT-OVERFLOW':'Increase the text box or shorten the text.'}
    for warning in schema_lint(doc) + tree.get('warnings', []):
        suffix = warning['code'].removeprefix('UD-WARN-')
        if suffix == 'CONTRAST': continue
        identifier = warning.get('id') or paths.get(warning.get('path'))
        if suffix == 'COMPONENT-UNUSED' and identifier in set_components:
            continue
        add('UD-LINT-'+suffix, 'warning' if suffix == 'TEXT-OVERFLOW' else 'info', identifier,
            warning['message'], suggestions.get(suffix,'Review this layout warning.'), suffix != 'TEXT-OVERFLOW')
    def bounds_of(n):
        return n.get('bounds') or [n.get(k,0) for k in ('x','y','w','h')]
    def union(a,b):
        if a is None: return b
        if b is None: return a
        x,y=min(a[0],b[0]),min(a[1],b[1])
        return [x,y,max(a[0]+a[2],b[0]+b[2])-x,max(a[1]+a[3],b[1]+b[3])-y]
    def overlaps(a,b):
        return bool(a and b and a[0] < b[0]+b[2] and a[0]+a[2] > b[0] and
                    a[1] < b[1]+b[3] and a[1]+a[3] > b[1])
    paint_bounds = {}
    def collect_bounds(n):
        if n.get('visible') is False or n.get('opacity',1) == 0: return None
        box=bounds_of(n)
        for child in n.get('children',[]): box=union(box,collect_bounds(child))
        # Effects can paint beyond geometry; a generous conservative envelope
        # prevents a numeric contrast claim over an earlier shadow or blur.
        if any(n.get(k) for k in ('shadow','blur','backdrop_blur')):
            box=[box[0]-256,box[1]-256,box[2]+512,box[3]+512]
        paint_bounds[n['id']]=box
        return box
    for page in tree.get('pages',[]):
        for n in page.get('children',[]): collect_bounds(n)
    def walk(n, background=None, uncertain=False, clip=None):
        if n.get('visible') is False: return
        identifier = n.get('id')
        background = _background(n.get('fill'),background)
        # Group opacity/blending/masks need full scene compositing. Decline a
        # numeric claim instead of treating opacity as a foreground-only alpha.
        uncertain = uncertain or (n.get('type') != 'text' and n.get('opacity',1) < 1) or bool(n.get('blend') or n.get('mask') or n.get('backdrop_blur'))
        bounds = n.get('bounds') or [n.get(k,0) for k in ('x','y','w','h')]
        if clip is not None and bounds[2] > 0 and bounds[3] > 0 and (bounds[0] < clip[0]-.5 or bounds[1] < clip[1]-.5 or
                bounds[0]+bounds[2] > clip[0]+clip[2]+.5 or bounds[1]+bounds[3] > clip[1]+clip[3]+.5):
            add('UD-LINT-CLIPPED','warning',identifier,'Node extends outside an ancestor clipping frame.',
                'Check whether the crop is intentional; resize or move the node if needed.',True)
        if n.get('type') == 'text':
            runs = [run for line in n.get('text',{}).get('lines',[]) for run in line.get('runs',[])]
            if runs and (background is None or uncertain):
                add('UD-LINT-CONTRAST-UNKNOWN','info',identifier,'Text contrast cannot be determined from a known opaque solid background.',
                    'Review a snapshot against the actual background; images, gradients, opacity and overlapping content need visual review.',True)
            elif runs:
                failures = []
                for run in runs:
                    rgba = _rgba(run.get('color'))
                    fg = _over((*rgba[:3],rgba[3]*n.get('opacity',1)),background) if rgba else None
                    if fg is None: continue
                    try: weight = int(run.get('face','').rsplit(':',1)[-1])
                    except ValueError: weight = 400
                    size = run.get('size',14)
                    minimum = 3 if size >= 24 or (size >= 56/3 and weight >= 700) else 4.5
                    ratio = contrast_ratio(fg,background)
                    if ratio < minimum: failures.append((ratio,minimum))
                if failures:
                    ratio, minimum = min(failures)
                    add('UD-LINT-CONTRAST','warning',identifier,f'Text contrast {ratio:.2f}:1 is below {minimum:g}:1 for its size and weight.',
                        'Increase foreground/background contrast; confirm text readability in context.',True)
        child_clip = bounds if n.get('clip') else clip
        if clip is not None and n.get('clip'):
            left,top=max(clip[0],bounds[0]),max(clip[1],bounds[1])
            child_clip=[left,top,max(0,min(clip[0]+clip[2],bounds[0]+bounds[2])-left),max(0,min(clip[1]+clip[3],bounds[1]+bounds[3])-top)]
        earlier = None
        for child in n.get('children',[]):
            walk(child,background,uncertain or overlaps(earlier,paint_bounds.get(child['id'])),child_clip)
            earlier=union(earlier,paint_bounds.get(child['id']))
    for page in tree.get('pages',[]):
        earlier=None
        for n in page.get('children',[]):
            walk(n,uncertain=overlaps(earlier,paint_bounds.get(n['id'])))
            earlier=union(earlier,paint_bounds.get(n['id']))
    refs = {ref for _,ref in iter_token_refs(doc)}
    colors = {}
    for name in doc.get('tokens',{}).get('color',{}):
        value = resolve_token('$color.'+name,doc.get('tokens',{}),mode,modes)
        color = _rgba(value)
        if color: colors.setdefault(color,[]).append(name)
    def raw_colors(value):
        if isinstance(value,str) and COLOR_RE.fullmatch(value): yield value
        elif isinstance(value,dict):
            for child in value.values(): yield from raw_colors(child)
        elif isinstance(value,list):
            for child in value: yield from raw_colors(child)
    for identifier,ref in index_nodes(doc).items():
        n = ref.node
        if ref.kind == 'page': continue
        layout=n.get('layout') or {}
        for key in ('gap','pad'):
            values = layout.get(key,[])
            values = values if isinstance(values,list) else [values]
            for value in values:
                if isinstance(value,str) and value.startswith('$'):
                    value=resolve_token(value,doc.get('tokens',{}),mode,modes)
                if isinstance(value,(int,float)) and not isinstance(value,bool) and abs(value/4-round(value/4)) > .001:
                    add('UD-LINT-SPACING-GRID','info',identifier,f'{key} spacing {value:g}px is outside a 4px grid.',
                        'Consider a shared spacing token on the design spacing scale; intentional exceptions are valid.',True)
        paints = [n.get(field) for field in ('fill','color','stroke')]
        if isinstance(n.get('text'),list):
            paints.extend(run.get('color') for run in n['text'] if isinstance(run,dict))
        for paint in paints:
            for value in set(raw_colors(paint)):
                matches=colors.get(_rgba(value))
                if matches:
                    add('UD-LINT-COLOR-TOKEN','info',identifier,f'Literal {value} matches a color token.',
                        'Consider using $color.'+sorted(matches)[0]+' instead of the literal.',False)
    if node is None:
        for group,entries in doc.get('tokens',{}).items():
            for name in entries:
                token=f'${group}.{name}'
                if token not in refs:
                    add('UD-LINT-TOKEN-UNUSED','info',None,f'{token} is not referenced by the document.',
                        'Consider removing it, or retain it deliberately as part of the design system.',False)
    output.sort(key=lambda f:(f['mode'] or '',f['node_id'] or '',f['code'],f['message']))
    result={'ok':True,'rules_version':RULES_VERSION,'findings':output,'total':total,
            'by_severity':dict(counts),'truncated':total>len(output)}
    while len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()) > MAX_BYTES-1024:
        result['findings'].pop(); result['truncated']=True
    return result
