"""Bounded, durable Figma import worker. A transaction advances one page.

The row lock serializes dispatchers; a crash rolls back only the current page.
Final document, placements and terminal job state commit together.
"""
from __future__ import annotations
import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import httpx
from PIL import Image
from sqlalchemy import select, or_
from agentplatform.db import UiDesignerImport, utcnow
from agentplatform import artifact_store
from . import store
from .figma_convert import convert

log = logging.getLogger(__name__)
MAX_STAGED = 16 << 20

class RetryLater(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, int(seconds))



def job_view(job):
    """Compact public progress; worker state and full checkpoints stay private."""
    import copy
    view = {k: getattr(job, k) for k in ("id", "slug", "file_key", "state", "pages", "error",
                                       "created_at", "updated_at")}
    report = copy.deepcopy({k:v for k,v in job.report.items() if k in
        ("name", "version", "pages_imported", "failed_pages", "pages", "references")})
    references = report.get("references", [])
    if len(references) > 100:
        report["references_omitted"] = len(references) - 100
        report["references"] = references[:100]
    # Counts always survive; detailed converter warnings are bounded for MCP.
    # The complete fidelity report remains in the durable import record.
    def size():
        return len(json.dumps({**view, "report":report}, default=str).encode())
    for page in report.get("pages", {}).values():
        items = page.get("items", [])
        if len(items) > 20:
            page["items_omitted"] = len(items) - 20
            page["items"] = items[:20]
    while size() > 30 * 1024:
        pages = [p for p in report.get("pages", {}).values() if p.get("items")]
        if not pages:
            # Pathological reports retain page progress and aggregate results.
            report.pop("pages", None)
            report["details_omitted"] = True
            break
        page = max(pages, key=lambda p: len(json.dumps(p["items"])))
        page["items"].pop()
        page["items_omitted"] = page.get("items_omitted", 0) + 1
    return {**view, "report":report}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def tree_file(result):
    f = next(f for f in result.get("files", []) if f["name"] == "tree.json")
    raw = base64.b64decode(f["b64"], validate=True)
    if len(raw) > 8 << 20:
        raise ValueError("page too large")
    return json.loads(raw)


def namespace(doc, prefix):
    """Remap structural references only; user text must never change."""
    import copy
    import hashlib
    doc = copy.deepcopy(doc)
    names = {n['id'] for n in walk(doc) if isinstance(n.get('id'), str)}
    names.update(doc.get('components', {}))
    mapping = {name: prefix + hashlib.sha256(name.encode()).hexdigest()[:32] for name in names}
    for node in walk(doc):
        if 'type' not in node and 'children' not in node:
            continue
        if node.get('id') in mapping:
            node['id'] = mapping[node['id']]
        if node.get('of') in mapping:
            node['of'] = mapping[node['of']]
        if 'overrides' in node:
            node['overrides'] = {
                (mapping.get(key.split('.', 1)[0], key.split('.', 1)[0]) + '.' + key.split('.', 1)[1]
                 if '.' in key else key): value for key, value in node['overrides'].items()}
    doc['components'] = {mapping[k]:v for k,v in doc.get('components', {}).items()}
    token_map = {}
    for group, tokens in doc.get('tokens', {}).items():
        token_map.update({f'${group}.{k}':f'${group}.{prefix}{k}' for k in tokens})
        doc['tokens'][group] = {prefix+k:v for k,v in tokens.items()}
    def paints(value, key=''):
        if key in ('title','meta','d') or ((key == 'text' or key.endswith('.text')) and isinstance(value, str)):
            return value
        if isinstance(value, str):
            return token_map.get(value, value)
        if isinstance(value, list):
            return [paints(v) for v in value]
        if isinstance(value, dict):
            return {k:paints(v,k) for k,v in value.items()}
        return value
    return paints(doc)


class FigmaImportWorker:
    def __init__(self, session_factory, settings, producer=None, fetch=None):
        self.sf, self.settings, self.producer = session_factory, settings, producer
        self.fetch = fetch or self._fetch
        self.caps = store.Caps(max_total_bytes=settings.ui_designer_max_total_bytes,
                               max_version_bytes_per_doc=settings.ui_designer_max_version_bytes_per_doc)

    async def _fetch(self, args):
        token = Path(self.settings.dispatcher_token_file).read_text().strip()
        async with httpx.AsyncClient(timeout=330) as client:
            r = await client.post(self.settings.api_internal_url + '/api/internal/ui-designer/figma-fetch',
                                  json=args, headers={'Authorization': 'Bearer ' + token})
            r.raise_for_status()
            return r.json()

    async def call(self, **args):
        args = {k:v for k,v in args.items() if v is not None}
        result = await self.fetch(args)
        if not result.get('ok'):
            raise ValueError('Figma fetch failed')
        meta = result.get('meta') or json.loads(result.get('output') or '{}')
        if not meta.get('ok'):
            if meta.get('error') == 'UD-IMPORT-RATE-LIMITED':
                raise RetryLater(meta.get('retry_after_seconds', 60))
            if meta.get('error') == 'UD-IMPORT-PAGE-TOO-BIG' and meta.get('frames'):
                return result, meta
            raise ValueError('Figma fetch failed')
        if len(result.get('files', [])) > 8:
            raise ValueError('too many files')
        return result, meta

    async def advance(self):
        from .import_notifications import notify_import
        async with self.sf() as pending_session:
            completed = (await pending_session.execute(select(UiDesignerImport).where(
                UiDesignerImport.state == 'done', UiDesignerImport.report['notification_pending'].as_boolean() == True).order_by(UiDesignerImport.updated_at.desc()).limit(100))).scalars().all()
            pending = [j.id for j in completed if j.report.get('notification_pending')]
        for ident in pending:
            try:
                await notify_import(self.sf, self.producer, ident)
            except Exception:
                log.warning('Figma import notification pending')
        async with self.sf() as s:
            retry_at = UiDesignerImport.report['next_attempt_at'].as_string()
            query = select(UiDesignerImport).where(
                UiDesignerImport.state.in_(['queued','running']),
                or_(retry_at.is_(None), retry_at <= datetime.now(timezone.utc).isoformat())
            ).order_by(UiDesignerImport.created_at).limit(1)
            if s.bind.dialect.name != 'sqlite':
                query = query.with_for_update(skip_locked=True)
            job = (await s.execute(query)).scalars().first()
            if job is None:
                return False
            ident = job.id
            try:
                async with s.begin_nested():
                    await self._advance(s, job)
                    if len(json.dumps(job.staged).encode()) > MAX_STAGED:
                        raise ValueError('staging budget exceeded')
                    await s.flush()
                    if (await store.usage(s))['total'] > self.caps.max_total_bytes:
                        raise ValueError('import storage budget exceeded')
                await s.commit()
            except RetryLater as e:
                await s.refresh(job)
                job.report = {**job.report, 'next_attempt_at':(datetime.now(timezone.utc)+timedelta(seconds=e.seconds)).isoformat()}
                await s.commit()
            except Exception:
                # Nested rollback preserves the import row's outer transaction lock.
                await s.refresh(job)
                # Never persist upstream exception bodies (URLs or credentials).
                pending_page = next((k for k,v in job.pages.items() if v['state'] == 'pending'), None)
                if pending_page:
                    job.staged = {k:v for k,v in job.staged.items() if k != pending_page}
                    await store.stage_page(s, ident, pending_page, staged={}, status='failed',
                                           counts={'error':'UD-IMPORT-PAGE-FAILED'})
                else:
                    job.state, job.error = 'failed', 'UD-IMPORT-FAILED: import could not be completed'
                    job.staged = {}
                job.updated_at = utcnow()
                await s.commit()
                log.warning('Figma import failed', exc_info=False)
            return True

    async def _advance(self, s, job):
        job.state, job.updated_at = 'running', utcnow()
        options = job.report.get('options', {})
        if not job.pages:
            _, meta = await self.call(mode='file_meta', file_key=job.file_key)
            pages = meta.get('pages', [])
            selected = options.get('pages')
            if selected:
                pages = [p for p in pages if p['id'] in selected]
            if not pages or len(pages) > 50:
                raise ValueError('no pages or too many pages')
            job.pages = {p['id']: {'state':'pending', 'name': p.get('name','Page')} for p in pages}
            job.report = {**job.report, 'name': meta.get('name','Figma import'), 'version':meta.get('version')}
            return
        for page_id, info in job.pages.items():
            if info['state'] != 'pending':
                continue
            progress = dict(job.staged.get(page_id) or {})
            if 'tree' not in progress:
                if progress.get('frames'):
                    remaining = [f for f in progress['frames'] if f['id'] not in progress['frame_done']]
                    if remaining:
                        frame = remaining[0]
                        fetched, submeta = await self.call(mode='tree_page', file_key=job.file_key,
                            node_ids=[frame['id']], version=job.report.get('version'))
                        if submeta.get('error'):
                            raise ValueError('frame too large')
                        subtree = tree_file(fetched).get('document', {})
                        progress['styles'] = {**progress.get('styles', {}), **tree_file(fetched).get('styles', {})}
                        progress['frame_children'] = progress['frame_children'] + [
                            n for n in walk(subtree) if n.get('id') == frame['id']]
                        progress['frame_done'] = progress['frame_done'] + [frame['id']]
                        await store._check_caps(s, self.caps, max(0,len(json.dumps(progress).encode())-len(json.dumps(job.staged.get(page_id,{})).encode())), what='import')
                        job.staged = {**job.staged, page_id:progress}
                        if len(json.dumps(job.staged).encode()) > MAX_STAGED:
                            raise ValueError('staging budget exceeded')
                        await store._check_caps(s, self.caps, 0, what='import')
                        return
                    dump = {'document':{'type':'CANVAS','id':page_id,'name':info['name'],
                                        'children':progress['frame_children']}, 'styles':progress.get('styles', {})}
                else:
                    result, tree_meta = await self.call(mode='tree_page', file_key=job.file_key,
                        **({'node_ids':options['node_ids']} if options.get('node_ids') else {'page_ids':[page_id]}),
                        version=job.report.get('version'))
                    if tree_meta.get('error') == 'UD-IMPORT-PAGE-TOO-BIG':
                        frames = tree_meta['frames']
                        if not frames or len(frames) > 64:
                            raise ValueError('too many oversized page frames')
                        job.staged = {**job.staged, page_id:{'frames':frames,'frame_done':[], 'frame_children':[]}}
                        return
                    dump = tree_file(result)
                root = dump.get('document', {})
                page = next((p for p in root.get('children',[]) if p.get('id') == page_id), root)
                progress = {'tree':page, 'styles':dump.get('styles', {}), 'image_assets':{}, 'references':[], 'reference_done':[]}
                await store._check_caps(s, self.caps, max(0,len(json.dumps(progress).encode())-len(json.dumps(job.staged.get(page_id,{})).encode())), what='import')
                job.staged = {**job.staged, page_id:progress}
                if len(json.dumps(job.staged).encode()) > MAX_STAGED:
                    raise ValueError('staging budget exceeded')
                return
            page = progress['tree']
            refs = sorted({n['imageRef'] for n in walk(page) if n.get('type') == 'IMAGE' and n.get('imageRef')})
            if len(refs) > 128:
                raise ValueError('too many images')
            assets = dict(progress['image_assets'])
            missing_refs = [ref for ref in refs if ref not in assets]
            for start in range(0, len(missing_refs), 8):
                fetched, _ = await self.call(mode='asset_batch', file_key=job.file_key, image_refs=missing_refs[start:start+8])
                for f in fetched.get('files', []):
                    ref = f.get('meta',{}).get('image_ref')
                    if ref not in missing_refs[start:start+8]:
                        continue
                    raw = base64.b64decode(f['b64'], validate=True)
                    if len(raw) > 8 << 20:
                        raise ValueError('image too large')
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.width * image.height > 50_000_000:
                            raise ValueError('image too large')
                        image.load()
                        out = io.BytesIO(); image.convert('RGBA').save(out, format='PNG')
                        assets[ref] = await store.add_asset(s, data=out.getvalue(), mime='image/png', width=image.width, height=image.height, caps=self.caps)
                # Missing upstream image refs are checkpointed too, then reported by conversion.
                for ref in missing_refs[start:start+8]:
                    assets.setdefault(ref, None)
                progress['image_assets'] = assets
                job.staged = {**job.staged, page_id:progress}
                return
            references = list(progress['references'])
            frame_ids = [n['id'] for n in page.get('children', []) if n.get('type') in
                         ('FRAME','COMPONENT','COMPONENT_SET','SECTION') and n.get('id')]
            if len(frame_ids) > 64:
                raise ValueError('too many reference frames')
            frame_ids = [n for n in frame_ids if n not in progress['reference_done']]
            for start in range(0, len(frame_ids), 8):
                fetched, _ = await self.call(mode='references', file_key=job.file_key,
                                            node_ids=frame_ids[start:start+8], version=job.report.get('version'))
                for f in fetched.get('files', []):
                    node_id = f.get('meta', {}).get('node_id')
                    if node_id not in frame_ids[start:start+8]:
                        continue
                    raw = base64.b64decode(f['b64'], validate=True)
                    artifact = await artifact_store.create(s, data=raw, owner=job.initiated_by,
                        name=f'Figma reference {node_id}', source='tool', tags=['figma-reference'],
                        meta={'import_id':job.id,'figma_node_id':node_id}, settings=self.settings,
                        commit=False)
                    references.append({'node_id':node_id,'artifact_id':artifact.id})
                returned = {r['node_id'] for r in references}
                progress['missing_references'] = progress.get('missing_references', []) + [
                    node for node in frame_ids[start:start+8] if node not in returned]
                progress['references'] = references
                progress['reference_done'] = progress['reference_done'] + frame_ids[start:start+8]
                job.staged = {**job.staged, page_id:progress}
                return
            assets = {k:v for k,v in assets.items() if v}
            doc, report = convert({'name':job.report['name'], 'document':page, 'restGroupTransforms':True, 'image_assets':assets, 'styles':progress.get('styles', {})})
            report['missing_references'] = progress.get('missing_references', [])
            report['missing_images'] = [ref for ref in refs if not progress['image_assets'].get(ref)]
            doc = namespace(doc, f'p{list(job.pages).index(page_id)}-')
            staged = {**job.staged, page_id:{'doc':doc,'assets':list(assets.values()),'report':report,'references':references}}
            if len(json.dumps(staged).encode()) > MAX_STAGED:
                raise ValueError('staging budget exceeded')
            await store._check_caps(s, self.caps, max(0,len(json.dumps(staged).encode())-len(json.dumps(job.staged).encode())), what='import')
            await store.stage_page(s, job.id, page_id, staged=staged[page_id])
            return
        if not job.staged:
            raise ValueError('no pages imported')
        doc = {'schema':1, 'title':options.get('title') or job.report['name'], 'pages':[], 'components':{}}
        assets = set()
        for staged in job.staged.values():
            doc['pages'].extend(staged['doc']['pages'])
            doc['components'].update(staged['doc'].get('components',{}))
            # Figma styles from different pages normally share names/values.
            for kind, tokens in staged['doc'].get('tokens',{}).items():
                doc.setdefault('tokens',{}).setdefault(kind,{}).update(tokens)
            assets.update(staged['assets'])
        staged_pages = job.staged
        job.staged = {}
        await s.flush()
        await store.create_document(s, slug=job.slug, title=doc['title'][:120], owner=job.initiated_by,
                                    doc=doc, imported_assets=assets, import_id=job.id, caps=self.caps, reason='Figma import')
        job.state = 'done'
        job.report = {**job.report, 'pages_imported':len(staged_pages), 'failed_pages':[k for k,v in job.pages.items() if v['state'] == 'failed'],
                      'pages': {k:v['report'] for k,v in staged_pages.items()}, 'notification_pending':True, 'references':[r for v in staged_pages.values() for r in v.get('references',[])]}
        job.staged = {}

    async def run_forever(self):
        while True:
            try:
                worked = await self.advance()
            except Exception:
                log.warning('Figma import worker unavailable', exc_info=True)
                worked = False
            await asyncio.sleep(1 if worked else 5)
