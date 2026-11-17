import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { nodeLabel, type RNode, type UiEditOp } from "../../lib/uiDesigner";

type Icon={id:string;name:string;tags:string[];categories:string[]};
type Source={name:string;version:string;commit:string;license:string};
type Catalog={revision:number;source:Source;icons:Icon[];total:number;next_cursor:string|null};
type Detail=Icon&{revision?:number;view_box:number[];d:string;paths:{d:string;fill:boolean}[]};
type Props={pageId:string;pageTitle:string;selected:RNode|null;disabled:boolean;onInsert:(op:UiEditOp)=>void};
const hex=(value:string)=>/^#(?:[a-f0-9]{3}|[a-f0-9]{6}|[a-f0-9]{8})$/i.test(value);
function safeDetail(value:Detail,revision:number){
  const validPath=(d:unknown)=>typeof d==="string"&&d.length>0&&/^[MmLlHhVvCcSsQqTtAaZz0-9eE+\-.,\s]+$/.test(d);
  return value&&(value.revision===undefined||value.revision===revision)&&Array.isArray(value.view_box)&&value.view_box.length===4&&value.view_box.every((n)=>Number.isFinite(n)&&Math.abs(n)<=100000)&&value.view_box[2]>0&&value.view_box[3]>0&&Array.isArray(value.paths)&&value.paths.length>0&&value.paths.length<=32&&value.paths.every((path)=>path&&typeof path.fill==="boolean"&&validPath(path.d))&&value.paths.reduce((sum,path)=>sum+path.d.length,0)<=32768;
}
export default function IconPicker({pageId,pageTitle,selected,disabled,onInsert}:Props){
  const [input,setInput]=useState(""),[query,setQuery]=useState(""),[cursor,setCursor]=useState<string|null>(null),[previous,setPrevious]=useState<(string|null)[]>([]),[retry,setRetry]=useState(0);
  const [catalog,setCatalog]=useState<Catalog|null>(null),[loading,setLoading]=useState(true),[catalogError,setCatalogError]=useState("");
  const [chosen,setChosen]=useState<string|null>(null),[detail,setDetail]=useState<Detail|null>(null),[detailError,setDetailError]=useState(""),[detailBusy,setDetailBusy]=useState(false);
  const [size,setSize]=useState("24"),[color,setColor]=useState(()=>getComputedStyle(document.documentElement).getPropertyValue("--ds-accent").trim()),[stroke,setStroke]=useState("2"),[error,setError]=useState("");
  const catalogStamp=useRef("");
  const searchSignature=JSON.stringify([query,cursor,retry]),activeSearch=useRef(searchSignature);activeSearch.current=searchSignature;
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setCatalogError("");setCatalog(null);setChosen(null);setDetail(null);
    api<Catalog>(`/api/ui-designer/icons?${new URLSearchParams({q:query,limit:"24",...(cursor?{cursor}:{})})}`,{signal:controller.signal})
      .then((result)=>{if(controller.signal.aborted||activeSearch.current!==searchSignature)return;if(!Number.isSafeInteger(result.revision)||result.revision<1||!Array.isArray(result.icons)||result.icons.length>24||result.icons.some((icon)=>typeof icon.id!=="string"||typeof icon.name!=="string")||!result.source||![result.source.name,result.source.version,result.source.commit,result.source.license].every((value)=>typeof value==="string"))throw new Error("The icon catalog was invalid.");catalogStamp.current=searchSignature;setCatalog(result);})
      .catch((e)=>{if(!controller.signal.aborted&&activeSearch.current===searchSignature)setCatalogError(apiErrorMessage(e,e instanceof Error?e.message:"Could not load icons."));})
      .finally(()=>{if(!controller.signal.aborted&&activeSearch.current===searchSignature)setLoading(false);});
    return()=>controller.abort();
  },[query,cursor,retry,searchSignature]);
  useEffect(()=>{
    if(!chosen||!catalog)return;const controller=new AbortController();setDetail(null);setDetailError("");setDetailBusy(true);
    api<Detail>(`/api/ui-designer/icons/${encodeURIComponent(chosen)}?${new URLSearchParams({revision:String(catalog.revision)})}`,{signal:controller.signal})
      .then((result)=>{if(controller.signal.aborted)return;if(result.id!==chosen||!safeDetail(result,catalog.revision))throw new Error("The icon preview contained unsupported data.");setDetail(result);})
      .catch((e)=>{if(!controller.signal.aborted)setDetailError(apiErrorMessage(e,e instanceof Error?e.message:"Could not preview this icon."));})
      .finally(()=>{if(!controller.signal.aborted)setDetailBusy(false);});
    return()=>controller.abort();
  },[chosen,catalog]);
  const frame=selected?.type==="frame"&&!selected.of&&!selected.id.includes("/")?selected:null;
  const parent=frame?.id||pageId,target=frame?nodeLabel(frame):pageTitle;
  const previewSize=Number(size)>=16&&Number(size)<=128?Number(size):24,previewStroke=Number(stroke)>=.5&&Number(stroke)<=4?Number(stroke):2,previewColor=hex(color.trim())?color.trim():"var(--ds-accent)";
  const detailCurrent=detail&&detail.id===chosen&&catalogStamp.current===searchSignature&&catalog?detail:null;
  function insert(){
    if(disabled||!detailCurrent||!catalog)return;const pixels=Number(size),weight=Number(stroke);
    if(!size.trim()||!Number.isFinite(pixels)||pixels<16||pixels>128){setError("Icon size must be from 16 to 128.");return;}
    if(!stroke.trim()||!Number.isFinite(weight)||weight<.5||weight>4){setError("Stroke width must be from 0.5 to 4.");return;}
    if(!hex(color.trim())){setError("Choose a hexadecimal icon colour.");return;}
    const id=`icon-${Array.from(crypto.getRandomValues(new Uint8Array(8)),(byte)=>byte.toString(16).padStart(2,"0")).join("")}`;
    setError("");onInsert({op:"icon",icon:detailCurrent.id,revision:catalog.revision,parent,id,size:pixels,color:color.trim(),stroke_width:weight});
  }
  return <section aria-label="Native icons"><h2>Icons</h2>
    <form className="ud-actions" onSubmit={(e)=>{e.preventDefault();setPrevious([]);setCursor(null);setQuery(input.trim());setRetry((n)=>n+1);}}><label>Search icons<Input aria-label="Search icons" maxLength={80} value={input} onChange={(e)=>setInput(e.target.value)} /></label><Button type="submit">Search icons</Button></form>
    {loading&&<p role="status">Loading icons…</p>}{catalogError&&<p role="alert">{catalogError} <Button onClick={()=>setRetry((n)=>n+1)}>Reload icons</Button></p>}
    {catalog&&catalogStamp.current===searchSignature&&<>
      <p className="muted">{catalog.total} matching icons · catalog revision {catalog.revision}</p>
      {catalog.icons.length===0&&<p role="status">No icons match this search.</p>}
      <div className="ud-actions" aria-label="Icon search results">{catalog.icons.map((icon)=><Button variant={icon.id===chosen?"primary":"secondary"} key={icon.id} onClick={()=>setChosen(icon.id)} aria-pressed={icon.id===chosen} aria-label={`Select icon ${icon.id}`}>{icon.name}</Button>)}</div>
      <div className="ud-actions"><Button disabled={loading||!previous.length} onClick={()=>{setCursor(previous.at(-1)??null);setPrevious((pages)=>pages.slice(0,-1));}}>Previous icons</Button><Button disabled={loading||!catalog.next_cursor} onClick={()=>{setPrevious((pages)=>[...pages,cursor]);setCursor(catalog.next_cursor);}}>Next icons</Button></div>
      <p className="muted">{catalog.source.name} {catalog.source.version} · {catalog.source.license} · source {catalog.source.commit.slice(0,12)}. <a href="https://lucide.dev/license" target="_blank" rel="noopener noreferrer">Lucide license and attribution</a></p>
    </>}
    {detailBusy&&<p role="status">Loading icon preview…</p>}{detailError&&<p role="alert">{detailError}</p>}
    {detailCurrent&&<section aria-label="Selected icon preview"><h3>{detailCurrent.name}</h3>
      <svg role="img" aria-label={`Preview ${detailCurrent.name}`} viewBox={detailCurrent.view_box.join(" ")} width={previewSize} height={previewSize} style={{maxWidth:"100%"}}>{detailCurrent.paths.map((path,index)=><path key={index} d={path.d} fill={path.fill?previewColor:"none"} stroke={previewColor} strokeWidth={previewStroke} strokeLinecap="round" strokeLinejoin="round" />)}</svg>
      <p>Destination: {target} ({parent})</p>
      <div className="ud-actions"><label>Icon size<Input aria-label="Icon size" inputMode="decimal" value={size} disabled={disabled} onChange={(e)=>setSize(e.target.value)} /></label><label>Icon colour<Input aria-label="Icon colour" value={color} disabled={disabled} onChange={(e)=>setColor(e.target.value)} /></label><label>Icon stroke width<Input aria-label="Icon stroke width" inputMode="decimal" value={stroke} disabled={disabled} onChange={(e)=>setStroke(e.target.value)} /></label></div>
      <p className="muted">Size scales native geometry and stroke proportionally; stroke width is measured at the 24-unit source size.</p>
      <Button disabled={disabled} onClick={insert}>Insert icon</Button>
    </section>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
