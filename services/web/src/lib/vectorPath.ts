export type PathPoint={kind:"M"|"L"|"C";x:number;y:number;c1x?:number;c1y?:number;c2x?:number;c2y?:number};
export type EditablePath={points:PathPoint[];closed:boolean};
export const MAX_PATH_POINTS=128;
export function parseEditablePath(d:string):EditablePath|null{
  if(d.length>32768)return null;
  const tokens:string[]=[];const pattern=/\s+|,|[A-Za-z]|[+-]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][+-]?\d+)?/gy;let at=0;
  while(at<d.length){pattern.lastIndex=at;const match=pattern.exec(d);if(!match)return null;at=pattern.lastIndex;if(!/^[\s,]+$/.test(match[0]))tokens.push(match[0]);}
  if(!tokens.length)return {points:[],closed:false};
  const points:PathPoint[]=[];let index=0,command="",closed=false;
  while(index<tokens.length){
    if(/^[A-Za-z]$/.test(tokens[index]))command=tokens[index++];
    if(command==="Z"){if(index!==tokens.length || !points.length)return null;closed=true;break;}
    if(!["M","L","C"].includes(command) || (points.length===0 && command!=="M") || (points.length>0 && command==="M"))return null;
    const count=command==="C"?6:2,values=tokens.slice(index,index+count).map(Number);
    if(values.length!==count || values.some((n)=>!Number.isFinite(n)||Math.abs(n)>100000))return null;
    index+=count;points.push(command==="C"?{kind:"C",c1x:values[0],c1y:values[1],c2x:values[2],c2y:values[3],x:values[4],y:values[5]}:{kind:command as "M"|"L",x:values[0],y:values[1]});
    if(points.length>MAX_PATH_POINTS)return null;
    if(command==="M")command="L";
  }
  return {points,closed};
}
export function pathData(path:EditablePath){const number=(n:number)=>String(Math.round(n*1000)/1000);return path.points.map((p)=>p.kind==="C"?`C ${[p.c1x!,p.c1y!,p.c2x!,p.c2y!,p.x,p.y].map(number).join(" ")}`:`${p.kind} ${number(p.x)} ${number(p.y)}`).join(" ")+(path.closed&&path.points.length?" Z":"");}
