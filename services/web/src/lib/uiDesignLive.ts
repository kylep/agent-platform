import { api } from "../api";

type Listener=(event:{type:string;version?:number})=>void;
const listeners=new Map<string,Set<Listener>>();let events:EventSource|null=null;
export function watchUiDesign(slug:string,listener:Listener){
  const room=listeners.get(slug)??new Set<Listener>();room.add(listener);listeners.set(slug,room);
  if(!events&&typeof EventSource!=="undefined"){
    events=new EventSource("/api/ui-designer/events",{withCredentials:true});
    events.addEventListener("design",(message)=>{try{const event=JSON.parse(message.data);if(typeof event.slug!=="string"||!["version","restored","imported","deleted"].includes(event.type))return;for(const callback of listeners.get(event.slug)??[])callback(event);}catch{/* ignore malformed stream frames */}});
  }
  return()=>{room.delete(listener);if(!room.size)listeners.delete(slug);if(!listeners.size){events?.close();events=null;}};
}
type Head={controller:AbortController;promise:Promise<number>;users:number;done:boolean};
const heads=new Map<string,Head>();
export function readUiHead(slug:string,signal:AbortSignal):Promise<number>{
  if(signal.aborted)return Promise.reject(new DOMException("Aborted","AbortError"));
  let head=heads.get(slug);
  if(!head||head.controller.signal.aborted){
    const controller=new AbortController();const created:Head={controller,users:0,done:false,promise:Promise.resolve(0)};
    created.promise=api<{version:number}>(`/api/ui-designer/docs/${encodeURIComponent(slug)}/versions`,{signal:controller.signal}).then((r)=>{if(!Number.isSafeInteger(r.version)||r.version<1)throw new Error("Unavailable design");return r.version;}).finally(()=>{created.done=true;if(heads.get(slug)===created)heads.delete(slug);});head=created;heads.set(slug,created);
  }
  const current=head;current.users++;
  return new Promise((resolve,reject)=>{
    let finished=false;const finish=()=>{if(finished)return;finished=true;signal.removeEventListener("abort",abort);current.users--;if(!current.users&&!current.done)current.controller.abort();};
    const abort=()=>{finish();reject(new DOMException("Aborted","AbortError"));};signal.addEventListener("abort",abort,{once:true});
    current.promise.then((version)=>{if(!finished){finish();resolve(version);}},(error)=>{if(!finished){finish();reject(error);}});
  });
}
let active=0;const queue:(()=>void)[]=[];
export function thumbnailTask<T>(signal:AbortSignal,task:()=>Promise<T>):Promise<T>{return new Promise((resolve,reject)=>{
  const start=()=>{if(signal.aborted){reject(new DOMException("Aborted","AbortError"));pump();return;}active++;task().then(resolve,reject).finally(()=>{active--;pump();});};queue.push(start);pump();
});}
function pump(){while(active<3&&queue.length)queue.shift()!();}
