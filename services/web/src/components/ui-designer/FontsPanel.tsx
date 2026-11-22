import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import type { UiFont } from "../../lib/uiFonts";

type Props={base:string;version:number;fonts:UiFont[];loading:boolean;listError:string;canWrite:boolean;editorBusy:boolean;onWriteLock:(locked:boolean)=>void;onCommitted:(version:number)=>void;onConflict:()=>void};
export default function FontsPanel(props:Props){
  const {base,version,fonts,loading,listError,canWrite,editorBusy}=props;
  const callbacks=useRef(props);callbacks.current=props;
  const [file,setFile]=useState<File|null>(null),[busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const fileInput=useRef<HTMLInputElement>(null);
  const pending=useRef<string|null>(null),working=useRef(false),alive=useRef(true),request=useRef<AbortController|null>(null);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;request.current?.abort();callbacks.current.onWriteLock(false);};},[]);
  function unlock(){working.current=false;setBusy(false);callbacks.current.onWriteLock(false);}
  async function send(body:string){
    const controller=new AbortController();request.current=controller;working.current=true;setBusy(true);setError("");callbacks.current.onWriteLock(true);
    try{
      const receipt=await api<{version:number}>(`${base}/fonts`,{method:"POST",body,signal:controller.signal});
      if(!alive.current || controller.signal.aborted)return;
      pending.current=null;setUncertain(false);setFile(null);if(fileInput.current)fileInput.current.value="";setNotice(`Font installed at version ${receipt.version}. To undo, restore an earlier document version.`);
      callbacks.current.onCommitted(receipt.version);unlock();
    }catch(e){
      if(!alive.current || controller.signal.aborted)return;
      const status=e instanceof Error ? Number(/^(\d+):/.exec(e.message)?.[1]):0;
      if(!(status>=400 && status<500 && status!==408)){setUncertain(true);setBusy(false);working.current=false;setError("The font upload could not be confirmed. Retry the same upload before editing this document.");}
      else{pending.current=null;setUncertain(false);setError(apiErrorMessage(e,"Could not install this font."));if(status===409){setError("The document changed before this upload. Review the latest version, then upload again.");callbacks.current.onConflict();}unlock();}
    }
  }
  async function upload(){
    if(working.current || pending.current || !canWrite || editorBusy || !file)return;
    setError("");setNotice("");
    if(!/\.(ttf|otf)$/i.test(file.name)){setError("Choose a static TTF or OTF font file.");return;}
    if(file.size<1 || file.size>1024*1024){setError("The font file must be no larger than 1 MiB.");return;}
    working.current=true;setBusy(true);callbacks.current.onWriteLock(true);
    const expected_version=version;
    try{
      const bytes=new Uint8Array(await file.arrayBuffer());if(!alive.current)return;
      if(!((bytes[0]===0 && bytes[1]===1 && bytes[2]===0 && bytes[3]===0) || String.fromCharCode(...bytes.slice(0,4))==="OTTO"))throw new Error("Choose a static TTF or OTF font file.");
      let binary="";for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),(b)=>b.toString(16).padStart(2,"0")).join("");
      const body=JSON.stringify({data_base64:btoa(binary),expected_version,request_id});pending.current=body;await send(body);
    }catch(e){if(alive.current){setError(e instanceof Error?e.message:"Could not read the font file.");unlock();}}
  }
  const locked=busy || uncertain || editorBusy;
  return <section aria-label="Document fonts"><h2>Document fonts</h2>
    <p className="muted">Upload a static TTF or OTF file, up to 1 MiB, with an embedded SIL Open Font License 1.1. Up to eight faces per document; custom families cannot replace bundled fonts. The server checks the file and its license.</p>
    {loading && <p role="status">Loading installed fonts…</p>}{listError && <p role="alert">{listError}</p>}
    {fonts.length ? <ul aria-label="Installed document fonts">{fonts.map((font)=><li key={font.sha256}><strong>{font.family}</strong> · weight {font.weight} · {font.license}<details><summary>License for {font.family} {font.weight}</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{font.license_text}</pre></details></li>)}</ul>:!loading && <p>No custom fonts installed in this version.</p>}
    {!canWrite && <p className="muted">Font uploads are available in the latest editable document.</p>}
    <p className="ud-edit-field"><label htmlFor="ud-font-file">Font file</label><Input ref={fileInput} id="ud-font-file" type="file" accept=".ttf,.otf,font/ttf,font/otf" disabled={!canWrite || locked || fonts.length>=8} onChange={(e)=>{setFile(e.target.files?.[0] ?? null);setError("");setNotice("");}} /></p>
    <Button disabled={!canWrite || locked || !file || fonts.length>=8} onClick={()=>void upload()}>Upload font</Button>
    {busy && <p role="status">Validating and installing font…</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <Button disabled={busy || !canWrite} onClick={()=>{if(pending.current && !working.current)void send(pending.current);}}>Retry font upload</Button>}
  </section>;
}
