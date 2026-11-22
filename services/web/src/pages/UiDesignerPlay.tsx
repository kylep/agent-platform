import { useDesignerAccess } from "../lib/useDesignerAccess";
import { watchUiDesign } from "../lib/uiDesignLive";
import MotionPlayback from "../components/ui-designer/MotionPlayback";
import type { MotionInput } from "../lib/uiMotion";
import "../components/ui-designer/Prototype.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../api";
import { UD, nodeLabel, type Resolved, type UiPrototypeLink } from "../lib/uiDesigner";
import { nodeBox, prototypeFrames, prototypeHotspots } from "../lib/prototype";
import { useTitle } from "../lib/title";

export default function UiDesignerPlay() {
  const { slug = "" } = useParams(); const [params] = useSearchParams();
  const requestedVersion = params.get("version"), mode = params.get("mode") || "dark", requestedFrame = params.get("frame");
  const [version, setVersion] = useState<number | null>(null), [tree,setTree] = useState<Resolved | null>(null);
  const [frameId,setFrameId] = useState(""), [back,setBack] = useState<string[]>([]);
  const [error,setError] = useState(""), [ready,setReady] = useState(false), [loaded,setLoaded] = useState(false), [paused,setPaused] = useState(false);
  const [previous,setPrevious] = useState<string | null>(null);
  const [epoch,setEpoch] = useState(0), [effect,setEffect] = useState({ transition:"instant",duration:0 });
  const [reduced,setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [motion,setMotion]=useState<MotionInput|null>(null),[motionNotes,setMotionNotes]=useState<string[]>([]);
  const transitionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chain = useRef(0), navigation = useRef(0), hoverLocked = useRef(false), pointer = useRef<{x:number;y:number} | null>(null);
  const invalidateNavigation = useCallback(() => {
    navigation.current++;
    if (transitionTimer.current) clearTimeout(transitionTimer.current);
    transitionTimer.current = null;
  }, []);
  useTitle("Prototype", "UI Designer");
  const base = `${UD}/${encodeURIComponent(slug)}`;
  const {canRead,checking,unavailable,error:accessError,reload:reloadAccess}=useDesignerAccess(base);
  const retainedPin=useRef<{scope:string;version:number}|null>(null);
  useEffect(() => {
    const media=window.matchMedia("(prefers-reduced-motion: reduce)"); const update=()=>setReduced(media.matches);
    media.addEventListener("change",update); return ()=>media.removeEventListener("change",update);
  },[]);
  useEffect(()=>watchUiDesign(slug,(event)=>{if(event.type==="access")reloadAccess();}),[slug,reloadAccess]);
  useEffect(() => {
    if(!canRead){invalidateNavigation();setTree(null);setPrevious(null);setMotion(null);setVersion(null);setReady(false);setLoaded(false);return;}
    const controller = new AbortController();
    let cancelled=false; invalidateNavigation(); chain.current=0; hoverLocked.current=false;
    setMotion(null);setMotionNotes([]);
    setTree(null);setPrevious(null);setVersion(null);setReady(false);setLoaded(false);setError("");setPaused(false);setBack([]);
    async function load() {
      try {
        if (!["dark", "light"].includes(mode)) throw new Error("Choose dark or light mode to play.");
        if (requestedVersion && (!/^[1-9]\d*$/.test(requestedVersion) || !Number.isSafeInteger(Number(requestedVersion)))) throw new Error("Choose a valid version to play.");
        const pinScope=`${base}:${requestedVersion}:${mode}:${requestedFrame}`;
        const pinned = requestedVersion ? Number(requestedVersion) : retainedPin.current?.scope===pinScope ? retainedPin.current.version : (await api<{version:number}>(`${base}/versions`, { signal: controller.signal })).version;
        if (cancelled) return;
        retainedPin.current={scope:pinScope,version:pinned};
        const resolved=await api<Resolved>(`${base}/resolved?${new URLSearchParams({version:String(pinned),mode})}`, { signal: controller.signal });
        if (cancelled) return;
        const frames=prototypeFrames(resolved);
        if (!frames.length) throw new Error("This version has no frames to play.");
        if (requestedFrame && !frames.some((f)=>f.id===requestedFrame)) throw new Error("The requested frame is unavailable in this version.");
        setVersion(pinned);setTree(resolved);setFrameId(requestedFrame || frames[0].id);setEpoch((n)=>n+1);
      } catch(e) { if (!cancelled) setError(e instanceof Error && !/^\d+:/.test(e.message) ? e.message : apiErrorMessage(e,"Could not load prototype.")); }
    }
    void load(); return ()=>{cancelled=true; controller.abort(); invalidateNavigation();};
  },[base,requestedVersion,mode,requestedFrame,invalidateNavigation,canRead]);
  useEffect(()=>{if(reduced && motion){setMotion(null);setPrevious(null);setReady(loaded);}},[reduced,motion,loaded]);
  const frames=useMemo(()=>tree ? prototypeFrames(tree):[],[tree]);
  const frame=frames.find((f)=>f.id===frameId);
  const hotspots=useMemo(()=>frame ? prototypeHotspots(frame):[],[frame]);
  const tooMany=hotspots.length>1000;
  const targets=useMemo(()=>new Set(frames.map((f)=>f.id)),[frames]);
  const activateRef=useRef<(link:UiPrototypeLink,automatic?:boolean)=>void>(()=>{});
  function pictureUrl(id:string) { return `${base}/render?${new URLSearchParams({node:id,version:String(version),mode})}`; }
  function navigate(target:string,link?:UiPrototypeLink,automatic=false,history?:string[],force=false) {
    if (!targets.has(target)) {setError("The destination frame is unavailable in this version.");return;}
    if(target===frameId && !force) return;
    if(automatic && chain.current>=25) {setPaused(true);return;}
    chain.current=automatic ? chain.current+1:0;
    setMotion(null);setMotionNotes([]);
    invalidateNavigation(); setPrevious(ready ? pictureUrl(frameId) : null); hoverLocked.current=true;setReady(false);setLoaded(false);setError("");setPaused(false);
    setBack(history ?? [...back,frameId]);setFrameId(target);setEpoch((n)=>n+1);
    const transition=link?.transition || "instant";
    const requestedDuration=link?.duration_ms ?? (transition==="smart-animate" ? 800:200);
    if(transition==="smart-animate" && !reduced && requestedDuration>0 && version) {
      setMotion({version,mode,from_node:frameId,to_node:target,duration_ms:Math.max(200,requestedDuration),easing:link?.easing ?? "ease-in-out",fps:24,max_edge:720});
      if(requestedDuration<200)setMotionNotes(["Smart animation uses a minimum duration of 200 ms."]);
    }
    setEffect({transition:reduced ? "instant":transition,duration:reduced || transition==="instant" ? 0 : requestedDuration});
  }
  activateRef.current=(link,automatic=false)=>navigate(link.target,link,automatic);
  useEffect(()=>{
    if(!ready || paused || tooMany) return;
    const token=navigation.current;
    const timers=hotspots.filter(({node})=>node.link_to?.trigger==="after-delay" && node.link_to.target!==frameId).map(({node})=>setTimeout(()=>{
      if(token===navigation.current) activateRef.current(node.link_to!,true);
    },node.link_to?.delay_ms ?? 1000));
    return ()=>timers.forEach(clearTimeout);
  },[ready,paused,tooMany,hotspots,frameId,epoch]);
  const imageToken = navigation.current;
  const crop=frame ? nodeBox(frame):{x:0,y:0,w:1,h:1};
  const exit=`/ui-designer/${encodeURIComponent(slug)}?${new URLSearchParams({...(version ? {version:String(version)}:{}),mode})}`;
  if(!canRead)return <div className="ud-prototype"><div className="page-header"><h1>Prototype · {slug}</h1><Link to={`/ui-designer/${encodeURIComponent(slug)}`}>Exit prototype</Link></div>{checking ? <p role="status">Checking document access…</p>:<><p role="alert">{unavailable ? "This design is unavailable or private.":accessError || "Could not load this design."}</p><Button onClick={reloadAccess}>Retry document access</Button></>}</div>;
  return <div className="ud-prototype">
    <div className="page-header"><h1>Prototype · {slug}{version ? ` · v${version}`:""}</h1><Link to={exit}>Exit prototype</Link></div>
    <div className="ud-prototype-controls">
      <Button disabled={!back.length || !ready} onClick={()=>navigate(back.at(-1)!,undefined,false,back.slice(0,-1))}>Back</Button>
      <Button disabled={!frames.length} onClick={()=>{chain.current=0;setPaused(false);navigate(requestedFrame || frames[0].id,undefined,false,[],true);}}>Restart</Button>
      <label>Frame <Select aria-label="Prototype frame" value={frameId} disabled={!frames.length} onChange={(e)=>navigate(e.target.value)}>{frames.map((f)=><option key={f.id} value={f.id}>{nodeLabel(f)}</option>)}</Select></label>
    </div>
    {error && <p role="alert">{error}</p>}
    {!tree && !error && <p role="status">Loading prototype…</p>}
    {tooMany && <p role="alert">This frame has more than 1,000 interactive areas. Reduce its links to play it.</p>}
    {paused && <p role="status">Automatic playback paused after 25 transitions. <Button onClick={()=>{chain.current=0;setPaused(false);}}>Resume playback</Button></p>}
    {frame && !tooMany && <>
      {!ready && !error && <p role="status">Loading frame…</p>}
      <div className="ud-prototype-stage" style={{aspectRatio:`${Math.max(1,crop.w)} / ${Math.max(1,crop.h)}`,maxWidth:`min(100%, ${Math.max(1,crop.w)}px)`}} onPointerMove={(e)=>{const next={x:e.clientX,y:e.clientY};if(pointer.current && (next.x!==pointer.current.x || next.y!==pointer.current.y)) hoverLocked.current=false;pointer.current=next;}} onPointerLeave={()=>{hoverLocked.current=false;pointer.current=null;}}>
        {previous && <img className="ud-prototype-previous" src={previous} alt="" aria-hidden="true" />}
        <img key={`${base}:${version}:${frameId}:${epoch}`} className="ud-prototype-picture" alt={`Prototype frame ${nodeLabel(frame)}`} data-transition={loaded ? effect.transition : "instant"} data-loaded={loaded && !motion} style={{animationDuration:`${effect.duration}ms`}} src={pictureUrl(frameId)}
          onLoad={()=>{if (imageToken !== navigation.current) return; setLoaded(true); if (motion && !reduced) return; if (effect.duration > 0 && effect.transition!=="smart-animate") transitionTimer.current=setTimeout(()=>{if(imageToken===navigation.current){setReady(true);setPrevious(null);}},effect.duration); else {setReady(true);setPrevious(null);}}} onError={()=>{if(imageToken !== navigation.current)return;setReady(false);setError("Could not render this frame. Choose another frame or restart.");}} />
        {loaded && motion && <div className="ud-prototype-motion"><MotionPlayback key={`${base}:${version}:${epoch}`} base={base} input={motion} reduced={reduced} onStart={()=>{if(imageToken===navigation.current)setPrevious(null);}} onNotes={setMotionNotes} onDone={()=>{if(imageToken===navigation.current){setReady(true);setPrevious(null);setMotion(null);}}} /></div>}
        {ready && hotspots.filter(({node})=>node.link_to?.trigger!=="after-delay").map(({node,box},i)=><button key={node.id} type="button" className="ud-prototype-hotspot" aria-label={`Go to ${node.link_to!.target} from ${nodeLabel(node)}`} data-trigger={node.link_to!.trigger || "tap"}
          style={{left:`${100*(box.x-crop.x)/Math.max(1,crop.w)}%`,top:`${100*(box.y-crop.y)/Math.max(1,crop.h)}%`,width:`${100*box.w/Math.max(1,crop.w)}%`,height:`${100*box.h/Math.max(1,crop.h)}%`,zIndex:i+1}}
          onClick={()=>{if (!node.link_to?.trigger || node.link_to.trigger==="tap") activateRef.current(node.link_to!);}} onPointerEnter={()=>{if(node.link_to?.trigger==="hover" && !hoverLocked.current) activateRef.current(node.link_to,true);}} onFocus={()=>{if(node.link_to?.trigger==="hover") activateRef.current(node.link_to,true);}} />)}
      </div>
      {hotspots.some(({node})=>node.bounds || node.rotate) && <p className="muted">Rotated interactive areas use approximate rectangular bounds.</p>}
      {motionNotes.length>0 && <ul aria-label="Motion notes">{motionNotes.map((note,index)=><li key={index}>{note}</li>)}</ul>}
      {reduced && <p className="muted">Reduced motion: transitions appear instantly.</p>}
    </>}
  </div>;
}
