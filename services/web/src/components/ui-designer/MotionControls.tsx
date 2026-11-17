import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { ARTIFACT_ID_RE } from "../../lib/artifacts";
import { nodeLabel, type RNode, type UiExport } from "../../lib/uiDesigner";
import type { MotionEasing, MotionInput } from "../../lib/uiMotion";
import MotionPlayback from "./MotionPlayback";

type Props={base:string;version:number;mode:string;frames:RNode[];canExport:boolean};
export default function MotionControls({base,version,mode,frames,canExport}:Props) {
  const [from,setFrom]=useState(frames[0]?.id ?? ""),[to,setTo]=useState(frames[1]?.id ?? frames[0]?.id ?? "");
  const [duration,setDuration]=useState("800"),[easing,setEasing]=useState<MotionEasing>("ease-in-out"),[format,setFormat]=useState("mp4");
  const [preview,setPreview]=useState<MotionInput|null>(null),[epoch,setEpoch]=useState(0),[busy,setBusy]=useState(false),[uncertain,setUncertain]=useState(false),[error,setError]=useState(""),[result,setResult]=useState<UiExport|null>(null);
  const [reduced,setReduced]=useState(()=>matchMedia("(prefers-reduced-motion: reduce)").matches);
  const pending=useRef<string|null>(null),controller=useRef<AbortController|null>(null),generation=useRef(0);
  useEffect(()=>{const media=matchMedia("(prefers-reduced-motion: reduce)");const changed=()=>setReduced(media.matches);media.addEventListener("change",changed);return()=>media.removeEventListener("change",changed);},[]);
  useEffect(()=>{const cancel=()=>controller.current?.abort();generation.current++;cancel();pending.current=null;setBusy(false);setUncertain(false);setPreview(null);setResult(null);setError("");const invalidate=()=>{generation.current++;cancel();};return invalidate;},[base,version,mode]);
  function input():MotionInput {
    const ms=Number(duration);
    if(!frames.some((f)=>f.id===from) || !frames.some((f)=>f.id===to) || from===to)throw new Error("Choose two different frames.");
    if(!Number.isInteger(ms) || ms<200 || ms>5000)throw new Error("Motion duration must be an integer from 200 to 5000 ms.");
    return {version,mode,from_node:from,to_node:to,duration_ms:ms,easing,fps:24,max_edge:720};
  }
  function previewMotion(){try{const next=input();setPreview(next);setEpoch((n)=>n+1);setError("");}catch(e){setError((e as Error).message);}}
  async function exportMotion(retry=false){
    if(busy || !canExport || (uncertain && !retry))return;
    try {if(!retry){const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),(byte)=>byte.toString(16).padStart(2,"0")).join("");pending.current=JSON.stringify({...input(),format,request_id});}}catch(e){setError((e as Error).message);return;}
    if(!pending.current)return;
    const request=new AbortController();controller.current=request;const scope=generation.current;
    setBusy(true);setError("");setResult(null);
    try{
      const value=await api<UiExport>(`${base}/motion/export`,{method:"POST",body:pending.current,signal:request.signal});
      if(request.signal.aborted || scope!==generation.current)return;
      if(!ARTIFACT_ID_RE.test(value.artifact_id))throw new Error("The motion export returned an invalid artifact link.");
      setResult(value);pending.current=null;setUncertain(false);
    }catch(e){
      if(request.signal.aborted || scope!==generation.current)return;
      const status=e instanceof Error ? Number(/^(\d+):/.exec(e.message)?.[1]):0;
      const unknown=!(status>=400 && status<500 && status!==408);
      setUncertain(unknown);if(!unknown)pending.current=null;
      setError(apiErrorMessage(e,"Could not export motion.")+(unknown ? " The outcome is unknown. Retry the same export safely.":""));
    }finally{if(!request.signal.aborted && scope===generation.current)setBusy(false);}
  }
  const locked=busy || uncertain;
  return <section aria-label="Motion export" className="ud-export-controls"><h2>Motion</h2><p className="muted">Animate two frames from version {version}. Silent MP4 and GIF exports use the same matching and timing as the preview.</p>
    <fieldset disabled={locked}><div className="ud-actions">
      <label>From frame <Select aria-label="Motion from frame" value={from} onChange={(e)=>{setFrom(e.target.value);setPreview(null);}}>{frames.map((f)=><option key={f.id} value={f.id}>{nodeLabel(f)}</option>)}</Select></label>
      <label>To frame <Select aria-label="Motion to frame" value={to} onChange={(e)=>{setTo(e.target.value);setPreview(null);}}>{frames.map((f)=><option key={f.id} value={f.id}>{nodeLabel(f)}</option>)}</Select></label>
      <label>Duration (ms) <Input aria-label="Motion duration (ms)" inputMode="numeric" value={duration} onChange={(e)=>{setDuration(e.target.value);setPreview(null);}} /></label>
      <label>Easing <Select aria-label="Export motion easing" value={easing} onChange={(e)=>{setEasing(e.target.value as MotionEasing);setPreview(null);}}>{["linear","ease-in","ease-out","ease-in-out"].map((v)=><option key={v} value={v}>{v}</option>)}</Select></label>
      <label>Format <Select aria-label="Motion format" value={format} onChange={(e)=>setFormat(e.target.value)}><option value="mp4">MP4</option><option value="gif">GIF</option></Select></label>
      <Button disabled={frames.length<2} onClick={previewMotion}>Preview motion</Button><Button disabled={!canExport || frames.length<2} onClick={()=>void exportMotion()}>Export motion</Button>
    </div></fieldset>
    {busy && <p role="status">Exporting motion…</p>}{error && <p role="alert">{error}</p>}{uncertain && <Button disabled={busy} onClick={()=>void exportMotion(true)}>Retry motion export</Button>}
    {reduced && <p className="muted">Reduced motion: previews show the destination instantly. Exported files retain their chosen animation.</p>}
    {preview && <div style={{maxWidth:720}}>{reduced ? <img alt="Motion destination frame" style={{maxWidth:"100%"}} src={`${base}/render?${new URLSearchParams({version:String(version),node:preview.to_node,mode,max_edge:"720"})}`} /> : <MotionPlayback key={`${epoch}:${base}:${version}:${mode}`} base={base} input={preview} reduced={false} />}</div>}
    {result && <div role="status"><p>Exported {result.name} · v{version}{" "}<Link to={`/artifacts/${result.artifact_id}`}>Open motion artifact</Link>{" · "}<a href={`/api/artifacts/${result.artifact_id}/content`} download={result.name}>Download motion</a></p>{result.warnings?.length ? <ul aria-label="Motion export notes">{result.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul>:null}</div>}
  </section>;
}
