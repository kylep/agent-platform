"""Pinned same-database library copies. Pure planning; no I/O or moving heads."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from .errors import UiDesignerError, invalid, issue
from .schema import (ID_RE, REF_RE, TOKEN_GROUPS, TOKEN_NAME, image_assets, index_nodes,
                     normalize_document, slugify, validate_document)

HEX = re.compile(r'^[0-9a-f]{32}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
TOKEN = re.compile(rf'^({"|".join(TOKEN_GROUPS)})\.({TOKEN_NAME})$')
MAX_LIBRARIES = 8
MAX_ANCESTRY = 32
_LITERAL = {'id','title','meta','d','asset','of','image','link_to','variant','props','axes','properties','when','bindings','libraries'}


def _fail(message, path='$.libraries'):
    raise invalid([issue('UD-VALUE',path,message)])


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _name(namespace, kind, source):
    digest=hashlib.sha256((kind+'\0'+source).encode()).hexdigest()[:12]
    room=48-len(namespace)-len(digest)-2
    return namespace+'-'+slugify(source,'item')[:room].rstrip('-')+'-'+digest


def _values(value, mapping=None, refs=None, key=None):
    """Only real property values, never literal text/title/meta/path payloads."""
    if key in _LITERAL or (key=='text' and isinstance(value,str)):
        return copy.deepcopy(value)
    if isinstance(value,str):
        match=REF_RE.fullmatch(value)
        if match:
            source=match.group(1)+'.'+match.group(2)
            if refs is not None: refs.add(source)
            if mapping is not None and source in mapping: return '$'+match.group(1)+'.'+mapping[source]
        return value
    if isinstance(value,list): return [_values(v,mapping,refs) for v in value]
    if isinstance(value,dict): return {k:_values(v,mapping,refs,k) for k,v in value.items()}
    return copy.deepcopy(value)


def _nodes(root):
    yield root
    for child in root.get('children',[]): yield from _nodes(child)


def _closure(source, selection):
    components, sets, tokens=set(),set(),set(selection['tokens'])
    pending=list(selection['components'])+list(selection['component_sets'])
    while pending:
        key=pending.pop()
        if key in components or key in sets: continue
        if key in source.get('component_sets',{}):
            sets.add(key)
            definition=source['component_sets'][key]
            pending.extend(v['of'] for v in definition['variants'])
            for spec in definition.get('properties',{}).values():
                if spec['type']=='instance-swap': pending.extend(spec['allowed'])
        elif key in source.get('components',{}):
            components.add(key)
            for node in _nodes(source['components'][key]):
                if node.get('link_to') or any(k.rsplit('.',1)[-1]=='link_to' for k in node.get('overrides',{})):
                    _fail('A copied definition links to a foreign page frame; remove its prototype link before importing.')
                if node.get('type')=='instance':
                    pending.append(node['of'])
                    definition=source.get('component_sets',{}).get(node['of'],{})
                    for name,value in node.get('props',{}).items():
                        if definition.get('properties',{}).get(name,{}).get('type')=='instance-swap': pending.append(value)
                for field,value in node.items():
                    if field not in ('children','overrides'): _values(value,refs=tokens,key=field)
                for key,value in node.get('overrides',{}).items(): _values(value,refs=tokens,key=key.rsplit('.',1)[-1])
        else: _fail(f'Source has no component or component set {key!r}.')
    checked=set()
    while tokens-checked:
        token=sorted(tokens-checked)[0]; checked.add(token)
        match=TOKEN.fullmatch(token)
        if not match or match.group(2) not in source.get('tokens',{}).get(match.group(1),{}):
            _fail(f'Source has no token {token!r}.')
        _values(source['tokens'][match.group(1)][match.group(2)],refs=tokens)
    return components,sets,tokens


def _rewrite_node(node, ids, tokens, source):
    out={}
    for field,value in node.items():
        if field=='id': out[field]=ids[value]
        elif field=='of': out[field]=ids[value]
        elif field=='children': out[field]=[_rewrite_node(child,ids,tokens,source) for child in value]
        elif field=='overrides':
            out[field]={}
            for key,v in value.items():
                child,sep,prop=key.rpartition('.')
                target=(ids[child]+'.'+prop) if sep else key
                out[field][target]=_values(v,tokens,key=prop if sep else key)
        elif field=='props':
            specs=source.get('component_sets',{}).get(node.get('of'),{}).get('properties',{})
            out[field]={name:ids[v] if specs.get(name,{}).get('type')=='instance-swap' else copy.deepcopy(v) for name,v in value.items()}
        else: out[field]=_values(value,tokens,key=field)
    return out


def _rewrite_set(definition,ids):
    out=copy.deepcopy(definition)
    for spec in out.get('properties',{}).values():
        if spec['type']=='instance-swap':
            spec['default']=ids[spec['default']]
            spec['allowed']=[ids[v] for v in spec['allowed']]
    for variant in out['variants']:
        variant['of']=ids[variant['of']]
        variant['bindings']={name:[ids[v] for v in values] for name,values in variant.get('bindings',{}).items()}
    return out


def _owned(doc, groups):
    owned={'components':{},'component_sets':{},'tokens':{}}
    for group in ('components','component_sets'):
        for key in groups[group]: owned[group][key]=canonical_hash(doc[group][key])
    for token in groups['tokens']:
        group,name=token.split('.',1)
        owned['tokens'][token]=canonical_hash(doc['tokens'][group][name])
    return owned


def _local_conflicts(destination,record):
    conflicts=[]
    for group,entries in record['owned'].items():
        for key,oldhash in entries.items():
            if group=='tokens':
                token_group,name=key.split('.',1); current=destination.get('tokens',{}).get(token_group,{})
                exists=name in current; value=current.get(name)
            else:
                current=destination.get(group,{})
                exists=key in current; value=current.get(key)
            if not exists or canonical_hash(value)!=oldhash:
                conflicts.append({'kind':'local-modification','id':group+':'+key,
                                  'message':'Previously imported content was modified or removed locally.'})
    return conflicts


def _check_modes(destination,source):
    before,after=destination.get('modes') or [],source.get('modes') or []
    if before==after: return
    empty=(not destination.get('components') and not destination.get('component_sets') and not destination.get('tokens') and
           not any(p.get('children') for p in destination.get('pages',[])))
    if not before and empty:
        if after: destination['modes']=copy.deepcopy(after)
        return
    _fail('Library source and destination mode names/order must match; only an empty destination can adopt modes.')


def _plan(destination_doc,source_doc,*,destination_id,source_id,source_version,source_slug,namespace,selection,previous=None):
    if not isinstance(destination_id,str) or not isinstance(source_id,str) or not HEX.fullmatch(destination_id) or not HEX.fullmatch(source_id): _fail('Document ids must be 32 lowercase hex characters.')
    if destination_id==source_id: _fail('A document cannot import itself.')
    if isinstance(source_version,bool) or not isinstance(source_version,int) or source_version<1: _fail('Choose an explicit positive source version.')
    if not isinstance(namespace,str) or not ID_RE.fullmatch(namespace) or len(namespace)>16: _fail('Namespace is an id of at most 16 characters.')
    source=normalize_document(source_doc); destination=normalize_document(destination_doc)
    validate_document(source); validate_document(destination)
    for key,limit,available in [('components',200,source.get('components',{})),('component_sets',50,source.get('component_sets',{})),('tokens',4000,None)]:
        values=selection.get(key)
        if not isinstance(values,list) or len(values)>limit or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):
            _fail('Library selections are bounded lists of distinct names.')
        if available is not None and any(v not in available for v in values): _fail(f'Source selection contains an unknown {key} name.')
    if not any(selection.values()): _fail('Select at least one component, component set or token.')
    if previous:
        if source_version<=previous['source_version']: _fail('Update requires an explicit newer retained source version.')
        conflicts=_local_conflicts(destination,previous)
        if conflicts:
            return {'doc':destination,'assets':set(),'report':{'counts':{'conflicts':len(conflicts)},'added':[],'updated':[],'removed':[],'conflicts':conflicts[:200],'truncated':len(conflicts)>200}}
    elif namespace in destination.get('libraries',{}): _fail('That library namespace is already pinned.')
    _check_modes(destination,source)
    components,sets,tokens=_closure(source,selection)
    ancestry={source_id}
    for record in source.get('libraries',{}).values():
        owned=record['owned']
        if components.intersection(owned['components']) or sets.intersection(owned['component_sets']) or tokens.intersection(owned['tokens']):
            ancestry.update(record['ancestry']); ancestry.add(record['source_id'])
    if destination_id in ancestry: _fail('This import would create a transitive document-library cycle.')
    if len(ancestry)>MAX_ANCESTRY: _fail('Library dependency ancestry exceeds 32 documents.')
    node_ids=set(components)|sets
    for key in components: node_ids.update(n['id'] for n in _nodes(source['components'][key]))
    ids={key:_name(namespace,'id',key) for key in sorted(node_ids)}
    token_map={key:_name(namespace,'token:'+key.split('.',1)[0],key.split('.',1)[1]) for key in sorted(tokens)}
    if len(set(ids.values()))!=len(ids): _fail('Namespaced ids collided; choose another namespace.')
    old_owned=previous['owned'] if previous else {'components':{},'component_sets':{},'tokens':{}}
    old_node_ids=set(previous['maps']['ids'].values()) if previous else set()
    taken=set(index_nodes(destination))|set(destination.get('component_sets',{}))
    if (set(ids.values()) & (taken-old_node_ids)): _fail('A generated library id collides with unowned destination content.')
    for old_group in ('components','component_sets'):
        for key in old_owned[old_group]: destination.get(old_group,{}).pop(key,None)
    for token in old_owned['tokens']:
        group,name=token.split('.',1); destination.get('tokens',{}).get(group,{}).pop(name,None)
    for key in components: destination.setdefault('components',{})[ids[key]]=_rewrite_node(source['components'][key],ids,token_map,source)
    for key in sets: destination.setdefault('component_sets',{})[ids[key]]=_rewrite_set(source['component_sets'][key],ids)
    groups={'components':{ids[key] for key in components},'component_sets':{ids[key] for key in sets},'tokens':set()}
    for token in sorted(tokens):
        group,name=token.split('.',1); target=token_map[token]
        entries=destination.setdefault('tokens',{}).setdefault(group,{})
        if target in entries: _fail('A generated token name collides with unowned destination content.')
        entries[target]=_values(source['tokens'][group][name],token_map)
        groups['tokens'].add(group+'.'+target)
    owned=_owned(destination,groups)
    record={'source_id':source_id,'source_version':source_version,'source_slug':source_slug,'namespace':namespace,
            'selection':copy.deepcopy(selection),'maps':{'ids':ids,'tokens':token_map},'owned':owned,'ancestry':sorted(ancestry)}
    destination.setdefault('libraries',{})[namespace]=record
    assets={sha for _,sha in image_assets({'components':{key:destination['components'][key] for key in groups['components']}})}
    def labels(value):
        return {('component' if group=='components' else 'component_set' if group=='component_sets' else 'token')+':'+key for group,entries in value.items() for key in entries}
    old,new=labels(old_owned),labels(owned)
    changed=set()
    for group,entries in owned.items():
        for key,digest in entries.items():
            if key in old_owned[group] and old_owned[group][key]!=digest:
                changed.update(labels({group:{key:digest}}))
    report={'counts':{'components':len(components),'component_sets':len(sets),'tokens':len(tokens),'assets':len(assets)},
            'added':sorted(new-old),'updated':sorted(changed),'removed':sorted(old-new),'conflicts':[],'truncated':False}
    report['counts'].update({key:len(report[key]) for key in ('added','updated','removed')})
    report['counts']['conflicts']=0
    try:
        validate_document(destination)
    except UiDesignerError as error:
        if previous is None: raise
        report['conflicts']=[{'kind':'invalid-update','id':namespace,
            'message':'Updated definitions violate destination references or budgets: '+str(error)[:300]}]
        report['counts']['conflicts']=1
        return {'doc':normalize_document(destination_doc),'assets':set(),'report':report}
    for key in ('added','updated','removed'):
        if len(report[key])>200: report[key]=report[key][:200]; report['truncated']=True
    return {'doc':destination,'assets':assets,'report':report}


def plan_import(destination_doc,source_doc,*,destination_id,source_id,source_version,source_slug,namespace,components,component_sets,tokens):
    return _plan(destination_doc,source_doc,destination_id=destination_id,source_id=source_id,source_version=source_version,
                 source_slug=source_slug,namespace=namespace,selection={'components':components,'component_sets':component_sets,'tokens':tokens})


def plan_update(destination_doc,source_doc,*,destination_id,library_id,source_version,source_slug):
    validate_document(destination_doc)
    record=destination_doc.get('libraries',{}).get(library_id)
    if record is None: _fail('Library pin not found.')
    return _plan(destination_doc,source_doc,destination_id=destination_id,source_id=record['source_id'],source_version=source_version,
                 source_slug=source_slug,namespace=record['namespace'],selection=record['selection'],previous=record)


def validate_libraries(doc,c):
    """Closed provenance. Hash mismatches are intentionally allowed local edits;
    the update planner reports them as conflicts instead of forbidding edits."""
    libraries=doc.get('libraries',{})
    def bad(path,message): c.add('UD-VALUE',path,message)
    def obj(value,path,keys):
        if not isinstance(value,dict): bad(path,'expected an object'); return False
        if set(value)!=set(keys): bad(path,'expected exactly: '+', '.join(sorted(keys)))
        return all(key in value for key in keys)
    if not isinstance(libraries,dict): bad('$.libraries','expected a library-id map'); return
    if len(libraries)>MAX_LIBRARIES: bad('$.libraries','at most 8 pinned libraries'); return
    claimed_ids,claimed_tokens=set(),set()
    for library_id,record in libraries.items():
        path='$.libraries.'+str(library_id)
        if not isinstance(library_id,str) or not ID_RE.fullmatch(library_id) or len(library_id)>16:
            bad(path,'library namespace is an id of at most 16 characters')
        keys={'source_id','source_version','source_slug','namespace','selection','maps','owned','ancestry'}
        if not obj(record,path,keys): continue
        if record['namespace']!=library_id: bad(path+'.namespace','namespace must equal the library key')
        if not isinstance(record['source_id'],str) or not HEX.fullmatch(record['source_id']): bad(path+'.source_id','expected a 32-hex source document id')
        version=record['source_version']
        if isinstance(version,bool) or not isinstance(version,int) or version<1: bad(path+'.source_version','expected a positive retained version')
        if not isinstance(record['source_slug'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',record['source_slug']): bad(path+'.source_slug','expected a display slug')
        ancestry=record['ancestry']
        if not isinstance(ancestry,list) or len(ancestry)>MAX_ANCESTRY or any(not isinstance(v,str) or not HEX.fullmatch(v) for v in ancestry):
            bad(path+'.ancestry','expected at most 32 source document ids')
        elif len(set(ancestry))!=len(ancestry) or record['source_id'] not in ancestry: bad(path+'.ancestry','ancestry must be distinct and include the source id')
        selection=record['selection']
        if obj(selection,path+'.selection',{'components','component_sets','tokens'}):
            for group,limit in [('components',200),('component_sets',50),('tokens',4000)]:
                values=selection[group]; pattern=TOKEN if group=='tokens' else ID_RE
                if not isinstance(values,list) or len(values)>limit or any(not isinstance(v,str) or not pattern.fullmatch(v) for v in values): bad(path+'.selection.'+group,'expected a bounded name list')
                elif len(set(values))!=len(values): bad(path+'.selection.'+group,'names must be distinct')
        maps=record['maps']; ids={}; tokens={}
        if obj(maps,path+'.maps',{'ids','tokens'}):
            for group,pattern,limit in [('ids',ID_RE,6000),('tokens',TOKEN,4000)]:
                values=maps[group]
                if not isinstance(values,dict) or len(values)>limit:
                    bad(path+'.maps.'+group,'expected a bounded remap object'); continue
                for source,dest in values.items():
                    if not isinstance(source,str) or not pattern.fullmatch(source) or not isinstance(dest,str) or not (ID_RE.fullmatch(dest) if group=='ids' else re.fullmatch(TOKEN_NAME,dest)):
                        bad(path+'.maps.'+group,'invalid remap name')
                mapped=list(values.values())
                if any(not isinstance(v,str) for v in mapped): continue
                if len(set(mapped))!=len(mapped): bad(path+'.maps.'+group,'remaps must be one-to-one')
                claimed=claimed_ids if group=='ids' else claimed_tokens
                claims=set(mapped) if group=='ids' else {key.split('.',1)[0]+'.'+v for key,v in values.items() if isinstance(key,str) and '.' in key}
                if claims & claimed: bad(path+'.maps.'+group,'two library pins claim the same destination name')
                claimed.update(claims)
                if group=='ids': ids=values
                else: tokens=values
        owned=record['owned']
        if obj(owned,path+'.owned',{'components','component_sets','tokens'}):
            for group,limit in [('components',200),('component_sets',50),('tokens',4000)]:
                values=owned[group]; pattern=TOKEN if group=='tokens' else ID_RE
                if not isinstance(values,dict) or len(values)>limit:
                    bad(path+'.owned.'+group,'expected bounded owned hashes'); continue
                allowed={key.split('.',1)[0]+'.'+v for key,v in tokens.items() if isinstance(key,str) and '.' in key and isinstance(v,str)} if group=='tokens' else {v for v in ids.values() if isinstance(v,str)}
                for name,digest in values.items():
                    if not isinstance(name,str) or not pattern.fullmatch(name) or not isinstance(digest,str) or not HASH.fullmatch(digest): bad(path+'.owned.'+group,'expected name -> canonical SHA256')
                    elif name not in allowed: bad(path+'.owned.'+group,'owned name must occur in the remap')
