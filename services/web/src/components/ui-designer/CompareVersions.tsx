import "./CompareVersions.css";
import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { opSummary, type UiDiff, type UiVersionRow } from "../../lib/uiDesigner";

type VisualDiff = {
  mime: string; image: string; base_image: string; width: number; height: number; box: [number, number, number, number];
  changed_pixels: number; total_pixels: number; regions: { x:number; y:number; w:number; h:number; node_ids:string[] }[];
  node_ids:string[]; truncated:boolean; warnings?:string[];
};
type Props = { base:string; versions:UiVersionRow[]; initialVersion:number; pageId:string; selectedId:string|null; mode:string; onSelectNode:(id:string)=>void };
function imageUrl(result:VisualDiff, base=false) {
  const image=base ? result.base_image : result.image;
  if (result.mime!=="image/png" || typeof image!=="string" || image.length>12*1024*1024 ||
      !/^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(image) || !Number.isSafeInteger(result.width) || !Number.isSafeInteger(result.height) ||
      result.width<=0 || result.height<=0 || result.width*result.height>10_000_000 ||
      !Array.isArray(result.box) || result.box.length!==4 || !result.box.every(Number.isFinite) || result.box[2]<=0 || result.box[3]<=0)
    throw new Error("The visual comparison returned an invalid image.");
  return `data:image/png;base64,${image}`;
}
export default function CompareVersions({base,versions,initialVersion,pageId,selectedId,mode,onSelectNode}:Props) {
  const [from,setFrom]=useState(String(versions.find((v)=>v.version<initialVersion)?.version ?? initialVersion));
  const [to,setTo]=useState(String(initialVersion)), [scope,setScope]=useState("page");
  const [diff,setDiff]=useState<UiDiff|null>(null), [textError,setTextError]=useState("");
  const [tolerance,setTolerance]=useState("0"), [opacity,setOpacity]=useState(100), [showBoxes,setShowBoxes]=useState(true);
  const [visual,setVisual]=useState<{data:VisualDiff;url:string;baseUrl:string;tolerance:number;signature:string}|null>(null), [busy,setBusy]=useState(false), [error,setError]=useState("");
  const request=useRef<AbortController|null>(null);
  const node=scope==="selection" ? selectedId : pageId;
  const signature=`${base}:${from}:${to}:${node}:${mode}`;
  const activeSignature=useRef(signature);activeSignature.current=signature;
  const shownVisual=visual?.signature===signature ? visual:null;
  useEffect(()=>{
    const controller=new AbortController();setDiff(null);setTextError("");
    api<UiDiff>(`${base}/diff?${new URLSearchParams({from,to})}`,{signal:controller.signal}).then((r)=>{if(!controller.signal.aborted)setDiff(r);})
      .catch((e)=>{if(!controller.signal.aborted)setTextError(apiErrorMessage(e,"Could not load the edit history."));});
    return()=>controller.abort();
  },[base,from,to]);
  useEffect(()=>{
    setVisual(null);setError("");setBusy(false);
    return()=>request.current?.abort();
  },[signature]);
  async function compareVisual() {
    if(busy || !node)return;
    const threshold=Number(tolerance);
    if(!tolerance.trim() || !Number.isInteger(threshold) || threshold<0 || threshold>255){setError("Tolerance must be an integer from 0 to 255.");return;}
    const controller=new AbortController();request.current=controller;
    setBusy(true);setError("");setVisual(null);
    try {
      const result=await api<VisualDiff>(`${base}/visual-diff`,{method:"POST",signal:controller.signal,body:JSON.stringify({from_version:Number(from),to_version:Number(to),node,mode,scale:1,tolerance:threshold})});
      if(controller.signal.aborted || activeSignature.current!==signature)return;
      const url=imageUrl(result), baseUrl=imageUrl(result,true);
      if ((result.image.length+result.base_image.length)*3/4>8*1024*1024+4 || !Array.isArray(result.regions) || !Array.isArray(result.node_ids) ||
          !result.node_ids.every((id)=>typeof id==="string") || !result.regions.every((r)=>r && [r.x,r.y,r.w,r.h].every(Number.isFinite) && r.w>=0 && r.h>=0) ||
          !Number.isSafeInteger(result.changed_pixels) || !Number.isSafeInteger(result.total_pixels) || result.changed_pixels<0 || result.total_pixels<result.changed_pixels)
        throw new Error("The visual comparison returned invalid result data.");
      setVisual({data:result,url,baseUrl,tolerance:threshold,signature});
    }catch(e){if(!controller.signal.aborted && activeSignature.current===signature)setError(apiErrorMessage(e,e instanceof Error && !/^\d+:/.test(e.message) ? e.message:"Could not compare these versions."));}
    finally{if(!controller.signal.aborted && activeSignature.current===signature)setBusy(false);}
  }
  return <section aria-label="Compare versions">
    <div className="ud-actions">
      {(["from","to"] as const).map((key)=><label key={key}>{key === "from" ? "From version" : "To version"}<Select aria-label={`Compare ${key}`} value={key==="from" ? from:to} disabled={busy} onChange={(e)=>(key==="from" ? setFrom:setTo)(e.target.value)}>{versions.map((v)=><option key={v.version} value={v.version}>v{v.version}{v.label ? ` ${v.label}`:""} · {v.author}</option>)}</Select></label>)}
      <label>Compare scope<Select aria-label="Compare scope" value={scope} disabled={busy} onChange={(e)=>setScope(e.target.value)}><option value="page">Current page</option><option value="selection" disabled={!selectedId}>Selected node</option></Select></label>
    </div>
    <div className="ud-compare">{[from,to].map((v,i)=><figure key={`${i}:${node}:${v}:${mode}`}><img alt={`Version ${v}`} src={`${base}/render?${new URLSearchParams({mode,node:node || pageId,version:v})}`} /><figcaption>v{v}</figcaption></figure>)}</div>
    <div className="ud-actions"><label>Tolerance (0–255)<Input aria-label="Visual difference tolerance" type="number" min={0} max={255} step={1} value={tolerance} disabled={busy} onChange={(e)=>setTolerance(e.target.value)} /></label><Button onClick={compareVisual} disabled={busy || !node}>{busy ? "Comparing…":"Compare visually"}</Button></div>
    {busy && <p role="status">Comparing the pinned versions…</p>}{error && <p role="alert">{error}</p>}
    {shownVisual && <div aria-label="Visual comparison result">
      <p role="status">{shownVisual.data.changed_pixels===0 ? `No visual differences at tolerance ${shownVisual.tolerance}.`:`${shownVisual.data.changed_pixels.toLocaleString()} of ${shownVisual.data.total_pixels.toLocaleString()} pixels changed at tolerance ${shownVisual.tolerance}.`}</p>
      <p className="muted">Magenta marks changed pixels. Set heatmap opacity to 0% to see the aligned target design.</p>
      <div className="ud-actions"><label>Heatmap opacity <Input aria-label="Heatmap opacity" type="range" min={0} max={100} value={opacity} onChange={(e)=>setOpacity(Number(e.target.value))} /> {opacity}%</label><label><input type="checkbox" checked={showBoxes} onChange={(e)=>setShowBoxes(e.target.checked)} /> Show changed regions</label></div>
      <div className="ud-visual-diff" style={{aspectRatio:`${shownVisual.data.width}/${shownVisual.data.height}`,maxWidth:`min(100%, ${shownVisual.data.width}px)`}}>
        <img alt="Aligned target version" src={shownVisual.baseUrl} />
        <img className="ud-diff-heatmap" alt={`Visual differences from v${from} to v${to}`} src={shownVisual.url} style={{opacity:opacity/100}} />
        {showBoxes && <svg aria-label="Changed regions" role="img" viewBox={shownVisual.data.box.join(" ")} preserveAspectRatio="none">{shownVisual.data.regions.slice(0,200).map((region,i)=><rect key={i} x={region.x} y={region.y} width={region.w} height={region.h} fill="none" stroke="var(--ds-accent)" strokeWidth={1} vectorEffect="non-scaling-stroke" />)}</svg>}
      </div>
      {shownVisual.data.node_ids.length>0 && <div><h3>Changed nodes</h3><ul>{shownVisual.data.node_ids.slice(0,50).map((id)=><li key={id}>{id.includes("/") ? id:<Button variant="link" onClick={()=>onSelectNode(id)}>{id}</Button>}</li>)}</ul>{shownVisual.data.node_ids.length>50 && <p>{shownVisual.data.node_ids.length-50} more node IDs.</p>}</div>}
      {shownVisual.data.truncated && <p className="muted">The changed-region or node list was truncated.</p>}
      {shownVisual.data.warnings?.length ? <ul aria-label="Comparison notes">{shownVisual.data.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul>:null}
    </div>}
    {textError && <p role="alert">{textError}</p>}
    {diff && <details><summary>Document edits</summary><ul>{diff.chunks.length===0 && <li>No document edits</li>}{diff.chunks.map((c)=><li key={c.version}>v{c.version}{c.reason ? ` ${c.reason}`:""}: {c.ops.map(opSummary).join(", ")}</li>)}{diff.truncated && <li>Edit history truncated</li>}</ul></details>}
  </section>;
}
