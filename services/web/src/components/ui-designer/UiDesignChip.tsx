import "./UiDesignChip.css";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { readUiHead, thumbnailTask, watchUiDesign } from "../../lib/uiDesignLive";
const MAX_THUMB_BYTES=2*1024*1024;
async function thumbnail(url:string,signal:AbortSignal){
  const response=await fetch(url,{credentials:"include",signal});
  if(!response.ok||response.headers.get("content-type")?.split(";")[0].trim()!=="image/png"||Number(response.headers.get("content-length"))>MAX_THUMB_BYTES)throw new Error("Unavailable design");
  const chunks:Uint8Array[]=[];let count=0;
  if(response.body){const reader=response.body.getReader();try{while(true){const {done,value}=await reader.read();if(done)break;count+=value.byteLength;if(count>MAX_THUMB_BYTES){await reader.cancel();throw new Error("Preview too large");}chunks.push(value);}}finally{reader.releaseLock();}}
  else{const data=new Uint8Array(await response.arrayBuffer());if(data.length>MAX_THUMB_BYTES)throw new Error("Preview too large");chunks.push(data);count=data.length;}
  const bytes=new Uint8Array(count);let offset=0;for(const part of chunks){bytes.set(part,offset);offset+=part.length;}
  if(![137,80,78,71,13,10,26,10].every((byte,index)=>bytes[index]===byte))throw new Error("Invalid preview");
  return URL.createObjectURL(new Blob([bytes],{type:"image/png"}));
}
export function UiDesignChip({slug,node,preview=true}:{slug:string;node?:string;preview?:boolean}){
  const host=useRef<HTMLDivElement>(null),observed=useRef(0),urlRef=useRef<string|null>(null);
  const [visible,setVisible]=useState(false),[revision,setRevision]=useState(0),[version,setVersion]=useState<number|null>(null),[image,setImage]=useState<string|null>(null),[unavailable,setUnavailable]=useState(false);
  const identity=`${slug}:${node??""}`,imageIdentity=useRef(""),generation=useRef(0);
  const currentImage=imageIdentity.current===identity?image:null;
  const currentVersion=imageIdentity.current===identity?version:null;
  const href=`/ui-designer/${encodeURIComponent(slug)}${node?`?${new URLSearchParams({node})}`:""}`;
  useEffect(()=>{if(!preview)return;if(typeof IntersectionObserver==="undefined"){setVisible(true);return;}const observer=new IntersectionObserver((entries)=>setVisible(entries.some((entry)=>entry.isIntersecting)),{rootMargin:"160px"});if(host.current)observer.observe(host.current);return()=>observer.disconnect();},[preview]);
  useEffect(()=>{observed.current=0;setVersion(null);setImage(null);setUnavailable(false);},[slug,node]);
  useEffect(()=>{if(!visible||!preview)return;return watchUiDesign(slug,(event)=>{generation.current++;if(event.type==="deleted" || event.type==="access"){observed.current=0;setVersion(null);setImage(null);if(urlRef.current)URL.revokeObjectURL(urlRef.current);urlRef.current=null;}else if(Number.isSafeInteger(event.version)&&event.version!>0)observed.current=event.version!;setRevision((n)=>n+1);});},[slug,visible,preview]);
  useEffect(()=>{
    if(!visible||!preview)return;const controller=new AbortController(),token=generation.current;let resource:string|null=null;setUnavailable(false);setImage(null);
    thumbnailTask(controller.signal,async()=>{
      const head=await readUiHead(slug,controller.signal),current=Math.max(head,observed.current);observed.current=current;
      const query=new URLSearchParams({version:String(current),scale:"0.5",max_edge:"320",...(node?{node}:{})});
      resource=await thumbnail(`/api/ui-designer/docs/${encodeURIComponent(slug)}/render?${query}`,controller.signal);
      if(controller.signal.aborted || token!==generation.current){URL.revokeObjectURL(resource);resource=null;return;}
      urlRef.current=resource;imageIdentity.current=identity;setImage(resource);setVersion(current);
    }).catch(()=>{if(!controller.signal.aborted)setUnavailable(true);});
    return()=>{controller.abort();if(resource)URL.revokeObjectURL(resource);urlRef.current=null;};
  },[slug,node,preview,visible,revision,identity]);
  return <div ref={host} className="ud-reference" aria-label={`Design ${slug}${node?` node ${node}`:""}`}>
    <Link to={href}>UI Designer · {slug}{node?` #${node}`:""}</Link>
    {preview&&<>{currentImage&&!unavailable?<Link to={href} aria-label={`Open design ${slug}${node?` node ${node}`:""}`}><img key={currentImage} src={currentImage} alt={`Latest design preview for ${slug}${node?` node ${node}`:""}`} loading="lazy" onError={()=>{if(urlRef.current===currentImage)setUnavailable(true);}}/></Link>:<p className="muted">{unavailable?"Design unavailable or private.":visible?"Loading design preview…":"Design preview loads when visible."}</p>}{currentVersion&&!unavailable&&<small className="muted">Live · v{currentVersion}</small>}</>}
  </div>;
}
