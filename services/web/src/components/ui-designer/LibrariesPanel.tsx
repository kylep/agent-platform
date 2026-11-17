import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";

type Pin={library_id:string;source_id:string;source_slug:string;source_version:number;namespace:string;counts:Record<string,number>};
type Conflict={kind:string;id:string;message:string};
type Preview={version:number;library_id:string;source_version:number;counts:Record<string,number>;added:string[];updated:string[];removed:string[];conflicts:Conflict[];truncated:boolean};
type Write={version:number;library_id:string;replayed?:boolean};
type Pending={url:string;body:Record<string,unknown>};
type Props={base:string;version:number;canWrite:boolean;editorBusy:boolean;onWriteLock:(locked:boolean)=>void;onCommitted:(version:number)=>void;onConflict:()=>void};
function requestId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),(b)=>b.toString(16).padStart(2,"0")).join("");}
function numberVersion(raw:string){const value=Number(raw);if(!/^[1-9]\d*$/.test(raw.trim()) || !Number.isSafeInteger(value))throw new Error("Enter an explicit positive retained source version.");return value;}
function failure(e:unknown){
  if(e instanceof Error){const match=/^\d+: ([\s\S]*)$/.exec(e.message);if(match)try{const detail=JSON.parse(match[1]).detail;if(detail && typeof detail==="object")return {code:String(detail.code || ""),message:String(detail.message || "Library operation refused."),conflicts:Array.isArray(detail.conflicts) ? detail.conflicts as Conflict[]:[]};}catch{/* fall through */}}
  return {code:"",message:apiErrorMessage(e,e instanceof Error && !/^\d+:/.test(e.message) ? e.message:"Could not complete this library operation."),conflicts:[] as Conflict[]};
}
export default function LibrariesPanel(props:Props){
  const {base,version,canWrite,editorBusy}=props;
  const callbacks=useRef(props);callbacks.current=props;
  const [pins,setPins]=useState<Pin[]>([]),[loading,setLoading]=useState(true),[listError,setListError]=useState(""),[reload,setReload]=useState(0);
  const [source,setSource]=useState(""),[sourceVersion,setSourceVersion]=useState(""),[namespace,setNamespace]=useState("");
  const [components,setComponents]=useState(""),[sets,setSets]=useState(""),[tokens,setTokens]=useState("");
  const [updates,setUpdates]=useState<Record<string,string>>({}),[preview,setPreview]=useState<Preview|null>(null);
  const [busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[conflicts,setConflicts]=useState<Conflict[]>([]);
  const pending=useRef<Pending|null>(null),working=useRef(false),alive=useRef(true),operation=useRef<AbortController|null>(null);
  const scope=useRef({base,version});scope.current={base,version};
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;operation.current?.abort();callbacks.current.onWriteLock(false);};},[]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setListError("");
    api<{libraries:Pin[]}>(`${base}/libraries?${new URLSearchParams({version:String(version)})}`,{signal:controller.signal})
      .then((r)=>{if(!controller.signal.aborted)setPins(r.libraries);}).catch((e)=>{if(!controller.signal.aborted)setListError(apiErrorMessage(e,"Could not load library pins."));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[base,version,reload]);
  useEffect(()=>{setPreview(null);},[version]);
  const locked=busy || uncertain || editorBusy;
  function finish(){working.current=false;setBusy(false);callbacks.current.onWriteLock(false);}
  async function send(payload:Pending){
    setBusy(true);setError("");setConflicts([]);working.current=true;callbacks.current.onWriteLock(true);
    const controller=new AbortController();operation.current=controller;
    try{
      const receipt=await api<Write>(payload.url,{method:"POST",body:JSON.stringify(payload.body),signal:controller.signal});
      if(!alive.current || controller.signal.aborted)return;
      pending.current=null;setUncertain(false);setPreview(null);setNotice(`Library ${receipt.library_id} saved at destination v${receipt.version}.`);
      callbacks.current.onCommitted(receipt.version);setReload((n)=>n+1);finish();
    }catch(e){
      if(!alive.current || controller.signal.aborted)return;
      const definite=e instanceof Error && /^4\d\d:/.test(e.message) && !e.message.startsWith("408:");
      if(!definite){setUncertain(true);setBusy(false);working.current=false;setError("The library save could not be confirmed. Retry the same save before editing this document.");}
      else{pending.current=null;setUncertain(false);const refusal=failure(e);setError(refusal.message);setConflicts(refusal.conflicts);setPreview(null);if(refusal.code==="UD-VERSION-CONFLICT")callbacks.current.onConflict();finish();}
    }
  }
  async function importLibrary(event:React.FormEvent){
    event.preventDefault();if(working.current || pending.current || !canWrite || editorBusy)return;
    setError("");setNotice("");setConflicts([]);
    const controller=new AbortController();operation.current=controller;
    try{
      const sourcePin=numberVersion(sourceVersion),name=namespace.trim();
      if(!/^[a-z0-9][a-z0-9-]{0,15}$/.test(name))throw new Error("Use 1–16 lowercase letters, numbers or hyphens for the namespace.");
      const ids=(raw:string)=>[...new Set(raw.split(",").map((value)=>value.trim()).filter(Boolean))];
      const choices={components:ids(components),component_sets:ids(sets),tokens:ids(tokens)};
      if(!choices.components.length && !choices.component_sets.length && !choices.tokens.length)throw new Error("Select at least one component, component set or token ID.");
      const slug=source.trim();if(!slug)throw new Error("Enter the source document slug.");
      working.current=true;setBusy(true);callbacks.current.onWriteLock(true);
      const targetVersion=version;
      const sourceDoc=await api<{id:string}>(`/api/ui-designer/docs/${encodeURIComponent(slug)}?${new URLSearchParams({version:String(sourcePin),depth:"0"})}`,{signal:controller.signal});
      if(!alive.current || controller.signal.aborted)return;
      if(!/^[a-f0-9]{32}$/.test(sourceDoc.id))throw new Error("The source document did not return a valid identity.");
      const payload={url:`${base}/libraries/import`,body:{expected_version:targetVersion,request_id:requestId(),library:{source_id:sourceDoc.id,source_version:sourcePin,namespace:name,...choices}}};
      pending.current=payload;await send(payload);
    }catch(e){if(alive.current && !controller.signal.aborted){setError(failure(e).message);finish();}}
  }
  async function previewUpdate(pin:Pin){
    if(working.current || pending.current || !canWrite || editorBusy)return;
    const controller=new AbortController();operation.current=controller;const requestedAt=version;
    try{
      const selected=numberVersion(updates[pin.library_id] || "");if(selected<=pin.source_version)throw new Error("Choose a retained source version newer than the current pin.");
      working.current=true;setBusy(true);setError("");setNotice("");setConflicts([]);setPreview(null);
      const result=await api<Preview>(`${base}/libraries/${encodeURIComponent(pin.library_id)}/preview`,{method:"POST",body:JSON.stringify({source_version:selected}),signal:controller.signal});
      if(!alive.current || controller.signal.aborted)return;
      if(scope.current.base!==base || scope.current.version!==requestedAt || result.version!==requestedAt)throw new Error("The destination changed during preview. Preview again before applying.");
      setPreview(result);
    }catch(e){if(alive.current && !controller.signal.aborted)setError(failure(e).message);}
    finally{if(alive.current && !controller.signal.aborted){working.current=false;setBusy(false);}}
  }
  function applyPreview(){
    if(!preview || preview.version!==version || (preview.conflicts.length || (preview.counts.conflicts ?? 0)>0) || locked || !canWrite)return;
    const payload={url:`${base}/libraries/${encodeURIComponent(preview.library_id)}/apply`,body:{source_version:preview.source_version,expected_version:preview.version,request_id:requestId()}};
    pending.current=payload;void send(payload);
  }
  return <section aria-label="Document libraries"><h2>Libraries · v{version}</h2>
    <p className="muted">Libraries are pinned copies. Updates require an explicit preview and apply.</p>
    {!canWrite && <p className="muted">Library changes are available when editing the latest version with write access.</p>}
    {loading && <p role="status">Loading library pins…</p>}{listError && <p role="alert">{listError} <Button onClick={()=>setReload((n)=>n+1)}>Reload libraries</Button></p>}
    {!loading && !listError && pins.length===0 && <p className="muted">No library pins in this version.</p>}
    <ul>{pins.slice(0,50).map((pin)=><li key={pin.library_id}><h3>{pin.namespace}</h3><p>{pin.source_slug || pin.source_id} · source v{pin.source_version}</p><p className="muted">Source identity: {pin.source_id}</p>
      <label>New source version for {pin.namespace}<Input aria-label={`New source version for ${pin.namespace}`} inputMode="numeric" value={updates[pin.library_id] || ""} disabled={locked || !canWrite} onChange={(e)=>{setUpdates((old)=>({...old,[pin.library_id]:e.target.value}));setPreview(null);}} /></label>{" "}
      <Button disabled={locked || !canWrite} onClick={()=>previewUpdate(pin)}>Preview update {pin.namespace}</Button>
    </li>)}</ul>{pins.length>50 && <p className="muted">Showing the first 50 library pins.</p>}
    <form onSubmit={importLibrary}><fieldset disabled={locked || !canWrite}><legend>Import a pinned library</legend>
      <p className="ud-edit-field"><label htmlFor="ud-library-source">Source document slug</label><Input id="ud-library-source" value={source} onChange={(e)=>setSource(e.target.value)} required /></p>
      <p className="ud-edit-field"><label htmlFor="ud-library-source-version">Retained source version</label><Input id="ud-library-source-version" inputMode="numeric" value={sourceVersion} onChange={(e)=>setSourceVersion(e.target.value)} required /></p>
      <p className="ud-edit-field"><label htmlFor="ud-library-namespace">Namespace</label><Input id="ud-library-namespace" maxLength={16} value={namespace} onChange={(e)=>setNamespace(e.target.value)} required /></p>
      {([["components","Component IDs",components,setComponents],["sets","Component set IDs",sets,setSets],["tokens","Token IDs (for example color.brand)",tokens,setTokens]] as const).map(([key,label,value,set])=><p className="ud-edit-field" key={key}><label htmlFor={`ud-library-${key}`}>{label} · comma separated</label><Input id={`ud-library-${key}`} value={value} onChange={(e)=>set(e.target.value)} /></p>)}
      <Button type="submit">Import library</Button>
    </fieldset></form>
    {busy && <p role="status">Working on the library…</p>}{error && <p role="alert">{error}</p>}
    {uncertain && <Button disabled={busy} onClick={()=>{if(pending.current)void send(pending.current);}}>Retry library save</Button>}
    {notice && <p role="status">{notice}</p>}
    {preview && preview.version===version && <section aria-label="Library update preview"><h3>Update {preview.library_id} to source v{preview.source_version}</h3><p>Based on destination v{preview.version}.</p>
      <p>{Object.entries(preview.counts).map(([key,count])=>`${key.replaceAll("_"," ")}: ${count}`).join(" · ")}</p>
      {(["added","updated","removed"] as const).map((key)=><details key={key}><summary>{key} ({preview[key].length})</summary><ul>{preview[key].slice(0,25).map((id)=><li key={id}>{id}</li>)}</ul>{preview[key].length>25 && <p>Showing the first 25 IDs.</p>}</details>)}
      {preview.truncated && <p className="muted">The preview lists were truncated. Review the counts before applying.</p>}
      {(preview.conflicts.length>0 || (preview.counts.conflicts ?? 0)>0) && <p role="alert">Local changes conflict with this update. Resolve them before previewing again; they will not be overwritten.</p>}
      <ul>{preview.conflicts.slice(0,50).map((conflict,i)=><li key={i}>{conflict.kind} · {conflict.id}: {conflict.message}</li>)}</ul>
      <Button disabled={locked || !canWrite || !!preview.conflicts.length || (preview.counts.conflicts ?? 0)>0} onClick={applyPreview}>Apply library update</Button>
    </section>}
    {!!conflicts.length && <ul aria-label="Library conflicts">{conflicts.slice(0,50).map((conflict,i)=><li key={i}>{conflict.id}: {conflict.message}</li>)}</ul>}
  </section>;
}
