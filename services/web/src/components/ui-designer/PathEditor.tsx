import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Button } from "@ap/ui/button";
import { Input, Select, Textarea } from "@ap/ui/field";
import { MAX_PATH_POINTS, parseEditablePath, pathData, type EditablePath, type PathPoint } from "../../lib/vectorPath";

type Props={source:string;width:number;height:number;disabled:boolean;creating?:boolean;onSave:(d:string)=>void;onCancel?:()=>void};
export default function PathEditor({source,width,height,disabled,creating=false,onSave,onCancel}:Props){
  const [value,setValue]=useState(source),[dirty,setDirty]=useState(false),[selected,setSelected]=useState(0),[drawing,setDrawing]=useState(creating),[segment,setSegment]=useState("L"),[error,setError]=useState("");
  const [coordinates,setCoordinates]=useState<Record<string,string>>({});
  const parsed=useMemo(()=>parseEditablePath(value),[value]);const point=parsed?.points[selected];
  const svg=useRef<SVGSVGElement>(null),drag=useRef<{index:number;part:"anchor"|"first"|"second";inverse:DOMMatrix}|null>(null);
  const [frozen,setFrozen]=useState<{x:number;y:number;w:number;h:number}|null>(null);
  const currentDraft=useRef({dirty,value});currentDraft.current={dirty,value};
  useEffect(()=>{if(!currentDraft.current.dirty)setValue(source);else if(source===currentDraft.current.value)setDirty(false);},[source]);
  useEffect(()=>{const next:Record<string,string>={};if(point)for(const key of ["x","y",...(point.kind==="C"?["c1x","c1y","c2x","c2y"]:[])])next[key]=String(point[key as keyof PathPoint]);setCoordinates(next);},[point]);
  useEffect(()=>{if(disabled){drag.current=null;setFrozen(null);}},[disabled]);
  const positions=(parsed?.points ?? []).flatMap((p)=>[p.x,p.c1x,p.c2x].filter((n):n is number=>typeof n==="number"));
  const ys=(parsed?.points ?? []).flatMap((p)=>[p.y,p.c1y,p.c2y].filter((n):n is number=>typeof n==="number"));
  const left=Math.min(0,...positions),top=Math.min(0,...ys),right=Math.max(width,1,...positions),bottom=Math.max(height,1,...ys),pad=Math.max(5,(right-left)*.08,(bottom-top)*.08);
  const box=frozen ?? {x:left-pad,y:top-pad,w:right-left+2*pad,h:bottom-top+2*pad};const radius=Math.max(box.w,box.h)/45;
  function update(next:EditablePath){setValue(pathData(next));setDirty(true);setError("");}
  function changePoint(index:number,changes:Partial<PathPoint>){if(!parsed||disabled)return;update({...parsed,points:parsed.points.map((p,i)=>i===index?{...p,...changes}:p)});}
  function local(event:PointerEvent){const matrix=svg.current?.getScreenCTM();if(!matrix)return null;return new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());}
  function begin(event:PointerEvent<SVGCircleElement>,index:number,part:"anchor"|"first"|"second"){
    event.stopPropagation();if(disabled)return;setSelected(index);const matrix=svg.current?.getScreenCTM();if(!matrix)return;
    drag.current={index,part,inverse:matrix.inverse()};setFrozen(box);event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.focus();event.preventDefault();
  }
  function move(event:PointerEvent){const active=drag.current;if(!active||disabled)return;const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(active.inverse);const x=Math.max(-100000,Math.min(100000,p.x)),y=Math.max(-100000,Math.min(100000,p.y));changePoint(active.index,active.part==="anchor"?{x,y}:active.part==="first"?{c1x:x,c1y:y}:{c2x:x,c2y:y});}
  function key(event:KeyboardEvent,index:number,part:"anchor"|"first"|"second"){
    event.stopPropagation();if(disabled||!parsed)return;const amount=event.shiftKey?10:1;const steps:Record<string,[number,number]>={ArrowLeft:[-amount,0],ArrowRight:[amount,0],ArrowUp:[0,-amount],ArrowDown:[0,amount]};const step=steps[event.key];if(!step)return;event.preventDefault();const p=parsed.points[index],xKey=part==="anchor"?"x":part==="first"?"c1x":"c2x",yKey=part==="anchor"?"y":part==="first"?"c1y":"c2y";changePoint(index,{[xKey]:Math.max(-100000,Math.min(100000,p[xKey]!+step[0])),[yKey]:Math.max(-100000,Math.min(100000,p[yKey]!+step[1]))});
  }
  function add(event:PointerEvent){if(event.button!==0||!drawing||disabled||!parsed)return;if(parsed.points.length>=MAX_PATH_POINTS){setError(`Visual editing supports up to ${MAX_PATH_POINTS} points.`);return;}const p=local(event);if(!p)return;if(Math.abs(p.x)>100000||Math.abs(p.y)>100000){setError("Point coordinates must stay between -100000 and 100000.");return;}const previous=parsed.points.at(-1);const next:PathPoint=previous&&segment==="C"?{kind:"C",x:p.x,y:p.y,c1x:previous.x+(p.x-previous.x)/3,c1y:previous.y+(p.y-previous.y)/3,c2x:previous.x+2*(p.x-previous.x)/3,c2y:previous.y+2*(p.y-previous.y)/3}:{kind:previous?"L":"M",x:p.x,y:p.y};update({...parsed,points:[...parsed.points,next]});setSelected(parsed.points.length);}
  function applyCoordinates(){if(!point||!parsed)return;const patch:Record<string,number>={};for(const [name,raw]of Object.entries(coordinates)){const num=Number(raw);if(!raw.trim()||!Number.isFinite(num)||Math.abs(num)>100000){setError("Point coordinates must be finite numbers between -100000 and 100000.");return;}patch[name]=num;}changePoint(selected,patch);}
  return <section aria-label={creating?"Draw a new path":"Edit path points"}><h3>{creating?"New path":"Path points"}</h3>
    <p className="muted">Points use local path coordinates. Node placement and rotation stay unchanged.</p>
    {parsed ? <>
      <div className="ud-actions"><Button disabled={disabled} onClick={()=>setDrawing((on)=>!on)} aria-pressed={drawing}>{drawing?"Stop adding points":"Add points"}</Button><label>New segment<Select aria-label="New path segment" disabled={disabled} value={segment} onChange={(e)=>setSegment(e.target.value)}><option value="L">Line</option><option value="C">Cubic curve</option></Select></label><label><input type="checkbox" checked={parsed.closed} disabled={disabled} onChange={(e)=>update({...parsed,closed:e.target.checked})}/> Closed path</label></div>
      <svg ref={svg} aria-label="Path point editor" role="group" viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} style={{width:"100%",height:240,border:"1px solid var(--ds-border)",touchAction:"none"}} onPointerDown={add} onPointerMove={move} onPointerUp={()=>{drag.current=null;setFrozen(null);}} onPointerCancel={()=>{drag.current=null;setFrozen(null);}}>
        <path d={value} fill="none" stroke="var(--ds-accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" pointerEvents="none" />
        {parsed.points.map((p,i)=><g key={i}>
          {p.kind==="C" && <><line x1={parsed.points[i-1]?.x ?? p.x} y1={parsed.points[i-1]?.y ?? p.y} x2={p.c1x} y2={p.c1y} stroke="var(--ds-text-subtle)" vectorEffect="non-scaling-stroke"/><line x1={p.x} y1={p.y} x2={p.c2x} y2={p.c2y} stroke="var(--ds-text-subtle)" vectorEffect="non-scaling-stroke"/></>}
          {(["anchor",...(p.kind==="C"?["first","second"]:[])] as ("anchor"|"first"|"second")[]).map((part)=><circle key={part} cx={part==="anchor"?p.x:part==="first"?p.c1x:p.c2x} cy={part==="anchor"?p.y:part==="first"?p.c1y:p.c2y} r={radius} fill={part==="anchor"?"var(--ds-accent)":"var(--ds-surface)"} stroke="var(--ds-accent)" vectorEffect="non-scaling-stroke" tabIndex={disabled?-1:0} role="button" aria-label={part==="anchor"?`Anchor ${i+1}`:`${part==="first"?"First":"Second"} control ${i+1}`} onPointerDown={(e)=>begin(e,i,part)} onKeyDown={(e)=>key(e,i,part)} onClick={(e)=>{e.stopPropagation();setSelected(i);}} />)}
        </g>)}
      </svg>
      {parsed.points.length>0 && <><label>Selected point<Select aria-label="Selected path point" value={selected} disabled={disabled} onChange={(e)=>setSelected(Number(e.target.value))}>{parsed.points.map((p,i)=><option key={i} value={i}>Point {i+1} · {p.kind}</option>)}</Select></label>
        {point && <div>{Object.entries(coordinates).map(([name,raw])=><p className="ud-edit-field" key={name}><label htmlFor={`path-coordinate-${name}`}>{({x:"Point X",y:"Point Y",c1x:"First control X",c1y:"First control Y",c2x:"Second control X",c2y:"Second control Y"})[name]}</label><Input id={`path-coordinate-${name}`} value={raw} disabled={disabled} inputMode="decimal" onChange={(e)=>setCoordinates((old)=>({...old,[name]:e.target.value}))}/></p>)}<Button disabled={disabled} onClick={applyCoordinates}>Apply coordinates</Button>{" "}<Button disabled={disabled} onClick={()=>{const points=parsed.points.filter((_,i)=>i!==selected).map((p,i)=>i===0?{kind:"M" as const,x:p.x,y:p.y}:p);update({...parsed,points});setSelected(Math.max(0,selected-1));}}>Delete point</Button></div>}</>}
    </> : <p className="muted">This path is outside the visual editor's single-contour M/L/C/Z subset or 128-point limit. Its original data is preserved. Edit raw data, or explicitly reset a draft below.</p>}
    <p className="ud-edit-field"><label htmlFor="ud-path-data">SVG path data</label><Textarea id="ud-path-data" value={value} maxLength={32768} rows={4} disabled={disabled} onChange={(e)=>{setValue(e.target.value);setDirty(true);setSelected(0);}}/></p>
    {!parsed && <Button disabled={disabled} onClick={()=>{setValue("M 0 0 L 100 0 L 100 100 Z");setDirty(true);setSelected(0);}}>Reset to editable draft</Button>}
    {error && <p role="alert">{error}</p>}
    <div className="ud-actions"><Button disabled={disabled || (!creating&&!dirty)} onClick={()=>{if(creating&&(!value.trim()||(parsed&&parsed.points.length<2))){setError("Draw at least two points before creating the path.");return;}onSave(value);}}>Save path</Button><Button disabled={disabled} onClick={()=>{setValue(source);setDirty(false);setSelected(0);}}>Revert path draft</Button>{onCancel&&<Button disabled={disabled} onClick={onCancel}>Cancel new path</Button>}</div>
  </section>;
}
