import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";

type Finding = { code:string; severity:"error"|"warning"|"info"; node_id:string|null; mode:string|null; message:string; suggestion:string; approximate:boolean };
type Report = { version:number; rules_version:string; findings:Finding[]; total:number; by_severity:Record<string,number>; next_cursor:string|null; truncated:boolean };
type Props = { base:string; version:number; mode:string; pageId:string|null; selectedId:string|null; canvasNodeIds:string[]; onSelectNode:(id:string)=>void };
const DISPLAY_CAP=200;
export default function FindingsPanel({base,version,mode,pageId,selectedId,canvasNodeIds,onSelectNode}:Props) {
  const [scope,setScope]=useState("document"), [modes,setModes]=useState("current"), [severity,setSeverity]=useState("");
  const [report,setReport]=useState<(Report & {signature:string})|null>(null), [busy,setBusy]=useState(false), [error,setError]=useState("");
  const [modeInfo,setModeInfo]=useState<{version:number;values:string[]}|null>(null), [modeError,setModeError]=useState(""), [modeRetry,setModeRetry]=useState(0);
  const availableModes=modeInfo?.version===version ? modeInfo.values:null;
  const nodeIds=new Set(canvasNodeIds);
  useEffect(()=>{
    const controller=new AbortController();setModeInfo(null);setModeError("");
    api<{doc:{modes?:string[]}}>(`${base}?${new URLSearchParams({version:String(version),depth:"0"})}`,{signal:controller.signal})
      .then((result)=>{if(!controller.signal.aborted)setModeInfo({version,values:Array.isArray(result.doc?.modes) ? result.doc.modes.filter((value)=>typeof value==="string"):[]});})
      .catch(()=>{if(!controller.signal.aborted)setModeError("Could not load the document modes.");});
    return()=>controller.abort();
  },[base,version,modeRetry]);
  const request=useRef<AbortController|null>(null), pending=useRef(false);
  const node=scope==="selection" ? selectedId : scope==="page" ? pageId:null;
  const signature=JSON.stringify([base,version,mode,modes,severity,scope,node]);
  const active=useRef(signature);active.current=signature;
  const shown=report?.signature===signature ? report:null;
  useEffect(()=>{
    setReport(null);setError("");setBusy(false);pending.current=false;
    return()=>request.current?.abort();
  },[signature]);
  async function run(cursor?:string) {
    if(pending.current || !availableModes || (scope!=="document" && !node))return;
    const controller=new AbortController();request.current=controller;pending.current=true;
    setBusy(true);setError("");if(!cursor)setReport(null);
    const query=new URLSearchParams({version:String(version),limit:"50"});
    if(node)query.set("node",node);
    if(modes==="all")query.set("all_modes","true");else if(availableModes.includes(mode))query.set("mode",mode);
    if(severity)query.set("severity",severity);
    if(cursor)query.set("cursor",cursor);
    try {
      const result=await api<Report>(`${base}/lint?${query}`,{signal:controller.signal});
      if(controller.signal.aborted || active.current!==signature)return;
      if(result.version!==version || !Array.isArray(result.findings))throw new Error("The findings did not match the requested version. Run the check again.");
      setReport({...result,findings:[...(cursor ? shown?.findings ?? []:[]),...result.findings].slice(0,DISPLAY_CAP),signature});
    }catch(e){if(!controller.signal.aborted && active.current===signature)setError(apiErrorMessage(e,e instanceof Error && !/^\d+:/.test(e.message) ? e.message:"Could not check this design. Try again."));}
    finally{if(!controller.signal.aborted && active.current===signature){pending.current=false;setBusy(false);}}
  }
  return <section aria-label="Design findings">
    <h2>Findings · v{version}</h2>
    <p className="muted">Advisory design checks. Approximate findings need visual review; an empty result does not establish accessibility compliance.</p>
    <div className="ud-actions">
      <label>Findings scope <Select aria-label="Findings scope" value={scope} disabled={busy} onChange={(e)=>setScope(e.target.value)}><option value="document">Whole document</option><option value="page" disabled={!pageId}>Current page</option><option value="selection" disabled={!selectedId}>Selected node</option></Select></label>
      <label>Findings modes <Select aria-label="Findings modes" value={modes} disabled={busy} onChange={(e)=>setModes(e.target.value)}><option value="current">{availableModes?.includes(mode) ? `Current mode (${mode})`:"Document default mode"}</option><option value="all">All document modes</option></Select></label>
      <label>Finding severity <Select aria-label="Finding severity" value={severity} disabled={busy} onChange={(e)=>setSeverity(e.target.value)}><option value="">All severities</option><option value="error">Error</option><option value="warning">Warning</option><option value="info">Info</option></Select></label>
      <Button disabled={busy || !availableModes || (scope!=="document" && !node)} onClick={()=>run()}>Run lint</Button>
    </div>
    {modeError && <p role="alert">{modeError} <Button onClick={()=>setModeRetry((n)=>n+1)}>Retry loading modes</Button></p>}
    {!availableModes && !modeError && <p role="status">Loading document modes…</p>}
    {busy && <p role="status">{shown ? "Loading findings…":"Checking this design…"}</p>}
    {error && <p role="alert">{error}</p>}
    {shown && <>
      <p role="status">{shown.total===0 ? "No findings for these filters.":`Showing ${shown.findings.length} of ${shown.total} findings.`}</p>
      <p className="muted">Rules {shown.rules_version}. Across severity levels: {shown.by_severity.error ?? 0} errors, {shown.by_severity.warning ?? 0} warnings, {shown.by_severity.info ?? 0} informational.</p>
      <ul aria-label="Findings list">{shown.findings.map((finding,i)=><li key={`${finding.code}:${finding.node_id}:${finding.mode}:${i}`}>
        <p><strong>{finding.severity}</strong>{" · "}<code>{finding.code}</code>{finding.approximate && <strong> · Approximate — review visually</strong>}</p>
        <p>{finding.message}</p><p>Suggested change: {finding.suggestion}</p>
        <p className="muted">{finding.node_id ? nodeIds.has(finding.node_id) ? <Button variant="link" onClick={()=>onSelectNode(finding.node_id!)}>{finding.node_id}</Button>:`${finding.node_id} (not available on this canvas)`:"Document"}{" · "}{finding.mode ?? "All modes"}</p>
      </li>)}</ul>
      {shown.truncated && <p className="muted">The check reached its findings limit. Additional issues may exist.</p>}
      {shown.next_cursor && shown.findings.length<DISPLAY_CAP && <Button disabled={busy} onClick={()=>run(shown.next_cursor!)}>Load more findings</Button>}
      {shown.next_cursor && shown.findings.length>=DISPLAY_CAP && <p className="muted">Showing the first {DISPLAY_CAP} findings. Narrow the scope, mode or severity to review more.</p>}
    </>}
  </section>;
}
