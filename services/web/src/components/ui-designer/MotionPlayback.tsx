import { useEffect, useRef, useState } from "react";
import { api, apiErrorMessage } from "../../api";
import { motionEase, validateMotion, type MotionInput, type MotionManifest } from "../../lib/uiMotion";

type Props = { base: string; input: MotionInput; reduced: boolean; onDone?: () => void; onNotes?: (notes: string[]) => void; onStart?: () => void };
/** Only renderer-provided, bounded PNG data is decoded; no SVG, URLs or authored scripts. */
export default function MotionPlayback({base,input,reduced,onDone,onNotes,onStart}:Props) {
  const canvas=useRef<HTMLCanvasElement>(null), done=useRef(onDone); done.current=onDone;
  const notes=useRef(onNotes); notes.current=onNotes;
  const started=useRef(onStart);started.current=onStart;
  const [status,setStatus]=useState("Loading motion…"),[warnings,setWarnings]=useState<string[]>([]);
  const body=JSON.stringify(input);
  useEffect(()=>{
    const controller=new AbortController(); let frame=0, active=true;
    const images: HTMLImageElement[]=[], decoded=new Map<string,Promise<HTMLImageElement>>();
    setStatus("Loading motion…");setWarnings([]);
    function load(data:string):Promise<HTMLImageElement> { const existing=decoded.get(data);if(existing)return existing;const result=new Promise<HTMLImageElement>((resolve,reject)=>{const image=new Image();images.push(image);image.onload=()=>active ? resolve(image):reject(new Error("Cancelled"));image.onerror=()=>reject(new Error("Could not load a motion image."));image.src=`data:image/png;base64,${data}`;});decoded.set(data,result);return result; }
    async function run() {
      try {
        const manifest=validateMotion(await api<MotionManifest>(`${base}/motion/preview`,{method:"POST",signal:controller.signal,body}));
        const entries=await Promise.all(Object.entries(manifest.assets).map(async([key,data])=>[key,await load(data)] as const));
        const [first,last]=await Promise.all([load(manifest.from_image),load(manifest.to_image)]);
        if(!active || !canvas.current) return;
        const assets=Object.fromEntries(entries), surface=canvas.current;surface.width=manifest.width;surface.height=manifest.height;
        const ctx=surface.getContext("2d");if(!ctx) throw new Error("Motion playback is unavailable in this browser.");
        setWarnings(manifest.warnings);notes.current?.(manifest.warnings);setStatus("");started.current?.();
        const blend=document.createElement("canvas"); const blendContext=blend.getContext("2d")!;
        let start:number|null=null;
        function draw(now:number) {
          if(!active) return;
          if(start===null)start=now;
          const progress=reduced ? 1:Math.min(1,(now-start)/manifest.duration_ms),t=motionEase(progress,manifest.easing);
          ctx!.clearRect(0,0,surface.width,surface.height);
          if(progress===0 || progress===1)ctx!.drawImage(progress===0?first:last,0,0,surface.width,surface.height);
          else for(const layer of manifest.layers) {
            const a=layer.from ?? {...layer.to!,opacity:0},b=layer.to ?? {...layer.from!,opacity:0};
            const lerp=(x:number,y:number)=>x+(y-x)*t;
            const w=lerp(a.w,b.w),h=lerp(a.h,b.h),opacity=lerp(a.opacity,b.opacity);
            ctx!.save();ctx!.translate(lerp(a.x+a.w/2,b.x+b.w/2),lerp(a.y+a.h/2,b.y+b.h/2));ctx!.rotate(lerp(a.rotate,b.rotate)*Math.PI/180);
            if(a.asset===b.asset) {ctx!.globalAlpha=opacity;ctx!.drawImage(assets[a.asset],-w/2,-h/2,w,h);}
            else {
              const from=assets[a.asset],to=assets[b.asset];blend.width=Math.max(from.width,to.width);blend.height=Math.max(from.height,to.height);
              blendContext.globalAlpha=1-t;blendContext.drawImage(from,0,0,blend.width,blend.height);
              blendContext.globalCompositeOperation="lighter";blendContext.globalAlpha=t;blendContext.drawImage(to,0,0,blend.width,blend.height);
              ctx!.globalAlpha=opacity;ctx!.drawImage(blend,-w/2,-h/2,w,h);
            }
            ctx!.restore();
          }
          if(progress<1)frame=requestAnimationFrame(draw);else done.current?.();
        }
        frame=requestAnimationFrame(draw);
      }catch(e){if(active){const message=apiErrorMessage(e,e instanceof Error?e.message:"Could not preview motion.");setStatus(message);notes.current?.([`${message} Showing the destination frame instead.`]);done.current?.();}}
    }
    void run();return()=>{active=false;controller.abort();cancelAnimationFrame(frame);images.forEach((image)=>{image.onload=null;image.onerror=null;image.removeAttribute("src");});};
  },[base,body,reduced]);
  return <div className="ud-motion-playback"><canvas ref={canvas} aria-label="Motion preview" style={{display:status?"none":"block",width:"100%",height:"100%",objectFit:"contain"}} />{status && !onNotes && <p role="status">{status}</p>}{warnings.length>0 && !onNotes && <ul aria-label="Motion notes">{warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}</div>;
}
