import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { UD } from "../../lib/uiDesigner";

type Template={id:string;revision:number;title:string;description:string;width:number;height:number;tags:string[];preview_node:string};
type Pending={slug:string;title?:string;template_id?:string;template_revision?:number;request_id:string};
function requestId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),(b)=>b.toString(16).padStart(2,"0")).join("");}
function message(error:unknown){
  if(error instanceof Error){const match=/^\d+: ([\s\S]*)$/.exec(error.message);if(match)try{const detail=JSON.parse(match[1]).detail;if(detail && typeof detail==="object" && typeof detail.message==="string")return detail.message;}catch{/* plain error */}}
  return apiErrorMessage(error,error instanceof Error && !/^\d+:/.test(error.message) ? error.message:"Could not create the document. Try again.");
}
export default function CreateDocument(){
  const navigate=useNavigate();
  const [templates,setTemplates]=useState<Template[]>([]),[loading,setLoading]=useState(true),[catalogError,setCatalogError]=useState(""),[retryCatalog,setRetryCatalog]=useState(0);
  const [selected,setSelected]=useState(""),[slug,setSlug]=useState(""),[title,setTitle]=useState("");
  const [busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState("");
  const pending=useRef<Pending|null>(null),working=useRef(false),alive=useRef(true),mutation=useRef<AbortController|null>(null);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;mutation.current?.abort();};},[]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setCatalogError("");
    api<{templates:Template[]}>("/api/ui-designer/templates",{signal:controller.signal}).then((r)=>{
      if(controller.signal.aborted)return;
      if(!Array.isArray(r.templates) || r.templates.length>16 || r.templates.some((template)=>!template || typeof template.id!=="string" || typeof template.title!=="string" || !Number.isSafeInteger(template.revision) || template.revision<1 || !Number.isFinite(template.width) || !Number.isFinite(template.height) || template.width<=0 || template.height<=0))throw new Error("The starter template catalog is invalid.");
      setTemplates(r.templates);
    }).catch((e)=>{if(!controller.signal.aborted)setCatalogError(message(e));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[retryCatalog]);
  const template=templates.find((entry)=>entry.id===selected);
  async function send(payload:Pending){
    if(working.current)return;working.current=true;setBusy(true);setError("");
    const controller=new AbortController();mutation.current=controller;
    try{
      await api<{slug:string;version:number}>(UD,{method:"POST",body:JSON.stringify(payload),signal:controller.signal});
      if(!alive.current || controller.signal.aborted)return;
      pending.current=null;setUncertain(false);navigate(`/ui-designer/${encodeURIComponent(payload.slug)}`);
    }catch(e){
      if(!alive.current || controller.signal.aborted)return;
      const definite=e instanceof Error && /^4\d\d:/.test(e.message) && !e.message.startsWith("408:");
      if(definite){pending.current=null;setUncertain(false);setError(message(e));}
      else{setUncertain(true);setError("Document creation could not be confirmed. Retry the same create request before changing this form.");}
    }finally{if(alive.current && !controller.signal.aborted){working.current=false;setBusy(false);}}
  }
  function create(event:React.FormEvent){
    event.preventDefault();if(working.current || pending.current)return;setError("");
    const target=slug.trim();
    if(!/^[a-z0-9][a-z0-9-]{0,63}$/.test(target)){setError("Use 1–64 lowercase letters, numbers or hyphens for the document slug.");return;}
    if(selected && !template){setError("Choose an available starter template.");return;}
    const payload:Pending={slug:target,...(title.trim()?{title:title.trim()}:{}),...(template?{template_id:template.id,template_revision:template.revision}:{}),request_id:requestId()};
    pending.current=payload;void send(payload);
  }
  const locked=busy || uncertain;
  return <section aria-label="Create a design document"><h2>Create document</h2>
    <form onSubmit={create}><fieldset disabled={locked}>
      <p className="ud-edit-field"><label htmlFor="ud-create-template">Starter template</label><Select id="ud-create-template" value={selected} onChange={(e)=>setSelected(e.target.value)}><option value="">Blank document</option>{templates.map((entry)=><option key={entry.id} value={entry.id}>{entry.title}</option>)}</Select></p>
      {loading && <p role="status">Loading starter templates…</p>}
      {catalogError && <p role="alert">{catalogError} <Button disabled={locked} onClick={()=>setRetryCatalog((n)=>n+1)}>Reload templates</Button></p>}
      {template ? <section aria-label="Template details"><p><strong>{template.title}</strong> · {template.width} × {template.height} px · revision {template.revision}</p><p>{template.description}</p>{Array.isArray(template.tags) && template.tags.length>0 && <p className="muted">{template.tags.join(" · ")}</p>}</section>:<p className="muted">An empty document with no predefined frames.</p>}
      <p className="ud-edit-field"><label htmlFor="ud-create-slug">Document slug</label><Input id="ud-create-slug" maxLength={64} value={slug} onChange={(e)=>setSlug(e.target.value)} required /></p>
      <p className="ud-edit-field"><label htmlFor="ud-create-title">Document title (optional)</label><Input id="ud-create-title" maxLength={120} value={title} onChange={(e)=>setTitle(e.target.value)} placeholder={template?.title || "Document title"} /></p>
      <Button type="submit">{busy?"Creating…":"Create document"}</Button>
    </fieldset></form>
    {error && <p role="alert">{error}</p>}
    {uncertain && <Button disabled={busy} onClick={()=>{if(pending.current)void send(pending.current);}}>Retry create</Button>}
  </section>;
}
