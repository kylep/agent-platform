import { useEffect, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { nodeLabel, type RNode, type RPage, type UiEditOp, type UiInspection } from "../../lib/uiDesigner";
import PathEditor from "./PathEditor";

type Asset={sha256:string;mime:string;width:number|null;height:number|null};
type Props={base:string;version:number;page:RPage;selected:RNode|null;inspection:UiInspection|null;disabled:boolean;onCommit:(ops:UiEditOp[],inverse:UiEditOp[])=>void;onSelect:(node:RNode)=>void;onUpdate:(id:string,changes:Record<string,unknown>)=>void};
function parentOf(nodes:RNode[],id:string,parent:string,page:string):{id:string;nodes:RNode[]}|null{for(const node of nodes){if(node.id===id)return{id:parent||page,nodes};const hit=parentOf(node.children??[],id,node.id,page);if(hit)return hit;}return null;}
const assetHash=(value:string)=>/^[0-9a-f]{64}$/.test(value);
export default function VectorTools({base,version,page,selected,inspection,disabled,onCommit,onSelect,onUpdate}:Props){
  const authored=inspection?.id===selected?.id?inspection?.authored:null;
  const [creating,setCreating]=useState(false),[error,setError]=useState(""),[operands,setOperands]=useState<string[]>([]),[kind,setKind]=useState<"union"|"subtract"|"intersect"|"exclude">("union");
  const [assets,setAssets]=useState<Asset[]>([]),[assetCursor,setAssetCursor]=useState<string|null>(null),[assetsBusy,setAssetsBusy]=useState(false),[assetError,setAssetError]=useState("");
  const [image,setImage]=useState(""),[fit,setFit]=useState("cover");
  const newId=useRef(""),newParent=useRef(""),newWidth=useRef(240),newHeight=useRef(160),newAbsolute=useRef(false),newContext=useRef({page:"",selection:""}),creationSubmitted=useRef(false);
  const assetRequest=useRef<AbortController|null>(null),assetScope=useRef(`${base}:${version}`);assetScope.current=`${base}:${version}`;
  const group=selected?parentOf(page.children??[],selected.id,"",page.id):null;
  const candidates=(group?.nodes??[]).filter((node)=>["path","rect","ellipse"].includes(node.type||"")&&!node.id.includes("/")&&!node.of);
  const visibleCandidates=[...candidates.filter((node)=>node.id===selected?.id),...candidates.filter((node)=>node.id!==selected?.id)].slice(0,50);
  const candidateIds=candidates.map((node)=>node.id).join(",");
  const selectedId=selected?.id||"",initialOperand=candidates.some((node)=>node.id===selectedId)?selectedId:"";
  useEffect(()=>{setOperands(initialOperand?[initialOperand]:[]);setError("");},[initialOperand,group?.id,candidateIds]);
  useEffect(()=>{
    if(creating&&(page.id!==newContext.current.page || (selectedId!==newContext.current.selection && selectedId!==newId.current && !(creationSubmitted.current&&!selectedId)))){setCreating(false);setError("The new path draft was closed because its canvas context changed.");}
  },[creating,page.id,selectedId]);
  useEffect(()=>{if(creating&&selected?.id===newId.current&&authored?.id===newId.current)setCreating(false);},[creating,selected?.id,authored?.id]);
  useEffect(()=>{setAssets([]);setAssetCursor(null);setAssetError("");setAssetsBusy(false);return()=>assetRequest.current?.abort();},[base,version]);
  useEffect(()=>{setImage("");},[selected?.id]);
  const pathSelected=selected?.type==="path"&&!!authored;
  function start(){
    newId.current=`path-${Array.from(crypto.getRandomValues(new Uint8Array(8)),(n)=>n.toString(16).padStart(2,"0")).join("")}`;
    const frame=selected?.type==="frame"&&!selected.id.includes("/")&&!selected.of?selected:null;
    newParent.current=frame?.id||page.id;newWidth.current=Math.max(1,frame?.w||240);newHeight.current=Math.max(1,frame?.h||160);newAbsolute.current=!!frame&&!!authored?.layout;newContext.current={page:page.id,selection:selected?.id||""};creationSubmitted.current=false;setCreating(true);setError("");
  }
  function createPath(d:string){
    const color=getComputedStyle(document.documentElement).getPropertyValue("--ds-accent").trim();
    if(!/^#(?:[a-f0-9]{3}|[a-f0-9]{6}|[a-f0-9]{8})$/i.test(color)){setError("The current pen colour is unavailable.");return;}
    creationSubmitted.current=true;
    const node={id:newId.current,type:"path",title:"New path",x:0,y:0,w:newWidth.current,h:newHeight.current,d,stroke:{color,width:2},...(newAbsolute.current?{absolute:true}:{})};
    onCommit([{op:"insert",parent:newParent.current,node}],[{op:"remove",ids:[node.id]}]);onSelect(node);
  }
  async function loadAssets(cursor?:string){
    if(assetsBusy)return;const controller=new AbortController();assetRequest.current=controller;const requested=`${base}:${version}`;setAssetsBusy(true);setAssetError("");
    try{
      const result=await api<{assets:Asset[];next_cursor:string|null}>(`${base}/assets?${new URLSearchParams({version:String(version),limit:"50",...(cursor?{cursor}:{})})}`,{signal:controller.signal});
      if(controller.signal.aborted||assetScope.current!==requested)return;
      if(!Array.isArray(result.assets)||result.assets.some((asset)=>!assetHash(asset.sha256)))throw new Error("The document image list was invalid.");
      setAssets((old)=>[...(cursor?old:[]),...result.assets].slice(0,200));setAssetCursor(result.next_cursor);
    }catch(e){if(!controller.signal.aborted&&assetScope.current===requested)setAssetError(apiErrorMessage(e,e instanceof Error?e.message:"Could not load document images."));}
    finally{if(!controller.signal.aborted&&assetScope.current===requested)setAssetsBusy(false);}
  }
  function boolean(){if(operands.length<2||operands.length>8){setError("Choose between two and eight operands in order.");return;}onCommit([{op:"boolean",kind,ids:operands}],[]);}
  return <section aria-label="Vector tools"><h2>Vector tools</h2><Button disabled={disabled || (selected?.type==="frame" && (!authored || inspection?.version!==version))} onClick={start}>New path</Button>
    {creating?<PathEditor key={newId.current} source="" width={newWidth.current} height={newHeight.current} disabled={disabled} creating onSave={createPath} onCancel={()=>setCreating(false)}/>:pathSelected?<PathEditor key={selected.id} source={String(authored.d??"")} width={selected.w??240} height={selected.h??160} disabled={disabled||inspection?.version!==version} onSave={(d)=>onUpdate(selected.id,{d})}/>:null}
    {candidates.length>0&&<fieldset disabled={disabled}><legend>Boolean operands</legend><p className="muted">Select 2–8 sibling shapes in order. The first supplies the style and keeps its ID; subtraction follows this order.</p>
      {visibleCandidates.map((node)=><label key={node.id} style={{display:"block"}}><input type="checkbox" checked={operands.includes(node.id)} aria-label={`Boolean operand ${nodeLabel(node)}`} onChange={(e)=>{if(e.target.checked&&operands.length>=8){setError("Choose no more than eight operands.");return;}setOperands((old)=>e.target.checked?[...old,node.id]:old.filter((id)=>id!==node.id));setError("");}}/> {nodeLabel(node)}{operands.includes(node.id)?` · ${operands.indexOf(node.id)+1}`:""}</label>)}
      {candidates.length>50&&<p className="muted">Showing the first 50 sibling shapes.</p>}
      <label>Boolean operation<Select aria-label="Boolean operation" value={kind} onChange={(e)=>setKind(e.target.value as typeof kind)}><option value="union">Union</option><option value="subtract">Subtract</option><option value="intersect">Intersect</option><option value="exclude">Exclude</option></Select></label>
      <Button disabled={operands.length<2} onClick={boolean}>Apply boolean</Button>
    </fieldset>}
    {pathSelected&&<section aria-label="Path image fill"><h3>Image fill</h3><Button disabled={assetsBusy} onClick={()=>loadAssets()}>Load placed images</Button>{assetsBusy&&<p role="status">Loading document images…</p>}{assetError&&<p role="alert">{assetError}</p>}
      <label>Placed image<Select aria-label="Path placed image" value={image} disabled={disabled} onChange={(e)=>setImage(e.target.value)}><option value="">Choose a placed image</option>{assets.map((asset)=><option key={asset.sha256} value={asset.sha256}>{asset.sha256.slice(0,12)} · {asset.width??"?"} × {asset.height??"?"}</option>)}</Select></label>
      <label>Image fit<Select aria-label="Path image fit" value={fit} disabled={disabled} onChange={(e)=>setFit(e.target.value)}><option value="cover">Cover</option><option value="contain">Contain</option><option value="fill">Fill</option></Select></label>
      <p className="muted">Applying an image replaces the current fill paints. Other path properties stay unchanged.</p>
      <Button disabled={disabled||!assetHash(image)} onClick={()=>onUpdate(selected!.id,{fill:{image,fit}})}>Apply image fill</Button>
      {assetCursor&&assets.length<200&&<Button disabled={assetsBusy} onClick={()=>loadAssets(assetCursor!)}>More placed images</Button>}{assetCursor&&assets.length>=200&&<p className="muted">Showing the first 200 placed images.</p>}
    </section>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
